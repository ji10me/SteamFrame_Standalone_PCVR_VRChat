#!/usr/bin/env python3
"""Install the verified source kit into a private, recipient-local Frame setup."""
import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from portable_bundle import FILES, MANIFEST, digest, verify
from framectl import FEX_COMMIT, FEX_BINS, at, read_vdf, sha256

TOOLS = ('python3', 'clang', 'clang++', 'cmake', 'ninja', 'ld.lld', 'git', 'patchelf', 'cp')
APPS = {'438100': 'VRChat', '1493710': 'Proton Experimental',
        '1826330': 'Proton EasyAntiCheat Runtime', '3127680': 'FEX', '4183110': 'SteamLinuxRuntime_4'}
EAC = 'pfx/drive_c/users/steamuser/AppData/Roaming/EasyAntiCheat'
DXVK_KEYS = ('dxvk.trackPipelineLifetime', 'dxvk.numCompilerThreads', 'dxgi.maxDeviceMemory')


def active_vrchat(proc_root=Path('/proc')):
    for process in proc_root.glob('[0-9]*'):
        try:
            env = dict(x.split(b'=', 1) for x in (process / 'environ').read_bytes().split(b'\0') if b'=' in x)
        except OSError:
            continue
        if env.get(b'SteamAppId') == b'438100' or env.get(b'SteamGameId') == b'438100':
            return True
    return False


def preflight(profile, home=None, system_root=Path('/'), proc_root=Path('/proc')):
    home = Path(home or Path.home())
    steamapps = home / '.local/share/Steam/steamapps'
    errors = []
    warnings = []
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        errors.append('Run on the ARM64 Linux Frame, not a Mac or another PC.')
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        errors.append('Run as the Frame Steam user, without sudo.')
    if sys.version_info < (3, 9):
        errors.append('Python 3.9 or newer is required.')
    if any(c in str(home) for c in ('\n', '\r', '%')):
        errors.append('Home path cannot be represented safely in Steam launch options.')
    for tool in TOOLS:
        if not shutil.which(tool):
            errors.append('Missing build tool: ' + tool)
    if active_vrchat(proc_root):
        errors.append('Close VRChat completely before installation; live prefixes are not copied.')
    apps = {}
    for appid, label in APPS.items():
        try:
            state = at(read_vdf(steamapps / ('appmanifest_' + appid + '.acf')), 'AppState')
            directory = at(state, 'installdir')
            relative = Path(directory)
            if not directory or relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Invalid installation directory')
            location = steamapps / 'common' / relative
            if not location.is_dir():
                raise ValueError('Install directory is missing')
            expected = profile['tested_steam_builds'].get(appid)
            if expected and at(state, 'buildid') != expected:
                message = 'Untested Steam build for ' + label + '; review compatibility before installing.'
                # Proton Experimental updates often; its required DXVK keys are checked below.
                (warnings if appid == '1493710' else errors).append(message)
            # StateFlags bit 4 denotes installed; downloading/updating states need review.
            if at(state, 'StateFlags') != '4':
                errors.append('Steam installation is not complete: ' + label)
            apps[appid] = location
        except (OSError, ValueError, TypeError, KeyError):
            errors.append('Install in the default Steam library: ' + label + ' (' + appid + ')')
    standard = steamapps / 'common/FEX-Emu'
    if apps.get('3127680') != standard:
        errors.append('This kit requires the standard FEX-Emu directory.')
    for name in ('emulator.json', 'fex-compat-tool', 'unshare.py', 'toolmanifest.vdf',
                 'usr/share/fex-emu/GuestThunks/libvulkan-guest.so',
                 'usr/lib/aarch64-linux-gnu/fex-emu/HostThunks/libvulkan-host.so'):
        if not (standard / name).is_file():
            errors.append('Missing standard FEX file: ' + name)
    wrapper = standard / 'fex-compat-tool'
    if wrapper.is_file() and wrapper.read_text().count("    subprocess.call(['/usr/bin/printenv'])") != 1:
        errors.append('Standard FEX wrapper differs from the tested version.')
    prefix = steamapps / 'compatdata/438100'
    if not (prefix / 'pfx/user.reg').is_file() or not (prefix / EAC).is_dir():
        errors.append('Run your own VRChat once through Steam to initialize its Prefix/EAC, then close it.')
    if '438100' in apps and not (apps['438100'] / 'VRChat.exe').is_file():
        errors.append('Install the Windows version of VRChat using Proton Experimental.')
    if '1493710' in apps:
        dxvk = apps['1493710'] / 'files/lib/wine/dxvk/x86_64-windows'
        for library, keys in (('d3d11.dll', DXVK_KEYS[:2]), ('dxgi.dll', DXVK_KEYS[2:])):
            path = dxvk / library
            content = path.read_bytes() if path.is_file() else b''
            if any(key.encode() not in content for key in keys):
                errors.append('Required DXVK configuration keys unavailable in ' + library)
    for name in ('usr/share/guestos/fex-mesa/usr/lib/Scrt1.o',
                 'usr/share/guestos/fex-mesa/usr/lib/crti.o',
                 'usr/share/guestos/fex-mesa/usr/lib/crtn.o',
                 'usr/share/guestos/fex-mesa/usr/lib/libvulkan.so.1',
                 'usr/include/vulkan/vulkan.h', 'opt/steamvr/bin/linux64/vrclient.so'):
        if not (system_root / name).is_file():
            errors.append('Missing Frame system component: ' + name)
    existing = home / '.local/share/frame-pcvr/c' / ('438100-' + FEX_COMMIT[:8])
    if existing.exists():
        errors.append('An existing project Prefix is present; preserve it and review before reinstalling.')
    if shutil.disk_usage(home).free < 12 * 1024 ** 3:
        errors.append('At least 12 GiB free space is required; large Prefixes may need more.')
    return {'ready': not errors, 'errors': errors, 'warnings': warnings, 'no_changes': True}


