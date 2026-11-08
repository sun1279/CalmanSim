from collections import deque
from dataclasses import replace
import pytest
from calman_py.protocols import *
from calman_py.devices import CA410,MurideoSIXG
from calman_py.color_math import TargetConfig


MEAS=b'OK00,P1,0,0.312713,0.329016,100.0,+0.01,0.0,95.04492,100.0,108.89166\r'
IDENTITY=b'OK00,CA-410,00840,CA-VP427,Ver.1.00.0000,12345678,\r'
STATE=[b'OK00,00\r',b'OK00,4\r',b'OK00,0\r',b'OK00,60.00\r',b'OK00,2\r',b'OK00,+0.25\r']


class FakeSerial:
    def __init__(self,replies):
        self.replies=deque(replies); self.buffer=b''; self.writes=[]; self.closed=False
    def write(self,data):
        self.writes.append(data); self.buffer=self.replies.popleft(); return len(data)
    def reset_input_buffer(self): self.buffer=b''
    def read_until(self,delimiter,size):
        out=self.buffer; self.buffer=b''; return out
    def read(self,size):
        count=min(size,2); out=self.buffer[:count]; self.buffer=self.buffer[count:]; return out
    def close(self): self.closed=True


def test_ca410_official_mes2_and_status():
    r=parse_ca410_response(MEAS,True)
    assert r['xyz']==[95.04492,100.,108.89166]
    assert parse_ca410_response(MEAS.replace(b'OK00',b'OK04'),True)['warnings']
    for raw in (b'ER10\r',b'OK00,P1,0,1,2\r',MEAS.replace(b'100.0,108',b'nan,108')):
        with pytest.raises(DeviceError): parse_ca410_response(raw,True)
    with pytest.raises(ValueError): build_ca410_command('ZRC\rMES,2')


def test_ca410_setup_zero_measure_and_shutdown():
    transport=FakeSerial([b'OK00\r']*7+[IDENTITY]+STATE+[b'OK00\r']+STATE+[MEAS,b'OK00\r'])
    meter=CA410('test',transport=transport); meter.connect(); meter.zero_calibrate()
    assert meter.measure_xyz()['xyz'][1]==100
    meter.disconnect()
    assert transport.writes[0]==b'COM,1\r'
    assert b'MMS,1\r' in transport.writes
    assert b'ZRC\r' in transport.writes
    assert transport.writes[-2:]==[b'MES,2\r',b'COM,0\r']
    assert meter.identity['serial']=='12345678'
    assert meter.health=={'zero_status':2,'temperature_delta':.25}
    assert transport.closed


def test_native_murideo_golden_query_and_rgb():
    # Literal byte sequence reconstructed from native 0x10690760.
    assert build_murideo_handshake()==bytes.fromhex('aa0000060000006380006d')
    p=build_murideo_rgb((255,0,0),100)
    assert p[:9]==bytes.fromhex('aa00000d0000006c00')
    assert p[9:-1]==bytes([255,0,0,0,0,0,100,0])
    assert sum(p)%256==0
    assert parse_murideo_packet(p,0x6c)==p[9:-1]
    with pytest.raises(DeviceError): parse_murideo_packet(p[:-1]+b'\0')
    with pytest.raises(ValueError): build_murideo_rgb((1023,0,0))


def test_murideo_driver_partial_reads_ack_and_no_10bit_write():
    commands=[0x6f,0x7f,0x63,0x64,0x62,0x6c]
    transport=FakeSerial([build_murideo_packet(0x8063,b'\0')]+
                         [build_murideo_packet(c,b'\0\0\0') for c in commands])
    gen=MurideoSIXG('test',transport=transport); gen.connect()
    with pytest.raises(ValueError): gen.configure_output(TargetConfig(bit_depth=10))
    assert len(transport.writes)==1
    gen.configure_output(TargetConfig()); gen.show_pattern((255,0,0))
    assert transport.writes[-1]==build_murideo_rgb((255,0,0))
    gen.disconnect(); assert transport.closed


