#!/usr/bin/env python3
"""Build the Windows GUI plus a code-only, hash-pinned embedded Frame package."""
import argparse,base64,hashlib,io,json,os,shutil,struct,subprocess,tarfile,zipfile
from pathlib import Path
if __package__:
 from .sanitize_assets import sanitize
 from .release_bundle import files as release_files
else:
 from sanitize_assets import sanitize
 from release_bundle import files as release_files
ROOT=Path(__file__).resolve().parents[1]

def hash_bytes(data):return hashlib.sha256(data).hexdigest()
def put(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
def allocated_elf_sections(data):
 offset=struct.unpack_from('<Q',data,40)[0];size,count,names=struct.unpack_from('<HHH',data,58)
 rows=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*size) for i in range(count)]
 strings=data[rows[names][4]:rows[names][4]+rows[names][5]]
 return {strings[r[0]:].split(b'\0',1)[0]:(r[1],r[2],r[3],r[5],data[r[4]:r[4]+r[5]] if r[1]!=8 else b'') for r in rows if r[2]&2}
def zip_bytes(files):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for name,data in sorted(files.items()):
   info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3;info.external_attr=0o100600<<16;z.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
 return out.getvalue()

def admin_bootstrap(source):
 # Privileged code comes from this package, never from an editable remote helper.
 encoded=base64.b64encode(source).decode()
 code='''import os,pwd,base64
from pathlib import Path
path=str(Path(pwd.getpwuid(int(os.environ["SUDO_UID"])).pw_dir)/".local/share/frame-pcvr-easy/swap_admin.py")
exec(compile(base64.b64decode(PAYLOAD),path,"exec"),{"__name__":"__main__","__file__":path})
'''.replace('PAYLOAD',repr(encoded))
 expression='exec(__import__("base64").b64decode("'+base64.b64encode(code.encode()).decode()+'"))'
 if len(expression)>24000:raise ValueError('Admin command exceeds the bounded Windows command line')
 return expression

