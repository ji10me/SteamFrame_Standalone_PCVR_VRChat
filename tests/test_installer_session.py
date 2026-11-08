import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer import session


class SessionLifetimeTests(unittest.TestCase):
    def simulate(self, identities, affinity=None, limit=15, ready=True):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'releases/a-test';root.mkdir(parents=True)
            (root/'runtime.json').write_text(json.dumps({'prefix':str(Path(directory)/'prefix')}))
            now=[0.0];created=[];output=io.StringIO()
            class Child:
                terminated_at=None
                def poll(self):return None if self.terminated_at is None else 0
                def terminate(self):self.terminated_at=now[0]
                def wait(self,timeout=None):return 0
            def sleep(seconds):
                now[0]+=seconds
                if now[0]>limit:raise AssertionError('Supervisor did not stop at game exit')
            def spawn(command,**kwargs):
                child=Child();created.append((now[0],command,child));return child
            stats={'scene_pid':11,'scene_loading':False,'grid_alpha':0,'fade_alpha':0,'hmd_pose_valid':True}
            if not ready:stats.update(scene_loading=True,grid_alpha=1)
            with contextlib.ExitStack() as stack:
                replacements={'__file__':str(root/'session.py'),'stopping':False,
                              'find_game':lambda prefix:identities(now[0]),
                              'game_identity':lambda pid,prefix:next((v for v in identities(now[0]) if v[0]==pid),None),
                              'apply_tested_affinity':lambda pid:affinity(now[0]) if affinity else [],
                              'restore_affinity':lambda *args:None}
                for name,value in replacements.items():stack.enter_context(patch.object(session,name,value))
                stack.enter_context(patch.object(session.time,'monotonic',side_effect=lambda:100+now[0]))
                stack.enter_context(patch.object(session.time,'sleep',side_effect=sleep))
                stack.enter_context(patch.object(session.subprocess,'check_output',return_value=json.dumps(stats)))
                stack.enter_context(patch.object(session.subprocess,'Popen',side_effect=spawn))
                stack.enter_context(patch.object(session.signal,'signal'))
                stack.enter_context(contextlib.redirect_stdout(output))
                session.main()
            return created,output.getvalue()

    def test_transient_second_game_does_not_stop_active_gaze_publisher(self):
        target=(11,'100');other=(12,'101')
        created,output=self.simulate(lambda t:[other] if t>=10 else [target,other] if 6<=t<9 else [target])
        self.assertEqual(len(created),1)
        self.assertEqual(created[0][1][1],'11')
        self.assertEqual(created[0][2].terminated_at,10)
        self.assertIn('tracked_game_ended_or_replaced',output)

    def test_second_process_during_settle_does_not_prevent_first_publish(self):
        target=(11,'100');other=(12,'101')
        created,_=self.simulate(lambda t:[] if t>=10 else [target,other] if t>=3 else [target])
        self.assertEqual(len(created),1)
        self.assertEqual(created[0][0],3)
        self.assertEqual(created[0][2].terminated_at,10)

    def test_reused_pid_with_new_start_time_is_not_adopted(self):
        created,_=self.simulate(lambda t:[(11,'changed')] if t>=7 else [(11,'100')])
        self.assertEqual(len(created),1)
        self.assertEqual(created[0][2].terminated_at,7)

    def test_ambiguous_startup_waits_instead_of_selecting_an_unrelated_process(self):
        target=(11,'100');other=(12,'101')
        created,_=self.simulate(lambda t:[] if t>=10 else [target,other] if t<2 else [target])
        self.assertEqual(len(created),1)
        self.assertEqual(created[0][0],5)
        self.assertEqual(created[0][1][1],'11')

    def test_late_thread_layout_is_retried_while_gaze_remains_alive(self):
        calls=[]
        def affinity(t):
            calls.append(t)
            return [{'owned':'layout'}] if t>=7 else []
        created,output=self.simulate(lambda t:[] if t>=12 else [(11,'100')],affinity)
        self.assertEqual(calls,[0,2,4,6,8])
        self.assertEqual(len(created),1)
        self.assertEqual(created[0][2].terminated_at,12)
        self.assertIn('"tested_thread_affinity_applied": true',output)

    def test_unavailable_layout_retries_are_bounded(self):
        calls=[]
        created,output=self.simulate(lambda t:[] if t>=190 else [(11,'100')],
                                    lambda t:calls.append(t) or [],limit=195)
        self.assertEqual(calls,list(range(0,180,2)))
        self.assertEqual(len(created),1)
        self.assertIn('"affinity_retry_finished": true',output)

    def test_game_identity_change_stops_pending_retries(self):
        calls=[]
        created,_=self.simulate(lambda t:[(11,'new')] if t>=6 else [(11,'100')],
                                lambda t:calls.append(t) or [])
        self.assertEqual(calls,[0,2,4])
        self.assertEqual(created[0][2].terminated_at,6)

    def test_audio_layout_applies_even_when_controllers_and_gaze_are_waiting(self):
        calls=[]
        created,output=self.simulate(lambda t:[] if t>=10 else [(11,'100')],
                                    lambda t:calls.append(t) or [{'owned':'layout'}],ready=False)
        self.assertEqual(calls,[0])
        self.assertEqual(created,[])
        self.assertIn('"tested_thread_affinity_applied": true',output)


class GameIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.proc=Path(self.tmp.name);self.target=self.proc/'11';self.target.mkdir()
        self.prefix=self.proc/'private-prefix'
        (self.target/'comm').write_text('VRChat.exe\n')
        (self.target/'environ').write_bytes(b'SteamAppId=438100\0STEAM_COMPAT_DATA_PATH='+os.fsencode(self.prefix)+b'\0')
        fields=['0']*45;fields[0]='R';fields[19]='100'
        (self.target/'stat').write_text('11 (VRChat.exe) '+' '.join(fields))

    def test_expected_owner_title_prefix_and_start_are_returned(self):
        self.assertEqual(session.game_identity(11,self.prefix,self.proc),(11,'100'))

    def test_different_title_is_rejected(self):
        (self.target/'environ').write_bytes(b'SteamAppId=570\0STEAM_COMPAT_DATA_PATH='+os.fsencode(self.prefix)+b'\0')
        self.assertIsNone(session.game_identity(11,self.prefix,self.proc))

    def test_different_prefix_is_rejected(self):
        self.assertIsNone(session.game_identity(11,self.proc/'other',self.proc))

    def test_foreign_owner_is_rejected(self):
        owner=os.getuid()
        with patch.object(session.os,'getuid',return_value=owner+1):
            self.assertIsNone(session.game_identity(11,self.prefix,self.proc))

    def test_missing_process_and_invalid_pid_are_rejected(self):
        for pid in [12,0,-1,'../11',True]:
            self.assertIsNone(session.game_identity(pid,self.prefix,self.proc))

    def test_malformed_start_identity_is_rejected(self):
        (self.target/'stat').write_text('11 (VRChat.exe) R')
        self.assertIsNone(session.game_identity(11,self.prefix,self.proc))


if __name__=='__main__':unittest.main()
