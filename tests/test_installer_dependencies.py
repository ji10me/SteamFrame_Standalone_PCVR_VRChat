import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from installer import dependencies as deps
from installer import frame_setup as setup


class EditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.steam = Path(self.temp.name) / 'steamapps'
        self.game = self.steam / 'common/VRChat'
        self.game.mkdir(parents=True)
        (self.steam / 'appmanifest_438100.acf').write_text(
            '"AppState" { "appid" "438100" "installdir" "VRChat" "StateFlags" "4" }')

    def windows(self):
        data = bytearray(128)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 60, 64)
        data[64:70] = b'PE\0\0\x64\x86'
        (self.game / 'VRChat.exe').write_bytes(data)

    def android(self):
        with zipfile.ZipFile(self.game / 'VRChat.apk', 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'fixture metadata')

    def test_android_detected_without_modifying_any_files(self):
        self.android()
        before = {p.relative_to(self.steam): p.read_bytes() for p in self.steam.rglob('*') if p.is_file()}
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'android')
        self.assertIn('アンインストール', deps.edition_error(deps.game_edition(self.steam)))
        after = {p.relative_to(self.steam): p.read_bytes() for p in self.steam.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_android_edition_stops_before_download_or_platform_write(self):
        self.android()
        with patch.object(setup.platform, 'system', return_value='Linux'), \
             patch.object(setup.platform, 'machine', return_value='aarch64'), \
             patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup, 'game_active', return_value=False), \
             patch.object(deps, 'ensure_tools') as download, \
             patch.object(setup.platform_module(), 'prepare') as migration:
            with self.assertRaises(deps.AndroidPresentError):
                setup.prepare_dependencies({},lambda:None,self.steam.parent,{'steam':self.steam})
            download.assert_not_called();migration.assert_not_called()

    def test_initial_android_warning_has_no_journal_options_or_download_side_effects(self):
        self.android(); root=self.steam.parent/'easy'
        with patch.object(setup,'verify_kit',return_value={}), \
             patch.object(setup.platform,'system',return_value='Linux'), \
             patch.object(setup.platform,'machine',return_value='aarch64'), \
             patch.object(setup.os,'geteuid',return_value=1000), \
             patch.object(setup,'game_active',return_value=False), \
             patch.object(setup,'paths',return_value={'base':root,'steam':self.steam}), \
             patch.object(setup,'options_get') as options, \
             patch.object(deps,'wizard') as operation:
            with self.assertRaises(deps.AndroidPresentError):setup.install(True)
            options.assert_not_called();operation.assert_not_called()
        self.assertFalse(root.exists())
        self.assertEqual(deps.game_edition(self.steam)['edition'],'android')

    def test_confirmation_flag_removes_android_before_downloading(self):
        self.android()
        with patch.object(deps,'remove_android') as remove:
            setup.prepare_existing_game({'steam':self.steam},True)
            remove.assert_called_once_with(self.steam,setup.emit,confirmed=True,active=setup.game_active)

    def test_gui_receives_dedicated_exit_code_without_a_failed_install_journal(self):
        self.android();root=self.steam.parent/'easy';output=io.StringIO()
        with patch.object(sys,'argv',['frame_setup.py','install','--accept-alpha-license']), \
             patch.object(setup,'base',return_value=root), \
             patch.object(setup,'verify_kit',return_value={}), \
             patch.object(setup.platform,'system',return_value='Linux'), \
             patch.object(setup.platform,'machine',return_value='aarch64'), \
             patch.object(setup.os,'geteuid',return_value=1000), \
             patch.object(setup,'game_active',return_value=False), \
             patch.object(setup,'paths',return_value={'base':root,'steam':self.steam}), \
             patch.object(deps,'wizard') as operation, redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:setup.main()
            self.assertEqual(result.exception.code,42);operation.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['stage'],'android-present')
        self.assertFalse((root/'installation.json').exists())

    def test_windows_is_reused_even_when_removal_flag_is_present(self):
        self.windows()
        with patch.object(deps,'remove_android') as remove:
            setup.prepare_existing_game({'steam':self.steam},True)
            remove.assert_not_called()

    def test_unknown_and_mixed_are_never_automatically_removed(self):
        for mixed in (False,True):
            if mixed:self.windows();self.android()
            with patch.object(deps,'remove_android') as remove:
                with self.assertRaises(RuntimeError):setup.prepare_existing_game({'steam':self.steam},True)
                remove.assert_not_called()

    def test_verified_windows_executable_is_required(self):
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'unknown')
        (self.game / 'VRChat.exe').write_text('not an executable')
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'unknown')
        self.windows()
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'windows')

    def test_mixed_apk_and_windows_are_not_guessed(self):
        self.windows(); self.android()
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'mixed')
        self.assertIsNotNone(deps.edition_error(deps.game_edition(self.steam)))

    def test_manifest_path_escape_does_not_read_external_game(self):
        (self.steam / 'appmanifest_438100.acf').write_text(
            '"AppState" { "appid" "438100" "installdir" "../../outside" "StateFlags" "4" }')
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'unknown')

    def test_incomplete_download_is_not_accepted_as_installed(self):
        self.windows()
        p = self.steam / 'appmanifest_438100.acf'
        p.write_text(p.read_text().replace('"4"', '"1026"'))
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'incomplete')

    def test_incomplete_download_message_identifies_steam_state(self):
        self.windows()
        p=self.steam/'appmanifest_438100.acf'
        p.write_text(p.read_text().replace('"4"','"1026"'))
        message=deps.edition_error(deps.game_edition(self.steam))
        self.assertIn('StateFlags=1026',message)
        self.assertIn('ダウンロード画面',message)
        self.assertNotIn(str(self.steam),message)

    def test_nested_apk_with_another_name_is_detected(self):
        p=self.game/'android/content'
        p.mkdir(parents=True)
        with zipfile.ZipFile(p/'VRChat.APK','w') as archive:
            archive.writestr('AndroidManifest.xml',b'fixture')
        self.assertEqual(deps.game_edition(self.steam)['edition'],'android')

    def test_apk_link_outside_game_is_never_read(self):
        outside=self.steam.parent/'outside.apk';outside.write_text('private fixture')
        (self.game/'VRChat.apk').symlink_to(outside)
        with patch.object(deps.zipfile,'ZipFile') as read:
            self.assertEqual(deps.game_edition(self.steam)['edition'],'unknown')
            read.assert_not_called()

    def test_invalid_library_metadata_gets_an_actionable_error(self):
        (self.steam/'libraryfolders.vdf').write_text('"libraryfolders" {')
        message=deps.edition_error(deps.game_edition(self.steam))
        self.assertIn('Steamライブラリ',message)
        self.assertIn('再起動',message)

    def test_unknown_edition_lists_presence_without_private_paths(self):
        message=deps.edition_error(deps.game_edition(self.steam))
        self.assertIn('Windows実行ファイル=なし',message)
        self.assertIn('APK数=0',message)
        self.assertNotIn(str(self.steam),message)

    def test_android_in_another_steam_library_is_detected(self):
        other = self.steam.parent / 'other/steamapps'
        other.mkdir(parents=True)
        (self.steam / 'appmanifest_438100.acf').rename(other / 'appmanifest_438100.acf')
        (other / 'common/VRChat').mkdir(parents=True)
        with zipfile.ZipFile(other / 'common/VRChat/VRChat.apk', 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'fixture')
        (self.steam / 'libraryfolders.vdf').write_text(
            '"libraryfolders" { "1" { "path" ' + json.dumps(str(other.parent)) + ' } }')
        self.assertEqual(deps.game_edition(self.steam)['edition'], 'android')


