from dataclasses import replace
import pytest
import numpy as np
from calman_py.patterns import build_grayscale_patterns,build_measurement_patterns,PATTERNS
from calman_py.color_math import TargetConfig,resolve_pattern,signal_range
from calman_py.devices import SimulatedRig
from calman_py.session import Session,run_measurements


@pytest.mark.parametrize('count',[17,33,65,129])
@pytest.mark.parametrize('depth',[8,10])
@pytest.mark.parametrize('range_',['full','limited'])
def test_grayscale_codes_exact_and_equal_channels(count,depth,range_):
    c=TargetConfig(bit_depth=depth,range=range_,round_percent=True)
    patterns=build_grayscale_patterns(count,c)
    records=[resolve_pattern(p,c) for p in patterns]
    low,high=signal_range(c)
    codes=[r['codes'][0] for r in records]
    assert len(records)==count and len(set(codes))==count
    assert codes[0]==low and codes[-1]==high
    assert patterns[0]['id']=='black' and patterns[-1]['id']=='white'
    assert codes==[low+round(i*(high-low)/(count-1)) for i in range(count)]
    assert all(len(set(r['codes']))==1 for r in records)
    assert [r['codes'] for r in records]==[resolve_pattern(p,replace(c,round_percent=False))['codes'] for p in patterns]
    assert max(np.diff(codes))-min(np.diff(codes))<=1


def test_grayscale_does_not_round_fractional_percent():
    c=TargetConfig()
    patterns=build_grayscale_patterns(33,c)
    assert resolve_pattern(patterns[2],c)['codes']==(16,16,16)
    ordinary={k:v for k,v in patterns[2].items() if k not in ('exact_codes','code_format')}
    assert resolve_pattern(ordinary,c)['codes']==(15,15,15)
    with pytest.raises(ValueError): resolve_pattern(patterns[2],replace(c,range='limited'))


@pytest.mark.parametrize('count',[0,1,257,17.5,True])
def test_invalid_gray_counts(count):
    with pytest.raises(ValueError): build_grayscale_patterns(count,TargetConfig())


def test_gamut_seven_colors_and_accuracy_39():
    c=TargetConfig()
    gamut=build_measurement_patterns('gamut',c)
    assert [resolve_pattern(p,c)['codes'] for p in gamut]==[
        (255,255,255),(255,0,0),(0,255,0),(0,0,255),(0,255,255),(255,0,255),(255,255,0)]
    assert build_measurement_patterns('accuracy',c)==PATTERNS
    assert len(build_measurement_patterns('accuracy',c))==39


def test_gray_cancel_saved_resume_preserves_exact_codes(tmp_path):
    import threading
    c=TargetConfig(); rig=SimulatedRig(); rig.connect(); cancel=threading.Event()
    patterns=build_measurement_patterns('grayscale',c,33)
    s=Session(c,True,patterns,{'measurement_mode':'grayscale','gray_count':33},save_path=tmp_path/'gray.json')
    def stop(_):
        if len(s.records)==4: cancel.set()
    run_measurements(s,rig,rig,settle=0,cancel=cancel,on_point=stop)
    loaded=Session.load(s.save_path)
    assert loaded.status=='cancelled' and loaded.metadata['gray_count']==33
    run_measurements(loaded,rig,rig,settle=0)
    assert len(loaded.records)==33
    assert len({tuple(r['codes']) for r in loaded.records})==33
    assert all(len(set(r['codes']))==1 for r in loaded.records)
    assert loaded.summary()['gamma_fit']==pytest.approx(2.08,abs=.002)
