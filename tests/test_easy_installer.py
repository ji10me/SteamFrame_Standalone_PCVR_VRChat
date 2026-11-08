import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer import frame_setup as setup
from installer import launch
from installer.build_windows import allocated_elf_sections,bootstrap,zip_bytes,hash_bytes

class RollbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'owned';self.release=self.root/'releases/a-test';self.release.mkdir(parents=True)
  (self.release/'code').write_text('app');self.prefix=self.root/'p';self.prefix.mkdir();(self.prefix/'user.reg').write_text('recipient-private-data')
  self.state={'format':'frame-easy-install-v1','release':str(self.release),'prefix':str(self.prefix),'old_options':'prior user options','new_options':'owned options','phase':'installed'}
  setup.atomic(self.root/'installation.json',self.state);self.current='owned options';self.writes=[]
 def get(self):return self.current
 def put(self,v):self.writes.append(v);self.current=v
 def remove(self,active=lambda:False):setup.uninstall(self.root,self.get,self.put,active)
 def test_uninstall_restores_and_removes_only_software(self):
  sibling=self.root.parent/'other-app';sibling.mkdir();(sibling/'data').write_text('unrelated')
  self.remove();self.assertEqual(self.current,'prior user options');self.assertFalse(self.release.exists())
  self.assertEqual(list((self.root/'retained').glob('prefix-*'))[0].joinpath('user.reg').read_text(),'recipient-private-data')
  self.assertEqual((sibling/'data').read_text(),'unrelated');self.assertFalse((self.root/'installation.json').exists())
 def test_external_launch_change_is_never_overwritten(self):
  self.current='new user choice'
  with self.assertRaises(RuntimeError):self.remove()
  self.assertEqual(self.writes,[]);self.assertTrue(self.release.exists());self.assertTrue(self.prefix.exists())
 def test_running_game_refuses_all_changes(self):
  with self.assertRaises(RuntimeError):self.remove(lambda:True)
  self.assertEqual(self.writes,[]);self.assertTrue(self.prefix.exists())
 def test_crash_before_activation_can_be_removed(self):
  self.current=self.state['old_options'];self.state['phase']='preparing';setup.atomic(self.root/'installation.json',self.state)
  self.remove();self.assertEqual(self.writes,[])
 def test_crash_after_steam_write_restores_backup(self):
  self.state['phase']='activating';setup.atomic(self.root/'installation.json',self.state)
  self.remove();self.assertEqual(self.writes,['prior user options'])
 def test_path_outside_owned_release_refuses_before_steam_write(self):
  self.state['release']=str(self.root.parent/'other/a-evil');setup.atomic(self.root/'installation.json',self.state)
  with self.assertRaises(RuntimeError):self.remove()
  self.assertEqual(self.writes,[])
 def test_symlinked_retention_refuses_before_steam_write(self):
  other=self.root.parent/'private';other.mkdir();(self.root/'retained').symlink_to(other,target_is_directory=True)
  with self.assertRaises(RuntimeError):self.remove()
  self.assertEqual(self.writes,[]);self.assertFalse(list(other.iterdir()))
 def test_prefix_outside_scope_refuses_before_steam_write(self):
  self.state['prefix']=str(self.root.parent/'other');setup.atomic(self.root/'installation.json',self.state)
  with self.assertRaises(RuntimeError):self.remove()
  self.assertEqual(self.writes,[])
 def test_stale_compare_and_set_refuses(self):
  with self.assertRaises(RuntimeError):setup.set_options_checked('stale','replacement',self.get,self.put)
  self.assertEqual(self.writes,[])
 def test_active_owned_swap_blocks_uninstall_before_any_steam_write(self):
  module=setup.swap_module()
  with patch.object(module,'cleanup',side_effect=module.AuthenticationRequired('sudo required')):
   with self.assertRaises(module.AuthenticationRequired):self.remove()
  self.assertEqual(self.writes,[]);self.assertTrue(self.release.exists());self.assertTrue(self.prefix.exists())
 def test_foreign_steam_setting_does_not_remove_swap(self):
  self.current='external choice'
  with patch.object(setup.swap_module(),'cleanup') as cleanup:
   with self.assertRaises(RuntimeError):self.remove()
   cleanup.assert_not_called()

class LaunchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.prefix=self.root/'prefix';(self.prefix/'pfx/drive_c/users/steamuser/AppData/Roaming/EasyAntiCheat').mkdir(parents=True)
  self.config={'hashes':{},'standard':str(self.root/'stock fex'),'standard_proton':str(self.root/'stock proton'),'runtime':str(self.root/'own fex'),'proton':str(self.root/'own proton'),'prefix':str(self.prefix)}
  self.args=['steamrt',self.config['standard']+'/fex-compat-tool',self.config['standard_proton']+'/proton','waitforexitandrun','VRChat.exe']
 def test_normal_steam_boundaries_and_arguments_preserved(self):
  env={'SteamAppId':'438100','STEAM_COMPAT_TOOL_PATHS':self.config['standard']+':'+self.config['standard_proton']+':other'}
  result=launch.command(self.args,env,self.root,self.config)
  self.assertEqual(result,[self.args[0],self.config['runtime']+'/fex-compat-tool',self.config['proton']+'/proton',*self.args[3:]])
  self.assertEqual(env['STEAM_COMPAT_DATA_PATH'],str(self.prefix));self.assertIn('other',env['STEAM_COMPAT_TOOL_PATHS'])
 def test_other_app_is_not_redirected(self):
  with self.assertRaises(RuntimeError):launch.command(self.args,{'SteamAppId':'1'},self.root,self.config)
 def test_duplicate_steam_boundary_is_not_nested(self):
  with self.assertRaises(RuntimeError):launch.command(self.args+[self.args[1]],{'SteamAppId':'438100'},self.root,self.config)
 def test_changed_component_is_rejected(self):
  f=self.root/'part';f.write_text('before');self.config['hashes']['part']=setup.sha(f);f.write_text('changed')
  with self.assertRaises(RuntimeError):launch.command(self.args,{'SteamAppId':'438100'},self.root,self.config)
 def test_hash_path_escape_is_rejected(self):
  self.config['hashes']['../outside']='0'*64
  with self.assertRaises(RuntimeError):launch.command(self.args,{'SteamAppId':'438100'},self.root,self.config)

class BootstrapTests(unittest.TestCase):
 def execute(self,data,digest=None,action='install'):
  temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);home=Path(temporary.name)
  expression=bootstrap(digest or hash_bytes(data));code=base64.b64decode(expression.split('"')[3]).decode()
  output=io.TextIOWrapper(io.BytesIO(),encoding='utf-8');stdin=io.TextIOWrapper(io.BytesIO(data),encoding='utf-8')
  with patch('pathlib.Path.home',return_value=home),patch.object(sys,'stdin',stdin),patch.object(sys,'stdout',output),patch.object(sys,'argv',['bootstrap',action]),patch('subprocess.call',return_value=0) as call:
   with self.assertRaises(SystemExit) as result:exec(compile(code,'bootstrap','exec'),{})
   self.assertEqual(result.exception.code,0)
  return home,call
 def test_valid_transfer_stages_then_executes_only_expected_action(self):
  data=zip_bytes({'frame_setup.py':b'# fixture only\n','LICENSE.md':b'terms'})
  home,call=self.execute(data);args=call.call_args.args[0];self.assertEqual(args[-2:],['install','--accept-alpha-license'])
  self.assertTrue(Path(args[2]).is_relative_to(home));self.assertEqual(Path(args[2]).read_text(),'# fixture only\n')
  self.assertNotIn('--uninstall-android-first',args)
 def test_only_explicit_replace_action_passes_android_removal_consent(self):
  data=zip_bytes({'frame_setup.py':b'# fixture only\n'})
  home,call=self.execute(data,action='install-replace-android')
  self.assertEqual(call.call_args.args[0][-3:],['install','--accept-alpha-license','--uninstall-android-first'])
 def test_package_uninstall_never_passes_android_removal_consent(self):
  home,call=self.execute(zip_bytes({'frame_setup.py':b'# fixture only\n'}),action='uninstall')
  args=call.call_args.args[0];self.assertEqual(args[-1],'uninstall')
  self.assertNotIn('--uninstall-android-first',args)
 def test_transfer_corruption_is_rejected_before_remote_execute(self):
  with self.assertRaises(AssertionError):self.execute(zip_bytes({'frame_setup.py':b'x'}),'0'*64)
 def test_traversal_cannot_write_outside_staging(self):
  with self.assertRaises(AssertionError):self.execute(zip_bytes({'../escape':b'no'}))
 def test_windows_style_path_is_rejected(self):
  with self.assertRaises(AssertionError):self.execute(zip_bytes({'C:\\escape':b'no'}))
 def test_wrong_action_is_rejected(self):
  with self.assertRaises(AssertionError):self.execute(zip_bytes({'frame_setup.py':b'x'}),action='arbitrary')
 def test_symlink_member_is_rejected(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:
   info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,'outside')
  with self.assertRaises(AssertionError):self.execute(stream.getvalue())


