#!/usr/bin/env python3
"""Bounded VRChat-only native Frame performance recording over existing Steam IPC."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import time

from monitor_vrchat_memory import matching_process, safe_name, save_private_json
from steam_cdp import evaluate

FIELDS = ('timestamp(ms)', 'dashboard', 'fps', 'peakCpu(ms)', 'peakGpu(ms)', 'reprojected',
          'peakGpu(MHz)', 'peakPower(W)', 'refresh(Hz)', 'throttle', 'width', 'height')
PATTERN = 'perfrecording-steam.app.438100-*.csv'


def controller_module(source):
    """Resolve the controller imported by the installed UI's exact recording toggle."""
    setter = re.search(r'([\w$]+)\.p\.SteamVR\.SetHMDSettings\(\{record_vr_stats:[\w$]+\}\)', source)
    if not setter:
        raise RuntimeError('Steam UI recording control changed; inspect before using it')
    headers = list(re.finditer(r'[,\{]\d+:\(\([\w$]+,[\w$]+,([\w$]+)\)=>\{', source[:setter.start()]))
    if not headers:
        raise RuntimeError('Steam UI module boundary is unavailable')
    header = headers[-1]
    expression = (r'(?<![\w$])' + re.escape(setter[1]) + r'\s*=\s*'
                  + re.escape(header[1]) + r'\((\d+)\)')
    imported = re.search(expression, source[header.end():setter.start()])
    if not imported:
        raise RuntimeError('Steam UI controller import is unavailable')
    return int(imported[1])


def set_recording(module, enabled, rpc=evaluate):
    if type(enabled) is not bool or type(module) is not int or module <= 0:
        raise ValueError('Only the native recording boolean may be changed')
    literal = 'true' if enabled else 'false'
    # The existing UI uses this same one-field request. No perf graph, tracking,
    # refresh, resolution, motion smoothing, or power setting is sent.
    expression = ('(async()=>{if(!window.__framePerfRequire)'
                  'webpackChunksteamui.push([["frame-pcvr-performance-control"],{},'
                  'r=>window.__framePerfRequire=r]);'
                  f'const c=window.__framePerfRequire({module}).p;'
                  'if(!c.IsConnected()||typeof c.SteamVR.SetHMDSettings!=="function")'
                  'throw Error("SteamVR recording backend unavailable");'
                  f'await c.SteamVR.SetHMDSettings({{record_vr_stats:{literal}}});'
                  f'return {{record_request:{literal}}};' + '})()')
    result = rpc(expression)
    if result != {'record_request': enabled}:
        raise RuntimeError('Unexpected response to the native recording request')


def return_to_game(rpc=evaluate):
    # Same navigation action used by the installed UI's Return to Game handler.
    result = rpc('(()=>{if(typeof SteamClient.OpenVR?.VROverlay?.HideDashboard!=="function")'
                 'throw Error("SteamVR return-to-game action unavailable");'
                 'SteamClient.OpenVR.VROverlay.HideDashboard();'
                 'return {return_to_game_requested:true};})()')
    if result != {'return_to_game_requested': True}:
        raise RuntimeError('Return-to-game request was not acknowledged')


def visible_frames_ready(stats, pid):
    return (stats.get('pid') == pid and stats.get('scene_pid') == pid
            and stats.get('presents', 0) > 0 and stats.get('timing_available') is True
            and stats.get('scene_loading') is False and stats.get('grid_alpha', 1) < .01
            and (stats.get('timing') or {}).get('frame_index', 0) > 0)


def wait_visible_frames(tool, pid, identity, timeout=30):
    deadline = time.monotonic() + timeout
    last_index = None
    while time.monotonic() < deadline:
        identity()
        try:
            stats = json.loads(subprocess.check_output([str(tool)], text=True, stderr=subprocess.DEVNULL, timeout=5))
        except (subprocess.SubprocessError, ValueError):
            stats = {}
        if visible_frames_ready(stats, pid):
            index = stats['timing']['frame_index']
            if last_index is not None and index != last_index:
                return
            last_index = index
        else:
            last_index = None
        time.sleep(.5)
    raise RuntimeError('VRChat is not submitting visible VR frames; no performance comparison will be recorded')


