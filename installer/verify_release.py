#!/usr/bin/env python3
"""Offline checks of the actual installer resources, manifest and distribution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import zipfile
if __package__:
    from .frame_setup import verify_kit
    from .sanitize_assets import pe_sections
    from .compact_sources import verify_wine
else:
    from frame_setup import verify_kit
    from sanitize_assets import pe_sections
    from compact_sources import verify_wine


def sha(data):return hashlib.sha256(data).hexdigest()

def resources(exe):
    pe=struct.unpack_from('<I',exe,60)[0];optional=pe+24
    if struct.unpack_from('<H',exe,pe+4)[0]!=0x8664 or struct.unpack_from('<H',exe,optional+68)[0]!=2:
        raise ValueError('Expected AMD64 Windows GUI')
    sections=pe_sections(exe)
    def raw(rva):
        for s in sections.values():
            if s['address']<=rva<s['address']+max(s['size'],s['type']):return s['offset']+rva-s['address']
        raise ValueError('Unmapped resource RVA')
    rva=struct.unpack_from('<I',exe,optional+112+16)[0];root=raw(rva);result={}
    def walk(offset,path=()):
        named,number=struct.unpack_from('<HH',exe,offset+12)
        for i in range(named+number):
            ident,child=struct.unpack_from('<II',exe,offset+16+i*8)
            if ident&0x80000000:raise ValueError('Unexpected named resource')
            if child&0x80000000:walk(root+(child&0x7fffffff),path+(ident,))
            else:
                address,size=struct.unpack_from('<II',exe,root+child);where=raw(address)
                result[path+(ident,)]=exe[where:where+size]
    walk(root);return result


def safe_zip(data):
    z=zipfile.ZipFile(io.BytesIO(data));names=z.namelist()
    if len(set(names))!=len(names):raise ValueError('Duplicate ZIP paths')
    for i in z.infolist():
        p=Path(i.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in i.filename or i.flag_bits&1:
            raise ValueError('Unexpected ZIP member')
    return z


def verify(directory,openssh=None):
    d=Path(directory);exe=(d/'FrameSetup.exe').read_bytes();payload=(d/'frame-payload.zip').read_bytes()
    r=resources(exe);rcdata={path[1]:data for path,data in r.items() if path[0]==10}
    if rcdata[101]!=payload:raise ValueError('EXE embeds a different payload')
    if not any('0.3.5-alpha'.encode('utf-16le') in value for path,value in r.items() if path[0]==16):
        raise ValueError('Version resource is stale')
    z=safe_zip(payload)
    forbidden=['user.reg','system.reg','loginusers.vdf','authorized_keys','known_hosts','installation.json','calibration.json']
    for n in z.namelist():
        if Path(n).name in forbidden or n.startswith(('local/','output/','.git/')):raise ValueError('Private runtime file in package')
        if not n.startswith(('payload/','licenses/')) and any(v in z.read(n) for v in [b'/Users/',b'/home/'+b'steamos/',b'192.168.1.']):
            raise ValueError('Private development marker in '+n)
    with tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp);manifest=verify_kit(Path(tmp))
    if rcdata[102]!=z.read('LICENSE.md') or rcdata[103]!=z.read('THIRD_PARTY.md'):
        raise ValueError('License resource mismatch')
    wine_source=verify_wine(z.read('payload/sources/wine-mf-source.tar.xz'))
    for name in ['payload/video/libgstvideo4linux2.so','payload/wine/mf.dll']:
        if any(v in z.read(name) for v in [b'/Users/',b'/home/'+b'steamos/',b'192.168.1.']):raise ValueError('Private binary marker')
    if openssh:
        with zipfile.ZipFile(openssh) as official:
            for resource,name in [(201,'ssh.exe'),(202,'ssh-keygen.exe'),(203,'libcrypto.dll'),(204,'LICENSE.txt'),(205,'NOTICE.txt')]:
                if rcdata[resource]!=official.read('OpenSSH-Win64/'+name):raise ValueError('OpenSSH resource mismatch')
    distribution=safe_zip((d/'SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip').read_bytes())
    if set(distribution.namelist())!={'FrameSetup.exe','LICENSE.txt','THIRD_PARTY_LICENSES.txt'}:
        raise ValueError('End-user ZIP must contain exactly the installer and two notice files')
    if distribution.read('FrameSetup.exe')!=exe:raise ValueError('Distribution embeds another EXE')
    if z.read('LICENSE') not in distribution.read('LICENSE.txt'):raise ValueError('MIT license missing')
    notices=distribution.read('THIRD_PARTY_LICENSES.txt')
    for name in z.namelist():
        if name.startswith('licenses/') and z.read(name) not in notices:
            raise ValueError('Third-party notice missing')
    for ident in (204,205):
        if rcdata[ident] not in notices:raise ValueError('OpenSSH notice missing')
    if (d/'SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip').stat().st_size>=20_000_000:
        raise ValueError('Discord ZIP exceeds the conservative 20 MB budget')
    return {'version':manifest['version'],'embedded_files':len(manifest['files']),'payload_matches_exe':True,
            'checksums_and_sources_verified':True,'component_source_files':len(wine_source['files']),
            'distribution_files':3,'under_20_MB':True,'no_personal_runtime_files':True,'native_windows_ui_executed':False,
            'exe_bytes':len(exe),'exe_sha256':sha(exe),
            'zip_bytes':(d/'SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip').stat().st_size,
            'zip_sha256':sha((d/'SteamFrame-PCVR-VRChat-Setup-Windows-alpha.zip').read_bytes())}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--openssh',type=Path)
    a=p.parse_args();print(json.dumps(verify(a.directory,a.openssh),indent=2))
