#!/usr/bin/env python3
"""Launch VRChat only after tracked controllers and calibrated tracking settle."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

from steam_cdp import evaluate


def readiness_reason(stats):
    if not {'tracked_controllers', 'chaperone_calibration_state'} <= stats.keys():
        return 'tracking_probe_update_required'
    if stats.get('hmd_connected') is not True:
        return 'headset_disconnected'
    if stats.get('hmd_pose_valid') is not True or stats.get('hmd_tracking_result') != 200:
        return 'headset_tracking_not_ready'
    if stats.get('chaperone_calibration_state') != 1:
        return 'playspace_not_calibrated'
    count = stats.get('tracked_controllers')
    if type(count) is not int or count < 1:
        return 'controller_tracking_not_ready'
    # Covered proximity sensors and activity flags alone do not prove that
    # tracking resumed. No pose coordinates or device identifiers are collected.
    return 'ready'


def require_stopped(proc_root=Path('/proc')):
    for proc in Path(proc_root).glob('[0-9]*'):
        try:
            env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
        except OSError:
            continue
        if env.get(b'SteamAppId') == b'438100' or env.get(b'SteamGameId') == b'438100':
            raise RuntimeError('VRChat is already active; it will not be stopped or launched again')


def read_stats(tool):
    return json.loads(subprocess.check_output([str(tool)], text=True, stderr=subprocess.DEVNULL, timeout=5))


def wait_for_ready(read, timeout=120, *, guard=lambda: None, sleep=time.sleep, clock=time.monotonic,
                   progress=lambda reason: None):
    deadline = clock() + timeout
    stable_since = None
    previous = None
    while clock() < deadline:
        guard()
        reason = readiness_reason(read())
        now = clock()
        if reason != previous:
            progress(reason)
            previous = reason
        if reason == 'ready':
            if stable_since is None:
                stable_since = now
            if now - stable_since >= 3:
                return
        else:
            stable_since = None
        sleep(min(.5, max(0, deadline - clock())))
    raise RuntimeError('Tracking did not settle; no launch requested. Wake the headset/controllers and verify the playspace')


def running_app_module(source):
    # Resolve the same store called immediately after RunGame by the installed
    # Steam UI. Refuse changed UI code rather than hardcoding a webpack ID.
    action = re.search(r'SteamClient\.Apps\.RunGame\([^;{}]{1,300}?\),([\w$]+)\.oy\.SetRunningApp\(', source)
    if not action:
        raise RuntimeError('Steam UI launch handler changed; inspect before launching')
    headers = list(re.finditer(r'[,\{]\d+:\(\([\w$]+,[\w$]+,([\w$]+)\)=>\{', source[:action.start()]))
    if not headers:
        raise RuntimeError('Steam UI launch module boundary unavailable')
    header = headers[-1]
    imported = re.search(r'(?<![\w$])' + re.escape(action[1]) + r'\s*=\s*'
                         + re.escape(header[1]) + r'\((\d+)\)', source[header.end():action.start()])
    if not imported:
        raise RuntimeError('Steam UI running-app store unavailable')
    return int(imported[1])


def request_launch(module, rpc=evaluate):
    if type(module) is not int or module <= 0:
        raise ValueError('Invalid Steam UI module')
    expression = ('(async()=>{const options=await SteamClient.Apps.GetLaunchOptionsForApp(438100);'
                  'const vr=options.filter(x=>x.bIsVRLaunchOption===true||x.bIsVRLaunchOption===1);'
                  'if(vr.length!==1||!Number.isInteger(vr[0].nIndex)||vr[0].nIndex<0)'
                  'throw Error("Cannot identify one normal VR launch option");'
                  'if(!window.__framePerfRequire)webpackChunksteamui.push([["frame-pcvr-launch-control"],{},'
                  'r=>window.__framePerfRequire=r]);'
                  f'const store=window.__framePerfRequire({module}).oy;'
                  'if(typeof store.SetRunningApp!=="function")throw Error("Steam running-app store changed");'
                  'SteamClient.Apps.RunGame("438100","",vr[0].nIndex,500);'
                  'store.SetRunningApp(438100);return {appid:438100,launch_requested:true};})()')
    result = rpc(expression)
    if result != {'appid': 438100, 'launch_requested': True}:
        raise RuntimeError('Normal Steam launch was not acknowledged')
    return result


def launch_when_ready(source, read, *, stopped=require_stopped, rpc=evaluate, timeout=120,
                      sleep=time.sleep, clock=time.monotonic, progress=lambda reason: None):
    stopped()
    module = running_app_module(source)
    wait_for_ready(read, timeout, guard=stopped, sleep=sleep, clock=clock, progress=progress)
    stopped()
    if readiness_reason(read()) != 'ready':
        raise RuntimeError('Tracking changed before launch; no launch requested')
    return request_launch(module, rpc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true', help='Read tracking status only (default); works while playing')
    action.add_argument('--launch', action='store_true', help='Wait for tracking, then request normal Steam VR launch')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 300:
        parser.error('--timeout must be 5 to 300 seconds')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('Run on the ARM64 Frame')
    base = Path.home() / '.local/share/frame-pcvr'
    read = lambda: read_stats(base / 'tools/openvr_stats')
    if not args.launch:
        print(json.dumps({'tracking': readiness_reason(read()), 'launch_requested': False}))
        return
    require_stopped()
    source = (Path.home() / '.local/share/Steam/steamui/sp.js').read_text()
    result = launch_when_ready(source, read, timeout=args.timeout,
        progress=lambda reason: print(json.dumps({'waiting_for_tracking': reason}), flush=True))
    result['steam_ui_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
