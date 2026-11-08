from dataclasses import replace
from pathlib import Path
import csv
import math
import numpy as np
import pytest
from calman_py.color_math import *
from calman_py.patterns import PATTERNS,PATTERN_MAP
from calman_py.presets import GAMUTS


def test_matrix_reference_and_white():
    m=build_rgb_xyz_matrix('rec709',(.3127,.3290))
    assert m==pytest.approx(np.array([[.4123907993,.3575843394,.1804807884],
        [.2126390059,.7151686788,.0721923154],[.0193308187,.1191947798,.9505321522]]),abs=6e-11)
    for g in GAMUTS:
        assert build_rgb_xyz_matrix(g)@np.ones(3)==pytest.approx(xyY_to_xyz((*GAMUTS[g]['white'],1)))


def test_targets_match_existing_export():
    path=Path(__file__).parents[1]/'calman-patterns_Rec.709-sRGB_Power-2.2_8bit-full_quantized.csv'
    with path.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    for p,row in zip(PATTERNS,rows,strict=True):
        r=resolve_pattern(p,TargetConfig())
        assert tuple(r['codes'])==tuple(int(row[k]) for k in ('R_code','G_code','B_code'))
        assert xyz_to_xyY(r['target_xyz'])==pytest.approx([float(row[k]) for k in ('x','y','Y_nit')],abs=1e-10)


@pytest.mark.parametrize('gamma',['power','srgb','bt1886'])
@pytest.mark.parametrize('depth',[8,10])
@pytest.mark.parametrize('range_',['full','limited'])
@pytest.mark.parametrize('gamut',['rec709','p3','rec2020','adobe'])
def test_all_presets_patterns_finite(gamma,depth,range_,gamut):
    c=TargetConfig(gamma=gamma,bit_depth=depth,range=range_,gamut=gamut,black=.01)
    for p in PATTERNS:
        r=resolve_pattern(p,c)
        assert np.isfinite(r['target_xyz']).all()
        assert calculate_delta_e(r['target_xyz'],r['target_xyz'],c)['de2000']==pytest.approx(0,abs=1e-10)


def test_black_gamma_and_quantization():
    c=TargetConfig()
    assert calculate_target([.5]*3,c)[1]==pytest.approx(21.7637640824031)
    assert calculate_target([0]*3,c)[1]==pytest.approx(1e-9,abs=1e-20)
    assert eotf(.5,replace(c,gamma='srgb'))==pytest.approx(.21404114048223255)
    assert eotf(.5,replace(c,gamma='bt1886'))==pytest.approx(.18946457081379978)
    c=replace(c,black=.1,gamma='bt1886')
    assert calculate_target([.5]*3,c)[1]==pytest.approx((.5*(100**(1/2.4)-.1**(1/2.4))+.1**(1/2.4))**2.4)
    assert xyz_to_xyY(calculate_target([1,0,0],c))[:2]==pytest.approx([.64,.33])
    assert quantize_rgb([0,.5,1],replace(c,range='limited'))[0]==(16,126,235)
    assert quantize_rgb([0,.5,1],replace(c,range='limited',bit_depth=10))[0]==(64,502,940)


def test_sharma_ciede2000_reference():
    data=Path(__file__).parents[1]/'calman-simulator/tests/ciede2000-reference.txt'
    rows=[]
    for line in data.read_text().splitlines():
        try: values=[float(v) for v in line.split()]
        except ValueError: continue
        if len(values)==7: rows.append(values)
    assert len(rows)==34
    for v in rows:
        assert delta_e_2000(v[:3],v[3:6])==pytest.approx(v[6],abs=.000051)
        assert delta_e_2000(v[3:6],v[:3])==pytest.approx(v[6],abs=.000051)


def test_coverage_is_intersection_not_area_ratio():
    small=GAMUTS['rec709']['primaries']; large=GAMUTS['rec2020']['primaries']
    for space in ('xy','uv'):
        r=calculate_gamut_coverage(large,small,space)
        assert r['coverage_percent']==pytest.approx(100)
        assert r['area_ratio_percent']>100
        assert calculate_gamut_coverage(small,large,space)['coverage_percent']<100
        assert calculate_gamut_coverage(small[::-1],small,space)['coverage_percent']==pytest.approx(100)
    assert calculate_gamut_coverage([(0,0),(1,0),(0,1)],[(2,2),(3,2),(2,3)])['coverage_percent']==0


