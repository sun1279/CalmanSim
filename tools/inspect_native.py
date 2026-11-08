"""Read-only RTTI/vtable and x86 disassembly helper; analysis dependencies only."""
import argparse
import struct
from pathlib import Path
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

p = argparse.ArgumentParser()
p.add_argument('binary')
p.add_argument('--class-name', default='Murideogen')
p.add_argument('--address', type=lambda v: int(v, 0))
p.add_argument('--size', type=lambda v: int(v, 0), default=512)
a = p.parse_args()
d = Path(a.binary).read_bytes()
pe = pefile.PE(data=d)
base = pe.OPTIONAL_HEADER.ImageBase
def refs(value):
    needle = struct.pack('<I', value)
    return [i for i in range(len(d)) if d.startswith(needle, i)]
def va(off):
    return base + pe.get_rva_from_offset(off)
if a.address:
    off = pe.get_offset_from_rva(a.address - base)
    for i in Cs(CS_ARCH_X86, CS_MODE_32).disasm(d[off:off+a.size], a.address):
        print(f'{i.address:08x}: {i.mnemonic:8} {i.op_str}')
else:
    off = d.index(f'.?AV{a.class_name}@@'.encode()) - 8
    print('type', hex(va(off)))
    for r in refs(va(off)):
        # CompleteObjectLocator contains pTypeDescriptor at +12.
        col = r - 12
        if struct.unpack_from('<I', d, col)[0] != 0:
            continue
        for v in refs(va(col)):
            print('vtable', hex(va(v+4)))
            print(' '.join(hex(x) for x in struct.unpack_from('<64I', d, v+4)))
