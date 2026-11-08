#!/usr/bin/env python3
"""Create a code-only probe candidate for FEX Vulkan self-lookup repair."""
import argparse
import datetime
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from framectl import FEX_COMMIT, sha256


def main(source):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    base = Path.home() / '.local/share/frame-pcvr'
    source = Path(source).resolve()
    source.relative_to((base / 'candidates').resolve())
    parent = json.loads((source / 'integration.json').read_text())
    if parent.get('commit') != FEX_COMMIT or parent.get('state') != 'smoke-passed':
        raise RuntimeError('Use the validated original candidate as source')
    standard = Path.home() / '.local/share/Steam/steamapps/common/FEX-Emu'
    for name, digest in parent['standard_hashes'].items():
        if sha256(standard / name) != digest:
            raise RuntimeError('Standard FEX changed since staging')
    for name, digest in parent['runtime_hashes'].items():
        if sha256(source / 'usr/bin' / name) != digest:
            raise RuntimeError('Source runtime changed')
    candidate = Path(tempfile.mkdtemp(prefix='vk-lookup-', dir=base / 'candidates'))
    subprocess.run(['cp', '-a', '--reflink=auto', str(source) + '/.', str(candidate)], check=True)
    guest = candidate / 'usr/share/fex-emu/GuestThunks'
    original = guest / 'libvulkan-guest.so'
    dependency = guest / 'libframe_vulkan_thunk_base.so'
    original_hash = sha256(original)
    shutil.copy2(original, dependency)
    # Rename only this copied ELF's SONAME so the adapter can depend on it
    # without resolving its dependency back to itself (libvulkan.so.1).
    subprocess.run(['patchelf', '--set-soname', dependency.name, str(dependency)], check=True)
    code = Path(__file__).with_name('vulkan_lookup_adapter.c')
    sysroot = '/usr/share/guestos/fex-mesa'
    subprocess.run(['clang', '--target=x86_64-linux-gnu', '--sysroot=' + sysroot,
                    '-fuse-ld=lld', '-shared', '-fPIC', '-nostdlib', '-Wall', '-Wextra', '-Werror',
                    str(code), '-L' + str(guest), '-Wl,--no-as-needed', '-l:' + dependency.name,
                    '-L' + sysroot + '/usr/lib', '-lc', '-Wl,-z,defs',
                    '-Wl,-rpath,' + str(guest), '-Wl,-soname,libvulkan.so.1', '-o', str(original)], check=True)
    record = dict(parent)
    record.update(state='probe-only', candidate=str(candidate), compat_data=None,
                  parent_candidate=str(source), game_integration_tested=False,
                  lookup_adapter={'upstream_fix': '3397eb9b80783061cb7f109b2670c5d9fd4c3d8d',
                                  'source_sha256': sha256(code), 'original_guest_sha256': original_hash,
                                  'adapter_sha256': sha256(original), 'private_dependency_sha256': sha256(dependency),
                                  'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    (candidate / 'integration.json').write_text(json.dumps(record, indent=2))
    print(json.dumps({'candidate': str(candidate), 'state': 'probe-only',
                      'prefix_copied': False, 'original_candidate_modified': False,
                      'adapter': record['lookup_adapter']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    main(parser.parse_args().source)