def require_samples(result, require_closed=False):
    if result.get('all', {}).get('samples', 0) < 1:
        raise RuntimeError('Native recording contains zero performance samples; reject this trial')
    if require_closed and result.get('dashboard_closed', {}).get('samples', 0) < 5:
        raise RuntimeError('Native recording has insufficient dashboard-closed samples; reject this comparison')


def scope_identity(proc_root, pid, prefix, expected_start=None):
    proc = Path(proc_root) / str(pid)
    env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    started = int(fields[19])
    if (not matching_process(env, str(prefix)) or (proc / 'comm').read_text().strip() != 'VRChat.exe'
            or (expected_start is not None and started != expected_start)):
        raise RuntimeError('The selected PID is no longer this private VRChat launch')
    return started


def recording_window(module, seconds, identity, toggle=set_recording, sleep=time.sleep, clock=time.monotonic):
    if not 5 <= seconds <= 300:
        raise ValueError('Recording duration must be 5 to 300 seconds')
    identity()  # Refuse before sending a request if the launch is stale/wrong.
    try:
        toggle(module, False)
        identity()
        toggle(module, True)
        print(json.dumps({'recording': True, 'seconds': seconds}), flush=True)
        deadline = clock() + seconds
        while clock() < deadline:
            identity()
            sleep(min(1, max(0, deadline - clock())))
    finally:
        # Also runs if the start response was lost or the game exits midway.
        toggle(module, False)


def summarize(samples):
    if not samples:
        return {'samples': 0, 'seconds': 0}
    duration = sum(row['interval_s'] for row in samples)
    def percentile(key, fraction):
        values = sorted(row[key] for row in samples)
        return round(values[math.ceil(len(values) * fraction) - 1], 3)
    return {'samples': len(samples), 'seconds': round(duration, 3),
            'fps_time_weighted': round(sum(row['fps'] * row['interval_s'] for row in samples) / duration, 3),
            'cpu_interval_peak_p95_ms': percentile('peakCpu(ms)', .95),
            'cpu_interval_peak_max_ms': percentile('peakCpu(ms)', 1),
            'gpu_interval_peak_p95_ms': percentile('peakGpu(ms)', .95),
            'gpu_interval_peak_max_ms': percentile('peakGpu(ms)', 1),
            'refresh_Hz': sorted({row['refresh(Hz)'] for row in samples}),
            'throttle_fps': sorted({row['throttle'] for row in samples}),
            'resolution': sorted({(row['width'], row['height']) for row in samples}),
            'cpu_peak_over_two_vsync_samples': sum(row['peakCpu(ms)'] > 2000 / row['refresh(Hz)'] for row in samples),
            'gpu_peak_over_two_vsync_samples': sum(row['peakGpu(ms)'] > 2000 / row['refresh(Hz)'] for row in samples)}


