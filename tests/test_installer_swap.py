import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from installer import swap_setup as swap


class SwapOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name).resolve();self.root=self.home/'.local/share/frame-pcvr-easy';self.root.mkdir(parents=True)
        self.folder,self.target,self.receipt=swap.locations(self.root);self.folder.mkdir()
        self.target.write_bytes(b'\0'*8192);self.target.chmod(0o600)
        token=uuid.uuid4();data=bytearray(self.target.read_bytes());data[1036:1052]=token.bytes;data[4086:4096]=b'SWAPSPACE2';self.target.write_bytes(data)
        s=self.target.stat();self.uid=os.getuid() or 1000
        self.value={'format':'frame-swap-8g-v1','bytes':8192,'uid':self.uid,'identity':[s.st_dev,s.st_ino],'uuid':str(token)}
        self.receipt.write_text(json.dumps(self.value))
        self.rows=[dict(path='/dev/zram0',size_kib=1024,used_kib=0,priority=100)]
        self.stack=self.enterContext(__import__('contextlib').ExitStack())
        self.stack.enter_context(patch.object(swap,'SIZE',8192))
        self.stack.enter_context(patch.object(swap.os,'geteuid',return_value=self.uid))
        self.stack.enter_context(patch.object(swap,'available_memory_kib',return_value=4*1024**2))
        self.stack.enter_context(patch.object(swap,'read_swaps',side_effect=lambda:self.rows.copy()))
        self.stack.enter_context(patch.object(swap.os,'sysconf',return_value=4096))

    def as_root(self):
        self.stack.enter_context(patch.object(swap.os,'geteuid',return_value=0))
        self.stack.enter_context(patch.dict(os.environ,{'SUDO_UID':str(self.uid)}))
        self.stack.enter_context(patch.object(swap.pwd,'getpwuid',return_value=types.SimpleNamespace(pw_dir=str(self.home))))
        self.chown=self.stack.enter_context(patch.object(swap.os,'fchown'))

    def active(self):
        self.rows.append(dict(path=str(self.target),size_kib=4,used_kib=0,priority=10))

    def test_enable_only_our_file_and_preserve_zram(self):
        self.as_root();calls=[]
        def run(cmd,**kwargs):
            calls.append(cmd);self.active()
        with patch.object(swap.subprocess,'run',side_effect=run):result=swap.administer(self.root,True)
        self.assertTrue(result['active']);self.assertEqual(calls,[['/usr/bin/swapon','--priority','10','--',str(self.target)]])
        self.assertEqual(self.rows[0]['priority'],100);self.chown.assert_called_once()

    def test_already_active_never_enables_twice(self):
        self.as_root();self.active()
        with patch.object(swap.subprocess,'run') as run:
            self.assertTrue(swap.administer(self.root,True)['active']);run.assert_not_called()

    def test_foreign_swap_is_preserved_and_blocks_second_activation(self):
        self.as_root();self.rows.append(dict(path='/other/swapfile',size_kib=4,used_kib=0,priority=10))
        with patch.object(swap.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):swap.administer(self.root,True)
            run.assert_not_called()

    def test_wrong_sudo_user_is_rejected_before_chown_or_swapon(self):
        self.as_root()
        with patch.dict(os.environ,{'SUDO_UID':str(self.uid+1)}),patch.object(swap.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):swap.administer(self.root,True)
            run.assert_not_called();self.chown.assert_not_called()

    def test_changed_uuid_is_rejected_before_root_operation(self):
        self.as_root();data=bytearray(self.target.read_bytes());data[1036]^=1;self.target.write_bytes(data)
        with patch.object(swap.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):swap.administer(self.root,True)
            run.assert_not_called();self.chown.assert_not_called()

    def test_replaced_file_is_not_deleted_or_activated(self):
        other=self.folder/'replacement';other.write_bytes(self.target.read_bytes());other.chmod(0o600);other.replace(self.target)
        with self.assertRaises(RuntimeError):swap.cleanup(self.root)
        self.assertTrue(self.target.exists())

    def test_symlink_is_rejected_without_touching_destination(self):
        other=self.home/'unrelated';other.write_text('keep');self.target.unlink();self.target.symlink_to(other)
        with self.assertRaises(RuntimeError):swap.record(self.root)
        self.assertEqual(other.read_text(),'keep')

    def test_active_swap_refuses_unprivileged_removal(self):
        self.active()
        with self.assertRaises(swap.AuthenticationRequired):swap.cleanup(self.root)
        self.assertTrue(self.target.exists());self.assertTrue(self.receipt.exists())

    def test_cleanup_removes_only_inactive_owned_file(self):
        unrelated=self.folder/'user-note';unrelated.write_text('keep')
        swap.cleanup(self.root)
        self.assertFalse(self.target.exists());self.assertEqual(unrelated.read_text(),'keep')

    def test_existing_external_swap_is_reused_without_claiming_ownership(self):
        self.target.unlink();self.receipt.unlink();self.folder.rmdir()
        self.rows.append(dict(path='/someone-else/swap',size_kib=8388604,used_kib=42,priority=5))
        result=swap.prepare(self.root)
        self.assertTrue(result['active']);self.assertFalse(result['managed']);self.assertTrue(result['external_reused'])
        swap.cleanup(self.root);self.assertFalse(self.folder.exists());self.assertEqual(len(self.rows),2)

    def test_external_swap_above_zram_priority_is_not_silently_reused(self):
        self.target.unlink();self.receipt.unlink();self.folder.rmdir()
        self.rows[0]['priority']=5
        self.rows.append(dict(path='/other/swap',size_kib=8388604,used_kib=0,priority=10))
        with self.assertRaises(RuntimeError):swap.prepare(self.root)
        self.assertFalse(self.folder.exists())

    def test_low_priority_zram_is_preserved_instead_of_overridden(self):
        self.as_root();self.rows[0]['priority']=5
        with patch.object(swap.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):swap.administer(self.root,True)
            run.assert_not_called();self.chown.assert_not_called()

    def test_disable_checks_ram_before_any_swapoff(self):
        self.as_root();self.active();self.rows[-1]['used_kib']=2**30
        with patch.object(swap.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):swap.administer(self.root,False)
            run.assert_not_called();self.assertTrue(self.target.exists())

    def test_disable_targets_our_file_only(self):
        self.as_root();self.active();calls=[]
        def run(cmd,**kw):calls.append(cmd);self.rows.pop()
        with patch.object(swap.subprocess,'run',side_effect=run):result=swap.administer(self.root,False)
        self.assertFalse(result['active']);self.assertEqual(calls,[['/usr/bin/swapoff','--',str(self.target)]])
        self.assertEqual(self.rows[0]['path'],'/dev/zram0')

if __name__=='__main__':unittest.main()
