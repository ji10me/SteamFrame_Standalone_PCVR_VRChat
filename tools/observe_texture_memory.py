#!/usr/bin/env python3
"""Read VRChat's startup VRAM report before staging a texture-streaming trial."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re

from framectl import sha256
from monitor_vrchat_memory import matching_process, safe_name


def startup_vram_mib(content):
    # Only accept standalone engine diagnostic lines, not arbitrary chat text.
    content = content.split('Entering Room', 1)[0]
    values = set()
    prefix = r'^\s*(?:\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+Log\s+-\s*)?'
    patterns = [re.compile(prefix + r'VRAM:\s*(\d+)\s*(?:MB|MiB)\s*$', re.I),
                re.compile(prefix + r'(?:-\s+SystemInfo\.)?"?graphicsMemorySize"?\s*[:=]\s*(\d+)\s*,?\s*$', re.I)]
    for line in content.splitlines():
        for pattern in patterns:
            match = pattern.fullmatch(line)
            if match:
                values.add(int(match[1]))
    return next(iter(values)) if len(values) == 1 else None


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
    candidate = Path(trial['candidate']).resolve()
    candidate.relative_to((base / 'candidates').resolve())
    proc = Path('/proc') / str(int(observation['pid']))
    def identity():
        return dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
    env = identity()
    if not matching_process(env, str(prefix)) or (proc / 'comm').read_text().strip() != 'VRChat.exe':
        raise RuntimeError('The recorded PID no longer identifies this VRChat launch')
    start = float(trial['start_epoch'])
    logs = []
    directory = prefix / 'pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat'
    for path in directory.glob('output_log*.txt'):
        modified = path.stat().st_mtime
        if modified >= start:
            logs.append((modified, path))
    if not logs:
        raise RuntimeError('No fresh game log; never substitute the cloned prefix history')
    log = max(logs, key=lambda item: item[0])[1]
    # Graphics startup diagnostics are near the beginning; do not copy game log text.
    with log.open('rb') as handle:
        vram = startup_vram_mib(handle.read(1024 * 1024).decode(errors='replace'))
    dll = Path.home() / '.local/share/Steam/steamapps/common/Proton - Experimental/files/lib/wine/dxvk/x86_64-windows/dxgi.dll'
    supported = b'dxgi.maxDeviceMemory' in dll.read_bytes()
    if not matching_process(identity(), str(prefix)):
        raise RuntimeError('VRChat launch identity changed during collection')
    record = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'appid': '438100',
              'candidate': str(candidate), 'prefix': str(prefix), 'pid': int(proc.name),
              'start_epoch': start, 'fresh_game_log': True,
              'DXVK_CONFIG': env.get(b'DXVK_CONFIG', b'').decode(),
              'dxgi_memory_reporting_supported': supported, 'dxgi_dll_sha256': sha256(dll),
              'reported_device_memory_MiB': vram,
              'report_found': vram is not None,
              'streaming_budget_verified': False, 'texture_residency_reduction_verified': False}
    output = work / (args.label + '-base-environment.json')
    with output.open('w') as handle:
        os.chmod(output, 0o600)
        json.dump(record, handle, indent=2)
    print(json.dumps({'saved': str(output), 'reported_device_memory_MiB': vram,
                      'dxgi_memory_reporting_supported': supported,
                      'eligible_report_targets_MiB': [target for target in (4096, 2048)
                                                     if supported and vram is not None and vram > target]}))


if __name__ == '__main__':
    main()
