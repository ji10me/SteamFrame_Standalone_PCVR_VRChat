#!/usr/bin/env python3
"""Rebuild the supplied LGPL V4L2 source using the supplied compiler plan/config."""
import argparse,json,os,shlex,shutil,subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['source','headers','proton','plan','work','output']:p.add_argument('--'+n,type=Path,required=True)
 p.add_argument('--guest-root',type=Path,default=Path('/usr/share/guestos/fex-mesa'));p.add_argument('--runtime-libs',type=Path)
 a=p.parse_args()
 if a.work.exists() or a.output.exists():p.error('use new work and output paths')
 a.work.mkdir(parents=True);build=a.work/'build-v4l2-native';build.mkdir();shutil.copy2(a.plan/'config.h',build/'config.h')
 source=a.source.resolve();headers=a.headers.resolve()/'usr/include';sysroot=a.guest_root.resolve();resource=subprocess.check_output(['clang','-print-resource-dir'],text=True).strip()
 objects=[]
 for entry in json.loads((a.plan/'compile_commands.json').read_text()):
  args=shlex.split(entry['command'])[1:]
  args=[s.replace('../gst-plugins-good-1.22.0',str(source)).replace('@FRAME_VIDEO_WORK@',str(a.work.resolve())) for s in args]
  args=[s for s in args if s!='-MD' and s!='-I/usr/include/gstreamer-1.0' and not s.startswith('-fdiagnostics-color')]
  for flag in ['-MQ','-MF','-o']:
   if flag in args:i=args.index(flag);del args[i:i+2]
  target=a.work.resolve()/(Path(entry['file']).stem+'.o');objects.append(target)
  command=['clang','--target=x86_64-linux-gnu','--sysroot='+str(sysroot),'-ffreestanding','-nostdinc',
   '-isystem',resource+'/include','-isystem',str(headers/'x86_64-linux-gnu'),'-isystem',str(headers),'-isystem','/usr/include',
   '-I'+str(headers/'gstreamer-1.0'),'-I'+str(a.headers.resolve()/'usr/lib/x86_64-linux-gnu/gstreamer-1.0/include'),'-I'+str(build.resolve()),*args,'-o',str(target)]
  subprocess.run(command,cwd=build,check=True)
 libs=['libgstvideo-1.0.so.0','libgstallocators-1.0.so.0','libgstbase-1.0.so.0','libgstreamer-1.0.so.0','libgobject-2.0.so.0','libglib-2.0.so.0','libc.so.6','libm.so.6']
 command=['clang','--target=x86_64-linux-gnu','--sysroot='+str(sysroot),'-fuse-ld=lld','-shared','-nostdlib','-Wl,-z,defs','-Wl,--allow-shlib-undefined',*map(str,objects),'-L'+str(a.proton.resolve()/'files/lib/x86_64-linux-gnu'),'-L'+str(sysroot/'usr/lib')]
 if a.runtime_libs:command+=['-L'+str(a.runtime_libs.resolve())]
 subprocess.run([*command,*['-l:'+n for n in libs],'-o',str(a.output.resolve())],check=True)
 print('Private plugin rebuilt. This does not install or launch it.')
if __name__=='__main__':main()
