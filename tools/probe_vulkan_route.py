#!/usr/bin/env python3
"""Frame-only isolated Vulkan/thunk probes; never launch or alter a game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import struct
import subprocess
import tempfile
import time


def mapped_libraries(pid):
    proc = Path('/proc') / str(pid)
    records = []
    try:
        paths = sorted(set(line.split(maxsplit=5)[-1] for line in (proc / 'maps').read_text().splitlines()
                           if re.search(r'libvulkan|libframe_vulkan|freedreno|VkLayer', line)))
        for path in paths:
            if not path.startswith('/'):
                continue
            try:
                with (proc / 'root' / path.lstrip('/')).open('rb') as file:
                    header = file.read(20)
                machine = struct.unpack_from('<H', header, 18)[0] if header[:4] == b'\x7fELF' else None
            except OSError:
                machine = None
            records.append({'path': path, 'elf_machine': machine})
    except OSError:
        pass
    return records


def probe_processes(root_pid, executable):
    pending = [root_pid]
    seen = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        proc = Path('/proc') / str(pid)
        try:
            pending.extend(int(x) for x in (proc / 'task' / str(pid) / 'children').read_text().split())
            if os.fsencode(executable) in (proc / 'cmdline').read_bytes().split(b'\0'):
                yield pid
        except OSError:
            continue


def run_case(name, command, env, work, timeout=15):
    # Every probe has its own process group. A timeout kills only that group.
    start = time.monotonic()
    with (work / (name + '.log')).open('w') as log:
        proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        maps = []
        timed_out = False
        while proc.poll() is None:
            for pid in probe_processes(proc.pid, command[-1]):
                current = mapped_libraries(pid)
                if len(current) > len(maps):
                    maps = current
            if time.monotonic() - start > timeout:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                break
            time.sleep(0.1)
        proc.wait()
    text = (work / (name + '.log')).read_text(errors='replace')
    return {'name': name, 'returncode': proc.returncode, 'timed_out': timed_out,
            'seconds': round(time.monotonic() - start, 3), 'libraries': maps,
            'log_tail': text[-5000:]}


TURNIP_ALGORITHMS = ('profiled', 'profiled_imm', 'prefer_gmem', 'prefer_sysmem')


def main(candidate, mode, probe='route', multiview='none', stereo_size=None, validate=False, turnip_autotune=None):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run this probe on the ARM64 Frame')
    candidate = Path(candidate).resolve(strict=True)
    if turnip_autotune not in (None, *TURNIP_ALGORITHMS) or (turnip_autotune and mode != 'route'):
        raise ValueError('Turnip tuning is restricted to independent route/image probes')
    if probe in ('memory', 'foveation', 'vrs') and mode != 'route':
        raise ValueError('Capability queries require the independent route controls without transforming layers')
    if mode == 'route' and (multiview != 'none' or stereo_size is not None or validate):
        raise ValueError('Multiview and stereo size require a layer probe')
    if stereo_size is not None and stereo_size <= 0:
        raise ValueError('Stereo size must be positive')
    base = Path.home() / '.local/share/frame-pcvr/runs'
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='vulkan-route-', dir=base))
    work.chmod(0o700)
    config = work / 'config'
    config.mkdir()
    # Steam-support FEX uses DATA_PATH for its filesystem socket. Keep it short
    # and separate from the live game's prefix and all default FEX data paths.
    prefix_base = Path.home() / '.local/share/frame-pcvr/p'
    prefix_base.mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='probe-', dir=prefix_base))
    template = json.loads((candidate / 'ConfigTemplate.json').read_text()
                          .replace('@FEX_COMPAT_TOOL@', str(candidate))
                          .replace('@FEX_ROOTFS_PATH@', '/usr/share/guestos/fex-mesa'))
    template['Config']['ServerSocketPath'] = 'frame-pcvr-probe-' + work.name
    # A private database is required: portable config overrides must not lose it.
    database = json.loads((candidate / 'usr/share/fex-emu/ThunksDB.json').read_text())
    # This Arch graphics provider uses /usr/lib, while the upstream database
    # expands @PREFIX_LIB@ to lib64 for non-multiarch 64-bit guests.
    database['DB']['Vulkan']['Overlay'].extend([
        '/usr/lib/libvulkan.so.1', '/usr/lib/libvulkan.so.1.4.321',
        '/run/gfx/main/usr/lib/libvulkan.so.1', '/run/gfx/main/usr/lib/libvulkan.so.1.4.321',
    ])
    (config / 'ThunksDB.json').write_text(json.dumps(database))
    (config / 'Config.json').write_text(json.dumps(template))
    for enabled in (0, 1):
        (work / f'thunk-{enabled}.json').write_text(json.dumps({'Config': {}, 'ThunksDB': {'Vulkan': enabled}}))
    source = Path(__file__).with_name({'route': 'vulkan_route_probe.c', 'image': 'vulkan_image_probe.c',
                                      'memory': 'vulkan_memory_probe.c', 'foveation': 'vulkan_foveation_probe.c',
                                      'vrs': 'vulkan_vrs_probe.c'}[probe])
    native = work / 'probe-arm64'
    guest = work / 'probe-x86_64'
    native_libraries = ['-lvulkan'] if probe == 'memory' else ['-ldl']
    subprocess.run(['clang', '-Wall', '-Wextra', str(source)] + native_libraries + ['-o', str(native)], check=True)
    rootfs = Path('/usr/share/guestos/fex-mesa')
    lib = rootfs / 'usr/lib'
    extra = []
    if probe in ('image', 'memory', 'foveation', 'vrs'):
        resource = subprocess.check_output(['clang', '-print-resource-dir'], text=True).strip()
        extra = ['-ffreestanding', '-nostdinc', '-isystem', resource + '/include', '-isystem', '/usr/include']
        if probe == 'memory':
            extra += ['-DFRAME_PROBE_FREESTANDING']
    guest_libraries = ['-l:libvulkan.so.1'] if probe == 'memory' else []
    subprocess.run(['clang', '--target=x86_64-linux-gnu', '--sysroot=' + str(rootfs), '-fuse-ld=lld'] + extra + [
                    '-fPIE', '-pie', '-nostdlib', str(lib / 'Scrt1.o'), str(lib / 'crti.o'), str(source),
                    '-L' + str(lib)] + guest_libraries + ['-lc', str(lib / 'crtn.o'),
                    '-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2', '-o', str(guest)], check=True)
    env = dict(os.environ)
    # Do not inherit a title's FEX settings, Steam identity or rendering overrides.
    for key in list(env):
        if key.startswith(('FEX_', 'VK_', 'FDM_', 'RPO_', 'TU_', 'STEAM_COMPAT_', 'SteamApp', 'SteamGame')):
            del env[key]
    if turnip_autotune:
        env['TU_AUTOTUNE_ALGO'] = turnip_autotune
    env.update(FEX_PORTABLE='1', FEX_ROOTFS=str(rootfs), FEX_APP_CONFIG_LOCATION=str(config) + '/',
               FEX_APP_DATA_LOCATION=str(data) + '/', STEAM_COMPAT_DATA_PATH=str(data),
               FEX_SERVERSOCKETPATH='frame-pcvr-probe-' + work.name,
               tu_override_uncached_as_cache_coherent='true')
    fex = candidate / 'usr/bin/FEX'
    layer_env = dict(VK_INSTANCE_LAYERS='VK_LAYER_VALVE_rpo:VK_LAYER_VALVE_fdm_injection',
                     FDM_DEBUG='enable', RPO_DEBUG='enable')
    if validate:
        validation = Path('/opt/steamvr/bin/vrwebhelper/linuxarm64/libVkLayer_khronos_validation.so')
        if not validation.is_file():
            raise RuntimeError('Native validation library is unavailable')
        layer_dir = work / 'layers'
        layer_dir.mkdir()
        (layer_dir / 'validation.json').write_text(json.dumps({'file_format_version': '1.2.0', 'layer': {
            'name': 'VK_LAYER_KHRONOS_validation', 'type': 'GLOBAL', 'library_path': str(validation),
            'api_version': '1.3.0', 'implementation_version': '1', 'description': 'Private probe validation'}}))
        layer_env['VK_LAYER_PATH'] = str(layer_dir) + ':/usr/share/vulkan/explicit_layer.d'
        # Validation runs below the transforming layers, on their driver calls.
        layer_env['VK_INSTANCE_LAYERS'] += ':VK_LAYER_KHRONOS_validation'
    if multiview != 'none':
        layer_env['RPO_DEBUG'] += ',enable_doublewide_to_multiview_' + multiview
    if stereo_size is not None:
        layer_env.update(RPO_SWAPCHAIN_SIZE=str(stereo_size), FDM_SWAPCHAIN_SIZE=str(stereo_size))
    if mode == 'route':
        trials = [('native', [str(native)], env)]
        trials.extend((f'guest-thunk-{enabled}', [str(fex), str(guest)],
                       dict(env, FEX_APP_CONFIG=str(work / f'thunk-{enabled}.json'))) for enabled in (0, 1))
    elif mode == 'layers':
        layers = dict(env, **layer_env)
        trials = [('native-layers', [str(native)], layers),
                  ('guest-thunk-layers', [str(fex), str(guest)],
                   dict(layers, FEX_APP_CONFIG=str(work / 'thunk-1.json')))]
    else:
        emulator = json.loads((candidate / 'emulator.json').read_text())
        for key in ('argv', 'container_argv', 'main_argv'):
            emulator['emulator_v0'][key] = str(fex)
        emulator['emulator_v0']['server_argv'] = str(candidate / 'usr/bin/FEXServerManager')
        manifest = work / 'probe-emulator.json'
        manifest.write_text(json.dumps(emulator))
        runtime = Path.home() / '.local/share/Steam/steamapps/common/SteamLinuxRuntime_4'
        trial_env = dict(env, FEX_ROOTFS='', FEX_APP_CONFIG=str(work / 'thunk-1.json'),
                         STEAM_COMPAT_MACHINE_ARCHITECTURE='aarch64-linux-gnu',
                         STEAM_COMPAT_EMULATOR=str(manifest),
                         STEAM_COMPAT_GRAPHICS_PROVIDER=str(rootfs / 'graphics_provider.json'),
                         STEAM_COMPAT_DATA_PATH=str(data),
                         STEAM_COMPAT_TOOL_PATHS=str(candidate) + ':' + str(work),
                         PRESSURE_VESSEL_VARIABLE_DIR=str(work / 'runtime-var'),
                         VK_INSTANCE_LAYERS='VK_LAYER_VALVE_rpo:VK_LAYER_VALVE_fdm_injection',
                         FDM_DEBUG='enable', RPO_DEBUG='enable')
        # The FEX graphics provider lists only x86 ICDs. Select the ARM64 ICD
        # privately, after entering the container, using PV's imported driver.
        icd = work / 'arm64-icd.json'
        icd.write_text(json.dumps({'file_format_version': '1.0.1', 'ICD': {
            'library_path': '/usr/lib/pressure-vessel/overrides/lib/aarch64-linux-gnu/vulkan/libvulkan_freedreno.so',
            'library_arch': '64', 'api_version': '1.4.362'}}))
        # Candidate metadata must not silently override this probe's options.
        emulator['emulator_v0']['container_environment'].update(
            layer_env, FEX_APP_CONFIG=str(work / 'thunk-1.json'), VK_DRIVER_FILES=str(icd))
        manifest.write_text(json.dumps(emulator))
        trials = [('container-thunk-layers', [str(runtime / '_v2-entry-point'), '--verb=run', '--',
                  '/usr/bin/env', 'VK_DRIVER_FILES=' + str(icd),
                  'VK_LOADER_DEBUG=error,warn,layer,driver', str(guest)], trial_env)]
    cases = []
    for name, command, trial_env in trials:
        cases.append(run_case(name, command, trial_env, work, timeout=40 if mode == 'container' else 15))
        print(json.dumps({'work': str(work), 'case': cases[-1]}), flush=True)
    result = {'work': str(work), 'candidate': str(candidate), 'mode': mode, 'probe': probe,
              'multiview': multiview, 'stereo_size': stereo_size, 'validate': validate, 'cases': cases,
              'turnip_autotune_algo': turnip_autotune,
              'candidate_integration_sha256': hashlib.sha256((candidate / 'integration.json').read_bytes()).hexdigest()}
    driver = Path('/usr/lib/libvulkan_freedreno.so')
    if turnip_autotune and driver.is_file():
        result['native_driver_sha256'] = hashlib.sha256(driver.read_bytes()).hexdigest()
    (work / 'results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'saved': str(work / 'results.json')}), flush=True)
    return 1 if any(case['returncode'] != 0 or case['timed_out'] for case in cases) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--mode', choices=('route', 'layers', 'container'), default='route')
    parser.add_argument('--probe', choices=('route', 'image', 'memory', 'foveation', 'vrs'), default='route')
    parser.add_argument('--multiview', choices=('none', 'ubo'), default='none')
    parser.add_argument('--stereo-size', type=int)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--turnip-autotune', choices=TURNIP_ALGORITHMS)
    args = parser.parse_args()
    raise SystemExit(main(args.candidate, args.mode, args.probe, args.multiview, args.stereo_size, args.validate, args.turnip_autotune))
