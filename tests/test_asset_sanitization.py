import struct
import unittest
from installer.sanitize_assets import OLD, NEW, normalize


def elf_fixture(marker_section='.rodata', suffix=b'/session.c'):
    names=b'\0.shstrtab\0.text\0.rodata\0';data=bytearray(1024)
    data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<Q',data,40,512);struct.pack_into('<HHH',data,58,64,4,1)
    text=(OLD+suffix+b'\0') if marker_section=='.text' else b'\x90\xc3'
    rodata=(OLD+suffix+b'\0') if marker_section=='.rodata' else b'no path\0'
    data[128:128+len(names)]=names;data[192:192+len(text)]=text;data[256:256+len(rodata)]=rodata
    for i,row in enumerate([(0,0,0,0,0,0),(1,3,0,0,128,len(names)),(11,1,6,4096,192,len(text)),(17,1,2,8192,256,len(rodata))]):
        name,typ,flags,addr,off,size=row;struct.pack_into('<IIQQQQIIQQ',data,512+i*64,name,typ,flags,addr,off,size,0,0,1,0)
    return bytes(data)


class AssetPrivacyTests(unittest.TestCase):
    def test_source_path_prefix_changes_without_moving_tail_or_code(self):
        original=elf_fixture();sanitized,proof=normalize(original,'elf')
        self.assertEqual(len(original),len(sanitized));self.assertEqual(sanitized[192:194],b'\x90\xc3')
        self.assertNotIn(OLD,sanitized);self.assertIn(NEW+b'/session.c\0',sanitized)
        self.assertEqual(proof['diagnostic_prefix_replacements'],1)
        self.assertEqual([i for i,(a,b) in enumerate(zip(original,sanitized)) if a!=b],
                         [256+i for i,(a,b) in enumerate(zip(OLD,NEW)) if a!=b])

    def test_private_path_inside_executable_code_is_rejected(self):
        with self.assertRaises(ValueError):normalize(elf_fixture('.text'),'elf')

    def test_non_source_diagnostic_is_not_silently_rewritten(self):
        with self.assertRaises(ValueError):normalize(elf_fixture(suffix=b'/credentials.json'),'elf')

if __name__=='__main__':unittest.main()
