#!/usr/bin/env python3
"""Recipient-local optional 8 GiB swap; explicit sudo, no boot configuration."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import time
import uuid

SIZE = 8 * 1024**3
PRIORITY = 10
RESERVE = 20 * 1024**3

class AuthenticationRequired(RuntimeError):
    pass


def read_swaps(path=Path('/proc/swaps')):
    rows = []
    for line in Path(path).read_text().splitlines()[1:]:
        a = line.split()
        if len(a) != 5:
            raise RuntimeError('スワップ構成を確認できません。')
        rows.append(dict(path=a[0], size_kib=int(a[2]), used_kib=int(a[3]), priority=int(a[4])))
    return rows

def available_memory_kib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1])
    raise RuntimeError('利用可能なメモリー量を確認できません。')

def reusable_external(rows):
    compressed=[r for r in rows if r['path'].startswith('/dev/zram')]
    if not compressed:return []
    minimum=min(r['priority'] for r in compressed)
    return [r for r in rows if not r['path'].startswith('/dev/zram') and
            r['size_kib'] >= SIZE//1024-os.sysconf('SC_PAGESIZE')//1024 and r['priority'] < minimum]


def locations(root):
    root = Path(root)
    if root.is_symlink():
        raise RuntimeError('スワップ配置にsymlinkが含まれています。')
    folder = root / 'swap'
    if folder.is_symlink():
        raise RuntimeError('スワップの保管先がsymlinkです。')
    return folder, folder / 'swapfile', folder / 'receipt.json'


def save(path, value):
    temp = path.with_name(path.name + '.new')
    with temp.open('x') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def record(root):
    folder, target, receipt = locations(root)
    if receipt.is_symlink() or not receipt.is_file():
        raise RuntimeError('このインストーラーのスワップ記録がありません。')
    value = json.loads(receipt.read_text())
    if value.get('format') != 'frame-swap-8g-v1' or value.get('bytes') != SIZE:
        raise RuntimeError('スワップの記録が未対応です。')
    st = target.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != SIZE or
        stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid not in (0, value['uid']) or
        [st.st_dev, st.st_ino] != value['identity'] or st.st_blocks * 512 < SIZE):
        raise RuntimeError('スワップの対象ファイルが変更されています。')
    return target, value


def status(root, rows=None):
    rows = read_swaps() if rows is None else rows
    folder, target, receipt = locations(root)
    if receipt.exists():
        record(root)
        own = [r for r in rows if r['path'] == str(target)]
        return {'managed': True, 'active': bool(own), 'entry': own[0] if own else None,
                'persistent': False}
    # A manual swap or another application's swap is never adopted or removed.
    external = [r for r in rows if not r['path'].startswith('/dev/zram')]
    adequate = reusable_external(rows)
    return {'managed': False, 'active': bool(adequate), 'external': external,
            'external_reused': bool(adequate), 'entry': adequate[0] if adequate else None, 'persistent': False}


def prepare(root):
    root = Path(root)
    if os.geteuid() == 0:
        raise RuntimeError('準備はFrameの通常ユーザーで実行してください。')
    current = status(root)
    if current['managed']:
        return current
    if current['external']:
        if current['external_reused']:
            return current
        raise RuntimeError('既存の補助スワップを検出しました。変更せず停止します。')
    folder, target, receipt = locations(root)
    if folder.exists():
        raise RuntimeError('記録のないスワップ配置があります。上書きせず停止します。')
    fs = subprocess.check_output(['/usr/bin/findmnt', '-n', '-o', 'FSTYPE', '--target', str(root)], text=True).strip()
    if fs != 'ext4' or shutil.disk_usage(root).free < SIZE + RESERVE:
        raise RuntimeError('8GiB補助スワップにはext4と空き28GiB以上が必要です。')
    folder.mkdir(mode=0o700)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    token = str(uuid.uuid4())
    try:
        subprocess.run(['/usr/bin/fallocate', '--length', str(SIZE), '--', str(target)], check=True, timeout=30)
        subprocess.run(['/usr/bin/mkswap', '--uuid', token, '--label', 'frame-pcvr-8g', '--', str(target)], check=True, timeout=15)
        s = target.stat()
        value = {'format': 'frame-swap-8g-v1', 'bytes': SIZE, 'uid': os.getuid(),
                 'identity': [s.st_dev, s.st_ino], 'uuid': token, 'created_epoch': time.time()}
        save(receipt, value)
        record(root)
    except BaseException:
        # A failed, inactive, newly created file is ours to retire. Never touch an active file.
        if not any(r['path'] == str(target) for r in read_swaps()):
            if target.exists(): target.unlink()
            if receipt.exists(): receipt.unlink()
            if not any(folder.iterdir()): folder.rmdir()
        raise
    return status(root)


def cleanup(root):
    folder, target, receipt = locations(root)
    if not receipt.exists():
        return
    current = status(root)
    if current['active']:
        raise AuthenticationRequired('補助スワップの解除にFrameの管理者認証が必要です。')
    record(root)
    target.unlink(); receipt.unlink()
    # Preserve unfamiliar files rather than removing an entire directory.
    if not any(folder.iterdir()): folder.rmdir()


def administer(root, enable):
    root = Path(root)
    if os.geteuid() != 0:
        raise RuntimeError('Frameの管理者認証が必要です。')
    target, value = record(root)
    # Root may only administer the calling recipient's dedicated installation.
    uid = int(os.environ.get('SUDO_UID', '-1'))
    if uid < 1 or value['uid'] != uid or root != Path(pwd.getpwuid(uid).pw_dir) / '.local/share/frame-pcvr-easy':
        raise RuntimeError('管理者認証した本人の専用配置と一致しません。')
    rows = read_swaps(); own = [r for r in rows if r['path'] == str(target)]
    if enable and any(r['path'] != str(target) and not r['path'].startswith('/dev/zram') for r in rows):
        raise RuntimeError('他の補助スワップは変更せず停止します。')
    compressed=[r for r in rows if r['path'].startswith('/dev/zram')]
    if enable and (not compressed or any(r['priority'] <= PRIORITY for r in compressed)):
        raise RuntimeError('優先する圧縮スワップを確認できません。')
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if [s.st_dev, s.st_ino] != value['identity']:
            raise RuntimeError('操作前にファイルが変更されました。')
        header = os.read(fd, os.sysconf('SC_PAGESIZE'))
        if header[-10:] != b'SWAPSPACE2' or str(uuid.UUID(bytes=header[1036:1052])) != value['uuid']:
            raise RuntimeError('スワップの形式が一致しません。')
        if enable:
            if not own:
                os.fchown(fd, 0, 0)
                subprocess.run(['/usr/bin/swapon', '--priority', str(PRIORITY), '--', str(target)], check=True, timeout=30)
            result = status(root)
            if not result['active'] or result['entry']['priority'] != PRIORITY or result['entry']['size_kib'] != SIZE // 1024 - os.sysconf('SC_PAGESIZE') // 1024:
                raise RuntimeError('追加スワップの容量・優先度を確認できません。')
        elif own:
            if available_memory_kib() <= own[0]['used_kib'] + 1024**2:
                raise RuntimeError('VRChatを終了してメモリーに余裕を作ってから解除してください。')
            subprocess.run(['/usr/bin/swapoff', '--', str(target)], check=True, timeout=60)
        after = read_swaps()
        unaffected = lambda rows: [(r['path'], r['size_kib'], r['priority']) for r in rows if r['path'] != str(target)]
        if unaffected(after) != unaffected(rows) or (not enable and any(r['path'] == str(target) for r in after)):
            raise RuntimeError('スワップ構成の照合に失敗しました。')
        return status(root)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'status', 'enable', 'disable'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.umask(0o077)
    if args.action == 'status':
        value = status(root)
    else:
        # Share the installer lock across PCs and privileged/unprivileged stages.
        flags=os.O_RDWR|os.O_NOFOLLOW
        if os.geteuid()!=0:flags|=os.O_CREAT
        fd=os.open(root/'installer.lock',flags,0o600)
        with os.fdopen(fd,'a') as lock:
            owner=int(os.environ.get('SUDO_UID','-1')) if os.geteuid()==0 else os.getuid()
            if os.fstat(lock.fileno()).st_uid!=owner:
                raise RuntimeError('インストーラーロックの所有者が一致しません。')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            value=prepare(root) if args.action=='prepare' else administer(root,args.action=='enable')
    print(json.dumps(value), flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