def verify_candidate(candidate, home):
    candidate = Path(candidate).resolve()
    base = home / '.local/share/frame-pcvr'
    candidate.relative_to((base / 'candidates').resolve())
    record = json.loads((candidate / 'integration.json').read_text())
    if (record.get('state') != 'smoke-passed' or record.get('commit') != FEX_COMMIT
            or not record.get('standard_unchanged') or record.get('vulkan_trial') != 'native'
            or record.get('multiview_trial') != 'none' or not record.get('lookup_adapter')):
        raise RuntimeError('Expected the newly tested native Vulkan candidate')
    standard = home / '.local/share/Steam/steamapps/common/FEX-Emu'
    if set(record.get('runtime_hashes', {})) != set(FEX_BINS):
        raise RuntimeError('Incomplete runtime hashes')
    if not record.get('standard_hashes') or not record.get('extra_hashes'):
        raise RuntimeError('Missing integration hashes')
    for directory, table in ((standard, record['standard_hashes']),
                             (candidate / 'usr/bin', record['runtime_hashes']),
                             (candidate, record['extra_hashes'])):
        for name, expected in table.items():
            path = (directory / name).resolve()
            path.relative_to(directory.resolve())
            if sha256(path) != expected:
                raise RuntimeError('Integration changed since probing; no profile applied')
    prefix = Path(record['compat_data']).resolve()
    prefix.relative_to((base / 'c').resolve())
    if not (prefix / EAC).is_dir():
        raise RuntimeError('Private Prefix lacks initialized EAC launcher directory')
    return record, prefix


def apply_profile(candidate, profile, home=None):
    home = Path(home or Path.home())
    if active_vrchat():
        raise RuntimeError('VRChat started during installation; no profile applied')
    record, prefix = verify_candidate(candidate, home)
    candidate = Path(candidate)
    manifest_path = candidate / 'emulator.json'
    manifest = json.loads(manifest_path.read_text())
    emulator = manifest['emulator_v0']
    env = emulator['container_environment']
    if env.get('DXVK_CONFIG', '').strip():
        raise RuntimeError('Existing DXVK overrides require review')
    if any('FEX_DISKCACHE' in emulator.get(section, {}) for section in ('environment', 'container_environment')):
        raise RuntimeError('Existing DiskCache override requires review')
    template_path = candidate / 'ConfigTemplate.json'
    config_path = prefix / 'fex-emu/Config.json'
    template, config = json.loads(template_path.read_text()), json.loads(config_path.read_text())
    if any(item.get('Config', {}).get('DiskCache') != '1' for item in (template, config)):
        raise RuntimeError('Unexpected source DiskCache state')
    template['Config']['DiskCache'] = config['Config']['DiskCache'] = profile['DiskCache']
    env.update(DXVK_CONFIG=profile['DXVK_CONFIG'], PROTON_USE_XALIA=profile['PROTON_USE_XALIA'])
    for path, content in ((template_path, template), (config_path, config), (manifest_path, manifest)):
        path.write_text(json.dumps(content, indent=2) + '\n')
    for name in ('emulator.json', 'ConfigTemplate.json'):
        record['extra_hashes'][name] = sha256(candidate / name)
    record['portable_profile'] = {'name': profile['name'], 'other_device_game_tested': False,
                                  'avatar_preferences_edited': False}
    (candidate / 'integration.json').write_text(json.dumps(record, indent=2) + '\n')
    return prefix