class ActivationTests(unittest.TestCase):
 def test_failed_steam_write_is_restored_and_journal_survives(self):
  from contextlib import ExitStack
  with tempfile.TemporaryDirectory() as temporary,ExitStack() as stack:
   home=Path(temporary);root=home/'easy';kit=home/'kit';kit.mkdir();(kit/'LICENSE.md').write_text('terms')
   for name in ['launch.py','session.py','swap_setup.py']:(kit/name).write_text('# own source')
   std=home/'standard';std.mkdir();old=home/'stock-proton';old.mkdir()
   current=['original options'];writes=[]
   def put(value):
    writes.append(value);current[0]=value
    if len(writes)==1:raise RuntimeError('simulated disconnect after Steam accepted write')
   def runtime(release,p):
    path=release/'runtime';(path/'usr/bin').mkdir(parents=True)
    for n in ['emulator.json','ConfigTemplate.json','FEXCompatTool',*['usr/bin/'+n for n in setup.RUNTIME]]:(path/n).write_text('own code')
    return path
   def prefix(release,runtime,p):path=root/'p';path.mkdir();return path
   def proton(release,p):
    d=release/'proton/files/lib/wine/dxvk/x86_64-windows';d.mkdir(parents=True)
    for n in setup.DFR_HASHES:(d/n).write_text('own code')
    mf=release/'proton/files/lib/wine/x86_64-windows';mf.mkdir();(mf/'mf.dll').write_text('own MF code')
    v=release/'video';v.mkdir();(v/'libgstvideo4linux2.so').write_text('own plugin')
    return release/'proton'
   def bridge(release):
    for n in ['gaze-bridge','openvr-stats']:(release/n).write_text('own code')
   stack.enter_context(patch.object(setup.platform,'system',return_value='Linux'));stack.enter_context(patch.object(setup.platform,'machine',return_value='aarch64'));stack.enter_context(patch.object(setup.os,'geteuid',return_value=1000))
   replacements={'KIT':kit,'verify_kit':lambda:{},'prepare_dependencies':lambda *a:None,'prepare_existing_game':lambda *a:None,'preflight':lambda:{'ready':True},'paths':lambda:{'home':home,'base':root,'standard':std,'proton':old},
       'options_get':lambda:current[0],'options_set':put,'prepare_runtime':runtime,'prepare_prefix':prefix,'prepare_proton':proton,'prepare_bridge':bridge,
       'rebase_prefix_links':lambda *a:None,'probe_vulkan':lambda *a:None,'run':lambda *a,**kw:None,'game_active':lambda:False}
   for n,v in replacements.items():stack.enter_context(patch.object(setup,n,v))
   with self.assertRaisesRegex(RuntimeError,'simulated disconnect'):setup.install(True)
   self.assertEqual(current[0],'original options');self.assertEqual(len(writes),2)
   self.assertEqual(json.loads((root/'installation.json').read_text())['phase'],'failed')
   self.assertTrue((root/'p').is_dir());self.assertFalse(list(old.iterdir()))

