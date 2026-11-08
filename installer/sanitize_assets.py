"""Remove debug information and normalize source-path prefixes in diagnostics only."""
import hashlib
from pathlib import Path
import struct
import subprocess

OLD = b'/home/' + b'steamos/'
NEW = b'/usr/src/code/'
assert len(OLD) == len(NEW)


def elf_sections(data):
    if data[:6] != b'\x7fELF\x02\x01': raise ValueError('Expected little-endian ELF64')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, names = struct.unpack_from('<HHH', data, 58)
    rows = [struct.unpack_from('<IIQQQQIIQQ', data, offset+i*size) for i in range(count)]
    strings = data[rows[names][4]:rows[names][4]+rows[names][5]]
    return {strings[r[0]:].split(b'\0',1)[0].decode():
            {'offset':r[4], 'size':0 if r[1]==8 else r[5], 'execute':bool(r[2]&4),
             'allocated':bool(r[2]&2), 'address':r[3], 'flags':r[2], 'type':r[1]}
            for r in rows}


def pe_sections(data):
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[:2] != b'MZ' or data[pe:pe+4] != b'PE\0\0': raise ValueError('Expected PE')
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional = struct.unpack_from('<H', data, pe+20)[0]
    start = pe+24+optional
    result = {}
    for i in range(count):
        name, virtual_size, address, raw_size, offset, _, _, _, _, flags = struct.unpack_from('<8sIIIIIIHHI', data, start+i*40)
        result[name.rstrip(b'\0').decode()] = {'offset':offset, 'size':raw_size, 'execute':bool(flags&0x20000000),
                                               'allocated':True, 'address':address, 'flags':flags, 'type':virtual_size}
    return result


def content(data, section):
    return data[section['offset']:section['offset']+section['size']]


def normalize(data, kind):
    sections = elf_sections(data) if kind == 'elf' else pe_sections(data)
    output = bytearray(data); positions=[];cursor=0
    while True:
        index=data.find(OLD,cursor)
        if index<0:break
        matches=[(n,s) for n,s in sections.items() if s['offset']<=index and index+len(OLD)<=s['offset']+s['size']]
        end=data.find(b'\0',index)
        if (len(matches)!=1 or matches[0][1]['execute'] or
            not matches[0][0].startswith(('.rodata','.rdata')) or end<0 or
            not data[index:end].endswith((b'.c',b'.h'))):
            raise ValueError('Developer marker outside a diagnostic source filename')
        output[index:index+len(OLD)]=NEW;positions.append(index);cursor=index+len(OLD)
    for n,s in sections.items():
        if s['execute'] and content(data,s)!=content(output,s): raise ValueError('Executable section changed')
    if any(p in output for p in (OLD,b'/Users/',b'192.168.1.')):raise ValueError('Developer marker remains')
    return bytes(output), {'diagnostic_prefix_replacements':len(positions),'executable_sections_unchanged':True,
                           'equal_length_prefix_mapping':{'from':'/home/<build-user>/','to':NEW.decode()}}


def sanitize(source, output, strip, kind):
    source, output = Path(source), Path(output)
    if output.exists():raise ValueError('Refusing to replace an existing artifact')
    original=source.read_bytes()
    subprocess.run([str(strip),'--strip-debug','-o',str(output),str(source)],check=True)
    stripped=output.read_bytes()
    sections=elf_sections if kind=='elf' else pe_sections
    before, after = sections(original), sections(stripped)
    for name,s in before.items():
        if s['execute'] and (name not in after or content(original,s)!=content(stripped,after[name])):
            raise ValueError('Stripping changed executable code')
    if kind=='elf':
        for name,s in before.items():
            if s['allocated'] and (name not in after or content(original,s)!=content(stripped,after[name])):
                raise ValueError('Stripping changed an allocated ELF section')
    final, proof=normalize(stripped,kind);output.write_bytes(final)
    proof.update(original_sha256=hashlib.sha256(original).hexdigest(),packaged_sha256=hashlib.sha256(final).hexdigest(),
                 debug_information_removed=True)
    return proof
