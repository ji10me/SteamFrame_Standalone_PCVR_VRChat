#!/usr/bin/env python3
"""Finite thermal guard: only existing CPU/GPU maximum-frequency attributes.

Run through measure_frame_thermals.py. No governor, minimum frequency, voltage,
fan, thermal trip, service or persistent configuration is changed.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import time

CPU_FACTORY = {0: 2265600, 2: 3148800, 5: 2956800, 7: 3052800}
CPU_BASE = 2035200
GPU = Path('/sys/class/devfreq/3d00000.gpu')
CPU = Path('/sys/devices/system/cpu/cpufreq')
GPU_TABLE = (500, 578, 629, 680, 720, 770, 834, 903)
OWNER_FILE = Path('/var/lib/frame-pcvr-thermal/owner.uid')
PROTOCOL = 1


class StopGuard(RuntimeError):
    pass


def integer(path):
    return int(path.read_text().strip())


def helper_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def cap_paths():
    return [CPU / f'policy{n}/scaling_max_freq' for n in CPU_FACTORY] + [GPU / 'max_freq']


def decode_request(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StopGuard('duplicate_request_key')
            result[key] = value
        return result
    if len(data) > 1024:
        raise StopGuard('request_too_large')
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict) or value.get('protocol') != PROTOCOL or type(value.get('protocol')) is not int:
        raise StopGuard('unsupported_protocol')
    if value.get('operation') == 'check' and set(value) == {'protocol', 'operation'}:
        return value
    if (set(value) != {'protocol', 'operation', 'pid', 'seconds'} or value.get('operation') != 'measure'
            or type(value.get('pid')) is not int or not 0 < value['pid'] < 2**31
            or type(value.get('seconds')) is not int or not 10 <= value['seconds'] <= 120):
        raise StopGuard('unsupported_request')
    return value


def socket_request(connection, owner):
    """Authenticate the kernel-supplied peer UID before accepting fixed fields."""
    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != owner or uid == 0:
        raise StopGuard('peer_not_allowed')
    connection.settimeout(5)
    data = bytearray()
    while len(data) <= 1024:
        char = connection.recv(1)
        if not char:
            raise StopGuard('incomplete_request')
        if char == b'\n':
            return decode_request(data)
        data += char
    raise StopGuard('request_too_large')


def read_temperatures(root=Path('/sys/class/thermal')):
    values = {}
    for zone in root.glob('thermal_zone*'):
        name = (zone / 'type').read_text().strip()
        if name.startswith(('cpu', 'gpu')):
            value = integer(zone / 'temp') / 1000
            if not math.isfinite(value) or not 0 < value < 120:
                raise StopGuard('invalid_temperature')
            values[name] = value
    if not any(name.startswith('cpu') for name in values) or not any(name.startswith('gpu') for name in values):
        raise StopGuard('temperature_sensors_missing')
    return values


def identity(pid, owner, expected=None):
    proc = Path('/proc') / str(pid)
    if proc.stat().st_uid != owner or (proc / 'comm').read_text().strip() != 'VRChat.exe':
        raise StopGuard('game_identity_changed')
    env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
    started = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
    if env.get(b'SteamAppId') != b'438100' or (expected is not None and started != expected):
        raise StopGuard('game_identity_changed')
    return started


def next_cap(table, current, direction, cpu=False):
    if direction < 0:
        candidates = [v for v in table if v < current]
        if cpu:
            reduced = [v for v in candidates if v <= current * .95]
            return max(reduced) if reduced else min(candidates, default=current)
        return max(candidates, default=current)
    return min((v for v in table if v > current), default=current)


class ThermalPolicy:
    def __init__(self):
        self.last_change = -math.inf
        self.cool_since = None

    def update(self, now, peak):
        if not math.isfinite(peak) or not 0 < peak < 120:
            raise StopGuard('invalid_temperature')
        if peak >= 88:
            return 'stop'
        if peak >= 80:
            self.cool_since = None
            interval = .5 if peak >= 84 else 2
            if now - self.last_change >= interval:
                self.last_change = now
                return 'down'
        elif peak <= 76:
            if self.cool_since is None:
                self.cool_since = now
            if now - self.cool_since >= 10 and now - self.last_change >= 10:
                self.last_change = now
                self.cool_since = now
                return 'up'
        else:
            self.cool_since = None
        return 'hold'


def emit(value):
    # The control loop must not block when a consumer stops reading metrics.
    try:
        os.write(1, (json.dumps(value, separators=(',', ':')) + '\n').encode())
    except BlockingIOError:
        pass


def caller_alive():
    try:
        value = os.read(0, 32)
    except BlockingIOError:
        return
    # Any received byte or EOF means cancel; there is no command protocol.
    raise StopGuard('caller_stop_or_disconnect')


def cap_matches(path, target, previous, read=integer, *, clock=None, sleep=None):
    """Allow cpufreq's queued QoS update, without accepting unrelated values.

    A successful sysfs write can precede the policy worker updating the value
    exposed by scaling_max_freq. Keep each acknowledgement bounded to 100ms.
    """
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    deadline = clock() + .1
    while True:
        value = read(path)
        if value == target:
            return True
        remaining = deadline - clock()
        if value != previous or remaining <= 0:
            return False
        sleep(min(.01, remaining))


def restore_caps(original, owned, read=integer, write=None, *, pending=()):
    write = write or (lambda path, value: path.write_text(str(value)))
    results = {}
    for path, value in original.items():
        try:
            current = read(path)
            if current == value and path not in pending:
                results[str(path)] = 'already_original'
            elif current == value or current in owned[path]:
                # A timed-out upward request may still be queued even when
                # readback shows the original value. Supersede that request.
                write(path, value)
                results[str(path)] = 'restored' if cap_matches(path, value, current, read) else 'verification_failed'
            else:
                # An unrelated actor has changed this cap; do not overwrite it.
                results[str(path)] = 'external_change_preserved'
        except (OSError, ValueError):
            results[str(path)] = 'restore_failed'
    return results


def guard(pid, owner, seconds):
    started = identity(pid, owner)
    cpu_paths = {CPU / f'policy{n}/scaling_max_freq': value for n, value in CPU_FACTORY.items()}
    factory = {**cpu_paths, GPU / 'max_freq': 903000000}
    original = {path: integer(path) for path in factory}
    expected_base = {path: CPU_BASE for path in cpu_paths} | {GPU / 'max_freq': 629000000}
    if original != expected_base:
        raise StopGuard('start_from_original_powersave_limits')
    tables = {}
    for path, maximum in cpu_paths.items():
        values = sorted(map(int, (path.parent / 'scaling_available_frequencies').read_text().split()))
        if integer(path.parent / 'cpuinfo_max_freq') != maximum or CPU_BASE not in values or maximum not in values:
            raise StopGuard('unexpected_cpu_frequency_table')
        tables[path] = [v for v in values if CPU_BASE <= v <= maximum]
    available = set(map(int, (GPU / 'available_frequencies').read_text().split()))
    if not all(v * 1000000 in available for v in GPU_TABLE):
        raise StopGuard('unexpected_gpu_frequency_table')
    tables[GPU / 'max_freq'] = [v * 1000000 for v in GPU_TABLE]
    invariants = {GPU / 'min_freq': (GPU / 'min_freq').read_text(), GPU / 'governor': (GPU / 'governor').read_text()}
    for n in CPU_FACTORY:
        path = CPU / f'policy{n}/scaling_governor'
        invariants[path] = path.read_text()
        minimum = CPU / f'policy{n}/scaling_min_freq'
        invariants[minimum] = minimum.read_text()
        if integer(minimum) > CPU_BASE:
            raise StopGuard('unexpected_cpu_minimum')
    if integer(GPU / 'min_freq') != 231000000:
        raise StopGuard('unexpected_gpu_minimum')
    initial_sensors = set(read_temperatures())
    expected = dict(factory)
    # A stock Performance request can be accepted before its acknowledgement
    # or the next poll. Its fixed factory values belong to this finite trial.
    owned = {path: {original[path], factory[path]} for path in original}
    pending = set()
    active = False
    reason = 'finished'
    policy = ThermalPolicy()
    emit({'phase': 'ready', 'pid': pid, 'seconds': seconds, 'mode_changes': False,
          'helper_sha256': helper_digest(), 'protocol': PROTOCOL})
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            caller_alive()
            identity(pid, owner, started)
            temps = read_temperatures()
            if set(temps) != initial_sensors:
                raise StopGuard('temperature_sensor_disappeared')
            values = {path: integer(path) for path in factory}
            # Mode switching may briefly update these attributes in sequence.
            for path, value in values.items():
                if value not in (original[path], factory[path]):
                    raise StopGuard('external_limits_change')
                if value == factory[path]:
                    owned[path].add(value)
            peak = max(temps.values())
            emit({'phase': 'arming_sample', 'epoch': time.time(), 'peak_C': peak,
                  'gpu_max_MHz': values[GPU / 'max_freq'] / 1000000,
                  'cpu_max_kHz': {path.parent.name: values[path] for path in cpu_paths}})
            if peak >= 88:
                raise StopGuard('temperature_88C_stop')
            if values == factory:
                # Enter control at 80–87C: down-step immediately instead of
                # rejecting the heat produced by this very mode transition.
                active = True
                break
            if values == original and peak >= 80:
                raise StopGuard('not_cool_enough_to_start')
            time.sleep(.25)
        if not active:
            raise StopGuard('performance_start_timeout')
        emit({'phase': 'active'})
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            caller_alive()
            identity(pid, owner, started)
            if any(path.read_text() != value for path, value in invariants.items()):
                raise StopGuard('governor_or_minimum_changed')
            if any(integer(path) != value for path, value in expected.items()):
                raise StopGuard('external_limits_change')
            temps = read_temperatures()
            if set(temps) != initial_sensors:
                raise StopGuard('temperature_sensor_disappeared')
            peak = max(temps.values())
            action = policy.update(time.monotonic(), peak)
            if action == 'stop':
                reason = 'temperature_88C_stop'
                break
            changed = 0
            cpu_peak = max(value for name, value in temps.items() if name.startswith('cpu'))
            gpu_peak = max(value for name, value in temps.items() if name.startswith('gpu'))
            cpu_floor = all(expected[path] == tables[path][0] for path in cpu_paths)
            gpu_floor = expected[GPU / 'max_freq'] == tables[GPU / 'max_freq'][0]
            if action in ('down', 'up'):
                for path in expected:
                    if action == 'down':
                        # Protect the hot domain first. Keep a cool GPU fast
                        # when CPU hotspots dominate, and vice versa. Shared
                        # heat gets cross-domain relief at 84C if the hot
                        # domain has already reached this trial's floor.
                        needs_reduction = ((cpu_peak >= 80 or gpu_peak >= 86 or gpu_peak >= 84 and gpu_floor)
                                           if path in cpu_paths else
                                           (gpu_peak >= 80 or cpu_peak >= 86 or cpu_peak >= 84 and cpu_floor))
                        if not needs_reduction:
                            continue
                    value = next_cap(tables[path], expected[path], -1 if action == 'down' else 1, path in cpu_paths)
                    if value != expected[path]:
                        owned[path].add(value)  # Covers a write accepted before an error is returned.
                        pending.add(path)
                        path.write_text(str(value))
                        if not cap_matches(path, value, expected[path]):
                            emit({'phase': 'cap_verification_error', 'path': str(path),
                                  'previous': expected[path], 'requested': value, 'observed': integer(path)})
                            raise StopGuard('cap_verification_failed')
                        pending.remove(path)
                        expected[path] = value
                        changed += 1
            emit({'phase': 'sample', 'epoch': time.time(), 'peak_C': peak, 'action': action,
                  'cpu_peak_C': cpu_peak, 'gpu_peak_C': gpu_peak,
                  'changed_caps': changed,
                  'temperatures_C': temps, 'gpu_max_MHz': expected[GPU / 'max_freq'] / 1000000,
                  'cpu_max_kHz': {path.parent.name: expected[path] for path in cpu_paths}})
            time.sleep(.5)
    except (StopGuard, OSError, ValueError, KeyboardInterrupt, SystemExit) as exc:
        reason = str(exc) or type(exc).__name__
    finally:
        result = restore_caps(original, owned, pending=pending)
        emit({'phase': 'done', 'reason': reason, 'active': active, 'restoration': result})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--socket', action='store_true', help='Fixed systemd socket-activation entry point')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('The fixed helper must be installed and run by the system manager')
    if args.socket:
        if args.pid is not None or args.seconds != 60 or not stat.S_ISSOCK(os.fstat(0).st_mode):
            parser.error('Socket activation takes no process or duration arguments')
        metadata = OWNER_FILE.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise StopGuard('owner_configuration_not_trusted')
        owner = integer(OWNER_FILE)
        with socket.socket(fileno=os.dup(0)) as connection:
            request = socket_request(connection, owner)
            if request['operation'] == 'check':
                available = all(path.is_file() and os.access(path, os.W_OK) for path in cap_paths())
                connection.sendall((json.dumps({'phase': 'available', 'protocol': PROTOCOL,
                                               'helper_sha256': helper_digest(), 'writable_caps': available}) + '\n').encode())
                return
        args.pid, args.seconds = request['pid'], request['seconds']
    else:
        owner = int(os.environ.get('SUDO_UID', '0'))
        if owner == 0 or not 10 <= args.seconds <= 120 or not args.pid or args.pid <= 0:
            parser.error('Use sudo from the VRChat user, for a bounded 10–120 second trial')
        if not stat.S_ISFIFO(os.fstat(0).st_mode):
            parser.error('Run through the unprivileged measurement controller with its cancellation pipe')
    def interrupted(signum, frame):
        raise StopGuard('signal_' + str(signum))
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, interrupted)
    os.set_blocking(1, False)
    os.set_blocking(0, False)
    guard(args.pid, owner, args.seconds)


if __name__ == '__main__':
    main()
