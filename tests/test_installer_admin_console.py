import os
from pathlib import Path
import re
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
from installer.build_windows import admin_bootstrap


class AdminConsoleTests(unittest.TestCase):
    def test_privileged_source_comes_from_package_instead_of_remote_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);root=home/'.local/share/frame-pcvr-easy';root.mkdir(parents=True)
            (root/'swap_admin.py').write_text('raise RuntimeError("edited remote code must never run")')
            source=b'from pathlib import Path\nPath(__file__).with_name("marker").write_text("package source")\n'
            with patch('pwd.getpwuid',return_value=types.SimpleNamespace(pw_dir=str(home))),patch.dict(os.environ,{'SUDO_UID':'1000'}):
                exec(admin_bootstrap(source),{})
            self.assertEqual((root/'marker').read_text(),'package source')

    def test_console_keeps_root_action_exit_status_after_prompt(self):
        cpp=(Path(__file__).resolve().parents[1]/'installer/windows/setup.cpp').read_text()
        tail=re.search(r'LR"\((; status=.+?)\)";',cpp).group(1)
        for code in [0,1,2,43]:
            result=subprocess.run(['/bin/sh','-c',f'(exit {code})'+tail],input='\n',capture_output=True,text=True)
            self.assertEqual(result.returncode,code,result.stderr)

    def test_oversized_admin_source_is_rejected(self):
        with self.assertRaises(ValueError):admin_bootstrap(b'x'*30000)

if __name__=='__main__':unittest.main()
