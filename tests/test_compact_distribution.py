import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from installer import compact_sources
from installer.release_bundle import files


class SmallDistributionTests(unittest.TestCase):
    def test_exactly_one_installer_and_two_notice_documents(self):
        kit = {'LICENSE': b'MIT License\nCopyright (c) 2026 JITOME\n',
               'THIRD_PARTY.md': b'component overview', 'payload/sources/large.tar.xz': b'not duplicated',
               'licenses/a': b'original license A\r\n', 'licenses/b': b'original license B\n'}
        result = files(b'original exe', kit, b'original SSH license\r\n', b'original SSH notice\r\n')
        self.assertEqual(set(result), {'FrameSetup.exe', 'LICENSE.txt', 'THIRD_PARTY_LICENSES.txt'})
        self.assertEqual(result['FrameSetup.exe'], b'original exe')
        self.assertIn(kit['LICENSE'], result['LICENSE.txt'])
        for original in (kit['licenses/a'], kit['licenses/b'], b'original SSH license\r\n', b'original SSH notice\r\n'):
            self.assertIn(original, result['THIRD_PARTY_LICENSES.txt'])
        self.assertNotIn(b'not duplicated', b''.join(result.values()))

    def test_import_scope_preserves_implib_and_parent_but_removes_unrelated_objects(self):
        original = b'MODULE = ucrtbase.dll\nPARENTSRC = ../msvcrt\nSOURCES = \\\n\tprintf.c \\\n\tunrelated.c\n\nx86_64_SOURCES = unwanted.S\n'
        result = compact_sources.scoped_makefile(original, ['printf.c'])
        self.assertIn(b'PARENTSRC = ../msvcrt', result)
        self.assertIn(b'SOURCES = printf.c', result)
        self.assertNotIn(b'unrelated.c', result)
        self.assertNotIn(b'unwanted.S', result)

    def test_component_scope_does_not_rewrite_implementation_or_copyrights(self):
        source = {'configure': b'wine_fn_config_makefile ()\n{\n:;\n}\n',
                  'aclocal.m4': b'wine_fn_config_makefile ()\n{\n:;\n}\n',
                  'include/Makefile.in': b'SOURCES = \\\n\twine/vulkan.h \\\n\twine/vulkan_driver.h \\\n\tneeded.h\n',
                  'dlls/mf/sar.c': b'/* Copyright original author */\nMF implementation\n',
                  'include/needed.h': b'original header'}
        for directory in compact_sources.MODULES:
            source[directory + '/Makefile.in'] = b'MODULE = example.dll\nSOURCES = example.c\n'
            source[directory + '/example.c'] = b'/* Copyright original author */\noriginal implementation\n'
        result = compact_sources.scope(source)
        for name, data in source.items():
            if name.endswith(('.c', '.h')):
                self.assertEqual(result[name], data)
        self.assertNotIn(b'wine/vulkan.h', result['include/Makefile.in'])
        self.assertIn(b'needed.h', result['include/Makefile.in'])
        self.assertIn(b'SOURCES = example.c', result['dlls/mf/Makefile.in'])
        self.assertIn(b'SOURCES = example.c', result['dlls/winecrt0/Makefile.in'])

    def test_source_archive_has_reproducible_nonpersonal_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {'configure': b'#!/bin/sh\n', 'dlls/mf/source.c': b'original source'}
            a, b = root/'a.tar.xz', root/'b.tar.xz'
            compact_sources.write_archive(payload, a)
            compact_sources.write_archive(payload, b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with tarfile.open(a) as tar:
                for member in tar:
                    self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname), (0, 0, 0, '', ''))
                    name = member.name.removeprefix('wine-mf-source/')
                    self.assertEqual(tar.extractfile(member).read(), payload[name])

    def test_unpinned_upstream_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'wrong.tar.gz'
            source.write_bytes(b'not the approved upstream archive')
            with self.assertRaisesRegex(ValueError, 'pinned'):
                compact_sources.original_files(source)


if __name__ == '__main__':
    unittest.main()
