"""Checked, opt-in selection of a private Proton copy for one VRChat trial."""
from pathlib import Path

from framectl import sha256

DLLS = ('files/lib/wine/dxvk/x86_64-windows/d3d11.dll',
        'files/lib/wine/dxvk/x86_64-windows/dxgi.dll')
CHECKED_FILES = ('proton', 'version', *DLLS)


def verify_files(root, hashes):
    if set(hashes) != set(CHECKED_FILES):
        raise RuntimeError('Private Proton requires the complete fixed file checks')
    for name in CHECKED_FILES:
        path = (root / name).resolve(strict=True)
        path.relative_to(root.resolve(strict=True))
        if sha256(path) != hashes[name]:
            raise RuntimeError('Proton changed after staging; retain the existing launch')


def select_private_proton(args, environment, base, record):
    if record.get('format') != 'frame-dfr-private-proton-v1':
        raise RuntimeError('Unknown private Proton trial format')
    parent = Path.home() / '.local/share/Steam/steamapps/common/Proton - Experimental'
    if Path(record['parent']).resolve(strict=True) != parent.resolve(strict=True):
        raise RuntimeError('Private Proton parent is not the checked Steam tool')
    private = Path(record['path']).resolve(strict=True)
    private.relative_to((base / 'proton').resolve(strict=True))
    if private == (base / 'proton').resolve(strict=True):
        raise RuntimeError('A specific private Proton copy is required')
    verify_files(parent, record['parent_hashes'])
    verify_files(private, record['private_hashes'])
    for name in ('proton', 'version'):
        if record['private_hashes'][name] != record['parent_hashes'][name]:
            raise RuntimeError('This trial must preserve the stock Proton script and version')
    original_entrypoints = {str(parent / 'proton'), str(parent.resolve() / 'proton')}
    positions = [i for i, arg in enumerate(args) if arg in original_entrypoints]
    if len(positions) != 1:
        raise RuntimeError('Expected exactly one original Proton boundary')
    result = list(args)
    result[positions[0]] = str(private / 'proton')
    old_roots = {str(parent), str(parent.resolve())}
    paths = [str(private) if p in old_roots else p
             for p in environment.get('STEAM_COMPAT_TOOL_PATHS', '').split(':') if p]
    environment['STEAM_COMPAT_TOOL_PATHS'] = ':'.join([str(private)] + [p for p in paths if p != str(private)])
    return result
