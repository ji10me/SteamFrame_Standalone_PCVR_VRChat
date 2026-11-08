#!/usr/bin/env python3
"""Client for the fixed, user-restricted thermal measurement socket."""
import json
import os
from pathlib import Path
import socket
import stat
import struct

SOCKET_PATH = Path('/run/frame-pcvr-thermal/control.sock')
PROTOCOL = 1


def connect():
    parent = SOCKET_PATH.parent.lstat()
    endpoint = SOCKET_PATH.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022
            or not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != os.getuid() or endpoint.st_mode & 0o077):
        raise RuntimeError('The thermal socket is not managed with the expected ownership')
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(35)
        connection.connect(str(SOCKET_PATH))
        _, owner, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if owner != 0:
            raise RuntimeError('The thermal socket server is not the system manager')
        return connection
    except BaseException:
        connection.close()
        raise


def service_status():
    with connect() as connection:
        connection.settimeout(10)
        connection.sendall(b'{"protocol":1,"operation":"check"}\n')
        with connection.makefile('r') as stream:
            data = stream.readline(4096)
        result = json.loads(data)
        if result.get('phase') != 'available' or result.get('protocol') != PROTOCOL or result.get('writable_caps') is not True:
            raise RuntimeError('The installed thermal service did not pass its read-only check')
        return result


class Cancellation:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass


class SocketGuard:
    """Popen-like stream interface without executing any privileged command."""
    def __init__(self, connection, pid, seconds):
        if type(pid) is not int or pid <= 0 or type(seconds) is not int or not 10 <= seconds <= 120:
            raise ValueError('Invalid bounded VRChat measurement')
        self.connection = connection
        connection.sendall((json.dumps({'protocol': PROTOCOL, 'operation': 'measure', 'pid': pid, 'seconds': seconds}) + '\n').encode())
        self.stdin = Cancellation(connection)
        self.stdout = connection.makefile('r')
        self.returncode = None

    def poll(self):
        return self.returncode

    def finish(self, completed):
        self.returncode = 0 if completed else 1

    def terminate(self):
        self.stdin.close()

    def close(self):
        self.stdin.close()
        self.stdout.close()
        self.connection.close()