def test_murideo_timeout_and_device_error_fail():
    gen=MurideoSIXG('test',transport=FakeSerial([b'']))
    with pytest.raises(DeviceError): gen.connect()
    assert gen.serial is None
    transport=FakeSerial([build_murideo_packet(0x8063,b'\0'),build_murideo_packet(0x6f,b'\0\0\x02')])
    gen=MurideoSIXG('test',transport=transport); gen.connect()
    with pytest.raises(DeviceError,match='状态 2'): gen.configure_output(TargetConfig())


def test_ca410_zero_timeout_closes_stale_transport():
    transport=FakeSerial([b'OK00\r']*7+[IDENTITY]+STATE+[b''])
    meter=CA410('test',transport=transport); meter.connect()
    with pytest.raises(DeviceError): meter.zero_calibrate()
    assert not meter.connected and meter.serial is None and transport.closed


class CommandTransport(FakeSerial):
    def __init__(self,replies):
        super().__init__([]); self.command_replies=replies

    def write(self,data):
        self.writes.append(data)
        self.buffer=self.command_replies.get(data,b'OK00\r' if data[:3] not in (b'IDO',b'STR') else b'ER10\r')
        return len(data)


def ca_replies(zero=2):
    return {b'IDO,0,1\r':IDENTITY,b'STR,3\r':b'OK00,00\r',b'STR,1\r':b'OK00,4\r',
            b'STR,7\r':b'OK00,0\r',b'STR,28\r':b'OK00,60.00\r',
            b'STR,23\r':f'OK00,{zero}\r'.encode(),b'STR,15\r':b'OK00,+0.25\r',b'MES,2\r':MEAS}


@pytest.mark.parametrize('mode,value,expected',[(0,60,b'SCS,0\r'),(2,60,b'SCS,2\r'),
    (3,60,b'SCS,3\r'),(4,59.94,b'SCS,4,59.94\r'),(5,10,b'SCS,5,10.0\r')])
def test_ca410_sync_parameter_forms_and_effective_state(mode,value,expected):
    replies=ca_replies(); replies[b'STR,1\r']=f'OK00,{mode}\r'.encode()
    replies[b'STR,28\r']=f'OK00,{value:.2f}\r'.encode(); replies[b'STR,17\r']=f'OK00,{value:.1f}\r'.encode()
    transport=CommandTransport(replies)
    meter=CA410('test',transport=transport,sync_mode=mode,sync_hz=value,exposure_ms=value)
    meter.connect(); meter.assert_ready()
    assert transport.writes[1]==expected
    assert meter.snapshot()['effective']['sync_mode']==mode


def test_ca410_zero_state_and_external_config_mismatch_block_readiness():
    replies=ca_replies(zero=0); transport=CommandTransport(replies); meter=CA410('test',transport=transport)
    meter.connect()
    with pytest.raises(DeviceError,match='尚未执行零校准'): meter.assert_ready()
    replies[b'STR,23\r']=b'OK00,2\r'; replies[b'STR,7\r']=b'OK00,1\r'
    with pytest.raises(DeviceError,match='speed'): meter.assert_ready()
    assert b'MES,2\r' not in transport.writes


def test_ca410_optional_unsupported_queries_are_unknown_but_timeout_is_fatal():
    transport=CommandTransport({b'MES,2\r':MEAS}); meter=CA410('test',transport=transport)
    meter.connect(); meter.assert_ready()
    assert meter.health['zero_status'] is None
    assert any('未知' in w for w in meter.measure_xyz()['warnings'])
    transport.command_replies[b'STR,3\r']=b''
    with pytest.raises(DeviceError,match='超时'): meter.assert_ready()
    assert not meter.connected and transport.closed


def test_ca410_warning_bits_have_specific_meanings():
    result=parse_ca410_response(MEAS.replace(b'OK00',b'OK71'),True)
    assert len(result['warnings'])==4
    assert any('温度' in w for w in result['warnings'])
    assert any('保证量程' in w for w in result['warnings'])
