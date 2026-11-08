import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import guard_frame_thermals as guard
import install_frame_thermal_service as installer
from frame_thermal_socket import SocketGuard
from measure_frame_thermals import discover_launch


class Requests(unittest.TestCase):
    def test_wrong_process_owner_is_rejected_before_environment_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '42').mkdir()
            with patch.object(guard, 'Path', return_value=root), self.assertRaises(guard.StopGuard):
                guard.identity(42, os.getuid() + 1)

    def test_only_two_bounded_operations_are_accepted(self):
        check = {'protocol': 1, 'operation': 'check'}
        measure = {'protocol': 1, 'operation': 'measure', 'pid': 42, 'seconds': 60}
        self.assertEqual(guard.decode_request(json.dumps(check)), check)
        self.assertEqual(guard.decode_request(json.dumps(measure)), measure)
        for change in ({'seconds': 121}, {'seconds': 9}, {'seconds': True}, {'pid': True}, {'pid': -1},
                       {'protocol': True}, {'protocol': 2}, {'operation': 'run'}, {'path': '/etc/passwd'},
                       {'gpu_max': 9999}, {'command': 'anything'}):
            value = measure | change
            with self.subTest(change=change), self.assertRaises(guard.StopGuard):
                guard.decode_request(json.dumps(value))

    def test_ambiguous_and_oversized_requests_are_rejected(self):
        for value in ('[]', '{"protocol":1,"operation":"check","operation":"measure"}', 'x' * 1025):
            with self.subTest(value=value[:30]), self.assertRaises((ValueError, guard.StopGuard)):
                guard.decode_request(value)

    def test_peer_identity_is_checked_before_reading_a_request(self):
        class Peer:
            def __init__(self, uid):
                self.uid, self.reads = uid, 0
            def getsockopt(self, *args):
                return struct.pack('3i', 42, self.uid, 1000)
            def recv(self, size):
                self.reads += 1
                raise AssertionError('unauthorized request was read')
        with patch.object(socket, 'SO_PEERCRED', 17, create=True):
            for uid in (0, 1001):
                peer = Peer(uid)
                with self.assertRaises(guard.StopGuard):
                    guard.socket_request(peer, 1000)
                self.assertEqual(peer.reads, 0)

    def test_cancellation_half_closes_without_losing_restore_reply(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        connection = SocketGuard(left, 42, 60)
        self.addCleanup(connection.close)
        with right.makefile('r') as stream:
            self.assertEqual(json.loads(stream.readline()), {'protocol': 1, 'operation': 'measure', 'pid': 42, 'seconds': 60})
        connection.stdin.close()
        self.assertEqual(right.recv(1), b'')
        right.sendall(b'{"phase":"done","reason":"caller_stop"}\n')
        self.assertEqual(json.loads(connection.stdout.readline())['phase'], 'done')
        connection.finish(True)
        self.assertEqual(connection.poll(), 0)


class Setup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.units = self.root / 'units'
        self.units.mkdir()
        self.state = self.root / 'state'
        self.runtime = self.root / 'runtime'
        for name, value in (('STATE', self.state), ('UNITS', self.units), ('RUNTIME', self.runtime)):
            context = patch.object(installer, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.commands = []
        def command(args):
            self.commands.append(args)
            return subprocess.CompletedProcess(args, 0, '')
        context = patch.object(installer, 'run', side_effect=command)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ''))
        self.raw_command = context.start()
        self.addCleanup(context.stop)

    def test_service_is_user_restricted_and_has_only_five_writable_files(self):
        units = installer.unit_contents('steamos')
        service = units[installer.SERVICE_NAME]
        endpoint = units[installer.SOCKET_NAME]
        self.assertIn('SocketUser=steamos\nSocketMode=0600', endpoint)
        self.assertIn('Accept=yes\nMaxConnections=1', endpoint)
        line = next(line for line in service.splitlines() if line.startswith('ReadWritePaths='))
        paths = line.partition('=')[2].split()
        self.assertEqual(len(paths), 5)
        self.assertTrue(all(path.endswith('/scaling_max_freq') or path == installer.GPU_REAL for path in paths))
        self.assertIn('ProtectKernelTunables=yes', service)
        self.assertIn('RuntimeMaxSec=160', service)
        self.assertIn('RestrictAddressFamilies=AF_UNIX', service)
        self.assertIn(f'-I {self.state}/guard_frame_thermals.py --socket', service)

    def test_success_writes_private_code_without_touching_frequency_files(self):
        units = installer.unit_contents('steamos')
        with patch.object(installer, 'user_probe') as probe:
            installer.install(1000, b'reviewed code', units)
        self.assertEqual((self.state / 'guard_frame_thermals.py').read_bytes(), b'reviewed code')
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / 'guard_frame_thermals.py').stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(p.name for p in self.units.iterdir()), set(units))
        probe.assert_called_once_with(1000, hashlib.sha256(b'reviewed code').hexdigest())
        self.assertIn(['systemctl', 'enable', '--now', installer.SOCKET_NAME], self.commands)

    def test_failed_probe_rolls_back_new_files_and_disables_socket(self):
        with patch.object(installer, 'user_probe', side_effect=RuntimeError('probe failed')):
            with self.assertRaises(RuntimeError):
                installer.install(1000, b'reviewed code', installer.unit_contents('steamos'))
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertTrue(any(call.args[0] == ['systemctl', 'disable', '--now', installer.SOCKET_NAME]
                            for call in self.raw_command.call_args_list))

    def test_existing_unrelated_unit_is_preserved(self):
        target = self.units / installer.SOCKET_NAME
        target.write_text('unrelated unit')
        with self.assertRaises(RuntimeError):
            installer.install(1000, b'reviewed code', installer.unit_contents('steamos'))
        self.assertEqual(target.read_text(), 'unrelated unit')
        self.assertFalse(self.state.exists())

    def prepare_update(self):
        self.state.mkdir()
        old_payload = b'registered old helper'
        old_record = b'{"marker":"frame-pcvr-thermal-service-v1","owner_uid":1000,"custom_metadata":42}'
        (self.state / 'guard_frame_thermals.py').write_bytes(old_payload)
        (self.state / 'installation.json').write_bytes(old_record)
        context = patch.object(installer, 'current_installation', return_value=(old_payload, old_record))
        context.start()
        self.addCleanup(context.stop)
        return old_payload, old_record

    def test_update_keeps_unit_files_and_metadata_and_probes_new_code(self):
        self.prepare_update()
        units = installer.unit_contents('steamos')
        for name, text in units.items():
            (self.units / name).write_text(text)
        with patch.object(installer, 'user_probe') as probe:
            installer.upgrade(1000, b'reviewed replacement', units)
        self.assertEqual((self.state / 'guard_frame_thermals.py').read_bytes(), b'reviewed replacement')
        record = json.loads((self.state / 'installation.json').read_text())
        self.assertEqual(record['custom_metadata'], 42)
        for name, text in units.items():
            self.assertEqual((self.units / name).read_text(), text)
        probe.assert_called_once_with(1000, hashlib.sha256(b'reviewed replacement').hexdigest())

    def test_failed_update_restores_previous_code_and_record_bytes(self):
        payload, record = self.prepare_update()
        with patch.object(installer, 'user_probe', side_effect=RuntimeError('new code probe failed')):
            with self.assertRaises(RuntimeError):
                installer.upgrade(1000, b'reviewed replacement', installer.unit_contents('steamos'))
        self.assertEqual((self.state / 'guard_frame_thermals.py').read_bytes(), payload)
        self.assertEqual((self.state / 'installation.json').read_bytes(), record)

    def test_active_measurement_prevents_update_before_socket_stop(self):
        payload, record = self.prepare_update()
        with patch.object(installer, 'active_sessions', return_value=True), self.assertRaises(RuntimeError):
            installer.upgrade(1000, b'reviewed replacement', installer.unit_contents('steamos'))
        self.assertEqual(self.commands, [])
        self.assertEqual((self.state / 'guard_frame_thermals.py').read_bytes(), payload)

    def test_pinned_helper_matches_current_reviewed_source(self):
        payload = (Path(__file__).resolve().parents[1] / 'tools/guard_frame_thermals.py').read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), installer.HELPER_SHA256)


class Launch(unittest.TestCase):
    def test_only_current_private_vrchat_is_selected_and_ambiguity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, proc = root / 'base', root / 'proc'
            prefix = base / 'c/private-one'
            prefix.mkdir(parents=True)
            prefix = prefix.resolve()
            def process(pid, appid, private):
                path = proc / str(pid)
                path.mkdir(parents=True)
                (path / 'comm').write_text('VRChat.exe\n')
                (path / 'stat').write_text(f'{pid} (VRChat.exe) ' + ' '.join(['S'] + ['0'] * 18 + ['1234']))
                selected = prefix if private else root / 'other-project'
                (path / 'environ').write_bytes(b'SteamAppId=' + appid + b'\0STEAM_COMPAT_DATA_PATH=' + os.fsencode(selected) + b'\0')
            process(1, b'123', True)
            process(2, b'438100', False)
            process(3, b'438100', True)
            self.assertEqual(discover_launch(base, proc), (3, prefix))
            process(4, b'438100', True)
            with self.assertRaises(RuntimeError):
                discover_launch(base, proc)


if __name__ == '__main__':
    unittest.main()
