"""Verify a pinned, code-only ARM64 runtime before writing a private stage."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

COMMIT='6effd2384085818345ec32e6abb4181c7135a9a7'
ARCHIVE_SHA256='60776d5143bbf80b07fc89a18cd7da16fa5e18be9750e455ac52830d636d78f6'
BINARIES=tuple('usr/bin/'+n for n in ['FEX','FEXBash','FEXGetConfig','FEXOfflineCompiler','FEXServer','FEXServerManager','FEXpidof'])+('FEXCompatTool',)
REQUIRED=set(BINARIES)|{'ConfigTemplate.json','VERSIONS.txt','licenses/LICENSE','licenses/FEXCore/LICENSE'}


def inspect(data,expected_sha256=ARCHIVE_SHA256):
    if hashlib.sha256(data).hexdigest()!=expected_sha256:
        raise RuntimeError('同梱FEXのチェックサムが一致しません。')
    files={};total=0
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            p=PurePosixPath(member.name)
            if (not member.isfile() or p.is_absolute() or '..' in p.parts or '\\' in member.name or
                ':' in member.name or member.name in files or len(files)>=512):
                raise RuntimeError('同梱FEXに予期しないファイルがあります。')
            total+=member.size
            if member.size<0 or total>32*1024**2:raise RuntimeError('同梱FEXのサイズが上限を超えています。')
            files[member.name]=archive.extractfile(member).read()
    if 'PREBUILT.json' not in files:raise RuntimeError('同梱FEXの来歴情報がありません。')
    record=json.loads(files['PREBUILT.json'])
    if (record.get('format')!='frame-fex-prebuilt-v1' or record.get('commit')!=COMMIT or
        record.get('architecture')!='aarch64' or record.get('source_pristine') is not True):
        raise RuntimeError('同梱FEXが確認済みのソース・形式と異なります。')
    hashes=record.get('files',{})
    if set(files)!=(set(hashes)|{'PREBUILT.json'}) or not REQUIRED<=set(hashes):
        raise RuntimeError('同梱FEXの構成ファイルが一致しません。')
    for name,digest in hashes.items():
        if name not in REQUIRED and not name.startswith('licenses/'):
            raise RuntimeError('同梱FEXに不要なファイルがあります。')
        if hashlib.sha256(files[name]).hexdigest()!=digest:raise RuntimeError('同梱FEXの部品ハッシュが一致しません。')
    for name in BINARIES:
        head=files[name][:20]
        if len(head)<20 or head[:6]!=b'\x7fELF\x02\x01' or head[18:20]!=b'\xb7\x00':
            raise RuntimeError('同梱FEXがARM64 ELF実行ファイルではありません。')
    return record,files


def unpack(archive,destination):
    record,files=inspect(Path(archive).read_bytes())
    destination=Path(destination)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,data in files.items():
        p=destination/name;p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with p.open('xb') as out:out.write(data)
        p.chmod(0o700 if name in BINARIES else 0o600)
    return destination