class PrefixTests(unittest.TestCase):
 def test_dfr_prefix_selection_skips_older_valid_runtime_and_checks_dlls(self):
  with tempfile.TemporaryDirectory() as temporary:
   home=Path(temporary);base=home/'.local/share/frame-pcvr';proton=base/'proton/dfr';dlls=proton/'files/lib/wine/dxvk/x86_64-windows';dlls.mkdir(parents=True)
   expected={}
   for n in setup.DFR_HASHES:(dlls/n).write_text('accepted '+n);expected[n]=setup.sha(dlls/n)
   for name in ['a-old','z-dfr']:
    candidate=base/'candidates'/name;(candidate/'usr/bin').mkdir(parents=True)
    for n in setup.RUNTIME:(candidate/'usr/bin'/n).write_text('runtime '+n)
    record={'state':'smoke-passed','commit':setup.FEX_COMMIT,'standard_unchanged':True,'runtime_hashes':{n:setup.sha(candidate/'usr/bin'/n) for n in setup.RUNTIME},'standard_hashes':{}}
    if name=='z-dfr':record['private_proton']={'path':str(proton),'private_hashes':{'files/lib/wine/dxvk/x86_64-windows/'+n:h for n,h in expected.items()}}
    (candidate/'integration.json').write_text(json.dumps(record))
   with patch.object(setup,'DFR_HASHES',expected):
    self.assertEqual(setup.audited_local_candidate(home).name,'a-old')
    self.assertEqual(setup.audited_local_candidate(home,require_dfr=True).name,'z-dfr')
    (dlls/'dxgi.dll').write_text('changed')
    self.assertIsNone(setup.audited_local_candidate(home,require_dfr=True))
 def test_private_proton_links_rebase_but_personal_links_remain(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);prefix=root/'prefix';system=prefix/'pfx/drive_c/windows/system32';system.mkdir(parents=True)
   old=root/'stock';proton=root/'own';relative=Path('files/lib/wine/dxvk/x86_64-windows')
   (old/relative).mkdir(parents=True);(proton/relative).mkdir(parents=True)
   for n in setup.DFR_HASHES:(old/relative/n).write_text('stock');(proton/relative/n).write_text('DFR');(system/n).symlink_to(old/relative/n)
   (old/'builtin').write_text('same');(proton/'builtin').write_text('same');(system/'builtin').symlink_to(old/'builtin')
   mf=Path('files/lib/wine/x86_64-windows/mf.dll')
   for parent,text in [(old,'stock MF'),(proton,'patched MF')]:
    (parent/mf).parent.mkdir(parents=True);(parent/mf).write_text(text)
   (system/'mf.dll').symlink_to(old/mf)
   (system/'personal').symlink_to(root/'personal-settings');(system/'relative').symlink_to('../other')
   with patch.object(setup,'audited_local_candidate',return_value=None):setup.rebase_prefix_links(prefix,proton,{'proton':old,'home':root})
   self.assertEqual((system/'builtin').resolve(),(proton/'builtin').resolve());self.assertEqual((system/'personal').readlink(),root/'personal-settings')
   self.assertEqual((system/'relative').readlink(),Path('../other'))
   self.assertEqual((system/'mf.dll').resolve(),(proton/mf).resolve());self.assertEqual((old/mf).read_text(),'stock MF')
   for n in setup.DFR_HASHES:self.assertFalse((system/n).is_symlink());self.assertEqual((system/n).read_text(),'DFR');self.assertEqual((old/relative/n).read_text(),'stock')


class AffinityTests(unittest.TestCase):
 def test_known_layout_changes_only_eight_threads_and_restores_safely(self):
  from installer import session
  with tempfile.TemporaryDirectory() as directory:
   proc=Path(directory);root=proc/'11/task';root.mkdir(parents=True);masks={}
   for i,name in enumerate(['Job.Worker '+str(n) for n in range(7)]+['audio_client_ma','Main Thread'],100):
    p=root/str(i);p.mkdir();(p/'comm').write_text(name);(p/'stat').write_text(str(i)+' ('+name+') '+' '.join(['0']*19+[str(i+900)]));masks[i]={3,4,5,6,7}
   def put(i,v):masks[i]=set(v)
   with patch.object(session.os,'cpu_count',return_value=8):rows=session.apply_tested_affinity(11,proc,lambda i:masks[i],put)
   self.assertEqual(len(rows),8);self.assertEqual(masks[108],{3,4,5,6,7});self.assertEqual(masks[100],{3,4,5,6});self.assertEqual(masks[107],set(range(8)))
   masks[101]={4,5};session.restore_affinity(11,rows,proc,lambda i:masks[i],put)
   self.assertEqual(masks[100],{3,4,5,6,7});self.assertEqual(masks[101],{4,5});self.assertEqual(masks[107],{3,4,5,6,7})
 def test_unknown_topology_is_not_forced(self):
  from installer import session
  with patch.object(session.os,'cpu_count',return_value=12):self.assertEqual(session.apply_tested_affinity(1),[])
 def test_external_mask_prevents_the_entire_attempt(self):
  from installer import session
  with tempfile.TemporaryDirectory() as directory:
   proc=Path(directory);root=proc/'11/task';root.mkdir(parents=True);masks={};writes=[]
   for i,name in enumerate(['Job.Worker '+str(n) for n in range(7)]+['audio_client_ma'],100):
    p=root/str(i);p.mkdir();(p/'comm').write_text(name);(p/'stat').write_text(str(i)+' ('+name+') '+' '.join(['0']*19+[str(i+900)]));masks[i]={3,4,5,6,7}
   masks[103]={4,5}
   with patch.object(session.os,'cpu_count',return_value=8):
    self.assertEqual(session.apply_tested_affinity(11,proc,lambda i:masks[i],lambda i,v:writes.append((i,v))),[])
   self.assertEqual(writes,[])

if __name__=='__main__':unittest.main()
