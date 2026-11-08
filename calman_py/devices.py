"""Serial adapters plus a repeatable independent display simulation."""
from dataclasses import replace
import copy
import math
import threading
import time
import numpy as np
import serial
from serial.tools import list_ports
from .protocols import (DeviceError,DeviceCommandError,build_ca410_command,parse_ca410_response,
                       build_murideo_packet,parse_murideo_packet,build_murideo_rgb)
from .color_math import calculate_target,TargetConfig,build_rgb_xyz_matrix,eotf,xyz_to_xyY,xyY_to_xyz
from .presets import SIMULATED_DISPLAY_PROFILES


def available_ports():
    return [(p.device,p.description) for p in list_ports.comports()]


class CA410:
    def __init__(self,port,timeout=30,channel=0,sync_hz=60,speed=0,transport=None,sync_mode=4,exposure_ms=100):
        self.port=port; self.timeout=timeout; self.channel=channel
        self.sync_hz=sync_hz; self.speed=speed; self.serial=transport
        self.lock=threading.RLock(); self.connected=False
        self.log=[]
        self.sync_mode=sync_mode; self.exposure_ms=exposure_ms
        self.identity={}; self.effective_config={}; self.health={}; self.processor=False; self.probe_number=1

    def _exchange(self,command,optional=False):
        with self.lock:
            if not self.serial: raise DeviceError('CA410 未连接')
            data=build_ca410_command(command)
            try:
                self.serial.reset_input_buffer()
                if self.serial.write(data)!=len(data): raise DeviceError('CA410 写入不完整')
                raw=self.serial.read_until(b'\r',4096)
                if not raw.endswith(b'\r'): raise DeviceError('CA410 响应超时或不完整；请重新连接')
                self.log.append({'tx':data.hex(),'rx':raw.hex()})
                return parse_ca410_response(raw,command=='MES,2')
            except (DeviceError,serial.SerialException,OSError) as e:
                if optional and isinstance(e,DeviceCommandError) and e.code in (10,20,91):
                    return None
                self.connected=False
                if self.serial: self.serial.close()
                self.serial=None
                if isinstance(e,DeviceError): raise
                raise DeviceError(f'CA410 通信失败：{e}') from e

    def connect(self):
        if not 0<=self.channel<=99 or not math.isfinite(self.sync_hz) or not .5<=self.sync_hz<=240 or self.speed not in (0,1,2,3,4) or self.sync_mode not in range(6) or not math.isfinite(self.exposure_ms) or not 4<=self.exposure_ms<=4000:
            raise ValueError('无效 CA410 校准通道、同步频率或速度')
        try:
            if self.serial is None:
                self.serial=serial.Serial(self.port,38400,bytesize=7,parity='E',stopbits=2,
                                          rtscts=True,timeout=self.timeout,write_timeout=5)
            sync_command=f'SCS,{self.sync_mode}'
            if self.sync_mode==4: sync_command+=f',{self.sync_hz:.2f}'
            if self.sync_mode==5: sync_command+=f',{self.exposure_ms:.1f}'
            for command in ('COM,1',sync_command,f'FSC,{self.speed}',
                            'TMS,0','MMS,1','MDS,0',f'MCH,{self.channel}'):
                self._exchange(command)
            self.connected=True
            info=self._exchange('IDO,0,1',optional=True)
            if info is not None:
                self.identity=self._parse_identity(info)
                self.processor=self.identity['model'].startswith('CA-DP')
                if self.processor:
                    probe=self._query_number('STR,11',int)
                    if probe is None or not 1<=probe<=10: raise DeviceError('无法确认 CA410 数据处理器的当前探头')
                    self.probe_number=probe
                    probe_info=self._exchange(f'IDO,{probe},1',optional=True)
                    self.identity={'processor':self.identity,'probe':self._parse_identity(probe_info) if probe_info else None}
            self.refresh_state()
            return 'CA410 · 38400 7E2 RTS/CTS · 未实机验证'
        except Exception:
            self.disconnect(); raise

    def zero_calibrate(self):
        if not self.connected: raise DeviceError('请先连接 CA410')
        result=self._exchange('ZRC')
        self.refresh_state()
        return result

    @staticmethod
    def _parse_identity(reply):
        fields=reply['fields']
        if len(fields)!=6 or fields[0]!='CA-410': raise DeviceError('CA410 身份响应格式错误')
        return dict(zip(('product','variation','model','firmware','serial','custom'),fields))

    def _query_number(self,command,convert=float):
        reply=self._exchange(command,optional=True)
        if reply is None: return None
        try:
            if len(reply['fields'])!=1: raise ValueError()
            value=convert(reply['fields'][0])
            if not math.isfinite(value): raise ValueError()
            return value
        except (ValueError,TypeError): raise DeviceError(f'CA410 查询响应无效：{command}')

    def refresh_state(self):
        def query(index,convert=float,scoped=False):
            prefix=f'STR,{self.probe_number},' if scoped and self.processor else 'STR,'
            return self._query_number(prefix+str(index),convert)
        self.effective_config={'channel':query(3,int,True),'sync_mode':query(1,int),'speed':query(7,int)}
        if self.sync_mode==4: self.effective_config['sync_hz']=query(28)
        if self.sync_mode==5: self.effective_config['exposure_ms']=query(17)
        self.health={'zero_status':query(23,int,True),'temperature_delta':query(15,float,True)}
        if self.health['zero_status'] not in (None,0,1,2): raise DeviceError('CA410 零校准状态无效')
        return copy.deepcopy(self.health)

    def assert_ready(self):
        self.refresh_state()
        if self.health['zero_status']==0: raise DeviceError('CA410 尚未执行零校准，请先零校准')
        requested=self.requested_config()
        for key,value in self.effective_config.items():
            if value is not None and not math.isclose(value,requested[key],rel_tol=0,abs_tol=.011):
                raise DeviceError(f'CA410 实际 {key} 与请求配置不一致，请重新连接')

    def requested_config(self):
        values={'channel':self.channel,'sync_mode':self.sync_mode,'speed':self.speed}
        if self.sync_mode==4: values['sync_hz']=self.sync_hz
        if self.sync_mode==5: values['exposure_ms']=self.exposure_ms
        return values

    def snapshot(self):
        return copy.deepcopy({'identity':self.identity,'port':self.port,
                              'requested':self.requested_config(),'effective':self.effective_config})

    def measure_xyz(self):
        if not self.connected: raise DeviceError('请先连接 CA410')
        result=self._exchange('MES,2')
        if self.processor and int(result['probe'][1:])!=self.probe_number:
            raise DeviceError('CA410 返回的探头编号与所选探头不同，请检查数据处理器输出探头设置')
        if self.health.get('zero_status') is None: result['warnings'].append('CA410 零校准状态未知')
        if self.health.get('zero_status')==1: result['warnings'].append('CA410 建议重新零校准')
        return result

    def disconnect(self):
        with self.lock:
            if self.serial:
                transport=self.serial
                try:
                    if self.connected: self._exchange('COM,0')
                finally:
                    transport.close(); self.serial=None; self.connected=False


