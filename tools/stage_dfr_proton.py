#!/usr/bin/env python3
"""Copy only static Proton code into a private location; never copy a prefix."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tempfile

from framectl import sha256
from monitor_vrchat_memory import save_private_json
from private_proton import CHECKED_FILES, DLLS, verify_files


def pe_amd64(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            return False
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 26:
            return False
        stream.seek(offset)
        signature = stream.read(26)
    return (signature[:4] == b'PE\0\0' and struct.unpack_from('<H', signature, 4)[0] == 0x8664
            and bool(struct.unpack_from('<H', signature, 22)[0] & 0x2000)
            and struct.unpack_from('<H', signature, 24)[0] == 0x20b)


def main(payload):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.geteuid() == 0:
        raise RuntimeError('Run as the ordinary Frame user')
    base = Path.home() / '.local/share/frame-pcvr'
    payload = Path(payload).resolve(strict=True)
    payload.relative_to((base / 'runs').resolve())
    manifest = json.loads((payload / 'manifest.json').read_text())
    if manifest.get('format') != 'frame-dfr-dxvk-v1' or set(manifest.get('files', {})) != {'d3d11.dll', 'dxgi.dll'}:
        raise RuntimeError('Unexpected DFR payload manifest')
    for name, digest in manifest['files'].items():
        path = (payload / name).resolve(strict=True)
        path.relative_to(payload)
        if sha256(path) != digest or not pe_amd64(path):
            raise RuntimeError('DFR DLL hash or architecture mismatch')
    parent = Path.home() / '.local/share/Steam/steamapps/common/Proton - Experimental'
    hashes = {name: sha256(parent / name) for name in CHECKED_FILES}
    size = sum(p.lstat().st_size for p in parent.rglob('*') if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(base).free < size + 8 * 1024**3:
        raise RuntimeError('Insufficient free space for an isolated Proton copy plus reserve')
    directory = base / 'proton'
    directory.mkdir(mode=0o700, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix='dfr-', dir=directory))
    try:
        subprocess.run(['cp', '-a', '--reflink=auto', str(parent) + '/.', str(staged)], check=True)
        staged.chmod(0o700)
        verify_files(parent, hashes)
        verify_files(staged, hashes)
        for name in DLLS:
            target = staged / name
            target.resolve(strict=True).relative_to(staged)
            target.unlink()  # Never follow an unexpected destination link into the shared tool.
            shutil.copy2(payload / Path(name).name, target)
        record = {'format': 'frame-dfr-private-proton-v1', 'path': str(staged), 'parent': str(parent),
                  'parent_hashes': hashes, 'private_hashes': {name: sha256(staged / name) for name in CHECKED_FILES},
                  'payload_manifest_sha256': sha256(payload / 'manifest.json'),
                  'source': manifest.get('source'), 'only_graphics_dlls_replaced': list(DLLS)}
        verify_files(parent, hashes)
        verify_files(staged, record['private_hashes'])
        save_private_json(staged / 'frame-dfr-proton.json', record)
    except BaseException:
        shutil.rmtree(staged)
        raise
    print(json.dumps(record))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path)
    main(parser.parse_args().payload)
