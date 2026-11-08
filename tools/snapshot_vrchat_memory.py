#!/usr/bin/env python3
"""Snapshot one private VRChat launch's PSS, FEX allocations and GPU counters."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import tempfile

from monitor_vrchat_memory import drm_memory_clients, matching_process, safe_name


def categories_kib(content):
    """Retain allocation classes and sizes, without addresses or mapped paths."""
    result, category = {}, 'other'
    for line in content.splitlines():
        if re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line):
            parts = line.split(maxsplit=5)
            name = parts[5] if len(parts) == 6 else ''
            category = name if re.fullmatch(r'\[anon:FEX[A-Za-z0-9_]+\]', name) else 'other'
        elif line.startswith(('Pss:', 'SwapPss:', 'AnonHugePages:')):
            name, value = line.split(':', 1)
            values = result.setdefault(category, {})
            values[name] = values.get(name, 0) + int(value.split()[0])
    return result


def launch_processes(proc_root, prefix):
    result = []
    for proc in Path(proc_root).glob('[0-9]*'):
        try:
            def identity():
                return dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
            if not matching_process(identity(), str(prefix)):
                continue
            categories = categories_kib((proc / 'smaps').read_text())
            gpu = drm_memory_clients(proc)
            name = (proc / 'comm').read_text().strip()
            # Reject a process that exec'd out of this launch while reading it.
            if not matching_process(identity(), str(prefix)):
                continue
            result.append({'pid': int(proc.name), 'name': name, 'categories_kib': categories,
                           'pss_kib': sum(v.get('Pss', 0) for v in categories.values()),
                           'swap_pss_kib': sum(v.get('SwapPss', 0) for v in categories.values()),
                           'drm_clients': gpu})
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda row: (-(row['pss_kib'] + row['swap_pss_kib']), row['pid']))


def snapshot(proc_root, prefix, main_pid, zram_path):
    rows = launch_processes(proc_root, prefix)
    if not any(row['pid'] == main_pid for row in rows):
        raise RuntimeError('Main PID no longer belongs to this VRChat launch')
    meminfo = {}
    for line in (Path(proc_root) / 'meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree', 'AnonPages', 'Shmem', 'Slab', 'PageTables'):
            meminfo[key] = int(value.split()[0]) * 1024
    zram = None
    try:
        values = [int(x) for x in Path(zram_path).read_text().split()]
        if len(values) >= 3:
            zram = dict(zip(('original_bytes', 'compressed_bytes', 'physical_bytes'), values[:3]))
    except OSError:
        pass
    return {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'pid': main_pid,
            'processes': rows, 'launch_pss_kib': sum(row['pss_kib'] for row in rows),
            'launch_swap_pss_kib': sum(row['swap_pss_kib'] for row in rows),
            'system_bytes': meminfo, 'zram': zram,
            'accounting': 'PSS and swap are separate; GPU counters may overlap PSS or other clients. '
                          'Do not sum these as independent RAM allocations. Zram uses physical RAM.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=safe_name)
    parser.add_argument('--label', type=safe_name, required=True)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    base = Path.home() / '.local/share/frame-pcvr'
    work = base / 'runs/quality-tuning'
    trial = json.loads((work / (args.trial + '-game-trial.json')).read_text())
    observation = json.loads((work / (args.trial + '-game-observation.json')).read_text())
    prefix = Path(trial['prefix']).resolve()
    prefix.relative_to((base / 'c').resolve())
    report = snapshot('/proc', prefix, int(observation['pid']), '/sys/block/zram0/mm_stat')
    report.update(candidate=trial['candidate'], prefix=str(prefix))
    output = work / (args.label + '-memory-snapshot.json')
    with tempfile.NamedTemporaryFile('w', dir=work, prefix='.memory-snapshot-', delete=False) as handle:
        json.dump(report, handle, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, output)
    print(json.dumps({'saved': str(output), 'pid': report['pid'], 'process_count': len(report['processes']),
                      'launch_pss_kib': report['launch_pss_kib'], 'launch_swap_pss_kib': report['launch_swap_pss_kib'],
                      'system_bytes': report['system_bytes'], 'zram': report['zram']}))


if __name__ == '__main__':
    main()
