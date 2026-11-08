import copy
import csv
import json
import math
import threading
from dataclasses import replace
import numpy as np
import pytest
from calman_py.color_math import TargetConfig,calculate_gamma_metrics,calculate_target,eotf,resolve_pattern
from calman_py.measurement import MeasurementOptions
from calman_py.patterns import PATTERN_MAP,build_measurement_patterns
from calman_py.session import Session,run_measurements,atomic_json
from calman_py.devices import SimulatedRig


def measured(pattern, config, xyz=None):
    resolved=resolve_pattern(pattern,config)
    return {**resolved,'codes':list(resolved['codes']),'raw_xyz':list(xyz if xyz is not None else resolved['target_xyz']),
            'raw_response':'fixture','warnings':[],'timestamp':'2026-11-07T00:00:00Z'}


@pytest.mark.parametrize('gamma',['power','srgb','bt1886'])
@pytest.mark.parametrize('signal',[.01,.1,.5,.9])
def test_gamma_point_matches_eotf_and_absolute_is_distinct(gamma,signal):
    c=TargetConfig(gamma=gamma,black=0 if gamma=='srgb' else .1)
    Y=float(eotf(signal,c))*c.white
    result=calculate_gamma_metrics(signal,Y,c)
    assert result['point']==pytest.approx(2.2 if gamma=='power' else 2.4)
    assert result['absolute']==pytest.approx(math.log(Y/c.white)/math.log(signal))
    if gamma=='bt1886': assert abs(result['effective']-result['point'])>.01


def test_srgb_low_light_uses_measured_branch_and_invalid_endpoints_stay_missing():
    c=TargetConfig(gamma='srgb')
    result=calculate_gamma_metrics(.02,.1,c)
    assert result['point']==pytest.approx((.02/12.92)/.001*2.4)
    for signal,Y in ((0,0),(1,100),(.5,0),(.5,float('nan'))):
        assert all(v is None for v in calculate_gamma_metrics(signal,Y,c).values())


def test_gamma_arithmetic_mean_is_separate_from_measured_fit():
    c=TargetConfig(); patterns=build_measurement_patterns('grayscale',c,5)
    patterns=[patterns[i] for i in (0,1,3,4)]
    records=[]
    for p,g in zip(patterns,(2.2,2.0,2.6,2.2)):
        s=resolve_pattern(p,c)['signal'][0]
        records.append(measured(p,c,calculate_target([s]*3,replace(c,exponent=g))))
    session=Session(c,True,patterns,records=records)
    result=session.summary()
    assert result['gamma_point_mean']==pytest.approx(2.3)
    assert abs(result['gamma_fit']-2.3)>.2


def test_measured_white_updates_all_targets_without_changing_white_chromaticity_or_codes():
    c=TargetConfig(); patterns=[PATTERN_MAP[k] for k in ('gray-50','white')]
    gray=measured(patterns[0],c)
    s=Session(c,False,patterns,records=[gray],options=MeasurementOptions(white_reference='measured'))
    assert s.summary()['references']['missing']==['white']
    assert gray['delta_e'] is None and not gray['reference_ready']
    codes=copy.deepcopy(gray['codes']); raw=copy.deepcopy(gray['raw_xyz'])
    # A tinted 120 nit white supplies luminance only, not the Lab reference xy.
    s.records.append(measured(patterns[1],c,[100,120,100]))
    result=s.summary()
    assert result['references']['white']==120
    assert gray['codes']==codes and gray['raw_xyz']==raw
    assert gray['target_xyz'][1]==pytest.approx(resolve_pattern(patterns[0],c)['target_xyz'][1]*1.2)
    assert s.records[1]['delta_e']['de2000']>0
    s.options=replace(s.options,white_reference='manual')
    assert s.summary()['references']['white']==100
    assert gray['target_xyz'][1]==pytest.approx(resolve_pattern(patterns[0],c)['target_xyz'][1])


def test_auto_black_never_adds_an_extra_gamut_measurement():
    rig=SimulatedRig('reference'); rig.connect(); c=TargetConfig(gamma='bt1886')
    patterns=build_measurement_patterns('gamut',c)
    s=Session(c,True,patterns,options=MeasurementOptions(black_reference='auto',white_reference='measured'))
    run_measurements(s,rig,rig,settle=0)
    result=s.summary()
    assert len(s.records)==7 and result['references']['missing']==['black']
    assert result['de2000_mean'] is None and result['gamut_xy']['coverage_percent']==pytest.approx(100)
    s.config=replace(c,gamma='srgb')
    assert s.summary()['references']['ready']


def test_black_statistics_and_correction_are_reversible_and_preserve_raw_data(tmp_path):
    c=TargetConfig(); patterns=[PATTERN_MAP[k] for k in ('black','white')]
    s=Session(c,False,patterns,records=[measured(patterns[0],c,[10,10,10]),measured(patterns[1],c)])
    before=copy.deepcopy([r['raw_xyz'] for r in s.records])
    assert s.summary()['statistics_count']==1
    assert s.summary()['de2000_mean']==pytest.approx(0)
    s.options=replace(s.options,include_black=True)
    assert s.summary()['de2000_mean']>0
    s.options=replace(s.options,correction_name='half',correction_matrix=((.5,0,0),(0,.5,0),(0,0,.5)))
    assert s.summary()['white_nit']==50
    path=tmp_path/'corrected.json'; s.save(path); loaded=Session.load(path)
    assert [r['raw_xyz'] for r in loaded.records]==before
    assert loaded.records[1]['analysis_xyz'][1]==50
    loaded.recalculate(); assert loaded.records[1]['analysis_xyz'][1]==50
    loaded.options=MeasurementOptions()
    assert loaded.summary()['white_nit']==100


