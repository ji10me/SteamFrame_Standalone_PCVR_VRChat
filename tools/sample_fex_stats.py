#!/usr/bin/env python3
"""Read FEX's published Linux64 counters, never the game's address space.

Supports the pinned FEX v2/128-byte diagnostic format. Counters are not a
transactional snapshot or exclusive CPU timings. No FEX configuration changes.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import struct
import time

LIMIT = 4 * 1024 * 1024
KEYS = ('jit_ticks', 'signal_ticks', 'sigbus_count', 'smc_count',
        'float_fallback_count', 'cache_miss_count', 'cache_read_lock_ticks',
        'cache_write_lock_ticks', 'jit_count', 'disk_cache_hit_count',
        'disk_cache_miss_count', 'disk_cache_lookup_ticks', 'invalidation_count',
        'invalidation_ticks', 'callret_flush_count')


def parse_stats(data):
    if not 64 <= len(data) <= LIMIT:
        raise ValueError('Invalid published stats length')
    version, app, stride = struct.unpack_from('<BBH', data)
    head, size = struct.unpack_from('<II', data, 52)
    if (version, app, stride) != (2, 1, 128) or not 64 <= size <= len(data):
        raise ValueError('Unsupported or changing published stats format')
    rows, seen = {}, set()
    while head:
        if head < 64 or (head - 64) % stride or head + stride > size or head in seen:
            raise ValueError('Invalid or changing published stats chain')
        seen.add(head)
        nxt, tid = struct.unpack_from('<II', data, head)
        if tid:
            if tid in rows:
                raise ValueError('Changing published thread slots')
            rows[tid] = {'slot': head, 'counters': dict(zip(KEYS, struct.unpack_from('<15Q', data, head + 8)))}
        head = nxt
    return rows


def process_start(proc):
    return int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])


def identity(proc, prefix, expected=None):
    before = process_start(proc)
    environment = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
    if (proc.stat().st_uid != os.getuid() or (proc / 'comm').read_text().strip() != 'VRChat.exe'
            or environment.get(b'SteamAppId') != b'438100'
            or environment.get(b'STEAM_COMPAT_DATA_PATH') != os.fsencode(prefix)
            or (expected is not None and before != expected) or process_start(proc) != before):
        raise RuntimeError('Selected private VRChat identity changed')
    return before


def snapshot(fd, proc, prefix, started):
    identity(proc, prefix, started)
    # Retry on slot churn; these are published counters with no reader lock.
    for _ in range(3):
        first = os.pread(fd, LIMIT + 1, 0)
        try:
            rows = parse_stats(first)
            tasks = {}
            for tid in rows:
                try:
                    tasks[tid] = process_start(proc / 'task' / str(tid))
                except (OSError, ValueError, IndexError):
                    continue
            second = os.pread(fd, LIMIT + 1, 0)
            again = parse_stats(second)
        except ValueError:
            continue
        accepted = {}
        for tid, row in again.items():
            old = rows.get(tid)
            if old is None or old['slot'] != row['slot'] or tid not in tasks:
                continue
            try:
                if process_start(proc / 'task' / str(tid)) != tasks[tid]:
                    continue
            except (OSError, ValueError, IndexError):
                continue
            if any(row['counters'][k] < old['counters'][k] for k in KEYS):
                continue
            accepted[tid] = {**row, 'start': tasks[tid]}
        identity(proc, prefix, started)
        return accepted
    raise RuntimeError('Published stats changed or were malformed during sampling')


def difference(previous, current):
    total = {key: 0 for key in KEYS}
    matched = {}
    for tid, row in current.items():
        old = previous.get(tid)
        if old is None or (old['slot'], old['start']) != (row['slot'], row['start']):
            continue
        delta = {k: row['counters'][k] - old['counters'][k] for k in KEYS}
        if any(v < 0 for v in delta.values()):
            continue
        matched[tid] = delta
        for key in KEYS:
            total[key] += delta[key]
    # Empty coverage is unavailable, never an all-zero successful measurement.
    return {'matched_threads': len(matched), 'totals': total if matched else None}, matched


def open_stats(proc):
    # A same-UID stale file with this PID's name is not proof of publication.
    # Match the diagnostic mapping's device/inode without exporting addresses.
    mapped = set()
    for line in (proc / 'maps').read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5] == f'/dev/shm/fex-{proc.name}-stats':
            major, minor = (int(v, 16) for v in fields[3].split(':'))
            mapped.add((os.makedev(major, minor), int(fields[4])))
    for path in (proc / 'root/dev/shm' / f'fex-{proc.name}-stats',
                 Path('/dev/shm') / f'fex-{proc.name}-stats'):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1 or metadata.st_mode & 0o022
                or not 64 <= metadata.st_size <= LIMIT
                or (metadata.st_dev, metadata.st_ino) not in mapped):
            os.close(fd)
            raise RuntimeError('Untrusted or incomplete published stats file')
        return fd
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.pid < 1 or not args.prefix.is_absolute() or not 5 <= args.seconds <= 120:
        parser.error('Use a positive PID, absolute prefix, and 5–120 seconds')
    os.umask(0o077)
    # Exclusive creation: do not overwrite a previous observation or follow a symlink.
    with args.output.open('x') as output:
        report = {'pid': args.pid, 'available': False, 'samples': [],
                  'game_settings_changed': False, 'halfbarrier_executions_measured': False,
                  'notes': 'Non-transactional published diagnostics; matched surviving thread slots only. '
                           'Timings overlap and include waits; SIGBUS is not all alignment faults. '
                           'No game memory, names, account IDs, or gaze coordinates collected.'}
        fd = None
        try:
            proc = Path('/proc') / str(args.pid)
            started = identity(proc, args.prefix)
            fd = open_stats(proc)
            if fd is None:
                report['reason'] = 'published-stats-unavailable'
                return
            report['available'] = True
            report['start_ticks'] = started
            last = time.monotonic()
            cpu_start = time.process_time()
            previous = snapshot(fd, proc, args.prefix, started)
            deadline = last + args.seconds
            while time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                begin = time.monotonic_ns()
                current = snapshot(fd, proc, args.prefix, started)
                now = time.monotonic()
                summary, matched = difference(previous, current)
                report['samples'].append({'host_before_ns': begin, 'host_after_ns': time.monotonic_ns(),
                                          'epoch_s': time.time(), 'interval_seconds': now - last,
                                          **summary, 'main_delta': matched.get(args.pid)})
                previous, last = current, now
            report['collector_cpu_seconds'] = time.process_time() - cpu_start
        except BaseException as error:
            report['error'] = type(error).__name__
            raise
        finally:
            if fd is not None:
                os.close(fd)
            json.dump(report, output, indent=2)


if __name__ == '__main__':
    main()
