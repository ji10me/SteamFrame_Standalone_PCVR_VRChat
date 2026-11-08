#!/usr/bin/env python3
"""Build only the patched Wine MF PE component, without installing it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source','toolchain','work','output']:
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--patch',type=Path,help='Optional patch; the compact snapshot already includes the packaged fix')
    p.add_argument('--regenerate-configure',action='store_true',help='Regenerate configure with autoreconf')
    a=p.parse_args()
    if a.work.exists() or a.output.exists():p.error('Use new work/output paths and a private source copy')
    a.work.mkdir(parents=True)
    env={**os.environ,'PATH':str(a.toolchain.resolve()/'bin')+':'+os.environ['PATH'],
         'CC':os.environ.get('CC') or shutil.which('clang') or 'cc',
         'CXX':os.environ.get('CXX') or shutil.which('clang++') or 'c++',
         'CFLAGS':os.environ.get('CFLAGS','-O2'),'CXXFLAGS':os.environ.get('CXXFLAGS','-O2')}
    source=a.source.resolve()
    marker=source/'FRAME_MF_SOURCE.json'
    compact=json.loads(marker.read_text()) if marker.is_file() else {}
    if compact and compact.get('format')!='wine-mf-corresponding-source-v1':
        raise ValueError('Unknown component source format')
    if a.patch:
        already_applied=(compact.get('mf_rate_resume_patch_applied') and
                         compact.get('mf_patch_sha256')==hashlib.sha256(a.patch.read_bytes()).hexdigest())
        if not already_applied:
            subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(a.patch.resolve())],cwd=source,check=True)
    if a.regenerate_configure or not compact or not (source/'configure').is_file():
        subprocess.run([str(source/'autogen.sh')],cwd=source,env=env,check=True)
    help_text=subprocess.check_output([str(source/'configure'),'--help'],text=True)
    optional=[v for v in re.findall(r'^\s+(--without-[a-z0-9-]+)\s',help_text,re.M)
              if v not in ['--without-mingw','--without-pthread']]
    subprocess.run([str(source/'configure'),'--enable-archs=x86_64','--disable-tests',
                    '--with-mingw=llvm-mingw','--without-x',*optional],cwd=a.work,env=env,check=True)
    subprocess.run(['make','-j1','dlls/mf/x86_64-windows/mf.dll'],cwd=a.work,env=env,check=True)
    a.output.write_bytes((a.work/'dlls/mf/x86_64-windows/mf.dll').read_bytes())
    print('Private MF component built. It has not been installed or launched.')

if __name__=='__main__':main()
