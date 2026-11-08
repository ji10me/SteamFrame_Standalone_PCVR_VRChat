#!/usr/bin/env python3
"""Package complete build inputs for Wine MF and losslessly recompress GStreamer."""
import argparse
import difflib
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

WINE_COMMIT = '7b3ceaf8644bb5d57140903e12897efac69be16a'
WINE_ARCHIVE_SHA256 = '12e7f09e3987ffdf47d22bd1a10b62900bc6587c5eadcb0208b78e3519f029c3'
GSTREAMER_ARCHIVE_SHA256 = 'dbe456eb7c713f153136434a3f02626bb95d8657ddf8f503b3b0f1fa87c6f675'
MODULES = (
    'dlls/mf', 'dlls/advapi32', 'dlls/mfplat', 'dlls/ole32', 'dlls/kernelbase',
    'dlls/evr', 'dlls/urlmon', 'dlls/user32', 'dlls/winecrt0', 'dlls/ucrtbase',
    'dlls/kernel32', 'dlls/ntdll',
)
LIBRARIES = (
    'libs/compiler-rt', 'libs/musl', 'libs/strmbase', 'libs/gsm', 'libs/dxerr9',
    'libs/dxerr8', 'libs/uuid', 'libs/dxguid', 'libs/mfuuid', 'libs/strmiids',
    'libs/wbemuuid', 'libs/dmoguids', 'libs/adsiid', 'libs/wmcodecdspuuid',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original_files(archive):
    """Only regular files from the pinned upstream archive are accepted."""
    if sha(Path(archive).read_bytes()) != WINE_ARCHIVE_SHA256:
        raise ValueError('Wine archive is not the pinned upstream source')
    files = {}
    with tarfile.open(archive) as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unexpected source archive path')
            if len(path.parts) < 2 or not member.isfile():
                continue
            name = str(PurePosixPath(*path.parts[1:]))
            parts = PurePosixPath(name).parts
            keep = (len(parts) == 1 or parts[0] in ('include', 'tools', 'nls')
                    or any(name.startswith(d + '/') for d in (*MODULES, *LIBRARIES, 'dlls/msvcrt'))
                    or name in ('server/atom.c', 'server/protocol.def'))
            if keep and 'tests' not in parts:
                if name in files:
                    raise ValueError('Duplicate source path')
                files[name] = tar.extractfile(member).read()
    return files


def scoped_makefile(data, implib_sources):
    """Import-only DLLs need export definitions and implib C code, not their DLL bodies."""
    lines = data.decode().splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r'^\w*SOURCES\s*=', line):
            plain = line.startswith('SOURCES')
            while line.endswith('\\'):
                i += 1
                line = lines[i]
            if plain:
                result.append('SOURCES = ' + ' '.join(sorted(implib_sources)))
        else:
            result.append(line)
        i += 1
    return ('\n'.join(result) + '\n').encode()


def scope(files):
    """Change build selection only; source implementation files remain intact."""
    files = dict(files)
    allowed = '|'.join(('include', 'nls', *MODULES, *LIBRARIES, 'tools/*'))
    for name, variable in [('aclocal.m4', '$[1]'), ('configure', '$1')]:
        text = files[name].decode()
        needle = 'wine_fn_config_makefile ()\n{\n'
        if text.count(needle) != 1:
            raise ValueError('Unexpected Wine configure helper')
        hook = ('    # MF component source package: configure only the included source directories.\n'
                '    case "' + variable + '" in ' + allowed + ') ;; *) return ;; esac\n')
        files[name] = text.replace(needle, needle + hook).encode()
    name = 'include/Makefile.in'
    files[name] = files[name].replace(b'\twine/vulkan.h \\\n', b'').replace(b'\twine/vulkan_driver.h \\\n', b'')
    for directory in MODULES:
        if directory in ('dlls/mf', 'dlls/winecrt0'):
            continue
        implib = []
        for name, data in files.items():
            if name.startswith(directory + '/') and name.endswith('.c'):
                if re.search(rb'#\s*pragma\s+makedep[^\n]*\bimplib\b', data):
                    implib.append(name[len(directory) + 1:])
        name = directory + '/Makefile.in'
        files[name] = scoped_makefile(files[name], implib)
    # Full Wine autogen also generates unrelated Vulkan headers. This snapshot
    # already supplies the exports/headers and needs only the configure generator.
    files['autogen.sh'] = (b'#!/bin/sh\nset -eu\n'
                          b'# MF component snapshot; regenerate configure from the supplied inputs.\n'
                          b'autoreconf -ifv\n')
    return files


def write_archive(files, output):
    output = Path(output)
    with output.open('xb') as raw:
        with lzma.LZMAFile(raw, 'w', preset=9 | lzma.PRESET_EXTREME) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.USTAR_FORMAT) as tar:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo('wine-mf-source/' + name)
                    info.size = len(data)
                    info.mode = 0o755 if name in ('configure', 'autogen.sh') else 0o644
                    tar.addfile(info, io.BytesIO(data))


