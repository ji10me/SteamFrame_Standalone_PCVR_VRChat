import io
import json
import contextlib
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import portable_bundle as bundle
import install_portable as installer

ROOT = Path(__file__).resolve().parents[1]


class KitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.source = self.work / 'source'
        for name in bundle.FILES:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.profile = json.loads((self.source / 'portable/profile.json').read_text())

    def unpack(self):
        archive = self.work / 'kit.tar.gz'
        bundle.build(self.source, archive)
        with tarfile.open(archive) as tar:
            # These are locally built, allowlisted regular files; never extract arbitrary inputs.
            for member in tar.getmembers():
                target = self.work / 'extracted' / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
                target.chmod(member.mode)
        return self.work / 'extracted' / bundle.TOP


class BundleTest(KitFixture):
    def test_zip_directly_contains_the_same_source_without_host_metadata(self):
        # Host metadata and nested archives must not appear in a recipient kit.
        extra = self.source / '__MACOSX/._private'
        extra.parent.mkdir()
        extra.write_text('private host metadata')
        tarpath, zippath, again = (self.work / n for n in ('a.tar.gz', 'a.zip', 'b.zip'))
        bundle.build(self.source, tarpath)
        bundle.build(self.source, zippath)
        bundle.build(self.source, again)
        self.assertEqual(zippath.read_bytes(), again.read_bytes())
        with tarfile.open(tarpath) as tar, zipfile.ZipFile(zippath) as archive:
            self.assertEqual(set(archive.namelist()), set(tar.getnames()))
            for item in archive.infolist():
                self.assertEqual(archive.read(item), tar.extractfile(item.filename).read())
                self.assertEqual(item.extra, b'')
                self.assertEqual(item.comment, b'')
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(item.flag_bits & 1, 0)  # No encryption.
                self.assertNotIn('__MACOSX', item.filename)
                self.assertFalse(item.filename.endswith(('.tar.gz', '.zip')))
        with self.assertRaises(FileExistsError):
            bundle.build(self.source, zippath)

    def test_allowlist_reproducible_archive_and_anonymous_metadata(self):
        extra = self.source / 'local/private.log'
        extra.parent.mkdir()
        extra.write_text('never export this')
        a, b = self.work / 'a.tar.gz', self.work / 'b.tar.gz'
        bundle.build(self.source, a)
        bundle.build(self.source, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual((self.work / 'INSTALL.ja.md').read_bytes(),
                         (self.source / 'portable/INSTALL.ja.md').read_bytes())
        self.assertEqual((self.work / 'LICENSE.md').read_bytes(),
                         (self.source / 'LICENSE.md').read_bytes())
        with tarfile.open(a) as tar:
            self.assertEqual(set(tar.getnames()), {bundle.TOP + '/' + n for n in (*bundle.FILES, bundle.MANIFEST)})
            for member in tar.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual((member.uid, member.gid, member.uname, member.gname, member.mtime), (0, 0, '', '', 0))
                self.assertFalse(Path(member.name).is_absolute())
                self.assertNotIn('..', Path(member.name).parts)
        self.assertTrue(a.with_name(a.name + '.sha256').read_text().startswith(bundle.digest(a.read_bytes())))
        bundle.verify(self.unpack())

    def test_private_payload_and_symlinks_refused(self):
        path = self.source / 'portable/profile.json'
        original = path.read_bytes()
        for secret in ('/Users/private-owner/project', '192.168.12.34',
                       'usr_' + '1234abcd-' * 4, 'ghp_' + 'a' * 30,
                       'person@example.test', '-----BEGIN OPENSSH PRIVATE KEY-----',
                       '7656119' + '0' * 10, 'authCookie=private-value',
                       '"authCookie": "private-value"', '"access_token": "private-value"', '\0binary'):
            with self.subTest(secret_type=secret[:8]):
                path.write_bytes(original + secret.encode())
                with self.assertRaises(ValueError):
                    bundle.build(self.source, self.work / 'rejected.tar.gz')
                self.assertFalse((self.work / 'rejected.tar.gz').exists())
        path.write_bytes(original)
        path.unlink()
        path.symlink_to(ROOT / 'portable/profile.json')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            bundle.read_payload(self.source)

    def test_modified_missing_or_extra_payload_refused(self):
        kit = self.unpack()
        target = kit / 'portable/profile.json'
        original = target.read_bytes()
        target.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            bundle.verify(kit)
        target.write_bytes(original)
        (kit / 'auth.log').write_text('private')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            bundle.verify(kit)
        (kit / 'auth.log').unlink()
        target.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            bundle.verify(kit)


class RecipientFixture(KitFixture):
    def setUp(self):
        super().setUp()
        self.home = self.work / 'recipient'
        self.home.mkdir()
        self.base = self.home / '.local/share/frame-pcvr'
        self.steamapps = self.home / '.local/share/Steam/steamapps'
        self.system = self.work / 'system'
        self.proc = self.work / 'proc'
        self.proc.mkdir()
        self.standard = self.steamapps / 'common/FEX-Emu'

    def put(self, path, text='fixture'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def native_candidate(self):
        candidate = self.base / 'candidates/new-native'
        prefix = self.base / 'c/new-native'
        self.put(self.standard / 'stock', 'standard-unchanged')
        self.put(prefix / 'fex-emu/Config.json', json.dumps({'Config': {'DiskCache': '1', 'RootFS': 'kept'}}))
        (prefix / installer.EAC).mkdir(parents=True)
        manifest = {'emulator_v0': {'environment': {}, 'container_environment': {
            'VK_INSTANCE_LAYERS': '', 'FDM_DEBUG': '', 'RPO_DEBUG': '', 'untouched': 'yes'}}}
        self.put(candidate / 'emulator.json', json.dumps(manifest))
        self.put(candidate / 'ConfigTemplate.json', json.dumps({'Config': {'DiskCache': '1', 'other': 'kept'}}))
        for name in installer.FEX_BINS:
            self.put(candidate / 'usr/bin' / name)
        record = {'state': 'smoke-passed', 'commit': installer.FEX_COMMIT, 'standard_unchanged': True,
                  'candidate': str(candidate), 'compat_data': str(prefix), 'vulkan_trial': 'native',
                  'multiview_trial': 'none', 'lookup_adapter': {'present': True},
                  'standard_hashes': {'stock': installer.sha256(self.standard / 'stock')},
                  'runtime_hashes': {n: installer.sha256(candidate / 'usr/bin' / n) for n in installer.FEX_BINS},
                  'extra_hashes': {n: installer.sha256(candidate / n) for n in ('emulator.json', 'ConfigTemplate.json')}}
        self.put(candidate / 'integration.json', json.dumps(record))
        return candidate, prefix


class RecipientTest(RecipientFixture):
    def test_preflight_success_then_live_prefix_and_existing_install_stop(self):
        directories = {'438100': 'VRChat', '3127680': 'FEX-Emu', '1493710': 'Proton - Experimental',
                       '1826330': 'Proton EasyAntiCheat Runtime', '4183110': 'SteamLinuxRuntime_4'}
        for appid, directory in directories.items():
            build = self.profile['tested_steam_builds'].get(appid, '1')
            self.put(self.steamapps / ('appmanifest_' + appid + '.acf'),
                     '"AppState" { "installdir" "' + directory + '" "buildid" "' + build + '" "StateFlags" "4" }')
            (self.steamapps / 'common' / directory).mkdir(parents=True, exist_ok=True)
        for name in ('emulator.json', 'unshare.py', 'toolmanifest.vdf',
                     'usr/share/fex-emu/GuestThunks/libvulkan-guest.so',
                     'usr/lib/aarch64-linux-gnu/fex-emu/HostThunks/libvulkan-host.so'):
            self.put(self.standard / name)
        self.put(self.standard / 'fex-compat-tool', "    subprocess.call(['/usr/bin/printenv'])")
        self.put(self.steamapps / 'common/VRChat/VRChat.exe')
        self.put(self.steamapps / 'compatdata/438100/pfx/user.reg')
        (self.steamapps / 'compatdata/438100' / installer.EAC).mkdir(parents=True)
        dxvk = self.steamapps / 'common/Proton - Experimental/files/lib/wine/dxvk/x86_64-windows'
        self.put(dxvk / 'd3d11.dll', ' '.join(installer.DXVK_KEYS[:2]))
        self.put(dxvk / 'dxgi.dll', installer.DXVK_KEYS[2])
        for name in ('usr/share/guestos/fex-mesa/usr/lib/Scrt1.o', 'usr/share/guestos/fex-mesa/usr/lib/crti.o',
                     'usr/share/guestos/fex-mesa/usr/lib/crtn.o', 'usr/share/guestos/fex-mesa/usr/lib/libvulkan.so.1',
                     'usr/include/vulkan/vulkan.h', 'opt/steamvr/bin/linux64/vrclient.so'):
            self.put(self.system / name)
        with patch.object(installer.platform, 'system', return_value='Linux'), \
                patch.object(installer.platform, 'machine', return_value='aarch64'), \
                patch.object(installer.os, 'geteuid', return_value=1000, create=True), \
                patch.object(installer.shutil, 'which', return_value='/tool'), \
                patch.object(installer.shutil, 'disk_usage') as space:
            space.return_value.free = 20 * 1024 ** 3
            check = lambda: installer.preflight(self.profile, self.home, self.system, self.proc)
            self.assertEqual(check(), {'ready': True, 'errors': [], 'warnings': [], 'no_changes': True})
            self.assertFalse(self.base.exists())
            self.put(self.proc / '123/environ', 'SteamAppId=438100\0')
            self.assertIn('live prefixes', ' '.join(check()['errors']))
            (self.proc / '123/environ').unlink()
            (self.base / 'c' / ('438100-' + installer.FEX_COMMIT[:8])).mkdir(parents=True)
            self.assertIn('existing project Prefix', ' '.join(check()['errors']))
            self.put(self.steamapps / 'appmanifest_1493710.acf',
                     '"AppState" { "installdir" "Proton - Experimental" "buildid" "changed" "StateFlags" "4" }')
            result = check()
            self.assertNotIn('Untested Steam build', ' '.join(result['errors']))
            self.assertIn('Untested Steam build', ' '.join(result['warnings']))
            self.put(self.steamapps / 'appmanifest_3127680.acf',
                     '"AppState" { "installdir" "FEX-Emu" "buildid" "changed" "StateFlags" "4" }')
            self.assertIn('Untested Steam build', ' '.join(check()['errors']))

    def test_profile_private_only_and_hashes_refreshed(self):
        candidate, prefix = self.native_candidate()
        standard_before = (self.standard / 'stock').read_bytes()
        with patch.object(installer, 'active_vrchat', return_value=False):
            installer.apply_profile(candidate, self.profile, self.home)
        manifest = json.loads((candidate / 'emulator.json').read_text())['emulator_v0']['container_environment']
        self.assertEqual(manifest['DXVK_CONFIG'], self.profile['DXVK_CONFIG'])
        self.assertEqual(manifest['PROTON_USE_XALIA'], '0')
        self.assertEqual(manifest['untouched'], 'yes')
        self.assertEqual(manifest['VK_INSTANCE_LAYERS'], '')
        self.assertEqual(json.loads((prefix / 'fex-emu/Config.json').read_text())['Config'], {'DiskCache': '0', 'RootFS': 'kept'})
        self.assertEqual((self.standard / 'stock').read_bytes(), standard_before)
        record, _ = installer.verify_candidate(candidate, self.home)
        self.assertFalse(record['portable_profile']['other_device_game_tested'])
        self.assertFalse(record['portable_profile']['avatar_preferences_edited'])

    def test_changed_runtime_live_game_and_external_prefix_refused(self):
        candidate, prefix = self.native_candidate()
        before = (candidate / 'emulator.json').read_bytes()
        with patch.object(installer, 'active_vrchat', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'started'):
                installer.apply_profile(candidate, self.profile, self.home)
        with patch.object(installer, 'active_vrchat', return_value=False):
            runtime = candidate / 'usr/bin/FEX'
            runtime.write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                installer.apply_profile(candidate, self.profile, self.home)
        self.assertEqual((candidate / 'emulator.json').read_bytes(), before)
        record = json.loads((candidate / 'integration.json').read_text())
        record['compat_data'] = str(self.work / 'external-prefix')
        runtime.write_text('fixture')
        (candidate / 'integration.json').write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            installer.verify_candidate(candidate, self.home)

    def test_installer_pipeline_uses_private_helpers_and_generates_options_only(self):
        kit = self.unpack()
        candidate, prefix = self.native_candidate()
        calls = []

        def run(session, installed, label, argv, extra_env=None):
            calls.append((label, argv))
            log = session / (label + '.log')
            if label in ('04-route', '05-image'):
                proof = session / (label + '.json')
                proof.write_text('{}')
                log.write_text(json.dumps({'saved': str(proof)}) + '\n')
            else:
                log.write_text(json.dumps({'candidate': str(candidate), 'prefix': str(prefix)}) + '\n')
            return log

        with patch.object(Path, 'home', return_value=self.home), \
                patch.object(installer, 'run_step', side_effect=run), \
                patch.object(installer, 'active_vrchat', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            session = installer.install(kit, self.profile, self.work / 'audited-stage', accept_alpha_license=True)
        self.assertEqual([c[0] for c in calls], ['02-stage', '03-lookup', '04-route', '05-image', '06-native'])
        bundle.verify(session / 'kit')
        line = (session / 'LAUNCH_OPTIONS.txt').read_text()
        self.assertIn('--eac-env -- %command%', line)
        self.assertNotIn('--capture-launch', line)
        self.assertTrue((session / 'kit/tools/vrchat-launch.py').stat().st_mode & 0o100)
        result = json.loads((session / 'installation.json').read_text())
        self.assertFalse(result['steam_options_changed'])
        self.assertFalse(result['eac_game_login_vr_tested'])
        self.assertTrue(result['private_do_not_share'])
        self.assertTrue(result['alpha_terms_acknowledged'])
        self.assertEqual(result['alpha_license_sha256'], bundle.digest((kit / 'LICENSE.md').read_bytes()))

    def test_without_license_acknowledgement_install_does_not_write(self):
        kit = self.unpack()
        with patch.object(Path, 'home', return_value=self.home):
            with self.assertRaisesRegex(RuntimeError, 'accept-alpha-license'):
                installer.install(kit, self.profile, self.work / 'audited-stage')
        self.assertFalse(self.base.exists())

    def test_probe_failure_never_produces_launch_options_or_applies_profile(self):
        kit = self.unpack()
        candidate, prefix = self.native_candidate()
        before = (candidate / 'emulator.json').read_bytes()

        def run(session, installed, label, argv, extra_env=None):
            if label == '04-route':
                raise RuntimeError('Independent probe failed')
            log = session / (label + '.log')
            log.write_text(json.dumps({'candidate': str(candidate), 'prefix': str(prefix)}))
            return log

        with patch.object(Path, 'home', return_value=self.home), \
                patch.object(installer, 'run_step', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                installer.install(kit, self.profile, self.work / 'audited-stage', accept_alpha_license=True)
        self.assertFalse(list(self.base.rglob('LAUNCH_OPTIONS.txt')))
        self.assertEqual((candidate / 'emulator.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
