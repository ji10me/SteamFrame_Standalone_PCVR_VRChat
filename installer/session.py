#!/usr/bin/env python3
"""Finite gaze publishers scoped to this installation's exact game process."""
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

stopping=False
AFFINITY_RETRY_SECONDS=2
AFFINITY_STARTUP_SECONDS=180
def stop(*_):
    global stopping
    stopping=True

def game_identity(pid,prefix,proc=Path('/proc')):
    if type(pid) is not int or pid<1:return None
    p=proc/str(pid)
    try:
        if p.stat().st_uid!=os.getuid() or (p/'comm').read_text().strip()!='VRChat.exe':return None
        env=dict(v.split(b'=',1) for v in (p/'environ').read_bytes().split(b'\0') if b'=' in v)
        if env.get(b'SteamAppId')==b'438100' and env.get(b'STEAM_COMPAT_DATA_PATH')==os.fsencode(prefix):
            return pid,(p/'stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,ValueError,IndexError):pass
    return None

def find_game(prefix,proc=Path('/proc')):
    result=[]
    for p in proc.glob('[0-9]*'):
        if not p.name.isdecimal():continue
        identity=game_identity(int(p.name),prefix,proc)
        if identity:result.append(identity)
    return result

def apply_tested_affinity(pid,proc=Path('/proc'),get=None,put=None):
    """Only the previously tested eight-thread layout; preserve external tuning."""
    if os.cpu_count()!=8 or (get is None and not hasattr(os,'sched_getaffinity')):return []
    get=get or os.sched_getaffinity;put=put or os.sched_setaffinity
    rows=[];root=proc/str(pid)/'task'
    def identity(p):return (p/'stat').read_text().rsplit(')',1)[1].split()[19],(p/'comm').read_text().strip()
    for p in root.glob('[0-9]*'):
        try:
            start,name=identity(p)
            target={3,4,5,6} if re.fullmatch(r'Job.Worker [0-9]+',name) else set(range(8)) if name=='audio_client_ma' else None
            if target is None:continue
            original=set(get(int(p.name)))
            if original!={3,4,5,6,7}:return []
            rows.append({'tid':int(p.name),'start':start,'name':name,'original':original,'target':target})
        except OSError:return []
    if {r['name'] for r in rows}!={'audio_client_ma',*('Job.Worker '+str(i) for i in range(7))} or len(rows)!=8:return []
    applied=[]
    try:
        for row in rows:
            if identity(root/str(row['tid']))!=(row['start'],row['name']) or set(get(row['tid']))!=row['original']:raise RuntimeError('thread changed')
            put(row['tid'],row['target']);applied.append(row)
    except (OSError,RuntimeError):
        restore_affinity(pid,applied,proc,get,put);return []
    return applied

def restore_affinity(pid,rows,proc=Path('/proc'),get=None,put=None):
    if not rows:return
    get=get or os.sched_getaffinity;put=put or os.sched_setaffinity
    for row in rows:
        try:
            p=proc/str(pid)/'task'/str(row['tid']);start=(p/'stat').read_text().rsplit(')',1)[1].split()[19];name=(p/'comm').read_text().strip()
            if (start,name)==(row['start'],row['name']) and set(get(row['tid']))==row['target']:put(row['tid'],row['original'])
        except OSError:pass

def main():
    root=Path(__file__).resolve().parent;config=json.loads((root/'runtime.json').read_text());prefix=Path(config['prefix'])
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
    deadline=time.monotonic()+180;expected=None;child=None;settled=None;affinity=[];affinity_checked=False
    affinity_deadline=None;next_affinity=0
    def attempt_affinity(pid):
        nonlocal affinity,affinity_checked,affinity_deadline,next_affinity
        if affinity_checked:return
        now=time.monotonic()
        if affinity_deadline is None:affinity_deadline=now+AFFINITY_STARTUP_SECONDS
        if now>=affinity_deadline:
            affinity_checked=True
            print(json.dumps({'tested_thread_affinity_applied':False,'affinity_retry_finished':True}),flush=True)
            return
        if now<next_affinity:return
        # Exact thread layout/identities/original masks are rechecked on every
        # attempt. A rejected attempt cannot overwrite external CPU tuning.
        affinity=apply_tested_affinity(pid)
        affinity_checked=bool(affinity)
        next_affinity=now+AFFINITY_RETRY_SECONDS
        print(json.dumps({'tested_thread_affinity_applied':bool(affinity),
                          'affinity_retry_pending':not affinity_checked}),flush=True)
    exit_reason='stop_requested'
    try:
        while not stopping:
            # Wine can briefly spawn another process with the same inherited
            # title/prefix identity. Once selected, inspect only our pinned PID.
            if expected:
                if game_identity(expected[0],prefix)!=expected:
                    exit_reason='tracked_game_ended_or_replaced';break
            if not expected:
                games=find_game(prefix)
                if time.monotonic()>deadline:
                    exit_reason='game_discovery_timeout';break
                if len(games)!=1:time.sleep(.5);continue
                expected=games[0]
            pid=expected[0]
            # Audio and loading threads need their layout before VR becomes ready.
            # This does not depend on gaze/controller readiness.
            attempt_affinity(pid)
            if child is not None and child.poll() is None:
                time.sleep(.5);continue
            child=None
            try:
                stats=json.loads(subprocess.check_output([str(root/'openvr-stats')],stderr=subprocess.DEVNULL,text=True,timeout=5))
                ready=stats.get('scene_pid')==pid and not stats.get('scene_loading',True) and stats.get('grid_alpha',1)<.01 and stats.get('fade_alpha',1)<.01 and stats.get('hmd_pose_valid',False)
            except (OSError,ValueError,subprocess.SubprocessError):ready=False
            if not ready:settled=None;time.sleep(1);continue
            settled=settled or time.monotonic()
            if time.monotonic()-settled<3:time.sleep(.5);continue
            if game_identity(expected[0],prefix)!=expected:
                exit_reason='tracked_game_ended_or_replaced';break
            # Device-local calibration is never shipped to another recipient.
            calibration=[1,1,0,0]
            path=root.parent.parent/'calibration.json'
            if path.is_file():calibration=json.loads(path.read_text())['values']
            child=subprocess.Popen([str(root/'gaze-bridge'),str(pid),'1800','--flip-y','--calibration',*map(str,calibration)],stdin=subprocess.DEVNULL)
            time.sleep(.5)
    except BaseException:
        exit_reason='supervisor_error'
        raise
    finally:
        if expected:restore_affinity(expected[0],affinity)
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        print(json.dumps({'session_exit_reason':exit_reason}),flush=True)

if __name__=='__main__':main()
