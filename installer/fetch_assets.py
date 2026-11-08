#!/usr/bin/env python3
"""Legacy 0.2.x code-asset collector; not the 0.3.x MF/lifecycle asset set."""
import argparse,hashlib,io,json,subprocess,tarfile
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--host',required=True);p.add_argument('--control-socket',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=False)
 code=r'''
from pathlib import Path
import io,json,hashlib,tarfile,sys
b=Path.home()/'.local/share/frame-pcvr';q=b/'runs/quality-tuning';d=q/'foveation-capability-study/payload-analytic-map';v=q/'video-hw-study'
j=json.loads((b/'candidates/vk-native-opim11wf/integration.json').read_text())
files={}
for name,expected in {'d3d11.dll':'a112b719c417c78abe13de88bd6c9be45aac252a9c153ad432740b0a44298db0','dxgi.dll':'ae23062e195df360abbed15cafba0e2814324c920c2f40fc8b4865538538d89d'}.items():
 data=(d/name).read_bytes();assert hashlib.sha256(data).hexdigest()==expected;files['dfr/'+name]=data
plugin=(v/'plugins-pool/libgstvideo4linux2.so').read_bytes();assert hashlib.sha256(plugin).hexdigest()=='dbfbf66257f8fd5a94d750bb8ce20f62abfaa5038435f142d69594652d3f024d';files['video/libgstvideo4linux2.so']=plugin
compat={'standard_fex':j['standard_hashes'],'standard_proton':j['private_proton']['parent_hashes']}
files['compatibility.json']=(json.dumps(compat,indent=2)+'\n').encode()
source=v/'gst-plugins-good-pool-1.22.0';buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w:gz') as t:
 for f in sorted(source.rglob('*')):
  if not f.is_file() or f.is_symlink() or any(x in f.parts for x in ['.git','__pycache__']):continue
  n=str(f.relative_to(source));assert not n.startswith('/') and '..' not in Path(n).parts
  data=f.read_bytes();info=tarfile.TarInfo('gst-plugins-good-1.22.0-frame/'+n);info.size=len(data);info.mode=0o755 if f.stat().st_mode&0o111 else 0o644;info.mtime=0;t.addfile(info,io.BytesIO(data))
files['sources/gstreamer-frame-source.tar.gz']=buffer.getvalue()
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:
 for n,data in files.items():
  info=tarfile.TarInfo(n);info.size=len(data);info.mode=0o600;t.addfile(info,io.BytesIO(data))
'''
 archive=a.output/'assets.tar'
 with archive.open('wb') as out:subprocess.run(['ssh','-S',a.control_socket,'-o','BatchMode=yes',a.host,'python3 -B -'],input=code.encode(),stdout=out,check=True)
 with tarfile.open(archive) as t:
  for f in t.getmembers():
   if not f.isfile() or Path(f.name).is_absolute() or '..' in Path(f.name).parts:raise ValueError('Unsafe asset archive')
  t.extractall(a.output,filter='data')
 archive.unlink()
 for f in a.output.rglob('*'):
  if f.is_file() and f.suffix in ['.dll']:
   data=f.read_bytes()
   if any(v in data for v in [b'/Users/',b'/home/steamos/',b'192.168.1.']):raise ValueError('Developer path in code asset: '+f.name)
 print('Verified code-only assets and corresponding GStreamer source collected.')
if __name__=='__main__':main()