def wine(archive, generated, patch, output):
    original = original_files(archive)
    generated_files = {}
    with tarfile.open(generated) as tar:
        for name in ('configure', 'include/config.h.in'):
            member = tar.getmember(name)
            if not member.isfile():
                raise ValueError('Expected generated regular file')
            data = tar.extractfile(member).read()
            if any(v in data for v in (b'/Users/', b'/home/steamos/')):
                raise ValueError('Development path in generated configure')
            generated_files[name] = data
    before = {**original, **generated_files}
    files = scope(before)
    patch_data = Path(patch).read_bytes()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ('dlls/mf/sar.c',):
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(files[name])
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(Path(patch).resolve())],
                                cwd=root, capture_output=True)
        if result.returncode:
            raise ValueError('MF patch did not apply to the pinned source')
        files['dlls/mf/sar.c'] = (root / 'dlls/mf/sar.c').read_bytes()
    modified = {name: sha(data) for name, data in files.items() if name in before and data != before[name]}
    chunks = []
    for name in sorted(modified):
        chunks.extend(difflib.unified_diff(before[name].decode().splitlines(True),
                                         files[name].decode().splitlines(True),
                                         'a/' + name, 'b/' + name))
    files['SOURCE_SCOPE.patch'] = ''.join(chunks).encode()
    files['SOURCE_PATCHES/rate-resume.patch'] = patch_data
    files['SOURCE_BUILD.md'] = (
        '# Wine MF corresponding source\n\n'
        'This is a component source snapshot for mf.dll, not a complete Wine installation.\n'
        'Original MF code, headers, build tools, NLS data, import-library and static-runtime inputs are included.\n'
        'The rate-resume fix is already applied; SOURCE_PATCHES retains the patch.\n'
        'SOURCE_SCOPE.patch records the build-scope changes and MF fix. Original copyright/license notices remain.\n'
        'Use the accompanying rebuild_wine_mf.py with --source pointing here and an LLVM-MinGW SDK.\n'
        'A generated configure and config.h.in are provided. --regenerate-configure uses autoreconf if desired.\n'
        'Use clang, make, bison 3 or later and flex. The build creates mf.dll only and does not install it.\n'
        'The original upstream source is https://github.com/ValveSoftware/wine/tree/' + WINE_COMMIT + '\n'
    ).encode()
    manifest = {
        'format': 'wine-mf-corresponding-source-v1', 'upstream_commit': WINE_COMMIT,
        'upstream_archive_sha256': WINE_ARCHIVE_SHA256,
        'original_file_hashes': {n: sha(d) for n, d in sorted(original.items())},
        'generated_input_hashes': {n: sha(d) for n, d in sorted(generated_files.items())},
        'modified_files': modified, 'mf_rate_resume_patch_applied': True,
        'mf_patch_sha256': sha(patch_data),
        'files': {n: sha(d) for n, d in sorted(files.items())},
    }
    files['FRAME_MF_SOURCE.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    write_archive(files, output)
    return {'files': len(files), 'bytes': Path(output).stat().st_size, 'sha256': sha(Path(output).read_bytes())}


def verify_wine(data):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or not path.parts or path.parts[0] != 'wine-mf-source'):
                raise ValueError('Unexpected component source member')
            name = str(PurePosixPath(*path.parts[1:]))
            if name in files:
                raise ValueError('Duplicate component source member')
            files[name] = tar.extractfile(member).read()
    manifest = json.loads(files.pop('FRAME_MF_SOURCE.json'))
    if (manifest.get('format') != 'wine-mf-corresponding-source-v1'
            or manifest.get('upstream_commit') != WINE_COMMIT
            or manifest.get('upstream_archive_sha256') != WINE_ARCHIVE_SHA256
            or not manifest.get('mf_rate_resume_patch_applied')):
        raise ValueError('Unexpected Wine MF source provenance')
    if {n: sha(d) for n, d in files.items()} != manifest['files']:
        raise ValueError('Component source manifest mismatch')
    for name, expected in manifest['original_file_hashes'].items():
        if name not in manifest['modified_files'] and sha(files[name]) != expected:
            raise ValueError('Unrecorded upstream source change')
    for name in ('COPYING.LIB', 'dlls/mf/sar.c', 'include/Makefile.in',
                 'tools/makedep.c', 'SOURCE_SCOPE.patch', 'configure', 'include/config.h.in'):
        if name not in files:
            raise ValueError('Missing MF build/source input')
    if sha(files['SOURCE_PATCHES/rate-resume.patch']) != manifest['mf_patch_sha256']:
        raise ValueError('MF change record mismatch')
    return manifest


def gstreamer(archive, output):
    data = Path(archive).read_bytes()
    if sha(data) != GSTREAMER_ARCHIVE_SHA256:
        raise ValueError('GStreamer source is not the validated source snapshot')
    original = gzip.decompress(data)
    compressed = lzma.compress(original, preset=9 | lzma.PRESET_EXTREME)
    with Path(output).open('xb') as f:
        f.write(compressed)
    return {'bytes': len(compressed), 'sha256': sha(compressed), 'uncompressed_sha256': sha(original)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('wine-archive', 'generated-configure', 'mf-patch', 'gstreamer-archive', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    result = {'wine': wine(a.wine_archive, a.generated_configure, a.mf_patch, a.output / 'wine-mf-source.tar.xz'),
              'gstreamer': gstreamer(a.gstreamer_archive, a.output / 'gstreamer-frame-source.tar.xz')}
    (a.output / 'source-proof.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
