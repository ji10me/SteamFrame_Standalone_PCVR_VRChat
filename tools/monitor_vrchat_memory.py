#!/usr/bin/env python3
"""Bounded Frame-only memory observation, with an optional VRChat-only stop."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time


def matching_process(environ, prefix):
    return (environ.get(b'SteamAppId') == b'438100'
            and environ.get(b'STEAM_COMPAT_DATA_PATH') == os.fsencode(prefix))


def critical_memory(available, swap_free):
    return available < 750 * 1024**2 and swap_free < 1024 * 1024**2


def guard_decision(available, swap_free, reserve_mib, critical_count, reserve_count):
    """An optional RAM reserve must not depend on nearly exhausting swap."""
    critical_count = critical_count + 1 if critical_memory(available, swap_free) else 0
    reserve_count = reserve_count + 1 if reserve_mib is not None and available < reserve_mib * 1024**2 else 0
    reason = ('critical-memory-and-swap' if critical_count >= 2 else
              'sustained-low-available-ram' if reserve_count >= 3 else None)
    return critical_count, reserve_count, reason


def fresh_log_counts(directory, start_epoch):
    logs = []
    for path in Path(directory).glob('output_log*.txt'):
        try:
            modified = path.stat().st_mtime
            if modified >= start_epoch:
                logs.append((modified, path))
        except OSError:
            continue
    if not logs:
        return None
    try:
        content = max(logs, key=lambda item: item[0])[1].read_text(errors='replace')
    except OSError:
        return None
    return {marker: content.count(marker) for marker in
            ('Entering Room', 'Successfully joined', 'OnPlayerJoined', 'Exception', 'Error')}


def drm_memory_clients(proc):
    """Read driver counters once per DRM client, without double-counting dup fds."""
    clients = {}
    try:
        descriptors = list((proc / 'fd').iterdir())
    except OSError:
        return clients
    names = {'drm-total-memory': 'total_bytes', 'drm-resident-memory': 'resident_bytes',
             'drm-shared-memory': 'shared_bytes'}
    for descriptor in descriptors:
        try:
            if not str(descriptor.readlink()).startswith('/dev/dri/'):
                continue
            fields = dict(line.split(':', 1) for line in (proc / 'fdinfo' / descriptor.name).read_text().splitlines()
                          if line.startswith('drm-') and ':' in line)
            client = fields.get('drm-client-id', 'fd-' + descriptor.name).strip()
            counters = {}
            for field, label in names.items():
                if field not in fields:
                    continue
                parts = fields[field].split()
                scale = {'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}.get(parts[1] if len(parts) > 1 else '', 1)
                counters[label] = int(parts[0]) * scale
            clients[client] = counters
        except (OSError, ValueError, IndexError):
            continue
    return clients


def safe_name(value):
    if not re.fullmatch(r'[a-z0-9-]+', value):
        raise argparse.ArgumentTypeError('Use lowercase letters, digits and hyphens')
    return value


def save_private_json(output, report):
    with tempfile.NamedTemporaryFile('w', dir=output.parent, prefix='.memory-watch-', delete=False) as handle:
        json.dump(report, handle, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=safe_name)
    parser.add_argument('--label', type=safe_name)
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--guard', action='store_true')
    parser.add_argument('--reserve-mib', type=int,
                        help='With --guard, also stop after 3 samples below this available RAM (512–2048 MiB)')
    parser.add_argument('--snapshots', action='store_true', help='Capture scoped PSS/GPU snapshots on load changes (at most 20)')
    args = parser.parse_args()
    if args.reserve_mib is not None and (not args.guard or not 512 <= args.reserve_mib <= 2048):
        parser.error('--reserve-mib requires --guard and a value between 512 and 2048')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    if not 5 <= args.seconds <= 3600:
        raise ValueError('Duration must be between 5 and 3600 seconds')
    base = Path.home() / '.local/share/frame-pcvr'
    work = base / 'runs/quality-tuning'
    trial = json.loads((work / (args.trial + '-game-trial.json')).read_text())
    observation = json.loads((work / (args.trial + '-game-observation.json')).read_text())
    prefix = Path(trial['prefix']).resolve()
    prefix.relative_to((base / 'c').resolve())
    pid = int(observation['pid'])
    proc = Path('/proc') / str(pid)
    environ = lambda: dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
    if not matching_process(environ(), str(prefix)):
        raise RuntimeError('PID no longer identifies this VRChat prefix')
    output = work / ((args.label or args.trial + '-watch') + '-monitor.json')
    records, critical_count, reserve_count = [], 0, 0
    snapshots, last_room, last_join, last_snapshot = [], -1, -1, 0.0
    deadline = time.monotonic() + args.seconds
    previous_ticks, previous_time = None, None
    while True:
        now = time.monotonic()
        sample = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'pid': pid, 'alive': proc.exists()}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            if key in ('MemAvailable', 'SwapFree'):
                sample[key] = int(value.split()[0]) * 1024
        sample['memory_pressure'] = Path('/proc/pressure/memory').read_text().strip()
        if sample['alive']:
            try:
                fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                ticks = int(fields[11]) + int(fields[12])
                if previous_ticks is not None:
                    sample['cpu_percent'] = round((ticks - previous_ticks) / os.sysconf('SC_CLK_TCK') / (now - previous_time) * 100, 2)
                previous_ticks, previous_time = ticks, now
                sample['process_memory'] = {line.split(':', 1)[0]: line.split(':', 1)[1].strip()
                                           for line in (proc / 'status').read_text().splitlines()
                                           if line.startswith(('VmRSS:', 'VmSwap:', 'Threads:'))}
                sample['drm_clients'] = drm_memory_clients(proc)
            except OSError:
                sample['alive'] = False
        stats = base / 'tools/openvr_stats'
        if stats.exists():
            try:
                sample['compositor'] = json.loads(subprocess.check_output([str(stats)], text=True, stderr=subprocess.DEVNULL, timeout=5))
            except (subprocess.SubprocessError, ValueError):
                sample['compositor_unavailable'] = True
        counts = fresh_log_counts(prefix / 'pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat',
                                  float(trial.get('start_epoch', 0)))
        sample['fresh_game_log_found'] = counts is not None
        if counts is not None:
            sample['log_counts'] = counts
        critical_count, reserve_count, guard_reason = guard_decision(
            sample['MemAvailable'], sample['SwapFree'], args.reserve_mib, critical_count, reserve_count)
        if args.guard and guard_reason and sample['alive']:
            try:
                identity_matches = matching_process(environ(), str(prefix))
            except OSError:
                identity_matches = False
            if identity_matches:
                from steam_cdp import evaluate
                evaluate('SteamClient.Apps.TerminateApp("438100",false)')
                sample['guard_action'] = 'requested-VRChat-only-stop-before-OOM'
                sample['guard_reason'] = guard_reason
        if args.snapshots and sample['alive'] and not sample.get('guard_action'):
            counts = sample.get('log_counts', {})
            rooms, joins = counts.get('Entering Room', 0), counts.get('OnPlayerJoined', 0)
            reason = ('initial' if last_room < 0 else 'room-change' if rooms != last_room else
                      'join-event-increase-not-simultaneous-count' if joins >= last_join + 5 else
                      'low-system-memory' if sample['MemAvailable'] < 1500 * 1024**2 and now - last_snapshot >= 30 else None)
            if reason and now - last_snapshot >= 15 and len(snapshots) < 20:
                try:
                    from snapshot_vrchat_memory import snapshot
                    detail = snapshot('/proc', str(prefix), pid, '/sys/block/zram0/mm_stat')
                    detail.update(reason=reason, room_entries=rooms, join_events=joins)
                    snapshots.append(detail)
                    save_private_json(output.with_name(output.stem + '-snapshots.json'), snapshots)
                    last_room, last_join, last_snapshot = rooms, joins, now
                    sample['snapshot_reason'] = reason
                except (OSError, RuntimeError, ValueError):
                    sample['snapshot_unavailable'] = True
        records.append(sample)
        report = {'trial': args.trial, 'guard_enabled': args.guard, 'duration_seconds': args.seconds,
                  'thresholds_MiB': {'available': 750, 'swap_free': 1024}, 'records': records}
        if args.reserve_mib is not None:
            report['available_ram_reserve_MiB'] = args.reserve_mib
            report['reserve_consecutive_samples'] = 3
        save_private_json(output, report)
        if len(records) % 3 == 1 or sample.get('guard_action') or not sample['alive']:
            print(json.dumps(sample), flush=True)
        if sample.get('guard_action') or not sample['alive'] or time.monotonic() >= deadline:
            break
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    print('Saved ' + str(output), flush=True)


if __name__ == '__main__':
    main()