class AndroidRemovalTests(unittest.TestCase):
    def run_removal(self, editions, **kwargs):
        states=iter(editions);calls=[];now=[0]
        deps.remove_android('/fixture',lambda *a:None,
            inspect=lambda s:{'edition':next(states),'steamapps':'/fixture'},
            operation=lambda *a:calls.append(a), clock=lambda:now[0],
            sleep=lambda n:now.__setitem__(0,now[0]+n),**kwargs)
        return calls

    def test_no_confirmation_never_sends_remove(self):
        with patch.object(deps,'wizard') as operation:
            with self.assertRaises(deps.AndroidPresentError):deps.remove_android('/fixture',lambda *a:None)
            operation.assert_not_called()

    def test_only_android_game_is_removed_and_completion_is_awaited(self):
        self.assertEqual(self.run_removal(['android','android','incomplete','missing'],confirmed=True),[('remove',438100)])

    def test_changed_edition_and_running_game_never_receive_remove(self):
        for edition in ('windows','mixed','unknown','missing'):
            with self.assertRaises(RuntimeError):self.run_removal([edition],confirmed=True)
        with patch.object(deps,'wizard') as operation:
            with self.assertRaisesRegex(RuntimeError,'終了'):
                deps.remove_android('/fixture',lambda *a:None,confirmed=True,active=lambda:True,
                    inspect=lambda s:{'edition':'android','steamapps':'/fixture'})
            operation.assert_not_called()

    def test_other_library_is_not_removed(self):
        with patch.object(deps,'wizard') as operation:
            with self.assertRaisesRegex(RuntimeError,'別Steamライブラリ'):
                deps.remove_android('/fixture',lambda *a:None,confirmed=True,
                    inspect=lambda s:{'edition':'android','steamapps':'/other'})
            operation.assert_not_called()

    def test_timeout_stops_without_retrying_remove(self):
        now=[0];calls=[]
        with self.assertRaisesRegex(RuntimeError,'削除完了'):
            deps.remove_android('/fixture',lambda *a:None,confirmed=True,
                inspect=lambda s:{'edition':'android','steamapps':'/fixture'},
                operation=lambda *a:calls.append(a),clock=lambda:now[0],
                sleep=lambda n:now.__setitem__(0,now[0]+n),timeout=2)
        self.assertEqual(calls,[('remove',438100)])


