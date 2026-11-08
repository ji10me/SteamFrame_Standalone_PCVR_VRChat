#!/usr/bin/env python3
"""Temporary Performance trial with an independently running thermal guard.

Default check is read-only. --run uses the root-owned measurement socket after
one-time setup. Interactive sudo is available only through explicit --sudo.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import signal
import subprocess
import sys
import threading
import time

from guard_frame_thermals import read_temperatures
from launch_vrchat_when_ready import readiness_reason, read_stats
from monitor_vrchat_memory import safe_name, save_private_json
from record_vr_performance import (controller_module, return_to_game, scope_identity, set_recording,
                                   visible_frames_ready, wait_visible_frames, read_recording, require_samples, PATTERN)
from steam_cdp import evaluate
from frame_thermal_socket import connect, service_status, SocketGuard


def measurement_ready(stats, pid, *, without_controllers=False):
    reason = readiness_reason(stats)
    tracking = reason == 'ready'
    if without_controllers and reason == 'controller_tracking_not_ready':
        # Relax only the known zero-controller case. Keep HMD tracking,
        # calibration and complete probe data mandatory.
        count = stats.get('tracked_controllers')
        tracking = type(count) is int and count == 0
    return (tracking and visible_frames_ready(stats, pid)
            and stats.get('scene_app_is_vrchat') is True and stats.get('fade_alpha', 1) < .01)


def hmd_settings(rpc=evaluate):
    """VR-side render settings only; this does not read the visible system switch."""
    result = rpc('(async()=>{if(!window.__framePerfRequire)'
                 'webpackChunksteamui.push([["frame-pcvr-performance-control"],{},r=>window.__framePerfRequire=r]);'
                 'const T=window.__framePerfRequire(76950).yn;'
                 'if(T?.getVRPathPropertyKey?.()!=="/vrgamepadui/steamvr/hmd_settings")'
                 'throw Error("HMD proto changed");'
                 'const S=window.__framePerfRequire(65074).gZ;'
                 'if(typeof S?.ListenToProtoPathProperty!=="function")throw Error("HMD store changed");'
                 'const h=S.ListenToProtoPathProperty(T);try{await new Promise(r=>setTimeout(r,500));'
                 'const v=h.value;const o=v?.toObject?v.toObject():v;'
                 'return {profile:o?.performance_profile_value,recording:o?.record_vr_stats_value};'
                 '}finally{h.unregister()}})()')
    if not isinstance(result, dict) or result.get('profile') not in ('Performance', 'Powersave') or type(result.get('recording')) is not bool:
        raise RuntimeError('HMD settings are unavailable or changed; no mode request will be sent')
    return result


def set_profile(module, name, rpc=evaluate):
    if name not in ('Performance', 'Powersave') or type(module) is not int or module <= 0:
        raise ValueError('Only the bounded Performance/Powersave trial is supported')
    result = rpc('(async()=>{'
                 f'const c=window.__framePerfRequire({module}).p;'
                 'if(!c.IsConnected()||typeof c.SteamVR.SetHMDSettings!=="function")'
                 'throw Error("SteamVR backend unavailable");'
                 'const s=window.__framePerfRequire(39828);'
                 'const key="steamos_platform_performance_profile";'
                 'if(typeof s?.qt!=="function"||!s.rV?.clientSettings)'
                 'throw Error("System profile UI changed");'
                 'if(!["Performance","Powersave"].includes(s.rV.clientSettings[key]))'
                 'throw Error("External system profile selected; retain it");'
                 f'await s.qt(key,{json.dumps(name)});'
                 f'await c.SteamVR.SetHMDSettings({{performance_profile_value:{json.dumps(name)}}});'
                 f'return {{profile_request:{json.dumps(name)}}};' + '})()')
    if result != {'profile_request': name}:
        raise RuntimeError('Native mode request was not acknowledged')


def system_profile(rpc=evaluate):
    """Read the same setting used by the visible Frame power-profile switch."""
    result = rpc('(()=>{if(!window.__framePerfRequire)'
                 'webpackChunksteamui.push([["frame-pcvr-performance-control"],{},r=>window.__framePerfRequire=r]);'
                 'return window.__framePerfRequire(39828).rV?.clientSettings?.steamos_platform_performance_profile;})()')
    if result not in ('Performance', 'Powersave'):
        raise RuntimeError('System profile UI is unavailable or external; retain it')
    return result


MANAGER = ('com.steampowered.SteamOSManager1', '/com/steampowered/SteamOSManager1',
           'com.steampowered.SteamOSManager1.PerformanceProfile1', 'PerformanceProfile')


def hardware_profile(command=subprocess.run):
    result = command(['busctl', '--user', '--timeout=3', 'get-property', *MANAGER],
                     check=True, capture_output=True, text=True, timeout=5)
    value = shlex.split(result.stdout)
    if len(value) != 2 or value[0] != 's' or value[1] not in ('Performance', 'Powersave'):
        raise RuntimeError('An external or unsupported hardware profile is selected; retain it')
    return value[1]


def set_hardware_profile(name, command=subprocess.run):
    if name not in ('Performance', 'Powersave'):
        raise ValueError('Only the bounded stock Performance/Powersave trial is supported')
    command(['busctl', '--user', '--timeout=3', 'set-property', *MANAGER, 's', name],
            check=True, capture_output=True, text=True, timeout=5)


def restore_ui(module, original, request=set_profile, recording=set_recording, hardware=None):
    errors = []
    # Independent attempts: recording failure must not skip power restoration.
    actions = ([hardware] if hardware is not None else []) + [lambda: recording(module, False), lambda: request(module, original)]
    for action in actions:
        try:
            action()
        except Exception as exc:
            errors.append(type(exc).__name__)
    return errors


def discover_launch(base, proc_root=Path('/proc')):
    matches = []
    for proc in proc_root.glob('[0-9]*'):
        try:
            if proc.stat().st_uid != os.getuid() or (proc / 'comm').read_text().strip() != 'VRChat.exe':
                continue
            env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
            if env.get(b'SteamAppId') != b'438100' or not env.get(b'STEAM_COMPAT_DATA_PATH'):
                continue
            prefix = Path(os.fsdecode(env[b'STEAM_COMPAT_DATA_PATH'])).resolve()
            prefix.relative_to((base / 'c').resolve())
            pid = int(proc.name)
            scope_identity(proc_root, pid, prefix)
            matches.append((pid, prefix))
        except (OSError, ValueError, RuntimeError):
            continue
    if len(matches) != 1:
        raise RuntimeError('Exactly one active private VRChat launch is required')
    return matches[0]


def activate_performance(module, report, active, ended, ui=set_profile, read=hmd_settings,
                         hardware=set_hardware_profile, *, hardware_only=False):
    if ended.is_set():
        raise RuntimeError('The thermal guard ended before the mode transition')
    report['mode_attempted'] = True
    report['hardware_only'] = hardware_only
    if not hardware_only:
        report['stage'] = 'request_performance'
        ui(module, 'Performance')
        report['stage'] = 'read_performance'
        deadline = time.monotonic() + 3
        while True:
            report['performance_readback'] = read()
            if report['performance_readback']['profile'] == 'Performance':
                break
            if ended.is_set() or time.monotonic() >= deadline:
                raise RuntimeError('Native Performance mode was not confirmed')
            time.sleep(.1)
    if ended.is_set():
        raise RuntimeError('The thermal guard ended before hardware activation')
    report['stage'] = 'request_hardware_performance'
    report['hardware_attempted'] = True
    hardware('Performance')
    report['stage'] = 'wait_guard_active'
    deadline = time.monotonic() + 5
    if ended.is_set():
        raise RuntimeError('The thermal guard ended during hardware activation')
    while not active.wait(.1):
        if ended.is_set() or time.monotonic() >= deadline:
            raise RuntimeError('The independent guard did not confirm factory Performance limits')
    if ended.is_set():
        raise RuntimeError('The independent guard did not confirm factory Performance limits')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=safe_name, nargs='?')
    parser.add_argument('--label', type=safe_name)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--run', action='store_true', help='Run the finite measurement through the installed service')
    parser.add_argument('--without-controllers', action='store_true',
                        help='Measure HMD rendering/thermals with controllers off; keep HMD tracking and live-frame checks')
    parser.add_argument('--hardware-only', action='store_true',
                        help='Keep the native HMD render profile; request only temporary stock hardware Performance limits')
    parser.add_argument('--sudo', action='store_true', help='Use the older interactive sudo transport explicitly')
    parser.add_argument('--service-status', action='store_true', help='Read-only installed service check; no game or mode change')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.geteuid() == 0:
        raise RuntimeError('Run as the ordinary Steam Frame user')
    if not 10 <= args.seconds <= 120:
        parser.error('--seconds must be 10–120')
    if args.service_status:
        print(json.dumps(service_status()))
        return
    args.label = args.label or ('thermal-caps-' + time.strftime('%Y%m%d%H%M%S', time.gmtime()))
    base = Path.home() / '.local/share/frame-pcvr'
    work = base / 'runs/quality-tuning'
    if args.trial:
        observation = json.loads((work / (args.trial + '-game-observation.json')).read_text())
        pid, prefix = int(observation['pid']), Path(observation['prefix']).resolve()
    else:
        pid, prefix = discover_launch(base)
    prefix.relative_to((base / 'c').resolve())
    started = scope_identity('/proc', pid, prefix)
    tool = base / 'tools/openvr_stats'
    stats = read_stats(tool)
    if not measurement_ready(stats, pid, without_controllers=args.without_controllers):
        raise RuntimeError('Tracked, calibrated HMD and visible VRChat required; controllers may be omitted with --without-controllers')
    wait_visible_frames(tool, pid, lambda: scope_identity('/proc', pid, prefix, started), timeout=5)
    stats = read_stats(tool)
    if not measurement_ready(stats, pid, without_controllers=args.without_controllers):
        raise RuntimeError('HMD tracking or VRChat visibility changed before measuring')
    settings = hmd_settings()
    if settings != {'profile': 'Powersave', 'recording': False}:
        raise RuntimeError('This trial must start from the original Powersave mode with recording off')
    initial_system_profile = system_profile()
    if initial_system_profile != settings['profile']:
        raise RuntimeError('Visible system switch and VR profile differ; no measurement or mode request')
    original_hardware_profile = hardware_profile()
    peak = max(read_temperatures().values())
    if peak >= 76:
        raise RuntimeError('Wait until the CPU/GPU sensor maximum is below 76C')
    output = work / (args.label + '-thermal-trial.json')
    raw = work / (args.label + '-thermal-guard.jsonl')
    if output.exists() or raw.exists():
        raise RuntimeError('Use a fresh label; retain the existing trial')
    ui = (Path.home() / '.local/share/Steam/steamui/sp.js').read_text()
    module = controller_module(ui)
    if 'performance_profile_value' not in ui:
        raise RuntimeError('Installed native power control changed')
    helper = Path(__file__).with_name('guard_frame_thermals.py').resolve()
    if not args.run:
        digest = hashlib.sha256(helper.read_bytes()).hexdigest()
        installed = False
        if not args.sudo:
            try:
                installed = service_status().get('helper_sha256') == digest
            except (OSError, RuntimeError, ValueError):
                pass
        print(json.dumps({'ready': installed or args.sudo, 'game_ready': True, 'pid': pid,
                          'controller_tracking_required': not args.without_controllers,
                          'hardware_only': args.hardware_only,
                          'profile': settings['profile'], 'peak_C': peak, 'seconds': args.seconds,
                          'transport': 'legacy-sudo' if args.sudo else 'system-socket',
                          'initial_service_setup_required': not installed and not args.sudo,
                          'helper_sha256': digest}))
        return

    if args.sudo:
        print('Legacy transport: sudo authenticates on this terminal only.', flush=True)
        guard = subprocess.Popen(['sudo', '--', '/usr/bin/python3', '-I', str(helper), '--pid', str(pid),
                                  '--seconds', str(args.seconds)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 text=True, bufsize=1)
    else:
        guard = SocketGuard(connect(), pid, args.seconds)
    ready, active, ended = threading.Event(), threading.Event(), threading.Event()
    records, errors = [], []
    ready_row = {}
    report = {'pid': pid, 'seconds_requested': args.seconds, 'original': settings, 'mode_attempted': False,
              'controller_tracking_required': not args.without_controllers,
              'initial_tracked_controllers': stats['tracked_controllers'],
              'original_system_profile': initial_system_profile,
              'original_manager_profile': original_hardware_profile, 'hardware_attempted': False,
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    logs = Path.home() / '.local/share/Steam/logs'
    before = {path.name for path in logs.glob(PATTERN)}

    def reader():
        try:
            with raw.open('x') as stream:
                raw.chmod(0o600)
                for line in guard.stdout:
                    row = json.loads(line)
                    stream.write(line)
                    stream.flush()
                    records.append(row)
                    if row.get('phase') == 'ready':
                        ready_row.update(row)
                        ready.set()
                    if row.get('phase') == 'active':
                        active.set()
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if isinstance(guard, SocketGuard):
                guard.finish(any(row.get('phase') == 'done' for row in records))
            ended.set()
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    def interrupted(signum, frame):
        raise InterruptedError('Measurement interrupted')
    old_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        report['stage'] = 'wait_guard_ready'
        # Authentication can take time; power stays unchanged until READY.
        while not ready.wait(.25):
            scope_identity('/proc', pid, prefix, started)
            if ended.is_set():
                raise RuntimeError('The privileged guard did not start; no power setting was changed')
        scope_identity('/proc', pid, prefix, started)
        if ready_row.get('helper_sha256') != report['helper_sha256']:
            raise RuntimeError('The root-owned helper differs from this reviewed revision; reinstall it before measuring')
        activate_performance(module, report, active, ended, hardware_only=args.hardware_only)
        if not args.hardware_only and system_profile() != 'Performance':
            raise RuntimeError('Visible system profile switch did not confirm Performance')
        if args.hardware_only:
            report['stage'] = 'verify_unchanged_render_profile'
            report['render_profile_readback'] = hmd_settings()
            if report['render_profile_readback'] != settings:
                raise RuntimeError('Native render profile changed during the hardware-only trial')
        report['stage'] = 'return_to_game'
        return_to_game()
        report['stage'] = 'recording_start'
        if ended.is_set():
            raise RuntimeError('The thermal guard ended before native recording started')
        report['recording_start_request_epoch'] = time.time()
        set_recording(module, True)
        report['recording_start_ack_epoch'] = time.time()
        print(json.dumps({'measurement': True, 'seconds': args.seconds, 'pid': pid}), flush=True)
        deadline = time.monotonic() + args.seconds + 15
        next_tracking = 0
        last_frame_index = stats['timing']['frame_index']
        report['visibility_checks'] = []
        while not ended.wait(.5):
            scope_identity('/proc', pid, prefix, started)
            if time.monotonic() >= next_tracking:
                current = read_stats(tool)
                frame_index = (current.get('timing') or {}).get('frame_index')
                progressing = frame_index is not None and frame_index != last_frame_index
                visible = measurement_ready(current, pid, without_controllers=args.without_controllers) and progressing
                report['visibility_checks'].append({'epoch': time.time(), 'visible_and_tracked': visible,
                                                    'frames_progressing': progressing,
                                                    'tracked_controllers': current.get('tracked_controllers')})
                if not visible:
                    raise RuntimeError('Required tracking, VRChat visibility or live frames lost; end this trial')
                last_frame_index = frame_index
                next_tracking = time.monotonic() + 2
            if time.monotonic() >= deadline:
                raise RuntimeError('Thermal guard exceeded the bounded measurement')
    except (Exception, KeyboardInterrupt) as exc:
        errors.append(type(exc).__name__)
        report['failure_stage'] = report.get('stage')
        # Only this measurement's own UI/settings calls are in this scope.
        # Keep diagnostic exceptions private; do not include them in summaries.
        report['private_failure_detail'] = str(exc)[:4096]
    finally:
        # This is a cancellation pipe, never a command interpreter or a password channel.
        try:
            guard.stdin.close()
        except OSError:
            pass
        if not ready.is_set() and guard.poll() is None:
            guard.terminate()  # Cancel an uncompleted sudo prompt before any mode change.
        thread.join(timeout=5)
        if report['mode_attempted']:
            hardware = (lambda: set_hardware_profile('Powersave')) if report['hardware_attempted'] else None
            errors.extend(restore_ui(module, settings['profile'], hardware=hardware))
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        report['guard_exit_code'] = guard.poll()
        report['final_system_profile'] = system_profile()
        report['errors'] = errors
        report['guard_result'] = next((row for row in reversed(records) if row.get('phase') == 'done'), None)
        samples = [row for row in records if row.get('phase') == 'sample']
        report['sensor_max_C'] = max((row['peak_C'] for row in records if 'peak_C' in row), default=None)
        report['samples'] = len(samples)
        report['cap_changes'] = sum(row.get('changed_caps', 0) for row in samples)
        try:
            report['restored_hmd_settings'] = hmd_settings()
            report['restored_manager_profile'] = hardware_profile()
            report['restored_maximums'] = {p: int(Path(p).read_text()) for p in (
                '/sys/class/devfreq/3d00000.gpu/max_freq',
                *[f'/sys/devices/system/cpu/cpufreq/policy{n}/scaling_max_freq' for n in (0, 2, 5, 7)])}
        except Exception as exc:
            errors.append('restore_readback_' + type(exc).__name__)
        time.sleep(1)
        files = [path for path in logs.glob(PATTERN) if path.name not in before]
        if len(files) == 1:
            copied = work / (args.label + '.csv')
            shutil.copy2(files[0], copied)
            copied.chmod(0o600)
            try:
                report['native_recording'] = read_recording(copied)
                require_samples(report['native_recording'], True)
            except Exception as exc:
                errors.append('native_parse_' + type(exc).__name__)
        elif 'recording_start_request_epoch' in report:
            errors.append('native_recording_file_missing_or_ambiguous')
        restored = report.get('restored_hmd_settings') == settings
        result = report.get('guard_result') or {}
        caps_restored = all(value in ('restored', 'already_original') for value in result.get('restoration', {}).values())
        expected_limits = {'/sys/class/devfreq/3d00000.gpu/max_freq': 629000000} | {
            f'/sys/devices/system/cpu/cpufreq/policy{n}/scaling_max_freq': 2035200 for n in (0, 2, 5, 7)}
        report['completed'] = bool(not errors and restored and result.get('reason') == 'finished'
                                   and result.get('active') and result.get('restoration') and caps_restored
                                   and report.get('restored_maximums') == expected_limits)
        save_private_json(output, report)
        print(json.dumps({'report': output.name, 'completed': report['completed'],
                          'guard_reason': result.get('reason'),
                          'samples': report['samples'], 'sensor_max_C': report['sensor_max_C'],
                          'restored': report.get('restored_hmd_settings'), 'errors': errors}), flush=True)
        if isinstance(guard, SocketGuard):
            guard.close()
    if not report['completed']:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