def bootstrap(digest):
 code='''import sys,os,io,json,hashlib,tempfile,zipfile,subprocess,stat
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8");os.umask(0o077)
data=sys.stdin.buffer.read(128*1024*1024+1)
assert len(data)<=128*1024*1024 and hashlib.sha256(data).hexdigest()==DIGEST,"Package transfer checksum mismatch"
action=sys.argv[1];assert action in ["install","uninstall","install-replace-android"]
replace_android=action=="install-replace-android"
if replace_android:action="install"
root=Path.home()/".local/share/frame-pcvr-easy"
assert not root.is_symlink()
root.mkdir(parents=True,mode=0o700,exist_ok=True)
assert root.stat().st_uid==os.getuid() and root.stat().st_mode&0o022==0
stage=root/"staging";stage.mkdir(mode=0o700,exist_ok=True);assert not stage.is_symlink()
folder=Path(tempfile.mkdtemp(prefix="kit-",dir=stage))
with zipfile.ZipFile(io.BytesIO(data)) as z:
 entries=z.infolist();assert len(entries)<10000 and sum(i.file_size for i in entries)<256*1024*1024
 assert len({i.filename for i in entries})==len(entries)
 for i in entries:
  p=Path(i.filename)
  assert not p.is_absolute() and ".." not in p.parts and "\\\\" not in i.filename and ":" not in i.filename and not stat.S_ISLNK(i.external_attr>>16)
  target=folder/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(i));target.chmod(0o600)
command=[sys.executable,"-B",str(folder/"frame_setup.py"),action]
if action=="install":command.append("--accept-alpha-license")
if replace_android:command.append("--uninstall-android-first")
raise SystemExit(subprocess.call(command))
'''.replace('DIGEST',repr(digest))
 return 'exec(__import__("base64").b64decode("'+base64.b64encode(code.encode()).decode()+'"))'

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in ['assets','toolchain','openvr','openssh','dfr-repo','output']:p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 if hash_bytes(a.openssh.read_bytes())!='b67ff8becd203502190ec42ac7787c24c2cd3325ed7700485fe7a1c83d2a7957':raise ValueError('OpenSSH archive does not match the pinned official release')
 if hash_bytes((a.openvr/'openvr.h').read_bytes())!='1e6ed57199896cc1f7c5484e50fa18955e97be15be690beb28d998c877ead7fd':raise ValueError('OpenVR header changed')
 kit={}
 for n in ['frame_setup.py','launch.py','session.py','dependencies.py','platform_setup.py','swap_setup.py','steam_install.js','prebuilt_fex.py']:kit[n]=(ROOT/'installer'/n).read_bytes()
 kit['LICENSE']=(ROOT/'LICENSE').read_bytes();kit['LICENSE.md']=(ROOT/'LICENSE.md').read_bytes();kit['LICENSE.en.md']=(ROOT/'LICENSE.en.md').read_bytes();kit['compatibility.json']=(a.assets/'compatibility.json').read_bytes()
 for n in ['dfr/d3d11.dll','dfr/dxgi.dll','sources/gstreamer-frame-source.tar.xz','sources/wine-mf-source.tar.xz']:kit['payload/'+n]=(a.assets/n).read_bytes()
 if __package__:from .prebuilt_fex import inspect as inspect_fex
 else:from prebuilt_fex import inspect as inspect_fex
 fex=(a.assets/'fex/runtime.tar.xz').read_bytes();record,fex_files=inspect_fex(fex)
 kit['payload/fex/runtime.tar.xz']=fex
 for n,data in fex_files.items():
  if n.startswith('licenses/'):kit['licenses/FEX/'+n[len('licenses/'):]]=data
 original=(a.assets/'video/libgstvideo4linux2.so').read_bytes()
 if hash_bytes(original)!='ded2618848cda4156407823a877804d7b5fc00af079eb807b97db1d34305dba6':raise ValueError('Wrong original H.264 code')
 stripped=a.output/'libgstvideo4linux2.so'
 proof=sanitize(a.assets/'video/libgstvideo4linux2.so',stripped,a.toolchain/'llvm-strip','elf')
 code=stripped.read_bytes()
 if any(v in code for v in [b'/Users/',b'/home/steamos/',b'192.168.1.']):raise ValueError('Private path remains in plugin')
 kit['payload/video/libgstvideo4linux2.so']=code
 if hash_bytes(code)!='3f6417451f0060f5d573fded651b4fac9c751f0226e45ce6fcb6c958cd6643a6':raise ValueError('Wrong sanitized H.264 code')
 kit['payload/video/strip-proof.json']=(json.dumps(proof,indent=2)+'\n').encode()
 if hash_bytes((a.assets/'wine/mf.dll').read_bytes())!='df227462d91152062d3857fa01433b8b02d2991c66f057f19278cdbb0177359c':raise ValueError('Wrong original Wine MF code')
 mf=a.output/'mf.dll';proof=sanitize(a.assets/'wine/mf.dll',mf,a.toolchain/'llvm-strip','pe')
 if hash_bytes(mf.read_bytes())!='e4c80315b6d1bc9ad80c1ec3a19ec0247d7435082d18a0f8938bd00577c398e0':raise ValueError('Wrong sanitized Wine MF code')
 kit['payload/wine/mf.dll']=mf.read_bytes()
 kit['payload/wine/strip-proof.json']=(json.dumps(proof,indent=2)+'\n').encode()
 helpers=['framectl.py','steam_cdp.py','vulkan_lookup_adapter.c','vulkan_route_probe.c','vulkan_image_probe.c','openvr_stats.cpp']
 for n in helpers:kit['support/'+n]=(ROOT/'tools'/n).read_bytes()
 kit['support/tools/framectl.py']=(ROOT/'tools/framectl.py').read_bytes()
 kit['support/scripts/build-fex-stage.sh']=(ROOT/'scripts/build-fex-stage.sh').read_bytes()
 bridge=(ROOT/'tools/frame_gaze_bridge.cpp').read_text().replace('/.local/share/frame-pcvr/gaze','/.local/share/frame-pcvr-easy/gaze')
 kit['support/gaze/frame_gaze_bridge.cpp']=bridge.encode()
 kit['support/gaze/frame_gaze_projection.h']=(ROOT/'tools/frame_gaze_projection.h').read_bytes()
 kit['support/gaze/frame_gaze_poll.h']=(ROOT/'tools/frame_gaze_poll.h').read_bytes()
 kit['support/gaze/gaze_shm.h']=(ROOT/'third_party/vrchat-dfr-gaze/gaze_shm.h').read_bytes()
 kit['support/gaze/openvr.h']=(a.openvr/'openvr.h').read_bytes()
 for n in ['LICENSE','LICENSE-DXVK']:kit['licenses/DFR-'+n]=(ROOT/'third_party/vrchat-dfr-gaze'/n).read_bytes()
 kit['licenses/OpenVR-LICENSE']=(a.openvr/'LICENSE').read_bytes()
 kit['licenses/LLVM-runtime-LICENSE.TXT']=(a.toolchain.parent/'LICENSE.TXT').read_bytes()
 for f in (a.toolchain.parent/'x86_64-w64-mingw32/share/mingw32').glob('COPYING*'):kit['licenses/MinGW/'+f.name]=f.read_bytes()
 kit['licenses/GStreamer-COPYING']=(ROOT/'patches/gstreamer-iris-drain-restart/COPYING').read_bytes()
 kit['licenses/Wine-COPYING.LIB']=(ROOT/'patches/wine-mf-audio-rate-resume/COPYING').read_bytes()
 kit['payload/sources/wine-mf-rate-resume.patch']=(ROOT/'patches/wine-mf-audio-rate-resume/rate-resume.patch').read_bytes()
 kit['payload/sources/sanitize_assets.py']=(ROOT/'installer/sanitize_assets.py').read_bytes()
 kit['payload/sources/rebuild_wine_mf.py']=(ROOT/'installer/rebuild_wine_mf.py').read_bytes()
 kit['payload/sources/compact_sources.py']=(ROOT/'installer/compact_sources.py').read_bytes()
 for n in ['config.h','compile_commands.json']:kit['payload/sources/source-build/'+n]=(a.assets/'source-build'/n).read_bytes()
 kit['payload/sources/rebuild_gstreamer.py']=(ROOT/'installer/rebuild_gstreamer.py').read_bytes()
 kit['payload/sources/REBUILD.md']=(ROOT/'installer/REBUILD.md').read_bytes()
 kit['payload/sources/producer-build-v4l2.py']=(ROOT/'installer/producer_build_v4l2.py').read_bytes()
 kit['payload/sources/frame-iris-compat.patch']=(ROOT/'patches/gstreamer-iris-drain-restart/frame-iris-compat.patch').read_bytes()
 kit['payload/sources/dxvk-frame-source.tar']=subprocess.check_output(['git','-C',str(a.dfr_repo),'archive','HEAD'])
 for n in ['README.ja.md','THIRD_PARTY.md']:kit[n]=(ROOT/'installer'/n).read_bytes()
 # Own scripts/configuration must contain no development machine paths or addresses.
 for n,data in kit.items():
  if n.startswith('support/gaze/openvr') or n.startswith(('licenses/','payload/')):continue
  for marker in [b'/Users/',b'/home/steamos/',b'192.168.1.',b'PRIVATE KEY-----',b'github_pat_']:
   if marker in data:raise ValueError('Private development marker in '+n)
 manifest={'format':'frame-easy-dfr-h264-v1','version':'alpha-dfr-h264-swap-20261109-0.3.6','files':{n:hash_bytes(data) for n,data in sorted(kit.items())},
           'optional_swap':{'bytes':8*1024**3,'priority':10,'requires_recipient_admin_authentication':True,'persistent':False},
           'contains_game_or_prefix':False,'experimental_upscale_included':False,'third_party_original_licenses_preserved':True}
 kit['MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
 data=zip_bytes(kit);put(a.output/'frame-payload.zip',data)
 header='#pragma once\n#define FRAME_BOOTSTRAP '+json.dumps(bootstrap(hash_bytes(data)))+'\n#define FRAME_PACKAGE_ID '+json.dumps(hash_bytes(data)[:16])+'\n#define FRAME_SWAP_BOOTSTRAP '+json.dumps(admin_bootstrap(kit['swap_setup.py']))+'\n'
 put(a.output/'generated_package.h',header.encode())
 rc='101 RCDATA "'+str((a.output/'frame-payload.zip').resolve()).replace('\\','\\\\')+'"\n1 24 "'+str((ROOT/'installer/windows/app.manifest').resolve())+'"\n'
 for resource,name in [(102,'LICENSE.md'),(103,'installer/THIRD_PARTY.md')]:rc+=str(resource)+' RCDATA "'+str((ROOT/name).resolve())+'"\n'
 with zipfile.ZipFile(a.openssh) as z:
  for resource,name in [(201,'ssh.exe'),(202,'ssh-keygen.exe'),(203,'libcrypto.dll'),(204,'LICENSE.txt'),(205,'NOTICE.txt')]:
   path=a.output/'openssh'/name;put(path,z.read('OpenSSH-Win64/'+name));rc+=str(resource)+' RCDATA "'+str(path.resolve())+'"\n'
 rc+='''1 VERSIONINFO
FILEVERSION 0,3,6,0
PRODUCTVERSION 0,3,6,0
FILEFLAGSMASK 0x3fL
FILEFLAGS 0
FILEOS 0x40004L
FILETYPE 1
BEGIN
 BLOCK "StringFileInfo"
 BEGIN
  BLOCK "040904b0"
  BEGIN
   VALUE "CompanyName", "JITOME"
   VALUE "FileDescription", "Steam Frame PCVR for VRChat alpha setup"
   VALUE "ProductName", "Steam Frame PCVR for VRChat"
   VALUE "ProductVersion", "0.3.6-alpha"
  END
 END
 BLOCK "VarFileInfo"
 BEGIN
  VALUE "Translation", 0x409, 1200
 END
END
'''
 put(a.output/'package.rc',rc.encode())
 def tool(name):return a.toolchain/name
 subprocess.run([str(tool('llvm-rc')),'/fo',str(a.output/'package.res'),str(a.output/'package.rc')],check=True)
 subprocess.run([str(tool('x86_64-w64-mingw32-clang++')),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-municode','-mwindows','-static',
  '-I'+str(a.output),str(ROOT/'installer/windows/setup.cpp'),str(a.output/'package.res'),'-lwinhttp','-lshell32','-lole32','-lcomctl32','-luuid','-o',str(a.output/'FrameSetup.exe')],check=True)
 with zipfile.ZipFile(a.openssh) as z:
  distribution=release_files((a.output/'FrameSetup.exe').read_bytes(),kit,z.read('OpenSSH-Win64/LICENSE.txt'),z.read('OpenSSH-Win64/NOTICE.txt'))
 for name,data in distribution.items():put(a.output/name,data)
 # Build-only checksums are kept outside the end-user ZIP; publish its hash in the release notes.
 put(a.output/'SHA256SUMS.json',(json.dumps({n:hash_bytes(v) for n,v in distribution.items()},indent=2)+'\n').encode())
 archive=a.output/'SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip';archive.write_bytes(zip_bytes(distribution))
 archive.with_suffix(archive.suffix+'.sha256').write_text(hash_bytes(archive.read_bytes())+'  '+archive.name+'\n')
 print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':hash_bytes(archive.read_bytes()),'embedded_files':len(kit),'native_windows_gui':True,'python_on_pc_required':False},indent=2))
if __name__=='__main__':main()