def test_invalid_inputs_and_luminance_compensation():
    for c in (TargetConfig(white=0),TargetConfig(exponent=math.nan),TargetConfig(white_xy=(.8,.8))):
        with pytest.raises(ValueError): validate_config(c)
    with pytest.raises(ValueError): quantize_rgb([1,math.inf,0],TargetConfig())
    c=TargetConfig(); target=calculate_target([1]*3,c)
    assert calculate_delta_e(target*2,target,c)['de2000']>0
    assert calculate_delta_e(target*2,target,c,True)['de2000']==pytest.approx(0,abs=1e-9)
    with pytest.raises(ValueError): calculate_delta_e([0,0,0],target,c,True)


def test_luv_ictcp_rgb_balance_and_four_color_matrix():
    c=TargetConfig()
    white=calculate_target([1,1,1],c)
    assert xyz_to_luv(white,white)==pytest.approx([100.0,0.0,0.0],abs=1e-9)
    assert xyz_to_luv([0,0,0],white)==pytest.approx([0.0,0.0,0.0],abs=1e-9)
    gray50=calculate_target([.5,.5,.5],c)
    de=calculate_delta_e(gray50,gray50,c)
    assert de['de76_luv']==pytest.approx(0.0,abs=1e-12)
    assert de['de_itp']==pytest.approx(0.0,abs=1e-12)
    # ICtCp PQ reference: 10000 nits D65 white has I ≈ 1.0, Ct ≈ 0, Cp ≈ 0
    w10k=xyY_to_xyz((.312713,.329016,10000.0))
    ictcp=xyz_to_ictcp(w10k)
    assert ictcp[0]==pytest.approx(1.0,abs=2e-3)
    assert ictcp[1]==pytest.approx(0.0,abs=5e-3)
    assert ictcp[2]==pytest.approx(0.0,abs=5e-3)
    assert delta_e_itp(gray50*1.1,gray50)>0
    # RGB balance on exact target is 100% across R, G, B
    bal=calculate_rgb_balance(gray50,gray50,c)
    assert bal['rgb_percent']==pytest.approx([100.0,100.0,100.0],abs=1e-9)
    assert bal['luminance_error_percent']==pytest.approx(0.0,abs=1e-9)
    bal_110=calculate_rgb_balance(white*1.1,white,c)
    assert bal_110['luminance_error_percent']==pytest.approx(10.0,abs=1e-9)
    assert bal_110['delta_l']==pytest.approx(yn_to_l(1.1)-yn_to_l(1.0),abs=1e-9)
    # Secondaries match resolve_pattern for full-cyan, full-magenta, full-yellow
    sec=calculate_secondaries('rec709')
    for name,sig in (('cyan',[0,1,1]),('magenta',[1,0,1]),('yellow',[1,1,0])):
        assert sec[name]['xy']==pytest.approx(xyz_to_xyY(calculate_target(sig,c))[:2],abs=1e-12)
    # Four-Color Matrix recovers exact reference XYZ from scaled/rotated meter readings
    ref_r=calculate_target([1,0,0],c); ref_g=calculate_target([0,1,0],c); ref_b=calculate_target([0,0,1],c)
    ref_w=ref_r+ref_g+ref_b
    meter_err=np.array([[1.04,-0.02,0.01],[0.01,0.97,-0.01],[-0.01,0.02,1.03]])
    meas_r=meter_err@ref_r; meas_g=meter_err@ref_g; meas_b=meter_err@ref_b; meas_w=meter_err@ref_w
    fcm=calculate_four_color_matrix(ref_w,ref_r,ref_g,ref_b,meas_w,meas_r,meas_g,meas_b)
    for ref,meas in ((ref_w,meas_w),(ref_r,meas_r),(ref_g,meas_g),(ref_b,meas_b)):
        assert apply_four_color_matrix(fcm,meas)==pytest.approx(ref,rel=1e-9,abs=1e-9)
    # CCT for D65 (~6504 K)
    assert 6500<=calculate_cct((.312713,.329016),'mccamy')<=6510
    assert 6500<=calculate_cct((.312713,.329016),'calio')<=6510
    assert round_even(2.5)==2 and round_even(3.5)==4
