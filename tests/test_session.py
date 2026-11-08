import json
import threading
from pathlib import Path
import numpy as np
import pytest
from calman_py.color_math import TargetConfig
from calman_py.devices import SimulatedRig
from calman_py.patterns import PATTERNS
from calman_py.session import Session,run_measurements,atomic_json


def test_complete_simulation_export_and_reload(tmp_path):
    rig=SimulatedRig('uncalibrated'); rig.connect()
    s=Session(TargetConfig(),True,list(PATTERNS),save_path=tmp_path/'session.json')
    run_measurements(s,rig,rig,settle=0)
    summary=s.summary()
    assert s.status=='completed' and len(s.records)==39
    assert summary['gamma_fit']==pytest.approx(2.08,abs=.002)
    assert 92<summary['gamut_xy']['coverage_percent']<94
    assert summary['de2000_mean']>1
    s.export_csv(tmp_path/'session.csv')
    assert (tmp_path/'session.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    loaded=Session.load(s.save_path)
    assert loaded.summary()==s.summary()
    assert loaded.simulated and all(r['raw_response']=='SIMULATED' for r in loaded.records)


def test_reference_simulation_is_zero_delta(tmp_path):
    rig=SimulatedRig('reference'); rig.connect()
    s=Session(TargetConfig(),True,list(PATTERNS))
    run_measurements(s,rig,rig,settle=0)
    assert s.summary()['de2000_max']==pytest.approx(0,abs=1e-10)
    assert s.summary()['gamut_xy']['coverage_percent']==pytest.approx(100)


def test_cancel_and_resume_no_duplicate_points(tmp_path):
    rig=SimulatedRig(); rig.connect(); cancel=threading.Event()
    s=Session(TargetConfig(),True,list(PATTERNS),save_path=tmp_path/'s.json')
    def stop(r):
        if len(s.records)==3: cancel.set()
    run_measurements(s,rig,rig,settle=0,cancel=cancel,on_point=stop)
    assert s.status=='cancelled' and len(s.records)==3
    assert Session.load(s.save_path).status=='cancelled'
    cancel.clear(); run_measurements(s,rig,rig,settle=0,cancel=cancel)
    assert len(s.records)==39 and len({r['pattern']['id'] for r in s.records})==39


def test_timeout_preserves_previous_read_and_closes_devices(tmp_path):
    class FailingRig(SimulatedRig):
        def measure_xyz(self):
            if self.codes==(0,0,0): raise TimeoutError('test timeout')
            return super().measure_xyz()
    rig=FailingRig(); rig.connect()
    s=Session(TargetConfig(),True,list(PATTERNS),save_path=tmp_path/'s.json')
    with pytest.raises(TimeoutError): run_measurements(s,rig,rig,settle=0)
    assert not rig.connected
    assert len(s.records)==1 and s.status=='failed'
    assert len(Session.load(s.save_path).records)==1


def test_negative_read_preserved_and_marked_invalid():
    class NegativeRig(SimulatedRig):
        def measure_xyz(self): return {'xyz':[-.01,0,0],'raw':'negative','warnings':[]}
    rig=NegativeRig(); rig.connect(); s=Session(TargetConfig(),False,[PATTERNS[0]])
    run_measurements(s,rig,rig,settle=0)
    assert s.records[0]['raw_xyz']==[-.01,0,0] and s.records[0]['delta_e'] is None


def test_atomic_write_failure_preserves_previous_file(tmp_path):
    path=tmp_path/'s.json'; atomic_json(path,{'value':1})
    with pytest.raises(ValueError): atomic_json(path,{'value':float('nan')})
    assert json.loads(path.read_text())=={'value':1}
    assert list(tmp_path.iterdir())==[path]


def test_invalid_session_not_accepted(tmp_path):
    path=tmp_path/'s.json'; path.write_text('{"schema_version":99}')
    with pytest.raises(ValueError): Session.load(path)


def test_failed_single_remeasurement_keeps_old_point(tmp_path):
    rig=SimulatedRig(); rig.connect()
    s=Session(TargetConfig(),True,[PATTERNS[0]],save_path=tmp_path/'s.json')
    run_measurements(s,rig,rig,settle=0)
    previous=s.records[0].copy()
    def fail(): raise TimeoutError('remeasure timeout')
    rig.measure_xyz=fail
    with pytest.raises(TimeoutError): run_measurements(s,rig,rig,settle=0,pattern_ids=['white'])
    assert s.records[0]==previous
    assert Session.load(s.save_path).records[0]['raw_xyz']==previous['raw_xyz']


def test_csv_write_failure_preserves_previous_file(tmp_path,monkeypatch):
    path=tmp_path/'s.csv'; path.write_text('previous')
    s=Session(TargetConfig(),True,[])
    def fail(*args): raise OSError('disk error')
    monkeypatch.setattr(s,'_write_csv',fail)
    with pytest.raises(OSError): s.export_csv(path)
    assert path.read_text()=='previous' and list(tmp_path.iterdir())==[path]


def test_session_rejects_duplicate_records_and_invalid_metadata(tmp_path):
    rig=SimulatedRig(); rig.connect(); path=tmp_path/'s.json'
    s=Session(TargetConfig(),True,[PATTERNS[0]],save_path=path)
    run_measurements(s,rig,rig,settle=0)
    d=json.loads(path.read_text(encoding='utf-8')); d['records']*=2; atomic_json(path,d)
    with pytest.raises(ValueError,match='重复'): Session.load(path)
    d['records']=d['records'][:1]; d['metadata']={'settle_seconds':-1}; atomic_json(path,d)
    with pytest.raises(ValueError): Session.load(path)
