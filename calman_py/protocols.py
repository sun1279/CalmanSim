"""CA410 official ASCII protocol and locally inspected SIX-G binary framing.

SIX-G addresses and evidence are documented in docs/reverse-engineering.md.
No Calman library is loaded by the application.
"""
import math
import re
import struct


class DeviceError(RuntimeError):
    pass


class DeviceCommandError(DeviceError):
    def __init__(self, code, text):
        self.code = code
        super().__init__(f'CA410 返回错误：{text}')


def build_ca410_command(command):
    if not re.fullmatch(r'[A-Z]{3}(?:,[0-9.+-]+)*',command):
        raise ValueError('无效 CA410 命令')
    return command.encode('ascii')+b'\r'


def parse_ca410_response(raw, measurement=False):
    try:
        text=raw.decode('ascii').strip() if isinstance(raw,bytes) else raw.strip()
    except UnicodeDecodeError as e:
        raise DeviceError('CA410 响应不是 ASCII') from e
    fields=[x.strip() for x in text.split(',')]
    if re.fullmatch(r'ER\d{2}', fields[0]):
        raise DeviceCommandError(int(fields[0][2:]), text)
    if not re.fullmatch(r'OK\d{2}',fields[0]):
        raise DeviceError(f'CA410 返回错误：{text}')
    status=int(fields[0][2:])
    if status not in (*range(8),*range(64,72)):
        raise DeviceError(f'CA410 未知状态：{fields[0]}')
    warnings=[]
    for bit,message in ((1,'校准/色差参考登记的探头与当前探头不同'),(2,'零校准后温度变化较大，建议重新零校准'),
                        (4,'读数低于保证量程'),(64,'数据处理器电池电量低')):
        if status & bit: warnings.append(f'CA410 {fields[0]}：{message}')
    result={'raw':text,'status':status,'warnings':warnings,'fields':fields[1:]}
    if not measurement: return result
    if len(fields)!=11 or not re.fullmatch(r'P\d{1,2}',fields[1]):
        raise DeviceError('CA410 MES,2 响应不完整或格式错误')
    try:
        xyz=[float(v) for v in fields[8:11]]
    except ValueError as e:
        raise DeviceError('CA410 XYZ 无效') from e
    if not all(math.isfinite(v) for v in xyz): raise DeviceError('CA410 XYZ 不是有限数值')
    result.update(xyz=xyz,probe=fields[1])
    return result


def build_murideo_packet(command,payload=b''):
    if not isinstance(command,int) or not 0<=command<=65535: raise ValueError('命令范围错误')
    payload=bytes(payload)
    if len(payload)>240: raise ValueError('包过长')
    packet=b'\xaa\x00\x00'+struct.pack('<IH',len(payload)+5,command)+payload
    return packet+bytes([(-sum(packet))&255])


def parse_murideo_packet(packet,command=None):
    if len(packet)<10 or packet[:3]!=b'\xaa\x00\x00': raise DeviceError('Murideo 包头错误')
    if struct.unpack_from('<I',packet,3)[0]+5!=len(packet): raise DeviceError('Murideo 包长度错误')
    if sum(packet)&255: raise DeviceError('Murideo 校验和错误')
    actual=struct.unpack_from('<H',packet,7)[0]
    if command is not None and actual!=command: raise DeviceError(f'Murideo 响应命令不匹配：{actual:#x}')
    return packet[9:-1]


def build_murideo_handshake():
    return build_murideo_packet(0x8063,b'\x00')


def build_murideo_rgb(codes,size=100,background=(0,0,0)):
    if len(codes)!=3 or len(background)!=3 or any(type(x)!=int or not 0<=x<=255 for x in (*codes,*background)):
        raise ValueError('SIX-G RGB 要求三个 8 bit 整数码值')
    if not isinstance(size,int) or not 1<=size<=100: raise ValueError('窗口大小必须为 1..100%')
    return build_murideo_packet(0x6c,bytes((*codes,*background,size,0)))
