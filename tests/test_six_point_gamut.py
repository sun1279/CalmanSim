import json
import numpy as np
import pytest
from calman_py.color_math import calculate_gamut_coverage,xyY_to_xyz,xyz_to_xyY,calculate_delta_e,TargetConfig
from calman_py.patterns import build_measurement_patterns
from calman_py.devices import SimulatedRig
from calman_py.session import Session,run_measurements,atomic_json


# R,Y,G,C,B,M: secondaries are initially halfway along the triangle edges.
# RGB triangle area = 0.7*0.7/2 = 0.245. Moving a secondary inward by 0.1
# perpendicular to its opposite axis removes 0.7*0.1/2 = 0.035.
TARGET=np.array([[.8,.1],[.45,.45],[.1,.8],[.1,.45],[.1,.1],[.45,.1]])


@pytest.mark.parametrize('index,point',[(1,(.4,.4)),(3,(.2,.45)),(5,(.45,.2))])
def test_each_secondary_changes_area_with_rgb_fixed(index,point):
    measured=TARGET.copy(); measured[index]=point
    assert np.array_equal(measured[[0,2,4]],TARGET[[0,2,4]])
    r=calculate_gamut_coverage(measured,TARGET)
    assert r['measured_area']==pytest.approx(.210)
    assert r['target_area']==pytest.approx(.245)
    assert r['intersection_area']==pytest.approx(.210)
    assert r['coverage_percent']==pytest.approx(100*.210/.245)
    assert r['area_ratio_percent']==pytest.approx(r['coverage_percent'])
    assert r['point_count']==6 and r['method']=='ordered_polygon'
    assert np.array(r['measured_polygon'])==pytest.approx(measured)


def test_outward_secondary_increases_area_ratio_without_increasing_coverage_above_100():
    measured=TARGET.copy(); measured[1]=(.5,.5)
    r=calculate_gamut_coverage(measured,TARGET)
    assert r['measured_area']==pytest.approx(.280)
    assert r['intersection_area']==pytest.approx(.245)
    assert r['coverage_percent']==pytest.approx(100)
    assert r['area_ratio_percent']==pytest.approx(100*.280/.245)


@pytest.mark.parametrize('space',['xy','uv'])
def test_polygon_winding_does_not_change_area(space):
    measured=TARGET.copy(); measured[1]=(.4,.4)
    r=calculate_gamut_coverage(measured,TARGET,space)
    reversed_=calculate_gamut_coverage(measured[::-1],TARGET[::-1],space)
    assert 0<r['coverage_percent']<100
    assert r['coverage_percent']==pytest.approx(reversed_['coverage_percent'])
    assert r['measured_area']==pytest.approx(reversed_['measured_area'])
    assert calculate_gamut_coverage(TARGET,TARGET,space)['coverage_percent']==pytest.approx(100)


def test_disconnected_intersection_pieces_are_added_without_a_convex_hull():
    # L shape, area 5. A diagonal clipping edge x+y=3.5 leaves two separate
    # right triangles, each with legs 0.5 and area 0.125.
    measured=[(0,0),(3,0),(3,1),(1,1),(1,3),(0,3)]
    target=[(-1,4.5),(4.5,-1),(5,5)]
    r=calculate_gamut_coverage(measured,target)
    assert r['measured_area']==pytest.approx(5)
    assert r['intersection_area']==pytest.approx(.250)


@pytest.mark.parametrize('bad',[
    TARGET[[0,1,2,5,4,3]],  # Crossing boundary.
    [(0,0),(1,0),(2,0)],  # Collinear.
    [(0,0),(1,0),(0,0),(0,1)],  # Duplicate vertex.
    [(0,0),(1,0),(float('nan'),1)],
])
def test_invalid_boundaries_are_rejected(bad):
    with pytest.raises(ValueError): calculate_gamut_coverage(bad,TARGET)


def reference_session():
    c=TargetConfig(); rig=SimulatedRig('reference'); rig.connect()
    s=Session(c,True,build_measurement_patterns('gamut',c))
    run_measurements(s,rig,rig,settle=0)
    return s


def change_xy(s,key,xy):
    r=next(r for r in s.records if r['pattern']['id']==key)
    r['raw_xyz']=xyY_to_xyz((*xy,r['raw_xyz'][1])).tolist()
    r['delta_e']=calculate_delta_e(r['raw_xyz'],r['target_xyz'],s.config)


@pytest.mark.parametrize('key',['full-yellow','full-cyan','full-magenta'])
@pytest.mark.parametrize('space',['xy','uv'])
def test_session_uses_all_six_points(key,space):
    s=reference_session(); before=s.summary()
    xyz=next(r['raw_xyz'] for r in s.records if r['pattern']['id']==key)
    xy=np.array(xyz_to_xyY(xyz)[:2]); white=np.array(s.config.white_xy)
    change_xy(s,key,.8*xy+.2*white)
    after=s.summary(); name='gamut_'+space
    assert after['gamut_status']=='valid'
    assert after[name]['point_count']==6 and len(after[name]['measured_polygon'])==6
    assert after[name]['coverage_percent']<before[name]['coverage_percent']
    assert after[name]['area_ratio_percent']<before[name]['area_ratio_percent']


def test_white_is_not_a_polygon_vertex_and_missing_secondary_has_no_triangle_fallback():
    s=reference_session(); before=s.summary()['gamut_xy']
    change_xy(s,'white',(.4,.4))
    assert s.summary()['gamut_xy']==before
    s.records=[r for r in s.records if r['pattern']['id']!='white']
    assert s.summary()['gamut_xy']==before
    s.records=[r for r in s.records if r['pattern']['id']!='full-cyan']
    assert s.summary()['gamut_status']=='pending'
    assert 'gamut_xy' not in s.summary() and 'gamut_uv' not in s.summary()


def test_zero_luminance_or_crossed_boundary_is_invalid():
    s=reference_session()
    r=next(r for r in s.records if r['pattern']['id']=='full-cyan')
    r['raw_xyz']=[0,0,0]
    assert s.summary()['gamut_status']=='invalid' and 'gamut_xy' not in s.summary()
    s=reference_session()
    c=next(r for r in s.records if r['pattern']['id']=='full-cyan')
    m=next(r for r in s.records if r['pattern']['id']=='full-magenta')
    c['raw_xyz'],m['raw_xyz']=m['raw_xyz'],c['raw_xyz']
    result=s.summary()
    assert result['gamut_status']=='invalid' and '自交' in result['gamut_message']
    assert 'gamut_xy' not in result


def test_legacy_saved_summary_is_recomputed_with_six_points(tmp_path):
    s=reference_session(); path=tmp_path/'legacy.json'; s.save(path)
    d=json.loads(path.read_text(encoding='utf-8'))
    d['summary']['gamut_xy']={'point_count':3,'coverage_percent':42}
    atomic_json(path,d)
    loaded=Session.load(path)
    assert loaded.summary()['gamut_xy']['point_count']==6
    assert loaded.summary()['gamut_xy']['coverage_percent']==pytest.approx(100)
