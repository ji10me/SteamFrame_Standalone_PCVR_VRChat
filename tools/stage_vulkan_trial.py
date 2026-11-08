#!/usr/bin/env python3
"""Clone an inactive VRChat prefix and stage a private Vulkan integration trial."""
import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
from framectl import FEX_COMMIT, sha256


def main(source, mode, multiview='none', route_proof=None, image_proof=None, source_prefix=None):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    if mode not in ('rpo', 'foveated') and multiview != 'none':
        raise RuntimeError('Multiview conversion requires the foveated trial')
    base = Path.home() / '.local/share/frame-pcvr'
    source = Path(source).resolve()
    source.relative_to((base / 'candidates').resolve())
    record = json.loads((source / 'integration.json').read_text())
    if record.get('commit') != FEX_COMMIT:
        raise RuntimeError('Source candidate has not passed integration checks')
    if record.get('state') == 'probe-only' and record.get('lookup_adapter'):
        if not route_proof or not image_proof:
            raise RuntimeError('Lookup repair requires passing route and image proof files')
        route = json.loads(Path(route_proof).read_text())
        image = json.loads(Path(image_proof).read_text())
        for proof in (route, image):
            if proof.get('candidate') != str(source) or any(c['returncode'] or c['timed_out'] for c in proof['cases']):
                raise RuntimeError('Probe did not pass for this candidate')
        tested = next(c for c in route['cases'] if c['name'] == 'guest-thunk-1')
        if not all(s in tested['log_tail'] for s in ('instance_self_lookup=present', 'device_self_lookup=present')):
            raise RuntimeError('Vulkan self-lookup regression proof is missing')
        tested = next(c for c in image['cases'] if c['name'] == 'guest-thunk-1')
        if image.get('probe') != 'image' or len(re.findall(r'image=(?:2880x1440x1|1440x1440x2) mismatched_pixels=0 ', tested['log_tail'])) != 2:
            raise RuntimeError('Stereo GPU image proof is missing')
        guest = source / 'usr/share/fex-emu/GuestThunks'
        for name, key in [('libvulkan-guest.so', 'adapter_sha256'), ('libframe_vulkan_thunk_base.so', 'private_dependency_sha256')]:
            if sha256(guest / name) != record['lookup_adapter'][key]:
                raise RuntimeError('Lookup repair changed after probing')
        parent = Path(record['parent_candidate']).resolve()
        parent.relative_to((base / 'candidates').resolve())
        record['compat_data'] = json.loads((parent / 'integration.json').read_text())['compat_data']
        record['lookup_proof_files'] = {'route': str(route_proof), 'image': str(image_proof)}
    elif record.get('state') != 'smoke-passed':
        raise RuntimeError('Source candidate has not passed integration checks')
    standard = Path.home() / '.local/share/Steam/steamapps/common/FEX-Emu'
    for name, digest in record['standard_hashes'].items():
        if sha256(standard / name) != digest:
            raise RuntimeError('Standard FEX changed')
    for name, digest in record['runtime_hashes'].items():
        if sha256(source / 'usr/bin' / name) != digest:
            raise RuntimeError('Source runtime changed')
    for name, digest in record.get('extra_hashes', {}).items():
        path = (source / name).resolve()
        path.relative_to(source)
        if sha256(path) != digest:
            raise RuntimeError('Source integration changed')
    # An authenticated live Wine prefix must never be copied.
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
            if env.get(b'SteamAppId') == b'438100':
                raise RuntimeError('VRChat launch processes are still active: ' + proc.name)
        except OSError:
            pass
    prefix = base / 'c' / ('vk-' + uuid.uuid4().hex[:8])
    prefix.mkdir(mode=0o700)
    original_prefix = Path(source_prefix or record['compat_data']).resolve()
    original_prefix.relative_to((base / 'c').resolve())
    if not (original_prefix / 'pfx/user.reg').is_file():
        raise RuntimeError('Source prefix is missing its Wine registry')
    for name in ('pfx', 'version', 'tracked_files', 'config_info'):
        path = original_prefix / name
        if path.exists():
            subprocess.run(['cp', '-a', '--reflink=auto', str(path), str(prefix / name)], check=True)
    candidate = Path(tempfile.mkdtemp(prefix='vk-' + mode + '-', dir=base / 'candidates'))
    subprocess.run(['cp', '-a', '--reflink=auto', str(source) + '/.', str(candidate)], check=True)
    if record.get('lookup_adapter'):
        guest = candidate / 'usr/share/fex-emu/GuestThunks'
        subprocess.run(['patchelf', '--set-rpath', str(guest), str(guest / 'libvulkan-guest.so')], check=True)
        record['lookup_adapter']['adapter_sha256'] = sha256(guest / 'libvulkan-guest.so')
    database = json.loads((candidate / 'usr/share/fex-emu/ThunksDB.json').read_text())
    versioned = (Path('/usr/share/guestos/fex-mesa/usr/lib/libvulkan.so.1').resolve()).name
    for directory in ('/usr/lib', '/run/gfx/main/usr/lib'):
        database['DB']['Vulkan']['Overlay'].extend([directory + '/libvulkan.so.1', directory + '/' + versioned])
    (candidate / 'usr/share/fex-emu/ThunksDB.json').write_text(json.dumps(database, indent=2))
    config = prefix / 'fex-emu'
    config.mkdir()
    shutil.copy2(candidate / 'usr/share/fex-emu/ThunksDB.json', config / 'ThunksDB.json')
    (config / 'vulkan-app.json').write_text(json.dumps({'Config': {}, 'ThunksDB': {'Vulkan': 1}}))
    setup = config / 'setup-app.json'
    setup.write_text(json.dumps({'Config': {}, 'ThunksDB': {'Vulkan': 0}}))
    (config / 'Config.json').write_text((candidate / 'ConfigTemplate.json').read_text()
                                      .replace('@FEX_COMPAT_TOOL@', str(candidate))
                                      .replace('@FEX_ROOTFS_PATH@', '/usr/share/guestos/fex-mesa'))
    icd = prefix / 'arm64-icd.json'
    icd.write_text(json.dumps({'file_format_version': '1.0.1', 'ICD': {
        'library_path': '/usr/lib/pressure-vessel/overrides/lib/aarch64-linux-gnu/vulkan/libvulkan_freedreno.so',
        'library_arch': '64', 'api_version': '1.4.362'}}))
    emulator = json.loads((candidate / 'emulator.json').read_text())
    env = emulator['emulator_v0']['container_environment']
    emulator['emulator_v0']['environment']['FEX_APP_CONFIG'] = str(setup)
    emulator['emulator_v0']['main_argv'] = './usr/bin/FEX'
    env.update(FEX_APP_CONFIG=str(config / 'vulkan-app.json'), VK_DRIVER_FILES=str(icd),
               VK_INSTANCE_LAYERS='', FDM_DEBUG='', RPO_DEBUG='')
    if mode in ('rpo', 'foveated'):
        env.update(VK_INSTANCE_LAYERS='VK_LAYER_VALVE_rpo', RPO_DEBUG='enable')
        if mode == 'foveated':
            env.update(VK_INSTANCE_LAYERS='VK_LAYER_VALVE_rpo:VK_LAYER_VALVE_fdm_injection',
                       FDM_DEBUG='enable')
        if multiview != 'none':
            env['RPO_DEBUG'] += ',enable_doublewide_to_multiview_' + multiview
    (candidate / 'emulator.json').write_text(json.dumps(emulator, indent=2))
    extra_names = ['emulator.json', 'usr/share/fex-emu/ThunksDB.json', 'ConfigTemplate.json', 'FEXCompatTool']
    if record.get('lookup_adapter'):
        extra_names.extend(['usr/share/fex-emu/GuestThunks/libvulkan-guest.so',
                            'usr/share/fex-emu/GuestThunks/libframe_vulkan_thunk_base.so',
                            'usr/lib/aarch64-linux-gnu/fex-emu/HostThunks/libvulkan-host.so'])
    record.update(state='smoke-passed', candidate=str(candidate), compat_data=str(prefix), vulkan_trial=mode, multiview_trial=multiview,
                  parent_candidate=str(source), parent_compat_data=str(original_prefix),
                  extra_hashes={name: sha256(candidate / name) for name in extra_names})
    (candidate / 'integration.json').write_text(json.dumps(record, indent=2))
    result = {'candidate': str(candidate), 'prefix': str(prefix), 'mode': mode, 'multiview': multiview,
              'source': str(source), 'source_prefix': str(original_prefix)}
    (prefix / 'vulkan-trial.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--mode', choices=('native', 'rpo', 'foveated'), default='native')
    parser.add_argument('--multiview', choices=('none', 'ubo', 'instanced'), default='none')
    parser.add_argument('--route-proof', type=Path)
    parser.add_argument('--image-proof', type=Path)
    parser.add_argument('--source-prefix', type=Path, help='Clone another inactive prefix in the private c directory')
    args = parser.parse_args()
    main(args.source, args.mode, args.multiview, args.route_proof, args.image_proof, args.source_prefix)