def test_multi_sample_mean_is_xyz_not_delta_e_and_roundtrips(tmp_path):
    class RepeatingRig(SimulatedRig):
        def __init__(self): super().__init__(); self.read_count=0; self.shows=[]
        def show_pattern(self,codes,*args): self.shows.append(codes); super().show_pattern(codes,*args)
        def measure_xyz(self):
            self.read_count+=1
            return {'xyz':[10,20,30] if self.read_count==1 else [30,40,50], 'raw':str(self.read_count),'status':0,'probe':'P1'}
    rig=RepeatingRig(); rig.connect()
    s=Session(TargetConfig(),True,[PATTERN_MAP['white']],options=MeasurementOptions(sample_count=2),save_path=tmp_path/'s.json')
    run_measurements(s,rig,rig,settle=0)
    assert len(rig.shows)==1
    assert s.records[0]['raw_xyz']==[20,30,40]
    assert s.records[0]['sample_count']==2 and len(s.records[0]['samples'])==2
    assert Session.load(s.save_path).records[0]['xyz_stddev']==pytest.approx([math.sqrt(200)]*3)
    d=json.loads(s.save_path.read_text(encoding='utf8')); d['records'][0]['raw_xyz'][0]=999; atomic_json(s.save_path,d)
    with pytest.raises(ValueError,match='采样均值'): Session.load(s.save_path)


def test_cancel_partial_repeat_keeps_old_point_and_end_black_is_not_a_sample(tmp_path):
    c=TargetConfig(range='limited'); p=PATTERN_MAP['white']; cancel=threading.Event()
    class CancelRig(SimulatedRig):
        def __init__(self): super().__init__('reference'); self.shows=[]
        def show_pattern(self,codes,*args): self.shows.append(codes); super().show_pattern(codes,*args)
        def measure_xyz(self): cancel.set(); return super().measure_xyz()
    rig=CancelRig(); rig.connect(); previous=measured(p,c)
    s=Session(c,True,[p],records=[previous],options=MeasurementOptions(sample_count=3,end_black=True),save_path=tmp_path/'s.json')
    raw=copy.deepcopy(previous)
    run_measurements(s,rig,rig,settle=0,cancel=cancel,pattern_ids=['white'])
    assert s.status=='cancelled' and s.records[0]['raw_xyz']==raw['raw_xyz']
    assert len(s.metadata['partial_acquisition']['samples'])==1
    assert rig.shows[-1]==(16,16,16) and len(s.records)==1
    assert Session.load(s.save_path).metadata['partial_acquisition']['pattern_id']=='white'


def test_legacy_v1_uses_original_statistics_and_measured_gamma_reference(tmp_path):
    rig=SimulatedRig(); rig.connect(); c=TargetConfig()
    s=Session(c,True,build_measurement_patterns('accuracy',c)); run_measurements(s,rig,rig,settle=0)
    data=s.to_dict(); data['schema_version']=1; data.pop('options')
    path=tmp_path/'v1.json'; atomic_json(path,data)
    loaded=Session.load(path); result=loaded.summary()
    assert loaded.options.legacy and result['statistics_count']==39
    assert result['gamma_fit']==pytest.approx(2.08,abs=.002)
    assert result['calculation_policy']=='legacy-v1'
    loaded.export_csv(tmp_path/'legacy.csv')
    with (tmp_path/'legacy.csv').open(encoding='utf-8-sig',newline='') as f:
        exported={row['Pattern']:row for row in csv.DictReader(f)}
    gray=next(r for r in result['grayscale'] if r['gamma_effective'] is not None)
    assert float(exported[PATTERN_MAP[gray['pattern_id']]['name']]['GammaEffective'])==pytest.approx(gray['gamma_effective'])
    loaded.save(); assert json.loads(path.read_text(encoding='utf8'))['schema_version']==2


def test_resume_different_device_state_fails_before_showing_pattern():
    rig=SimulatedRig(); rig.connect(); c=TargetConfig()
    s=Session(c,True,build_measurement_patterns('gamut',c))
    run_measurements(s,rig,rig,settle=0)
    other=SimulatedRig('reference'); other.connect()
    with pytest.raises(ValueError,match='身份或有效配置'): run_measurements(s,other,other,settle=0)
    assert other.codes is None


def test_negative_sample_cannot_be_hidden_by_positive_average_or_used_as_reference():
    c=TargetConfig(); p=PATTERN_MAP['gray-50']
    r=measured(p,c,[1,2,3]); r['samples']=[{'xyz':[-1,2,3]},{'xyz':[3,2,3]}]
    s=Session(c,False,[p],records=[r]); summary=s.summary()
    assert r['delta_e'] is None and summary['grayscale'][0]['gamma'] is None
    r['pattern']=PATTERN_MAP['white']
    s.options=replace(s.options,white_reference='measured')
    assert s.summary()['references']['missing']==['white']


@pytest.mark.parametrize('kwargs',[{'sample_count':0},{'sample_count':True},{'sample_interval':float('nan')},
    {'correction_matrix':[[1,2]]},{'correction_offset':[1,float('inf'),0]},{'black_reference':'unknown'}])
def test_invalid_policy_never_reaches_a_measurement(kwargs):
    with pytest.raises(ValueError): MeasurementOptions(**kwargs)
