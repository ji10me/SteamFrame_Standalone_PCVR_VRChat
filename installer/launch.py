#!/usr/bin/env python3
"""Only the ordinary Steam launch of VRChat; no alternate account/game launcher."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for v in iter(lambda:f.read(1024*1024),b''):h.update(v)
    return h.hexdigest()

def command(args, environment, release, config):
    if environment.get('SteamAppId')!='438100':raise RuntimeError('This launcher is restricted to the normal VRChat Steam app.')
    for n,h in config['hashes'].items():
        p=release/n
        if p.is_symlink() or p.resolve().is_relative_to(release.resolve()) is False or sha(p)!=h:raise RuntimeError('Installed component changed; reinstall instead of guessing.')
    old=str(Path(config['standard'])/'fex-compat-tool');original=str(Path(config['standard_proton'])/'proton')
    if args.count(old)!=1 or args.count(original)!=1:raise RuntimeError('Expected exactly one Steam FEX/Proton boundary.')
    out=list(args);out[out.index(old)]=str(Path(config['runtime'])/'fex-compat-tool');out[out.index(original)]=str(Path(config['proton'])/'proton')
    prefix=Path(config['prefix']);eac=prefix/'pfx/drive_c/users/steamuser/AppData/Roaming/EasyAntiCheat'
    if not eac.is_dir():raise RuntimeError('Initialize VRChat EAC normally through Steam first.')
    environment.update(STEAM_COMPAT_DATA_PATH=str(prefix),FEX_APP_CONFIG_LOCATION=str(prefix/'fex-emu')+'/',
        FEX_APP_DATA_LOCATION=str(prefix)+'/',FEX_SERVERSOCKETPATH='frame-easy-vrchat',EAC_LAUNCHERDIR=str(eac))
    previous=[x for x in environment.get('STEAM_COMPAT_TOOL_PATHS','').split(':') if x]
    environment['STEAM_COMPAT_TOOL_PATHS']=':'.join([config['runtime'],config['proton'],*[x for x in previous if x not in (config['standard'],config['standard_proton'])]])
    return out

def main():
    release=Path(__file__).resolve().parent;config=json.loads((release/'runtime.json').read_text())
    separator=sys.argv.index('--');args=command(sys.argv[separator+1:],os.environ,release,config)
    with (release/'work/session.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-B',str(release/'session.py'),str(os.getpid())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    os.execvp(args[0],args)

if __name__=='__main__':main()
