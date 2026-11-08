"""Cancellable serial measurement pipeline and atomic, versioned session files."""
from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from functools import wraps
import csv
import copy
import json
import math
import os
import tempfile
import threading
import uuid
import numpy as np
from .color_math import (TargetConfig,resolve_pattern,calculate_delta_e,xyz_to_xyY,
    calculate_gamut_coverage,calculate_point_gamma,calculate_gamma_metrics,calculate_target,signal_range,validate_config,
    calculate_cct,calculate_rgb_balance)
from .patterns import GAMUT_BOUNDARY_IDS,PATTERN_MAP
from .measurement import MeasurementOptions,correct_xyz,resolve_references,aggregate_samples


def utc_now(): return datetime.now(timezone.utc).isoformat()


def session_locked(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self._lock:
            return method(self,*args,**kwargs)
    return call


def atomic_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
            temp=Path(f.name)
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp and temp.exists(): temp.unlink()


@dataclass
class Session:
    config: TargetConfig
    simulated: bool
    patterns: list
    metadata: dict=field(default_factory=dict)
    records: list=field(default_factory=list)
    status: str='ready'
    error: str|None=None
    id: str=field(default_factory=lambda:uuid.uuid4().hex)
    created_at: str=field(default_factory=utc_now)
    save_path: Path|None=None
    protocol_log: dict=field(default_factory=dict)
    options: MeasurementOptions=field(default_factory=MeasurementOptions)
    _lock: object=field(default_factory=threading.RLock,init=False,repr=False,compare=False)

    @session_locked
    def recalculate(self):
        key=(self.config,self.options,tuple((r['pattern']['id'],r['pattern']['type'],tuple(r['codes']),tuple(r['raw_xyz']),
              all(min(s['xyz'])>=0 for s in r.get('samples',[]))) for r in self.records))
        if getattr(self,'_calculation_key',None)==key:
            return self._reference_cache
        effective, state = resolve_references(self.config, self.records, self.options)
        lo, hi = signal_range(self.config)
        for r in self.records:
            r['signal'] = ((np.asarray(r['codes'])-lo)/(hi-lo)).tolist()
            r['analysis_xyz'] = correct_xyz(r['raw_xyz'], self.options)
            r['reference_ready'] = state['ready']
            r['target_xyz'] = calculate_target(r['signal'], effective or self.config).tolist()
            r['delta_e'] = r['delta_e_lc'] = None
            sample_valid = all(min(s['xyz']) >= 0 for s in r.get('samples', []))
            r['measurement_valid']=sample_valid and min(r['raw_xyz']) >= 0 and min(r['analysis_xyz']) >= 0
            if effective and r['measurement_valid']:
                r['delta_e'] = calculate_delta_e(r['analysis_xyz'], r['target_xyz'], effective)
                if r['analysis_xyz'][1] > 0 and r['target_xyz'][1] > 0:
                    r['delta_e_lc'] = calculate_delta_e(r['analysis_xyz'], r['target_xyz'], effective, True)
        self._calculation_key=key; self._reference_cache=(effective,state); self._summary_cache=None
        return effective, state

    @session_locked
    def to_dict(self):
        summary = self.summary()
        return {'schema_version':2,'id':self.id,'created_at':self.created_at,
                'config':self.config.to_dict(),'simulated':self.simulated,'patterns':self.patterns,
                'metadata':copy.deepcopy(self.metadata),'records':copy.deepcopy(self.records),'status':self.status,
                'error':self.error,'protocol_log':copy.deepcopy(self.protocol_log),'options':self.options.to_dict(),'summary':summary}

    @session_locked
    def save(self,path=None):
        if path is not None: self.save_path=Path(path)
        if self.save_path: atomic_json(self.save_path,self.to_dict())

    @classmethod
    def load(cls,path):
        # File size and numerical validation prevent unbounded/malformed imports.
        path=Path(path)
        if path.stat().st_size>50_000_000: raise ValueError('会话文件不能超过 50 MB')
        def invalid(v): raise ValueError(f'非法 JSON 数值：{v}')
        d=json.loads(path.read_text(encoding='utf-8-sig'),parse_constant=invalid)
        if type(d.get('schema_version')) is not int or d['schema_version'] not in (1,2) or type(d.get('simulated')) is not bool: raise ValueError('会话版本或模式无效')
        options = (MeasurementOptions(include_black=True, gamma_metric='effective', legacy=True)
                   if d['schema_version'] == 1 else MeasurementOptions(**d['options']))
        config=dict(d['config']); config['white_xy']=tuple(config['white_xy'])
        c=TargetConfig(**config); validate_config(c)
        metadata=d.get('metadata',{})
        if not isinstance(metadata,dict): raise ValueError('会话元数据无效')
        settle=metadata.get('settle_seconds',.5); window=metadata.get('window_percent',100)
        if not isinstance(settle,(int,float)) or not math.isfinite(settle) or not 0<=settle<=60:
            raise ValueError('会话稳定等待无效')
        if type(window) is not int or not 1<=window<=100: raise ValueError('会话色块大小无效')
        if d.get('status') not in ('ready','running','completed','cancelled','failed'): raise ValueError('会话状态无效')
        patterns=d['patterns']; records=d['records']
        if not isinstance(patterns,list) or len(patterns)>10000 or not isinstance(records,list) or len(records)>10000:
            raise ValueError('会话点数无效')
        ids=set()
        for p in patterns:
            resolve_pattern(p,c)
            if not isinstance(p.get('id'),str) or p['id'] in ids: raise ValueError('Pattern ID 重复或无效')
            ids.add(p['id'])
        record_ids=set(); planned={p['id']:p for p in patterns}
        for r in records:
            if r['pattern']['id'] not in ids: raise ValueError('记录不属于此会话')
            key=r['pattern']['id']
            if key in record_ids or r['pattern']!=planned[key]: raise ValueError('重复记录或 Pattern 定义不一致')
            record_ids.add(key)
            xyz=np.asarray(r['raw_xyz'],dtype=float)
            if xyz.shape!=(3,) or not np.isfinite(xyz).all(): raise ValueError('记录 XYZ 无效')
            expected=resolve_pattern(r['pattern'],c)
            if list(expected['codes'])!=r['codes']: raise ValueError('记录码值和配置不一致')
            samples=r.get('samples', [])
            if not isinstance(samples,list) or len(samples)>100: raise ValueError('记录采样数无效')
            if samples:
                aggregate=aggregate_samples(samples)
                if not np.allclose(aggregate['xyz'],xyz,rtol=1e-12,atol=1e-12):
                    raise ValueError('原始 XYZ 与采样均值不一致')
                r['sample_count']=aggregate['sample_count']; r['xyz_stddev']=aggregate['xyz_stddev']
        session=cls(config=c,simulated=d['simulated'],patterns=patterns,metadata=metadata,
                   records=records,status=d['status'],error=d.get('error'),id=d['id'],
                   created_at=d['created_at'],save_path=path,protocol_log=d.get('protocol_log',{}),options=options)
        session.recalculate()
        return session

    @session_locked
    def summary(self):
        effective, reference = self.recalculate()
        if getattr(self,'_summary_cache',None) is not None:
            return copy.deepcopy(self._summary_cache)
        valid=[r for r in self.records if r.get('delta_e')]
        stats=[r for r in valid if self.options.include_black or any(v>1e-12 for v in r['signal'])]
        de=[r['delta_e']['de2000'] for r in stats]
        result={'measured_count':len(self.records),'valid_count':len(valid),
                'statistics_count':len(stats),'include_black':self.options.include_black,
                'references':reference,'analysis_config':effective.to_dict() if effective else None,
                'calculation_policy':'legacy-v1' if self.options.legacy else 'calio-sdr-v2',
                'de2000_mean':sum(de)/len(de) if de else None,'de2000_max':max(de) if de else None}
        points={r['pattern']['id']:r for r in valid}
        all_points={r['pattern']['id']:r for r in self.records}
        missing=[k for k in GAMUT_BOUNDARY_IDS if k not in all_points]
        result['gamut_status']='pending' if missing else 'invalid'
        result['gamut_message']='等待完整 RGB/CMY 六色读数' if missing else ''
        if not missing:
            try:
                measured=[]; target=[]
                for key in GAMUT_BOUNDARY_IDS:
                    xyz=np.asarray(all_points[key]['analysis_xyz'],dtype=float)
                    if min(all_points[key]['raw_xyz']) < 0: raise ValueError(f'{key} 原始 XYZ 无效')
                    if any(min(s['xyz'])<0 for s in all_points[key].get('samples',[])): raise ValueError(f'{key} 存在负 XYZ 采样')
                    if xyz.shape!=(3,) or not np.isfinite(xyz).all() or np.any(xyz<0) or xyz[1]<=0:
                        raise ValueError(f'{key} 色度无效，不能计算六点面积')
                    measured.append(xyz_to_xyY(xyz)[:2])
                    target.append(xyz_to_xyY(resolve_pattern(PATTERN_MAP[key],self.config)['target_xyz'])[:2])
                xy=calculate_gamut_coverage(measured,target)
                uv=calculate_gamut_coverage(measured,target,'uv')
                result.update(gamut_xy=xy,gamut_uv=uv,gamut_status='valid')
            except ValueError as e:
                result['gamut_message']=str(e)
        gray=[]
        for r in self.records:
            signal=r['signal']
            if r['pattern']['type']=='rgb' and max(signal)-min(signal)<1e-10:
                metrics=calculate_gamma_metrics(signal[0],r['analysis_xyz'][1],effective) if effective and r['measurement_valid'] else dict(point=None,absolute=None,effective=None)
                rgb_bal=calculate_rgb_balance(r['analysis_xyz'],r['target_xyz'],effective) if effective and r['measurement_valid'] and r['analysis_xyz'][1]>0 and r['target_xyz'][1]>0 else None
                xy=xyz_to_xyY(r['analysis_xyz'])[:2] if r['measurement_valid'] and sum(r['analysis_xyz'])>0 else (None,None)
                cct=calculate_cct(xy) if xy[0] is not None else None
                gray.append({'pattern_id':r['pattern']['id'],'signal':signal[0],'Y':r['analysis_xyz'][1],
                             'target_Y':r['target_xyz'][1] if effective else None,
                             'cct':cct,'rgb_balance':rgb_bal['rgb_percent'] if rgb_bal else None,
                             'gamma':metrics[self.options.gamma_metric],**{'gamma_'+k:v for k,v in metrics.items()}})
        for key in ('point','absolute','effective'):
            values=[r['gamma_'+key] for r in gray if r['gamma_'+key] is not None]
            result['gamma_'+key+'_mean']=sum(values)/len(values) if values else None
        result['gamma_mean']=result['gamma_'+self.options.gamma_metric+'_mean']
        result['gamma_metric']=self.options.gamma_metric
        # Keep the old measured-black/white fit under its existing key for consumers.
        result['gamma_fit']=None
        if 'white' in points:
            w_xy=xyz_to_xyY(points['white']['analysis_xyz'])[:2]
            result['white_cct']=calculate_cct(w_xy) if w_xy[0] is not None else None
        if 'white' in points and 'black' in points:
            white=points['white']['analysis_xyz'][1]; black=points['black']['analysis_xyz'][1]
            result.update(white_nit=white,black_nit=black,contrast=white/black if black>0 else None)
            xs=[]; ys=[]
            for r in valid:
                p=r['pattern']; signal=r['signal']
                if p['type']=='rgb' and max(signal)-min(signal)<1e-10:
                    g=calculate_point_gamma(signal[0],r['analysis_xyz'][1],black,white)
                    if g is not None:
                        xs.append(math.log(signal[0])); ys.append(math.log((r['analysis_xyz'][1]-black)/(white-black)))
            result['gamma_fit']=float(np.dot(xs,ys)/np.dot(xs,xs)) if xs else None
        if self.options.legacy:
            for r in gray:
                r['gamma']=calculate_point_gamma(r['signal'],r['Y'],points['black']['analysis_xyz'][1],points['white']['analysis_xyz'][1]) if 'black' in points and 'white' in points else None
                r['gamma_effective']=r['gamma']
            values=[r['gamma'] for r in gray if r['gamma'] is not None]
            result['gamma_mean']=result['gamma_effective_mean']=sum(values)/len(values) if values else None
        result['grayscale']=sorted(gray,key=lambda p:p['signal'])
        self._summary_cache=copy.deepcopy(result)
        return result

    @session_locked
    def export_csv(self,path):
        self.recalculate()
        # Snapshot results at full precision; companion JSON retains all metadata.
        columns=['Pattern','R_code','G_code','B_code','X','Y','Z','x','y','Target_X','Target_Y','Target_Z',
                 'dE76','dE94','dE2000','dE2000_LC','Simulated','Timestamp','Warnings','Raw_response',
                 'Analysis_X','Analysis_Y','Analysis_Z','Samples','Reference_ready','GammaPoint','GammaAbsolute','GammaEffective','Correction','Calculation_policy']
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        temp=None
        try:
            f=tempfile.NamedTemporaryFile('w',encoding='utf-8-sig',newline='',dir=path.parent,delete=False)
            temp=Path(f.name)
            with f:
                self._write_csv(f,columns)
                f.flush(); os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            if temp and temp.exists(): temp.unlink()

    def _write_csv(self,f,columns):
        writer=csv.writer(f); writer.writerow(columns)
        summary=self.summary()
        gray_by_id={row['pattern_id']:row for row in summary['grayscale']}
        for r in self.records:
            x,y,_=xyz_to_xyY(r['analysis_xyz']); de=r.get('delta_e') or {}; lc=r.get('delta_e_lc') or {}
            state=summary['references']; gray=gray_by_id.get(r['pattern']['id'],{})
            target=r['target_xyz'] if state['ready'] else (None,None,None)
            writer.writerow([r['pattern']['name'],*r['codes'],*r['raw_xyz'],x,y,*target,
                de.get('de76'),de.get('de94'),de.get('de2000'),lc.get('de2000'),self.simulated,
                r['timestamp'],'; '.join(r['warnings']),r['raw_response'],*r['analysis_xyz'],r.get('sample_count',1),
                state['ready'],gray.get('gamma_point'),gray.get('gamma_absolute'),gray.get('gamma_effective'),
                self.options.correction_name,'legacy-v1' if self.options.legacy else 'calio-sdr-v2'])


def run_measurements(session,meter,generator,settle=0.5,size=100,cancel=None,on_point=None,pattern_ids=None):
    if not math.isfinite(settle) or not 0<=settle<=60: raise ValueError('稳定等待必须为 0..60 秒')
    cancel=cancel or threading.Event()
    validate_config(session.config)
    planned=[resolve_pattern(p,session.config) for p in session.patterns if pattern_ids is None or p['id'] in pattern_ids]
    session.status='running'; session.error=None
    try:
        session.save()
        if hasattr(meter,'assert_ready'): meter.assert_ready()
        snapshots={name:device.snapshot() for name,device in (('meter',meter),('generator',generator)) if hasattr(device,'snapshot')}
        previous_snapshot=session.metadata.get('devices')
        if session.records and previous_snapshot and previous_snapshot != snapshots:
            raise ValueError('设备身份或有效配置发生变化，不能续测；请新建会话')
        session.metadata['devices']=snapshots
        session.metadata['meter_health']=copy.deepcopy(getattr(meter,'health',{}))
        generator.configure_output(session.config)
        if hasattr(generator,'output_state'):
            session.metadata['generator_output']=generator.output_state
        done={r['pattern']['id'] for r in session.records}
        for resolved in planned:
            if cancel.is_set(): break
            if pattern_ids is None and resolved['pattern']['id'] in done: continue
            lo=16 if session.config.range=='limited' and session.config.bit_depth==8 else 64 if session.config.range=='limited' else 0
            generator.show_pattern(resolved['codes'],size,(lo,lo,lo))
            if cancel.wait(settle): break
            samples=[]
            session.metadata.pop('partial_acquisition',None)
            for sample_index in range(session.options.sample_count):
                if cancel.is_set(): break
                read=meter.measure_xyz()
                xyz=np.asarray(read['xyz'],dtype=float)
                if xyz.shape!=(3,) or not np.isfinite(xyz).all(): raise ValueError('探头读数不是三个有限数值')
                samples.append({'xyz':xyz.tolist(),'raw':read['raw'],'status':read.get('status'),
                                'probe':read.get('probe'),'warnings':read.get('warnings',[]),'timestamp':utc_now()})
                session.metadata['partial_acquisition']={'pattern_id':resolved['pattern']['id'],'codes':list(resolved['codes']),'samples':samples}
                session.save()
                if sample_index+1 < session.options.sample_count and cancel.wait(session.options.sample_interval): break
            if len(samples) != session.options.sample_count: break
            aggregate=aggregate_samples(samples); xyz=np.asarray(aggregate['xyz'])
            warnings=list(dict.fromkeys(resolved['warnings']+[w for s in samples for w in s['warnings']]))
            r={**resolved,'codes':list(resolved['codes']),'raw_xyz':xyz.tolist(),
               'analysis_xyz':correct_xyz(xyz,session.options),'reference_ready':False,
               'samples':samples,'sample_count':len(samples),'xyz_stddev':aggregate['xyz_stddev'],
               'raw_response':'\n'.join(s['raw'] for s in samples),
               'timestamp':utc_now(),'warnings':warnings,
               'delta_e':None,'delta_e_lc':None}
            # Store the completed raw acquisition before derived calculation/persistence.
            with session._lock:
                previous=next((i for i,p in enumerate(session.records) if p['pattern']['id']==r['pattern']['id']),None)
                if previous is None: session.records.append(r)
                else: session.records[previous]=r
                if any(min(s['xyz'])<0 for s in samples): r['warnings'].append('负 XYZ，保留原始读数；不计算色差')
                session.metadata.pop('partial_acquisition',None)
                session.recalculate()
                session.protocol_log={'meter':list(meter.log),'generator':list(generator.log)}
                session.save()
            if on_point: on_point(r)
        done_ids={r['pattern']['id'] for r in session.records}
        all_done=all(p['id'] in done_ids for p in session.patterns)
        session.status='cancelled' if (cancel.is_set() or not all_done) else 'completed'
        if session.options.end_black:
            lo,_=signal_range(session.config)
            generator.show_pattern((lo,lo,lo),100,(lo,lo,lo))
            session.metadata['end_pattern']='black'
    except Exception as e:
        session.status='failed'; session.error=str(e)
        # Close stale transports: a late reply must never become the next reading.
        for device in (meter,generator) if meter is not generator else (meter,):
            try:
                device.connected=False; device.disconnect()
            except Exception: pass
        raise
    finally:
        session.protocol_log={'meter':list(meter.log),'generator':list(generator.log)}
        session.save()
    return session
