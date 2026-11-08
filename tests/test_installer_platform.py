from pathlib import Path
import copy
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer import platform_setup as platform


class PlatformTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.steam=Path(self.tmp.name)/'steamapps';self.game=self.steam/'common/VRChat';self.game.mkdir(parents=True)
  (self.game/'VRChat.apk').write_bytes(b'original apk fixture')
  self.original=self.steam/'compatdata/438100';(self.original/'internal/com.vrchat.android').mkdir(parents=True)
  self.private=self.original/'internal/com.vrchat.android/settings';self.private.write_text('private Android fixture');self.private.chmod(0o600)
  self.inode=self.original.stat().st_ino;self.journal={};self.saved=[];self.calls=[]
  self.current={'effective':'lepton','priority':100,'tool':''};self.edition='android'
  self.deps=SimpleNamespace(game_edition=lambda s:{'edition':self.edition,'steamapps':str(self.steam),'game':str(self.game)},
                            edition_error=lambda e:'unknown edition',ensure_game=self.ensure_game)
 def ensure_game(self,steam,emit,edition='windows'):self.calls.append(('edition',edition));self.edition=edition
 def get(self):return dict(self.current)
 def put(self,value):self.calls.append(('compat',value));self.current={'effective':value or 'lepton','priority':250 if value else 100,'tool':value}
 def save(self):self.saved.append(copy.deepcopy(self.journal))
 def prepare(self):
  platform.prepare(self.steam,self.journal,self.save,lambda s,t,*a:shutil.copytree(s,t,symlinks=True),self.steam,lambda *a:None,
                   deps=self.deps,get=self.get,put=self.put,active=lambda:False)
 def legacy_trial(self):
  # A journal produced by old alpha versions: new installs no longer migrate APK data.
  backup=self.steam/'compatdata/.frame-easy-original-fixture';backup.mkdir()
  shutil.copytree(self.game,backup/'game')
  stat=self.original.stat();self.original.rename(backup/'android-data')
  self.journal['platform']={'original_edition':'android','previous':self.get(),'target':platform.PROTON,
   'change_requested':True,'data_moved':True,'data_move_pending':False,'data_restored':False,
   'restored':False,'backup':str(backup),'data_identity':[stat.st_dev,stat.st_ino]}
  self.put(platform.PROTON);self.edition='windows';self.save()
 def restore(self):
  platform.restore(self.steam,self.journal,self.save,lambda *a:None,deps=self.deps,get=self.get,put=self.put,active=lambda:False)
 def test_android_rename_preserves_private_data_and_restores_without_overwriting_windows(self):
  self.legacy_trial();record=self.journal['platform'];backup=Path(record['backup'])
  self.assertFalse(self.original.exists());self.assertEqual((backup/'android-data').stat().st_ino,self.inode)
  self.assertEqual((backup/'android-data/internal/com.vrchat.android/settings').read_text(),'private Android fixture')
  self.assertEqual((backup/'game/VRChat.apk').read_bytes(),b'original apk fixture')
  self.original.mkdir();(self.original/'windows-settings').write_text('new Windows fixture')
  self.restore();self.assertEqual(self.original.stat().st_ino,self.inode);self.assertTrue(record['restored'])
  self.assertEqual(self.private.read_text(),'private Android fixture');self.assertEqual(self.private.stat().st_mode&0o777,0o600)
  self.assertEqual(list(backup.glob('windows-data-*'))[0].joinpath('windows-settings').read_text(),'new Windows fixture')
  self.assertEqual(self.edition,'android');self.assertEqual(self.current['tool'],'')
 def test_foreign_compat_change_stops_before_moving_any_data_back(self):
  self.legacy_trial();backup=Path(self.journal['platform']['backup']);self.current={'tool':'user-tool','effective':'user-tool','priority':250}
  with self.assertRaisesRegex(RuntimeError,'変更'):self.restore()
  self.assertTrue((backup/'android-data').exists());self.assertFalse(self.original.exists())
 def test_crash_after_steam_accepts_new_tool_can_restore_previous_selection(self):
  self.edition='missing'
  real=self.put
  def disconnected(value):real(value);raise RuntimeError('disconnect')
  with self.assertRaisesRegex(RuntimeError,'disconnect'):
   platform.prepare(self.steam,self.journal,self.save,lambda s,t,*a:shutil.copytree(s,t),self.steam,lambda *a:None,
                    deps=self.deps,get=self.get,put=disconnected)
  self.assertTrue(self.journal['platform']['change_requested']);self.restore()
  self.assertEqual(self.private.read_text(),'private Android fixture');self.assertEqual(self.current['tool'],'')
 def test_crash_after_data_restore_before_journal_write_is_recognized_by_inode(self):
  self.legacy_trial();record=self.journal['platform'];backup=Path(record['backup'])
  (backup/'android-data').rename(self.original)
  self.assertFalse(record['data_restored']);self.restore();self.assertTrue(record['restored'])
 def test_backup_path_escape_is_rejected_before_compat_write(self):
  self.legacy_trial();self.journal['platform']['backup']=str(Path(self.tmp.name)/'outside');before=list(self.calls)
  with self.assertRaisesRegex(RuntimeError,'保管場所'):self.restore()
  self.assertEqual(self.calls,before)
 def test_windows_with_target_tool_does_not_move_data_or_redownload(self):
  self.edition='windows';self.current={'effective':platform.PROTON,'priority':250,'tool':platform.PROTON}
  self.prepare();self.restore();self.assertEqual(self.calls,[]);self.assertTrue(self.private.exists())
 def test_android_stops_before_journal_compat_or_data_changes(self):
  with self.assertRaisesRegex(RuntimeError,'アンインストール'):self.prepare()
  self.assertEqual(self.calls,[]);self.assertEqual(self.journal,{})
  self.assertEqual(self.original.stat().st_ino,self.inode);self.assertTrue(self.private.exists())
 def test_mixed_edition_is_rejected_before_any_platform_write(self):
  self.edition='mixed'
  with self.assertRaisesRegex(RuntimeError,'unknown'):self.prepare()
  self.assertEqual(self.calls,[]);self.assertEqual(self.journal,{})
 def test_missing_game_uses_steam_then_restores_original_tool(self):
  self.edition='missing';self.prepare();self.assertNotIn('backup',self.journal['platform'])
  self.assertIn(('edition','windows'),self.calls);self.restore();self.assertEqual(self.current['tool'],'')
 def test_compare_and_set_refuses_external_change(self):
  with self.assertRaisesRegex(RuntimeError,'変更'):
   platform.compat_set({'tool':'stale'},platform.PROTON,self.get,self.put)
  self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
