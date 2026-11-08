#!/usr/bin/env python3
"""Build/verify a source-only handoff kit from an explicit file allowlist."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import zipfile

FILES = (
    'LICENSE', 'LICENSE.md', 'LICENSE.en.md',
    'portable/README.ja.md', 'portable/INSTALL.ja.md', 'portable/THIRD_PARTY.md', 'portable/profile.json',
    'scripts/build-fex-stage.sh', 'tools/portable_bundle.py', 'tools/install_portable.py',
    'tools/framectl.py', 'tools/stage_fex_candidate.py', 'tools/stage_lookup_fix.py',
    'tools/stage_vulkan_trial.py', 'tools/probe_vulkan_route.py', 'tools/vrchat-launch.py',
    'tools/vulkan_lookup_adapter.c', 'tools/vulkan_smoke.c', 'tools/vulkan_route_probe.c',
    'tools/vulkan_image_probe.c', 'tools/vulkan_memory_probe.c',
)
MANIFEST = 'MANIFEST.json'
TOP = 'frame-pcvr-kit'
PRIVATE_PATTERNS = (
    r'/(?:Users|home)/[A-Za-z0-9_.-]+',
    r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b',
    r'\b(?:usr|wrld|avtr)_[0-9a-fA-F-]{32,}',
    r'\b7656119\d{10}\b',
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    r'(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}',
    r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----',
    r'https?://[^/\s]+:[^/@\s]+@',
    r'(?i)(?:authorization\s*:\s*bearer|authcookie\s*[=:]|password\s*=\s*[\x22\x27][^\x22\x27]+)',
    r'(?i)(?:authcookie|twofactorauth|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)'
    r'[\x22\x27]?\s*[=:]\s*[\x22\x27][A-Za-z0-9][^\x22\x27]{3,}[\x22\x27]',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inspect_payload(name, data):
    if b'\0' in data:
        raise ValueError('Binary data is forbidden: ' + name)
    text = data.decode('utf-8')
    for pattern in PRIVATE_PATTERNS:
        if re.search(pattern, text):
            # Do not echo the suspected secret into a console/log.
            raise ValueError('Possible private data in: ' + name)


def read_payload(root):
    root = Path(root).resolve()
    payload = {}
    for name in FILES:
        path = root / name
        if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
            raise ValueError('Symlink is forbidden: ' + name)
        if not path.is_file():
            raise ValueError('Missing allowlisted file: ' + name)
        data = path.read_bytes()
        inspect_payload(name, data)
        payload[name] = data
    return payload


def make_manifest(payload):
    return {'schema': 1, 'kind': 'source-install-kit', 'files': {
        name: {'sha256': digest(data), 'size': len(data),
               'mode': 0o700 if name.endswith(('.py', '.sh')) else 0o600}
        for name, data in sorted(payload.items())}}


def verify(root):
    root = Path(root).resolve()
    payload = read_payload(root)
    if (root / MANIFEST).is_symlink():
        raise ValueError('Manifest symlink is forbidden')
    actual = json.loads((root / MANIFEST).read_text())
    if actual != make_manifest(payload):
        raise ValueError('Kit manifest/hash mismatch')
    present = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if present != set(FILES) | {MANIFEST}:
        raise ValueError('Unexpected files in kit; use a fresh extraction')
    return actual


def build(root, output):
    payload = read_payload(root)
    manifest = make_manifest(payload)
    payload[MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    output = Path(output)
    if not (output.name.endswith('.tar.gz') or output.suffix == '.zip'):
        raise ValueError('Choose .tar.gz or .zip for the source kit')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Fixed timestamps, no source uid/gid/user/group names, no source paths.
    if output.suffix == '.zip':
        # Archive text directly. Do not copy Finder's AppleDouble/xattr entries
        # or nest another compressed archive inside the ZIP.
        with zipfile.ZipFile(output, mode='x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(TOP + '/' + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100000 | manifest['files'].get(name, {}).get('mode', 0o600)) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    else:
        with output.open('xb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                    for name, data in sorted(payload.items()):
                        info = tarfile.TarInfo(TOP + '/' + name)
                        info.size = len(data)
                        info.mode = manifest['files'].get(name, {}).get('mode', 0o600)
                        archive.addfile(info, io.BytesIO(data))
    checksum = digest(output.read_bytes())
    output.with_name(output.name + '.sha256').write_text(checksum + '  ' + output.name + '\n')
    manual = output.parent / 'INSTALL.ja.md'
    manual.write_bytes(payload['portable/INSTALL.ja.md'])
    license_file = output.parent / 'LICENSE.md'
    license_file.write_bytes(payload['LICENSE.md'])
    (output.parent / 'LICENSE').write_bytes(payload['LICENSE'])
    (output.parent / 'LICENSE.en.md').write_bytes(payload['LICENSE.en.md'])
    return {'archive': str(output), 'sha256': checksum, 'files': len(payload),
            'bytes': output.stat().st_size, 'source_only': True, 'manual': str(manual),
            'license': str(license_file)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path, help='Verify a freshly extracted kit')
    parser.add_argument('--output', type=Path, default=Path('out/portable/frame-pcvr-kit.tar.gz'))
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
        print('Kit file hashes and privacy checks passed.')
    else:
        print(json.dumps(build(Path(__file__).resolve().parents[1], args.output), indent=2))