class MurideoSIXG:
    def __init__(self,port,timeout=5,transport=None):
        self.port=port; self.timeout=timeout; self.serial=transport
        self.connected=False; self.log=[]; self.lock=threading.RLock()
        self.output_state={}

    def snapshot(self):
        return {'port':self.port,'driver':'SIX-G/GEN2 8-bit','identity':None,
                'identity_status':'unknown'}

    def _exact(self,size):
        data=b''; deadline=time.monotonic()+self.timeout
        while len(data)<size and time.monotonic()<deadline:
            chunk=self.serial.read(size-len(data))
            if not chunk: break
            data+=chunk
        if len(data)!=size: raise DeviceError('SIX-G 响应超时或不完整；请重新连接')
        return data

    def _exchange(self,packet,query=False):
        with self.lock:
            if self.serial is None: raise DeviceError('SIX-G 未连接')
            try:
                self.serial.reset_input_buffer()
                if self.serial.write(packet)!=len(packet): raise DeviceError('SIX-G 写入不完整')
                header=self._exact(7)
                size=int.from_bytes(header[3:7],'little')+5
                if not 10<=size<=256: raise DeviceError('SIX-G 响应长度异常')
                response=header+self._exact(size-7)
                self.log.append({'tx':packet.hex(),'rx':response.hex()})
                payload=parse_murideo_packet(response,int.from_bytes(packet[7:9],'little'))
                # Native callback checks byte 11 of a 13-byte setter reply.
                if not query:
                    if len(response)!=13: raise DeviceError('SIX-G ACK 长度错误')
                    if response[11]!=0: raise DeviceError(f'SIX-G 执行失败，状态 {response[11]}')
                return payload
            except (DeviceError,serial.SerialException,OSError) as e:
                self.connected=False
                if self.serial: self.serial.close()
                self.serial=None
                if isinstance(e,DeviceError): raise
                raise DeviceError(f'SIX-G 通信失败：{e}') from e

    def connect(self):
        try:
            if self.serial is None:
                self.serial=serial.Serial(self.port,115200,bytesize=8,parity='N',stopbits=1,
                                          timeout=self.timeout,write_timeout=self.timeout)
            payload=self._exchange(build_murideo_packet(0x8063,b'\0'),query=True)
            if len(payload)!=1 or payload[0]>5: raise DeviceError('SIX-G 查询响应无效')
            self.format_query=payload[0]
            self.connected=True
            return 'Murideo SIX-G · 115200 8N1 · 未实机验证'
        except Exception:
            self.disconnect(); raise

    def configure_output(self,config):
        if not self.connected: raise DeviceError('请先连接 SIX-G')
        if config.bit_depth!=8:
            raise ValueError('当前 SIX-G 驱动仅支持 8 bit 输入；10 bit 仅供模拟/计算')
        # Disable HDR and Dolby Vision, choose RGB and 8-bit output.
        for command in (0x6f,0x7f,0x63,0x64):
            self._exchange(build_murideo_packet(command,b'\0'))
        self.output_state={'requested_range':config.range,'hdmi_range':None,'timing':None,
                           'initial_format_query':self.format_query,
                           'range_source':'panel-preset-unverified','bit_depth':8,'hdr':False,
                           'message':'HDMI 范围与时序由发生器面板预设，未读回验证'}

    def show_pattern(self,codes,size=100,background=(0,0,0)):
        if not self.connected: raise DeviceError('请先连接 SIX-G')
        # Select the custom RGB pattern (native code selects ID 29).
        self._exchange(build_murideo_packet(0x62,bytes([29])))
        self._exchange(build_murideo_rgb(tuple(codes),size,background))

    def disconnect(self):
        with self.lock:
            if self.serial: self.serial.close()
            self.serial=None; self.connected=False


