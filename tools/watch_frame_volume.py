#!/usr/bin/env python3
"""Read only Frame volume-key transitions and output volume for a bounded period."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import platform
import selectors
import struct
import subprocess
import tempfile
import time

KEYS = {113: 'mute', 114: 'down', 115: 'up'}
EVENT = struct.Struct('@llHHi')


def pressed(fd):
    bits = bytearray(96)
    fcntl.ioctl(fd, 0x80000000 | len(bits) << 16 | ord('E') << 8 | 0x18, bits, True)
    return [name for code, name in KEYS.items() if bits[code // 8] & (1 << (code % 8))]


def volumes():
    p = subprocess.run(['pactl', '-f', 'json', 'list', 'sinks'], capture_output=True, text=True, timeout=3)
    if p.returncode:
        return None
    return [{'index': r['index'], 'hardware': r.get('properties', {}).get('device.api') == 'alsa',
             'mute': r['mute'], 'channels': {k: v['value'] for k, v in r.get('volume', {}).items()}}
            for r in json.loads(p.stdout)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    if not 5 <= args.seconds <= 300:
        raise ValueError('Use 5 to 300 seconds')
    work = Path.home() / '.local/share/frame-pcvr/runs/quality-tuning'
    output = work / ('volume-watch-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    selector = selectors.DefaultSelector()
    devices, events, samples = {}, [], []
    for path in Path('/sys/class/input').glob('event*'):
        name = (path / 'device/name').read_text().strip()
        if name not in ('gpio-keys', 'pmic_resin', 'steamos-manager'):
            continue
        try:
            # O_RDONLY only; never grab devices, send events, or change key-repeat settings.
            fd = os.open('/dev/input/' + path.name, os.O_RDONLY | os.O_NONBLOCK)
            devices[fd] = {'node': path.name, 'name': name, 'initial_pressed': pressed(fd)}
            selector.register(fd, selectors.EVENT_READ)
        except OSError:
            continue
    if not devices:
        raise RuntimeError('No readable Frame volume-key devices')
    deadline, next_sample = time.monotonic() + args.seconds, 0
    print(json.dumps({'watch_started': True, 'seconds': args.seconds, 'output': str(output),
                      'devices': list(devices.values()), 'only_volume_keys_recorded': True}), flush=True)
    try:
        while time.monotonic() < deadline:
            for selected, _ in selector.select(timeout=0.1):
                data = os.read(selected.fd, EVENT.size * 64)
                for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    sec, usec, kind, code, value = EVENT.unpack_from(data, offset)
                    if kind == 1 and code in KEYS:
                        event = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                 'device': devices[selected.fd]['name'], 'key': KEYS[code],
                                 'transition': {0: 'release', 1: 'press', 2: 'repeat'}.get(value, 'unknown')}
                        events.append(event)
                        print(json.dumps(event), flush=True)
            if time.monotonic() >= next_sample:
                try:
                    state = volumes()
                except (subprocess.SubprocessError, ValueError, OSError):
                    state = None
                samples.append({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'sinks': state})
                report = {'devices': list(devices.values()), 'events': events, 'samples': samples,
                          'read_only': True, 'duration_seconds': args.seconds}
                with tempfile.NamedTemporaryFile('w', dir=work, prefix='.volume-watch-', delete=False) as handle:
                    json.dump(report, handle, indent=2)
                    temporary = Path(handle.name)
                os.replace(temporary, output)
                next_sample = time.monotonic() + 0.5
    finally:
        for fd in devices:
            selector.unregister(fd)
            os.close(fd)
        selector.close()
    print(json.dumps({'watch_finished': True, 'volume_key_events': len(events), 'output': str(output)}), flush=True)


if __name__ == '__main__':
    main()