def run_step(session, kit, label, argv, extra_env=None):
    log = session / (label + '.log')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    # A build/probe must not inherit a caller's game identity or rendering setup.
    for name in list(env):
        if name.startswith(('SteamApp', 'SteamGame', 'STEAM_COMPAT_', 'FEX_', 'VK_', 'RPO_', 'FDM_')):
            env.pop(name)
    env.update(extra_env or {})
    print(label + ' ...', flush=True)
    with log.open('x') as output:
        result = subprocess.run(argv, cwd=kit, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(label + ' failed; private local log: ' + str(log))
    return log


def first_json(log):
    # The staging helpers print their JSON result before optional probe output.
    return json.JSONDecoder().raw_decode(log.read_text().lstrip())[0]


def probe_result(log):
    for line in reversed(log.read_text().splitlines()):
        try:
            saved = json.loads(line).get('saved')
        except (ValueError, AttributeError):
            continue
        if saved:
            return Path(saved)
    raise RuntimeError('Probe did not save its results')


def install(kit, profile, stage=None, *, accept_alpha_license=False):
    if not accept_alpha_license:
        raise RuntimeError('Read LICENSE.md first. Installation requires --accept-alpha-license to confirm the MIT license and no-warranty notice.')
    home = Path.home()
    os.umask(0o077)
    base = home / '.local/share/frame-pcvr'
    sessions = base / 'installations'
    sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
    session = Path(tempfile.mkdtemp(prefix='portable-', dir=sessions))
    installed_kit = session / 'kit'
    installed_kit.mkdir(mode=0o700)
    # Copy only verified kit sources; local builds/logs always go outside this tree.
    for name in (*FILES, MANIFEST):
        target = installed_kit / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit / name, target)
        target.chmod(0o700 if name.endswith(('.py', '.sh')) else 0o600)
    verify(installed_kit)
    print('Private installation: ' + str(session), flush=True)
    python = [sys.executable, '-B']

    def helper(label, name, *args):
        return run_step(session, installed_kit, label,
                        python + [str(installed_kit / 'tools' / name)] + list(map(str, args)))

    if stage is None:
        work = session / 'build'
        run_step(session, installed_kit, '01-build-fex', ['sh', str(installed_kit / 'scripts/build-fex-stage.sh')],
                 {'FEX_FRAME_WORK': str(work), 'FEX_FRAME_JOBS': '2'})
        stages = list(work.glob('*/stage'))
        if len(stages) != 1:
            raise RuntimeError('Could not identify the newly built FEX stage')
        stage = stages[0]
    original = first_json(helper('02-stage', 'stage_fex_candidate.py', stage))['candidate']
    repaired = first_json(helper('03-lookup', 'stage_lookup_fix.py', original))['candidate']
    route = probe_result(helper('04-route', 'probe_vulkan_route.py', repaired, '--probe', 'route'))
    image = probe_result(helper('05-image', 'probe_vulkan_route.py', repaired, '--probe', 'image'))
    result = first_json(helper('06-native', 'stage_vulkan_trial.py', repaired,
                               '--mode', 'native', '--route-proof', route, '--image-proof', image))
    candidate = Path(result['candidate'])
    prefix = apply_profile(candidate, profile)
    launcher = installed_kit / 'tools/vrchat-launch.py'
    options = ' '.join(shlex.quote(str(arg)) for arg in
                       (launcher, '--candidate', candidate, '--eac-env', '--')) + ' %command%'
    (session / 'LAUNCH_OPTIONS.txt').write_text(options + '\n')
    (session / 'installation.json').write_text(json.dumps({
        'schema': 1, 'candidate': str(candidate), 'prefix': str(prefix),
        'kit_manifest_sha256': digest((installed_kit / MANIFEST).read_bytes()),
        'alpha_terms_acknowledged': True,
        'alpha_license_sha256': digest((installed_kit / 'LICENSE.md').read_bytes()),
        'profile': profile['name'], 'local_probes_passed': True,
        'eac_game_login_vr_tested': False, 'steam_options_changed': False,
        'private_do_not_share': True}, indent=2) + '\n')
    print('Prepared. Paste the single line from this file into VRChat launch options:')
    print(session / 'LAUNCH_OPTIONS.txt')
    print('Select Proton Experimental and VR mode; follow portable/INSTALL.ja.md for avatar limits and first launch.')
    print('Login/VR/world entry must still be checked on this Frame. Do not share this installation directory.')
    return session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true', help='Read-only prerequisites check (default)')
    action.add_argument('--install', action='store_true', help='Build and stage privately; does not edit Steam options')
    parser.add_argument('--accept-alpha-license', action='store_true',
                        help='Confirm the MIT license and no-warranty notice in LICENSE.md')
    parser.add_argument('--stage', type=Path, help='Use an existing audited FEX build stage instead of building')
    args = parser.parse_args()
    kit = Path(__file__).resolve().parents[1]
    try:
        verify(kit)
        profile = json.loads((kit / 'portable/profile.json').read_text())
        if profile['schema'] != 1 or profile['fex_commit'] != FEX_COMMIT:
            raise RuntimeError('Unsupported portable profile')
        if args.install and not args.accept_alpha_license:
            raise RuntimeError('Read LICENSE.md first, then use --install --accept-alpha-license if you agree. No installation was performed.')
        result = preflight(profile)
        print(json.dumps(result, indent=2))
        if not result['ready']:
            return 2
        if args.install:
            install(kit, profile, args.stage.resolve() if args.stage else None,
                    accept_alpha_license=args.accept_alpha_license)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
