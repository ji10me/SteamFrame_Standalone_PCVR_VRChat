from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from installer import frame_setup as setup

GIB=1024**3


class Child:
    pid=12345
    returncode=None
    def __init__(self,polls=4):self.polls=polls
    def poll(self):
        self.polls-=1
        if self.polls<=0:self.returncode=0
        return self.returncode
    def wait(self,timeout):self.returncode=-15;return self.returncode


class BuildGuardTests(unittest.TestCase):
    def test_each_limit_has_a_distinct_reason_and_numeric_context(self):
        for args,reason in [((95,4*GIB,10*GIB,1,10),'温度上限'),
                            ((60,GIB-1,10*GIB,1,10),'空きRAM不足'),
                            ((60,4*GIB,100,1,10),'空き容量不足'),
                            ((60,4*GIB,10*GIB,11,10),'作業時間の上限')]:
            message=setup.resource_error(*args,'build-fex')
            self.assertIn(reason,message)
            for field in ['温度=','空きRAM=','空き容量=','作業時間=']:self.assertIn(field,message)
        self.assertIsNone(setup.resource_error(94.9,GIB,512*1024**2,10,10,'build-fex'))

    def run_build(self,samples,*,polls=4,timeout=7200,sleep_step=None):
        child=Child(polls);now=[0];signals=[];messages=[]
        def sleep(seconds):now[0]+=sleep_step if sleep_step is not None else seconds
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(setup,'sensors',side_effect=samples), \
             patch.object(setup.shutil,'disk_usage',return_value=type('Disk',(),{'free':10*GIB})()), \
             patch.object(setup.subprocess,'Popen',return_value=child), \
             patch.object(setup.os,'killpg',side_effect=lambda p,s:signals.append((p,s))), \
             patch.object(setup.time,'monotonic',side_effect=lambda:now[0]), \
             patch.object(setup.time,'sleep',side_effect=sleep), \
             patch.object(setup,'emit',side_effect=lambda *a:messages.append(a)):
            try:
                setup.run(['synthetic-build'],Path(tmp),'build-fex',timeout=timeout,cool_build=True)
            except Exception as error:
                return error,signals,messages
        return None,signals,messages

    def test_only_own_group_pauses_at_92_and_resumes_at_85(self):
        error,signals,messages=self.run_build([(78,4*GIB),(93,4*GIB),(89,4*GIB),(85,4*GIB)])
        self.assertIsNone(error)
        self.assertEqual(signals,[(Child.pid,signal.SIGSTOP),(Child.pid,signal.SIGCONT)])
        self.assertTrue(any(stage=='cooling' for stage,msg in messages))
        self.assertTrue(any('再開' in msg for stage,msg in messages))

    def test_95_degree_hard_stop_is_preserved(self):
        error,signals,_=self.run_build([(78,4*GIB),(95,4*GIB)])
        self.assertIn('温度上限95℃',str(error))
        self.assertEqual(signals,[(Child.pid,signal.SIGTERM)])

    def test_low_memory_while_paused_terminates_and_unstops_own_group(self):
        error,signals,_=self.run_build([(78,4*GIB),(93,4*GIB),(90,GIB-1)])
        self.assertIn('空きRAM不足',str(error))
        self.assertEqual(signals,[(Child.pid,signal.SIGSTOP),(Child.pid,signal.SIGTERM),(Child.pid,signal.SIGCONT)])

    def test_cooling_is_not_charged_to_active_build_timeout(self):
        error,signals,_=self.run_build([(78,4*GIB),(93,4*GIB),(85,4*GIB)],polls=3,timeout=1,sleep_step=5)
        self.assertIsNone(error)
        self.assertEqual(signals,[(Child.pid,signal.SIGSTOP),(Child.pid,signal.SIGCONT)])

    def test_cooling_has_a_finite_total_budget(self):
        error,signals,_=self.run_build([(78,4*GIB),(93,4*GIB),(90,4*GIB)],sleep_step=901)
        self.assertIn('温度調整が合計15分',str(error))
        self.assertEqual(signals,[(Child.pid,signal.SIGSTOP),(Child.pid,signal.SIGTERM),(Child.pid,signal.SIGCONT)])

    def test_hot_start_waits_before_creating_the_child(self):
        error,signals,messages=self.run_build([(93,4*GIB),(85,4*GIB),(74,4*GIB)],polls=2)
        self.assertIsNone(error)
        self.assertEqual(signals,[])
        self.assertTrue(any('作成前' in msg for stage,msg in messages))

    def test_active_timeout_still_stops_build(self):
        error,signals,_=self.run_build([(78,4*GIB),(78,4*GIB),(78,4*GIB)],sleep_step=2,timeout=1)
        self.assertIn('作業時間の上限',str(error))
        self.assertEqual(signals,[(Child.pid,signal.SIGTERM)])
