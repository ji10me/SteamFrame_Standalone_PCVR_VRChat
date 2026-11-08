from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from framectl import sha256
from private_proton import CHECKED_FILES, DLLS, select_private_proton
from stage_dfr_proton import pe_amd64


class PrivateProton(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        context = patch.object(Path, 'home', return_value=self.home)
        context.start(); self.addCleanup(context.stop)
        self.base = self.home / '.local/share/frame-pcvr'
        self.parent = self.home / '.local/share/Steam/steamapps/common/Proton - Experimental'
        self.private = self.base / 'proton/dfr-test'
        for root in (self.parent, self.private):
            for name in CHECKED_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original ' + name)
        for name in DLLS:
            (self.private / name).write_text('private graphics ' + name)
        self.record = {'format': 'frame-dfr-private-proton-v1', 'path': str(self.private),
                       'parent': str(self.parent),
                       'parent_hashes': {n: sha256(self.parent / n) for n in CHECKED_FILES},
                       'private_hashes': {n: sha256(self.private / n) for n in CHECKED_FILES}}
        self.command = ['/wrapper', '--', str(self.parent / 'proton'), 'waitforexitandrun', 'private-token']

    def test_only_the_proton_boundary_and_tool_visibility_are_changed(self):
        env = {'STEAM_COMPAT_TOOL_PATHS': str(self.parent) + ':/runtime', 'UNCHANGED': 'yes'}
        result = select_private_proton(self.command, env, self.base, self.record)
        expected = list(self.command); expected[2] = str(self.private / 'proton')
        self.assertEqual(result, expected)
        self.assertEqual(self.command[2], str(self.parent / 'proton'))
        self.assertEqual(env, {'STEAM_COMPAT_TOOL_PATHS': str(self.private) + ':/runtime', 'UNCHANGED': 'yes'})
        self.assertEqual((self.parent / DLLS[0]).read_text(), 'original ' + DLLS[0])

    def test_missing_or_duplicate_boundary_refuses_before_environment_changes(self):
        for args in (['/other/proton'], self.command + [str(self.parent / 'proton')]):
            env = {}
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                select_private_proton(args, env, self.base, self.record)
            self.assertEqual(env, {})

    def test_parent_steam_update_refuses(self):
        (self.parent / 'version').write_text('updated')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            select_private_proton(self.command, {}, self.base, self.record)

    def test_tampered_private_dll_refuses(self):
        (self.private / DLLS[0]).write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            select_private_proton(self.command, {}, self.base, self.record)

    def test_private_root_cannot_escape_even_through_a_link(self):
        link = self.base / 'proton/escape'
        link.symlink_to(self.parent, target_is_directory=True)
        self.record['path'] = str(link)
        with self.assertRaises(ValueError):
            select_private_proton(self.command, {}, self.base, self.record)

    def test_empty_checks_and_modified_proton_script_are_rejected(self):
        missing = json.loads(json.dumps(self.record)); missing['parent_hashes'] = {}
        with self.assertRaisesRegex(RuntimeError, 'complete'):
            select_private_proton(self.command, {}, self.base, missing)
        (self.private / 'proton').write_text('modified script')
        self.record['private_hashes']['proton'] = sha256(self.private / 'proton')
        with self.assertRaisesRegex(RuntimeError, 'preserve'):
            select_private_proton(self.command, {}, self.base, self.record)

    def test_payload_must_be_a_complete_amd64_pe32_plus_dll(self):
        path = self.home / 'payload.dll'
        data = bytearray(154)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 60, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<H', data, 132, 0x8664)
        struct.pack_into('<H', data, 150, 0x2000)
        struct.pack_into('<H', data, 152, 0x20b)
        path.write_bytes(data)
        self.assertTrue(pe_amd64(path))
        for offset, value in ((132, 0xaa64), (150, 0), (152, 0x10b)):
            broken = bytearray(data)
            struct.pack_into('<H', broken, offset, value)
            path.write_bytes(broken)
            self.assertFalse(pe_amd64(path))
        path.write_bytes(data[:150])
        self.assertFalse(pe_amd64(path))


if __name__ == '__main__':
    unittest.main()
