#!/usr/bin/env python3
"""One-time, root-owned installation of the fixed thermal measurement socket.

No sudoers changes. Only the invoking user can connect. Idle operation has no
helper process and no clock changes. --plan is read-only and needs no root.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import tempfile

STATE = Path('/var/lib/frame-pcvr-thermal')
UNITS = Path('/etc/systemd/system')
RUNTIME = Path('/run/frame-pcvr-thermal')
SOCKET_NAME = 'frame-pcvr-thermal.socket'
SERVICE_NAME = 'frame-pcvr-thermal@.service'
MARKER = 'frame-pcvr-thermal-service-v1'
HELPER_SHA256 = '7b9e219dff33bcd5c5519479f30ce9646ff7b6802c97bf01e95057e6b695b51f'
GPU_REAL = '/sys/devices/platform/soc@0/3d00000.gpu/devfreq/3d00000.gpu/max_freq'


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def unit_contents(user):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('Unsupported user name')
    writable = [f'/sys/devices/system/cpu/cpufreq/policy{n}/scaling_max_freq' for n in (0, 2, 5, 7)] + [GPU_REAL]
    socket_unit = f'''[Unit]
Description=VRChat finite thermal measurement endpoint

[Socket]
ListenStream={RUNTIME}/control.sock
Accept=yes
MaxConnections=1
Backlog=1
SocketUser={user}
SocketMode=0600
DirectoryMode=0755
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
'''
    service_unit = f'''[Unit]
Description=VRChat finite thermal measurement session

[Service]
Type=exec
ExecStart=/usr/bin/python3 -I {STATE}/guard_frame_thermals.py --socket
StandardInput=socket
StandardOutput=socket
StandardError=journal
Restart=no
RuntimeMaxSec=160
TimeoutStopSec=5
KillSignal=SIGTERM
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=CAP_SYS_PTRACE CAP_DAC_READ_SEARCH CAP_DAC_OVERRIDE
ReadWritePaths={' '.join(writable)}
'''
    return {SOCKET_NAME: socket_unit, SERVICE_NAME: service_unit}


def atomic_write(path, content, mode):
    descriptor, name = tempfile.mkstemp(prefix='.frame-thermal-', dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        staged.chmod(mode)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def active_sessions():
    return bool(run(['systemctl', 'list-units', 'frame-pcvr-thermal@*.service', '--state=active,activating,deactivating',
                     '--no-legend', '--no-pager']).stdout.strip())


def verify_existing(owner, payload, units):
    marker = STATE / 'installation.json'
    metadata = marker.lstat()
    if metadata.st_uid != 0 or not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        raise RuntimeError('Untrusted installation marker')
    record = json.loads(marker.read_text())
    if record.get('marker') != MARKER or record.get('owner_uid') != owner:
        raise RuntimeError('A different installation exists; it was not changed')
    expected = {'guard_frame_thermals.py': payload, 'owner.uid': f'{owner}\n'.encode()}
    expected.update({str(UNITS / name): text.encode() for name, text in units.items()})
    for name, content in expected.items():
        path = Path(name) if name.startswith('/') else STATE / name
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode) or status.st_uid != 0 or status.st_mode & 0o022 or path.read_bytes() != content:
            raise RuntimeError('The installed revision differs; remove it explicitly before replacing it')


def user_probe(owner, expected_hash):
    """Test the real systemd sandbox and peer credentials as the allowed user."""
    child = os.fork()
    if child == 0:
        try:
            account = pwd.getpwuid(owner)
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(owner)
            os.chdir('/')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(12)
                connection.connect(str(RUNTIME / 'control.sock'))
                connection.sendall(b'{"protocol":1,"operation":"check"}\n')
                data = bytearray()
                while len(data) < 4096:
                    char = connection.recv(1)
                    if not char or char == b'\n':
                        break
                    data += char
            result = json.loads(data)
            valid = (result.get('phase') == 'available' and result.get('protocol') == 1
                     and result.get('writable_caps') is True and result.get('helper_sha256') == expected_hash)
            os._exit(0 if valid else 1)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(child, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise RuntimeError('The user/socket/sandbox probe failed; no clock change was attempted')


def install(owner, payload, units):
    created = []
    already = os.path.lexists(STATE)
    if already:
        status = STATE.lstat()
        if not stat.S_ISDIR(status.st_mode) or status.st_uid != 0 or status.st_mode & 0o077:
            raise RuntimeError('Existing service directory has unexpected ownership')
        verify_existing(owner, payload, units)
    else:
        if os.path.lexists(RUNTIME / 'control.sock'):
            raise RuntimeError('An unregistered measurement socket exists; it was not changed')
        for name in units:
            if os.path.lexists(UNITS / name):
                raise RuntimeError('An unrelated unit exists; it was not changed')
    if active_sessions():
        raise RuntimeError('Wait until the current measurement ends before installing')
    if os.path.lexists(RUNTIME):
        status = RUNTIME.lstat()
        if not stat.S_ISDIR(status.st_mode) or status.st_uid != 0 or status.st_mode & 0o022:
            raise RuntimeError('The runtime directory is not root-managed')
    try:
        if not already:
            STATE.mkdir(mode=0o700)
            created.append(STATE)
            atomic_write(STATE / 'guard_frame_thermals.py', payload, 0o600)
            atomic_write(STATE / 'owner.uid', f'{owner}\n'.encode(), 0o600)
            atomic_write(STATE / 'installation.json', json.dumps({'marker': MARKER, 'owner_uid': owner,
                         'helper_sha256': hashlib.sha256(payload).hexdigest(),
                         'unit_hashes': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in units.items()}}).encode(), 0o600)
            for name, text in units.items():
                atomic_write(UNITS / name, text.encode(), 0o644)
                created.append(UNITS / name)
        run(['systemd-analyze', 'verify', *[str(UNITS / name) for name in units]])
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', SOCKET_NAME])
        user_probe(owner, hashlib.sha256(payload).hexdigest())
    except BaseException:
        if not already:
            subprocess.run(['systemctl', 'disable', '--now', SOCKET_NAME], capture_output=True, timeout=15)
            for path in reversed(created):
                if path == STATE:
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            subprocess.run(['systemctl', 'daemon-reload'], capture_output=True, timeout=15)
        raise
    print('専用の計測接続を設定しました。以後の計測でsudo認証は不要です。')
    print('CPU/GPU上限とSteamVR設定は変更していません。')


def uninstall(owner):
    status = STATE.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != 0 or status.st_mode & 0o077:
        raise RuntimeError('Untrusted service directory')
    record = json.loads((STATE / 'installation.json').read_text())
    if record.get('marker') != MARKER or record.get('owner_uid') != owner:
        raise RuntimeError('This is not the invoking user\'s installation')
    if set(path.name for path in STATE.iterdir()) != {'guard_frame_thermals.py', 'owner.uid', 'installation.json'}:
        raise RuntimeError('Unknown files were retained; review the service directory')
    if active_sessions():
        raise RuntimeError('Wait until the current measurement ends before removing')
    for name in (SOCKET_NAME, SERVICE_NAME):
        path = UNITS / name
        if (path.is_symlink() or path.stat().st_uid != 0
                or hashlib.sha256(path.read_bytes()).hexdigest() != record.get('unit_hashes', {}).get(name)):
            raise RuntimeError('A modified unit was retained; review it before removing')
    run(['systemctl', 'disable', '--now', SOCKET_NAME])
    for name in (SOCKET_NAME, SERVICE_NAME):
        path = UNITS / name
        if path.is_symlink() or path.stat().st_uid != 0:
            raise RuntimeError('Unexpected unit ownership')
        path.unlink()
    shutil.rmtree(STATE)
    run(['systemctl', 'daemon-reload'])
    print('専用の計測接続を削除しました。計測記録はそのまま残しています。')


def current_installation(owner, units):
    metadata = STATE.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise RuntimeError('Untrusted existing service directory')
    old_record = (STATE / 'installation.json').read_bytes()
    record = json.loads(old_record)
    old_payload = (STATE / 'guard_frame_thermals.py').read_bytes()
    if hashlib.sha256(old_payload).hexdigest() != record.get('helper_sha256'):
        raise RuntimeError('The existing helper has been modified; it was retained')
    verify_existing(owner, old_payload, units)
    return old_payload, old_record


def upgrade(owner, payload, units):
    """Replace only our idle, registered helper; keep exactly the same units."""
    old_payload, old_record = current_installation(owner, units)
    if old_payload == payload:
        install(owner, payload, units)
        return
    if active_sessions():
        raise RuntimeError('Wait until the active measurement ends before updating')
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', SOCKET_NAME],
                                capture_output=True, timeout=15).returncode == 0
    run(['systemctl', 'stop', SOCKET_NAME])
    try:
        if active_sessions():
            raise RuntimeError('An accepted measurement is still starting; retry after it ends')
        record = json.loads(old_record)
        record['helper_sha256'] = hashlib.sha256(payload).hexdigest()
        record['unit_hashes'] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in units.items()}
        atomic_write(STATE / 'guard_frame_thermals.py', payload, 0o600)
        atomic_write(STATE / 'installation.json', json.dumps(record).encode(), 0o600)
        run(['systemctl', 'start', SOCKET_NAME])
        user_probe(owner, hashlib.sha256(payload).hexdigest())
        if not was_active:
            run(['systemctl', 'stop', SOCKET_NAME])
    except BaseException:
        run(['systemctl', 'stop', SOCKET_NAME])
        atomic_write(STATE / 'guard_frame_thermals.py', old_payload, 0o600)
        atomic_write(STATE / 'installation.json', old_record, 0o600)
        if was_active:
            run(['systemctl', 'start', SOCKET_NAME])
        raise
    print('専用の制御コードを更新しました。接続権限とサービス定義は変更していません。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install', action='store_true')
    group.add_argument('--uninstall', action='store_true')
    group.add_argument('--upgrade', action='store_true')
    group.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    owner = int(os.environ.get('SUDO_UID', str(os.getuid())))
    if owner <= 0:
        raise RuntimeError('Run sudo from the Frame user, not a root login')
    user = pwd.getpwuid(owner).pw_name
    units = unit_contents(user)
    if not args.install and not args.uninstall and not args.upgrade:
        print(json.dumps({'owner_uid': owner, 'files': [str(STATE), *[str(UNITS / n) for n in units],
                                                      '/run/frame-pcvr-thermal-install.lock'],
                          'units': units, 'maximum_seconds': 120, 'sudoers_changes': False}, indent=2))
        return
    if os.geteuid() != 0:
        raise RuntimeError('Initial setup requires sudo once')
    descriptor = os.open('/run/frame-pcvr-thermal-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != 0 or not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
            raise RuntimeError('Untrusted installation lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.uninstall:
            uninstall(owner)
            return
        payload = Path(__file__).with_name('guard_frame_thermals.py').read_bytes()
        if hashlib.sha256(payload).hexdigest() != HELPER_SHA256:
            raise RuntimeError('The reviewed helper hash differs; nothing was installed')
        if args.upgrade:
            upgrade(owner, payload, units)
        else:
            install(owner, payload, units)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