def read_recording(path, settle_seconds=2):
    path = Path(path)
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('Native recording exceeds the bounded parser size')
    with path.open(newline='') as handle:
        rows = list(csv.reader(handle))
    start = next((i for i, row in enumerate(rows) if row and row[0].strip() == 'timestamp(ms)'), None)
    if start is None:
        raise ValueError('Native recording schema is missing')
    metadata = {row[0].strip(): row[1].strip() for row in rows[:start] if len(row) == 2}
    if metadata.get('appId') not in ('steam.app.438100', '438100'):
        raise ValueError('Refusing another application recording')
    keys = [key.strip() for key in rows[start]]
    if not set(FIELDS).issubset(keys) or len(keys) != len(set(keys)):
        raise ValueError('Native recording columns changed')
    samples, closed, settled = [], [], []
    previous, closed_duration, skipped = 0, 0, 0
    for row in rows[start + 1:]:
        if not row or not any(value.strip() for value in row):
            continue
        try:
            sample = {key: float(row[keys.index(key)].strip()) for key in FIELDS}
            if not all(math.isfinite(value) and value >= 0 for value in sample.values()):
                raise ValueError('Invalid metric')
            if sample['refresh(Hz)'] <= 0 or sample['timestamp(ms)'] <= previous or sample['dashboard'] not in (0, 1):
                raise ValueError('Invalid timestamp/state')
        except (IndexError, ValueError):
            raise ValueError('Invalid native recording row; refusing to bridge an unknown interval') from None
        sample['interval_s'] = (sample['timestamp(ms)'] - previous) / 1000
        previous = sample['timestamp(ms)']
        samples.append(sample)
        if sample['dashboard']:
            closed_duration = 0
        else:
            closed.append(sample)
            closed_duration += sample['interval_s']
            if closed_duration > settle_seconds:
                settled.append(sample)
    return {'schema': 1, 'appid': '438100', 'skipped_rows': skipped,
            'all': summarize(samples), 'dashboard_closed': summarize(closed),
            'settled_dashboard_closed': summarize(settled), 'settle_seconds_each_transition': settle_seconds,
            'samples': samples,
            'notes': 'fps is rounded by the native writer; peak CPU/GPU are interval maxima, not average CPU execution times. '
                     'Dashboard-closed samples exclude every interval that reported a visible dashboard.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=safe_name)
    parser.add_argument('--label', type=safe_name, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--return-to-game', action='store_true', help='Close the SteamVR dashboard before the recording')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    if not 5 <= args.seconds <= 300:
        parser.error('--seconds must be 5 to 300')
    base = Path.home() / '.local/share/frame-pcvr'
    work = base / 'runs/quality-tuning'
    output = work / (args.label + '-native-recording.json')
    if output.exists():
        raise RuntimeError('Retain the existing recording result instead of overwriting it')
    trial = json.loads((work / (args.trial + '-game-trial.json')).read_text())
    observation = json.loads((work / (args.trial + '-game-observation.json')).read_text())
    prefix = Path(trial['prefix']).resolve()
    prefix.relative_to((base / 'c').resolve())
    pid = int(observation['pid'])
    started = scope_identity('/proc', pid, prefix)
    source = Path.home() / '.local/share/Steam/steamui/sp.js'
    ui = source.read_text()
    module = controller_module(ui)
    logs = Path.home() / '.local/share/Steam/logs'
    before = {path.name for path in logs.glob(PATTERN)}
    report = {'appid': '438100', 'pid': pid, 'seconds_requested': args.seconds,
              'steam_ui_sha256': hashlib.sha256(ui.encode()).hexdigest(),
              'recording_start_attempted': False, 'recording_stop_requested': False}

    def toggle_recording(mid, enabled):
        action = 'start' if enabled else 'stop'
        if enabled:
            report['recording_start_attempted'] = True
            report['recording_stop_requested'] = False
        # Bound the native recording's start/stop time for correlation with
        # separately collected /proc samples; the IPC round trip is not zero.
        report[f'recording_{action}_request_epoch'] = time.time()
        set_recording(mid, enabled)
        report[f'recording_{action}_ack_epoch'] = time.time()
        if not enabled:
            report['recording_stop_requested'] = True

    def interrupted(signum, frame):
        raise InterruptedError('Native recording interrupted; requesting stop')

    old_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        if args.return_to_game:
            scope_identity('/proc', pid, prefix, started)
            return_to_game()
            report['return_to_game_requested'] = True
            time.sleep(3)
        wait_visible_frames(base / 'tools/openvr_stats', pid,
                            lambda: scope_identity('/proc', pid, prefix, started))
        recording_window(module, args.seconds, lambda: scope_identity('/proc', pid, prefix, started), toggle=toggle_recording)
        new = [path for path in logs.glob(PATTERN) if path.name not in before]
        if len(new) != 1:
            raise RuntimeError('Expected one new VRChat recording; inspect local logs without mixing sessions')
        # Stop response precedes final file flush on some builds.
        time.sleep(1)
        result = read_recording(new[0])
        report.update(result)
        require_samples(result, args.return_to_game)
        destination = work / (args.label + '.csv')
        if destination.exists():
            raise RuntimeError('Refusing to overwrite a native recording copy')
        shutil.copy2(new[0], destination)
        destination.chmod(0o600)
        report['source_recording'] = new[0].name
    except BaseException as error:
        report['error_type'] = type(error).__name__
        save_private_json(output, report)
        if report['recording_start_attempted'] and not report['recording_stop_requested']:
            print('Native recording stop was not confirmed; check Record VR Performance in the headset.', flush=True)
        raise
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    save_private_json(output, report)
    print(json.dumps({'saved': str(output), 'recording_stop_requested': report['recording_stop_requested'],
                      'dashboard_closed': report['dashboard_closed'],
                      'settled_dashboard_closed': report['settled_dashboard_closed']}, indent=2))


if __name__ == '__main__':
    main()