class SimulatedRig:
    """One coupled generator/meter; panel profile stays fixed during a run."""
    def __init__(self,profile='uncalibrated',seed=410):
        self.profile=profile; self.rng=np.random.default_rng(seed)
        self.connected=False; self.config=TargetConfig(); self.codes=None; self.log=[]

    def connect(self):
        self.connected=True
        return '模拟 CA410 + SIX-G（非真实读数）'

    def snapshot(self):
        return {'simulated':True,'profile':self.profile}

    def configure_output(self,config):
        self.config=config

    def show_pattern(self,codes,size=100,background=(0,0,0)):
        if not self.connected: raise DeviceError('模拟设备未连接')
        self.codes=tuple(codes)

    def zero_calibrate(self):
        return {'warnings':['模拟零校准，不会控制真实探头']}

    def measure_xyz(self):
        if not self.connected or self.codes is None: raise DeviceError('没有正在显示的色块')
        from .color_math import signal_range
        lo,hi=signal_range(self.config); signal=(np.array(self.codes)-lo)/(hi-lo)
        p=SIMULATED_DISPLAY_PROFILES[self.profile]
        if self.profile=='reference':
            xyz=calculate_target(signal,self.config)
        else:
            panel={'primaries':p['primaries'],'white':p['white_xy']}
            c=replace(self.config,gamma='power',exponent=p['gamma_exponent'],black=p['black_nit'],white=p['white_nit'])
            k=c.black/c.white; linear=(eotf(signal,c)-k)/(1-k)*p['channel_gains']
            xyz=build_rgb_xyz_matrix(panel)@linear
            x,y,_=xyz_to_xyY(xyz,p['white_xy'])
            xyz=xyY_to_xyz((x,y,float(xyz[1])*(c.white-c.black)+c.black))
            xyz=np.maximum(0,xyz*(1+self.rng.normal(0,p['noise_sigma'],3)))
        return {'xyz':xyz.tolist(),'raw':'SIMULATED','status':0,'warnings':[]}

    def disconnect(self): self.connected=False
