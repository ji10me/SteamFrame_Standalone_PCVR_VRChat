import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from installer import prebuilt_fex as fex


def fixture(*,change=None,extra=None):
    elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';elf[18:20]=b'\xb7\x00'
    files={n:bytes(elf) for n in fex.BINARIES}
    files.update({'ConfigTemplate.json':b'{}','VERSIONS.txt':b'fixture version',
                  'licenses/LICENSE':b'upstream MIT fixture','licenses/FEXCore/LICENSE':b'core license fixture'})
    record={'format':'frame-fex-prebuilt-v1','commit':fex.COMMIT,'architecture':'aarch64',
            'source_pristine':True,'files':{n:hashlib.sha256(d).hexdigest() for n,d in files.items()}}
    if change:change(record,files)
    files['PREBUILT.json']=json.dumps(record).encode()
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:xz') as t:
        for n,d in files.items():
            info=tarfile.TarInfo(n);info.size=len(d);t.addfile(info,io.BytesIO(d))
        if extra:
            info,data=extra;t.addfile(info,io.BytesIO(data))
    return stream.getvalue()


class PrebuiltFexTests(unittest.TestCase):
    def check(self,data):return fex.inspect(data,hashlib.sha256(data).hexdigest())

    def test_expected_code_and_original_licenses_are_retained(self):
        record,files=self.check(fixture())
        self.assertEqual(record['commit'],fex.COMMIT)
        self.assertTrue(fex.REQUIRED<=set(files))
        self.assertEqual(files['licenses/LICENSE'],b'upstream MIT fixture')

    def test_unpinned_archive_fails_before_reading_members(self):
        with self.assertRaisesRegex(RuntimeError,'チェックサム'):fex.inspect(b'arbitrary data')

    def test_wrong_source_commit_or_non_pristine_build_is_refused(self):
        for key,value in [('commit','0'*40),('architecture','x86_64'),('source_pristine',False)]:
            with self.assertRaises(RuntimeError):self.check(fixture(change=lambda r,f:r.update({key:value})))

    def test_missing_dependency_license_is_refused(self):
        def change(r,files):del r['files']['licenses/LICENSE'];del files['licenses/LICENSE']
        with self.assertRaises(RuntimeError):self.check(fixture(change=change))

    def test_changed_binary_and_non_arm64_binary_are_refused(self):
        def corrupt(r,files):files['usr/bin/FEX']=b'changed'
        with self.assertRaises(RuntimeError):self.check(fixture(change=corrupt))
        def wrong_arch(r,files):
            files['usr/bin/FEX']=b'not ARM64';r['files']['usr/bin/FEX']=hashlib.sha256(files['usr/bin/FEX']).hexdigest()
        with self.assertRaises(RuntimeError):self.check(fixture(change=wrong_arch))

    def test_traversal_links_duplicate_paths_and_unlisted_files_are_refused(self):
        cases=[]
        for n in ['../escape','/absolute','C:\\outside','usr/bin/FEX','user.reg']:
            i=tarfile.TarInfo(n);i.size=1;cases.append((i,b'x'))
        i=tarfile.TarInfo('licenses/link');i.type=tarfile.SYMTYPE;i.linkname='/outside';cases.append((i,b''))
        for case in cases:
            with self.subTest(name=case[0].name),self.assertRaises(RuntimeError):self.check(fixture(extra=case))

    def test_unpack_never_uses_an_existing_target(self):
        record,files=self.check(fixture())
        with tempfile.TemporaryDirectory() as tmp,patch.object(fex,'inspect',return_value=(record,files)):
            p=Path(tmp);archive=p/'runtime.tar.xz';archive.write_bytes(b'fixture')
            target=p/'stage';fex.unpack(archive,target)
            self.assertEqual((target/'usr/bin/FEX').stat().st_mode&0o777,0o700)
            with self.assertRaises(FileExistsError):fex.unpack(archive,target)