class DownloadTests(unittest.TestCase):
    def test_complete_tools_do_not_open_steam_install_ui(self):
        calls = []
        deps.ensure_tools('/fixture', lambda *a: None, complete=lambda *a: True,
                          operation=lambda *a: calls.append(a))
        self.assertEqual(calls, [])

    def test_only_missing_tool_is_requested_and_eula_waits_without_accepting(self):
        appid = 1826330
        calls = []; messages = []; now = [0]; phases = iter(['confirmation', 'config', 'working'])
        def complete(path, app): return app != appid or now[0] >= 3
        def operation(action, app):
            calls.append((action, app))
            return {'phase': next(phases)} if action == 'read' else {}
        deps.ensure_tools('/fixture', lambda *a: messages.append(a), complete=complete,
                          operation=operation, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n))
        self.assertEqual([c for c in calls if c[0] == 'start'], [('start', appid)])
        self.assertEqual([c for c in calls if c[0] == 'continue'], [('continue', appid)])
        self.assertTrue(any('規約' in message for stage, message in messages))

    def test_foreign_install_is_not_continued_or_canceled(self):
        calls = []
        def operation(action, app): calls.append(action); return {'phase': 'foreign'}
        with self.assertRaisesRegex(RuntimeError, '中断'):
            deps.ensure_tools('/fixture', lambda *a: None, complete=lambda *a: False, operation=operation)
        self.assertEqual(calls, ['start', 'read'])

    def test_timeout_keeps_steam_managed_download_and_launch_settings_untouched(self):
        calls = []; now = [0]
        def operation(action, app): calls.append(action); return {'phase': 'working'}
        with self.assertRaisesRegex(RuntimeError, '待ち時間'):
            deps.ensure_tools('/fixture', lambda *a: None, complete=lambda *a: False, operation=operation,
                              timeout=2, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n))
        self.assertNotIn('continue', calls)
        self.assertNotIn('cancel', calls)

    def test_request_outside_tool_allowlist_is_rejected_before_api_call(self):
        calls = []
        with self.assertRaises(ValueError): deps.wizard('start', 570, calls.append)
        self.assertEqual(calls, [])

    def test_installed_proton_in_another_library_does_not_open_download_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'steamapps';root.mkdir();other=Path(tmp)/'other/steamapps';other.mkdir(parents=True)
            (root/'libraryfolders.vdf').write_text('"libraryfolders" { "1" { "path" '+json.dumps(str(other.parent))+' } }')
            proton=other/'common/Proton - Experimental';proton.mkdir(parents=True)
            for n in ['proton','version']:(proton/n).write_text('fixture')
            (other/'appmanifest_1493710.acf').write_text('"AppState" { "appid" "1493710" "installdir" "Proton - Experimental" "StateFlags" "6" }')
            self.assertTrue(deps.tool_complete(root,1493710))
            status=deps.tool_status(root,1493710);self.assertEqual(Path(status['path']),proton.resolve())
            with patch.object(deps,'TOOLS',{1493710:'Proton Experimental'}):
                calls=[];deps.ensure_tools(root,lambda *a:None,operation=lambda *a:calls.append(a))
                self.assertEqual(calls,[])

    def test_incomplete_corrupt_or_updating_tool_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proton=root/'common/Proton - Experimental';proton.mkdir(parents=True)
            for n in ['proton','version']:(proton/n).write_text('fixture')
            for flags in [0,2,32,128,256|4,512|4,1026,4|1048576]:
                (root/'appmanifest_1493710.acf').write_text('"AppState" { "appid" "1493710" "installdir" "Proton - Experimental" "StateFlags" "'+str(flags)+'" }')
                self.assertFalse(deps.tool_complete(root,1493710),flags)

    def test_missing_tool_does_not_wait_forever_after_completed_wizard(self):
        now=[0];calls=[]
        def operation(action,app):calls.append((action,app));return {'phase':'complete'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(deps,'TOOLS',{1493710:'Proton Experimental'}):
            with self.assertRaisesRegex(RuntimeError,'取得が進んでいません'):
                deps.ensure_tools(tmp,lambda *a:None,operation=operation,clock=lambda:now[0],
                    sleep=lambda n:now.__setitem__(0,now[0]+n))
        self.assertEqual(calls.count(('resume',1493710)),1)
        self.assertLess(now[0],180)

    def test_tool_diagnostics_do_not_expose_install_paths(self):
        text=deps.tool_diagnostic({'state_flags':516,'manifest_count':1,'target_present':True,
                                  'reason':'steam_state','path':'/private/recipient/Steam'})
        self.assertIn('516',text);self.assertNotIn('/private',text)

    def test_completed_wizard_does_not_time_out_an_active_background_download(self):
        now=[0];calls=[]
        with tempfile.TemporaryDirectory() as tmp,patch.object(deps,'TOOLS',{1493710:'Proton Experimental'}):
            root=Path(tmp);target=root/'common/Proton - Experimental';target.mkdir(parents=True)
            manifest=root/'appmanifest_1493710.acf'
            def write(flags):manifest.write_text('"AppState" { "appid" "1493710" "installdir" "Proton - Experimental" "StateFlags" "'+str(flags)+'" }')
            write(1048576)
            def operation(action,app):
                calls.append((action,app))
                if now[0]>=130:
                    for n in ['proton','version']:(target/n).write_text('fixture')
                    write(4)
                return {'phase':'complete'}
            deps.ensure_tools(root,lambda *a:None,operation=operation,clock=lambda:now[0],
                              sleep=lambda n:now.__setitem__(0,now[0]+n))
        self.assertGreater(now[0],120)
        self.assertNotIn(('resume',1493710),calls)

    def test_game_platform_update_waits_for_windows_files_not_only_a_manifest(self):
        calls=[];now=[0];phases=iter(['config','idle','working'])
        def operation(action,app):
            calls.append((action,app))
            return {'phase':next(phases)} if action=='read' else {}
        deps.ensure_game('/fixture',lambda *a:None,
                         inspect=lambda s:{'edition':'windows' if now[0]>=3 else 'android'},operation=operation,
                         clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
        self.assertIn(('continue',438100),calls);self.assertIn(('resume',438100),calls)
        self.assertTrue(all(app==438100 for action,app in calls))

    def test_game_update_never_continues_a_foreign_install(self):
        calls=[]
        def operation(action,app):calls.append(action);return {'phase':'foreign'}
        with self.assertRaisesRegex(RuntimeError,'中断'):
            deps.ensure_game('/fixture',lambda *a:None,inspect=lambda s:{'edition':'android'},operation=operation)
        self.assertEqual(calls,['start','read'])

    def test_javascript_refuses_eula_foreign_game_and_low_disk_continuation(self):
        subprocess.run(['node', str(ROOT / 'tests/installer_dependency_ui_test.cjs'),
                        str(ROOT / 'installer/steam_install.js')], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
