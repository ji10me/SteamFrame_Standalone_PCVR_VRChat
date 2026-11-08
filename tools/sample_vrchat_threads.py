#!/usr/bin/env python3
"""Bounded read-only CPU thread samples, scoped to one private VRChat launch."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import time

from monitor_vrchat_memory import matching_process, safe_name, save_private_json


def task_counters(text):
    # comm may contain spaces and parentheses; fields start after the last ')'.
    fields = text.rsplit(')', 1)[1].split()
    return {'ticks': int(fields[11]) + int(fields[12]), 'start_ticks': int(fields[19])}


def role(comm):
    for prefix, label in (('VRChat', 'guest-default'), ('UnityMain', 'unity-main'), ('Gfx', 'graphics'),
                          ('UnityGfx', 'graphics'), ('dxvk-submit', 'dxvk-submit'),
                          ('dxvk-', 'dxvk-other'), ('Job.Worker', 'unity-job'),
                          ('Background Job', 'unity-background-job'), ('Async', 'async'),
                          ('FEX', 'fex'), ('threaded-ml', 'audio')):
        if comm.startswith(prefix):
            return label
    return 'other'  # Never export arbitrary names, paths, game/user identifiers.


def read_threads(proc_root, pid, prefix, start_ticks=None):
    proc = Path(proc_root) / str(pid)

    def identity():
        env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
        start = task_counters((proc / 'stat').read_text())['start_ticks']
        if not matching_process(env, str(prefix)) or (start_ticks is not None and start != start_ticks):
            raise RuntimeError('VRChat launch identity changed; stop sampling')
        return start

    initial_start = identity()
    result = {}
    for task in (proc / 'task').glob('[0-9]*'):
        try:
            counters = task_counters((task / 'stat').read_text())
            counters['role'] = 'main' if int(task.name) == pid else role((task / 'comm').read_text().strip())
            result[int(task.name)] = counters
        except (OSError, ValueError, IndexError):
            continue
    if identity() != initial_start:
        raise RuntimeError('VRChat process was replaced while sampling')
    return initial_start, result


def delta(previous, current, seconds, hz):
    if seconds <= 0 or hz <= 0:
        raise ValueError('Positive duration and clock rate required')
    rows = []
    for tid, now in current.items():
        old = previous.get(tid)
        if not old or old['start_ticks'] != now['start_ticks'] or now['ticks'] < old['ticks']:
            continue
        rows.append({'tid': tid, 'role': now['role'],
                     'cpu_percent': round((now['ticks'] - old['ticks']) / hz / seconds * 100, 2)})
    return sorted(rows, key=lambda row: (-row['cpu_percent'], row['tid']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=safe_name)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--label', type=safe_name, required=True)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    if not 5 <= args.seconds <= 600:
        raise ValueError('Duration must be 5 to 600 seconds')
    base = Path.home() / '.local/share/frame-pcvr'
    work = base / 'runs/quality-tuning'
    trial = json.loads((work / (args.trial + '-game-trial.json')).read_text())
    observation = json.loads((work / (args.trial + '-game-observation.json')).read_text())
    prefix = Path(trial['prefix']).resolve()
    prefix.relative_to((base / 'c').resolve())
    pid = int(observation['pid'])
    started, previous = read_threads('/proc', pid, prefix)
    last = time.monotonic()
    deadline = last + args.seconds
    report = {'pid': pid, 'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'), 'records': [],
              'notes': '100% equals one CPU core. Disappeared/new/reused threads are excluded from deltas; '
                       'these samples are not a complete launch CPU total. Thread names are reduced to roles.'}
    output = work / (args.label + '-threads.json')
    while time.monotonic() < deadline:
        time.sleep(min(2, max(0, deadline - time.monotonic())))
        try:
            _, current = read_threads('/proc', pid, prefix, started)
        except (OSError, RuntimeError):
            report['stopped_reason'] = 'process-exited-or-identity-changed'
            break
        now = time.monotonic()
        rows = delta(previous, current, now - last, report['clock_ticks_per_second'])
        report['records'].append({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                  'interval_seconds': round(now - last, 4), 'top_threads': rows[:12]})
        previous, last = current, now
        save_private_json(output, report)
    save_private_json(output, report)
    print(json.dumps({'saved': str(output), 'samples': len(report['records'])}))


if __name__ == '__main__':
    main()
