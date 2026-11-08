"""SDR calculations following CalculationPoint -> Colorspace in local CalIO.

XYZ is absolute cd/m²; signals are 0..1. Coverage uses ordered 2D polygons.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import numpy as np
from .presets import GAMUTS


@dataclass(frozen=True)
class TargetConfig:
    gamut: str = 'rec709'
    white_xy: tuple[float, float] = (0.312713, 0.329016)
    gamma: str = 'power'
    exponent: float = 2.2
    black: float = 0.0
    white: float = 100.0
    bit_depth: int = 8
    range: str = 'full'
    round_percent: bool = True

    def to_dict(self):
        return asdict(self)


def _vector(v, n=3):
    a = np.asarray(v, dtype=float)
    if a.shape != (n,) or not np.isfinite(a).all():
        raise ValueError(f'要求 {n} 个有限数值')
    return a


def xyY_to_xyz(xyY):
    x, y, Y = _vector(xyY)
    if x < 0 or y <= 0 or x+y > 1+1e-12 or Y < 0:
        raise ValueError('无效 xyY：x≥0，y>0，x+y≤1，Y≥0')
    return np.array([x*Y/y, Y, (1-x-y)*Y/y])


def xyz_to_xyY(xyz, fallback=None):
    a = _vector(xyz)
    if a.sum() <= 0:
        return (*fallback, float(a[1])) if fallback else (None, None, float(a[1]))
    return float(a[0]/a.sum()), float(a[1]/a.sum()), float(a[1])


def xy_to_uv_prime(xy):
    x, y = _vector(xy, 2)
    d = -2*x+12*y+3
    if d <= 0: raise ValueError('无效色度')
    return (4*x/d, 9*y/d)


def build_rgb_xyz_matrix(gamut, white_xy=None):
    g = GAMUTS[gamut] if isinstance(gamut, str) else gamut
    raw = np.column_stack([xyY_to_xyz((*p, 1)) for p in g['primaries']])
    w = xyY_to_xyz((*(white_xy or g['white']), 1))
    scale = np.linalg.solve(raw, w)
    if np.any(scale <= 0): raise ValueError('白点必须位于色域三角形内部')
    return raw @ np.diag(scale)


def validate_config(c):
    if c.gamut not in GAMUTS or c.gamma not in ('power', 'srgb', 'bt1886'):
        raise ValueError('不支持的色域或 Gamma')
    if not all(math.isfinite(v) for v in (c.exponent, c.black, c.white)):
        raise ValueError('参数必须为有限数值')
    if not 0 <= c.black < c.white or not 0 < c.exponent <= 10:
        raise ValueError('要求 0≤黑场<白场，0<Gamma≤10')
    signal_range(c)
    build_rgb_xyz_matrix(c.gamut, c.white_xy)
    return c


def round_even(value):
    low = math.floor(value)
    fraction = value - low
    if abs(fraction - 0.5) < 1e-12:
        return low if low % 2 == 0 else low + 1
    return round(value)


def signal_range(c):
    if c.bit_depth not in (8, 10) or c.range not in ('full', 'limited'):
        raise ValueError('无效信号格式')
    return ((16,235) if c.bit_depth == 8 else (64,940)) if c.range == 'limited' else (0, 2**c.bit_depth-1)


def quantize_rgb(rgb, c):
    a = _vector(rgb)
    if np.any((a < 0) | (a > 1)): raise ValueError('RGB 信号必须在 0..1')
    if c.round_percent: a = np.array([round_even(float(v)*100)/100 for v in a])
    lo, hi = signal_range(c)
    codes = tuple(lo+round_even(float(v)*(hi-lo)) for v in a)
    return codes, (np.array(codes)-lo)/(hi-lo)


def eotf(signal, c):
    v = np.asarray(signal, dtype=float)
    if not np.isfinite(v).all() or np.any((v < 0) | (v > 1)): raise ValueError('无效信号')
    k = c.black/c.white
    if c.gamma == 'bt1886':
        return ((c.white**(1/2.4)-c.black**(1/2.4))*v+c.black**(1/2.4))**2.4/c.white
    linear = np.where(v <= .04045, v/12.92, ((v+.055)/1.055)**2.4) if c.gamma == 'srgb' else v**c.exponent
    return k+(1-k)*linear


def calculate_target(rgb, c):
    validate_config(c)
    a = _vector(rgb); k = c.black/c.white
    unoffset = build_rgb_xyz_matrix(c.gamut,c.white_xy) @ ((eotf(a,c)-k)/(1-k))
    x,y,_ = xyz_to_xyY(unoffset,c.white_xy)
    return xyY_to_xyz((x,y,max(float(unoffset[1])*(1-k)+k,1e-11)*c.white))


def resolve_pattern(p, c):
    validate_config(c); warnings = []
    if p['type'] == 'rgb': rgb = _vector(p['rgb'])
    elif p['type'] in ('xyYn', 'relativeGray'):
        xy = c.white_xy if p['type'] == 'relativeGray' else (p['x'],p['y'])
        linear = np.linalg.solve(build_rgb_xyz_matrix(c.gamut,c.white_xy),xyY_to_xyz((*xy,p['Yn'])))
        if np.any(linear < 0) or max(linear)>1: warnings.append('原始定义超出目标色域，已缩放并裁切')
        rgb = np.clip(linear/max(1,float(max(linear))),0,1)**(1/2.2)
    else: raise ValueError('未知 Pattern 类型')
    if 'exact_codes' in p:
        if p['type'] != 'rgb' or p.get('code_format') != {'bit_depth':c.bit_depth,'range':c.range}:
            raise ValueError('精确码值的信号格式与会话不一致')
        low, high = signal_range(c)
        codes = p['exact_codes']
        if len(codes) != 3 or any(type(v) is not int or not low <= v <= high for v in codes):
            raise ValueError('精确 RGB 码值无效')
        actual = (np.array(codes)-low)/(high-low)
        if not np.allclose(rgb,actual,rtol=0,atol=1e-12):
            raise ValueError('Pattern 信号与精确码值不一致')
        codes = tuple(codes)
    else:
        codes, actual = quantize_rgb(rgb,c)
    return {'pattern':dict(p),'codes':codes,'signal':actual.tolist(),
            'target_xyz':calculate_target(actual,c).tolist(),'warnings':warnings}


def xyz_to_lab(xyz, white):
    a,w = _vector(xyz),_vector(white)
    if np.any(w<=0): raise ValueError('参考白无效')
    r=a/w; f=np.where(r>216/24389,np.cbrt(r),(24389/27*r+16)/116)
    return np.array([116*f[1]-16,500*(f[0]-f[1]),200*(f[1]-f[2])])


def xyz_to_luv(xyz, white):
    a,w = _vector(xyz),_vector(white)
    if np.any(w<=0): raise ValueError('参考白无效')
    def uv(v):
        d = float(v[0]+15*v[1]+3*v[2])
        return (0.0,0.0) if d==0 else (4*float(v[0])/d, 9*float(v[1])/d)
    L = float(xyz_to_lab(a,w)[0]); p,qw = uv(a),uv(w)
    return np.array([L, 13*L*(p[0]-qw[0]), 13*L*(p[1]-qw[1])])


_XYZ_TO_LMS_RAW = np.array([
    [0.400238220153002, 0.707593156246992, -0.080805581487871],
    [-0.226298103801373, 1.165315591308231, 0.045700774541672],
    [0.0, 0.0, 0.918224951158247],
])
_CROSSTALK4 = np.array([
    [0.92, 0.04, 0.04],
    [0.04, 0.92, 0.04],
    [0.04, 0.04, 0.92],
])
_XYZ_TO_LMS = _CROSSTALK4 @ _XYZ_TO_LMS_RAW
_LMS_TO_ICTCP = np.array([
    [0.5, 0.5, 0.0],
    [1.613700009, -3.323396143, 1.709696134],
    [4.378062447, -4.245539799, -0.132522648],
])


def nits_to_pq(nits):
    v = float(nits)
    neg = v < 0
    x = abs(v) / 10000.0
    xm = x ** 0.1593017578125
    res = ((0.8359375 + 18.8515625 * xm) / (1.0 + 18.6875 * xm)) ** 78.84375
    return -res if neg else res


def xyz_to_ictcp(xyz):
    a = _vector(xyz)
    lms = _XYZ_TO_LMS @ a
    pq_lms = np.array([nits_to_pq(v) for v in lms])
    return _LMS_TO_ICTCP @ pq_lms


def delta_e_itp(measured_xyz, target_xyz):
    m = xyz_to_ictcp(measured_xyz)
    t = xyz_to_ictcp(target_xyz)
    dI, dCt, dCp = m - t
    return float(720.0 * math.sqrt(dI*dI + 0.25*dCt*dCt + dCp*dCp))


def yn_to_l(yn):
    v = float(yn)
    num = abs(v)
    num2 = math.cbrt(num) if num > 216/24389 else (24389/27*num + 16.0)/116.0
    num3 = num2 * 116.0 - 16.0
    return num3 if v > 0.0 else -num3


def calculate_rgb_balance(measured_xyz, target_xyz, config):
    validate_config(config)
    m_xyz = _vector(measured_xyz)
    t_xyz = _vector(target_xyz)
    inv_m = np.linalg.inv(build_rgb_xyz_matrix(config.gamut, config.white_xy))
    m_norm = m_xyz / config.white
    t_norm = t_xyz / config.white
    linear_rgb = inv_m @ m_norm
    target_linear_rgb = inv_m @ t_norm
    if t_norm[1] > 0:
        comp_t_norm = t_norm * (m_norm[1] / t_norm[1])
    else:
        comp_t_norm = t_norm
    comp_target_rgb = inv_m @ comp_t_norm
    rgb_percent = [100.0 + yn_to_l(linear_rgb[i]) - yn_to_l(comp_target_rgb[i]) for i in range(3)]
    rgb_absolute_percent = [100.0 + yn_to_l(linear_rgb[i]) - yn_to_l(target_linear_rgb[i]) for i in range(3)]
    return {
        'linear_rgb': linear_rgb.tolist(),
        'target_linear_rgb': target_linear_rgb.tolist(),
        'rgb_percent': rgb_percent,
        'rgb_absolute_percent': rgb_absolute_percent,
        'luminance_error_percent': float((m_xyz[1] / t_xyz[1] - 1.0) * 100.0) if t_xyz[1] > 0 else None,
        'delta_l': float(yn_to_l(m_norm[1]) - yn_to_l(t_norm[1])),
    }


def calculate_secondaries(gamut, white_xy=None):
    matrix = build_rgb_xyz_matrix(gamut, white_xy)
    result = {}
    for name, stim in (
        ('red', (1,0,0)), ('green', (0,1,0)), ('blue', (0,0,1)),
        ('cyan', (0,1,1)), ('magenta', (1,0,1)), ('yellow', (1,1,0)),
        ('white', (1,1,1)),
    ):
        xyz = matrix @ np.asarray(stim, dtype=float)
        x, y, Yn = xyz_to_xyY(xyz)
        u, v = xy_to_uv_prime((x, y))
        result[name] = {'xy': (x, y), 'x': x, 'y': y, 'Yn': Yn, 'u_prime': u, 'v_prime': v, 'xyz': xyz.tolist()}
    return result


def calculate_four_color_matrix(ref_white, ref_red, ref_green, ref_blue,
                                meas_white, meas_red, meas_green, meas_blue):
    """Port of Cal.CalIO.FourColorMatrix.CalculateMatrixDBL (ASTM E2214 / CIE 1982)."""
    def norm_xyz(v):
        a = _vector(v)
        s = float(a.sum())
        if s <= 0: raise ValueError('四色矩阵输入的 XYZ 和必须大于 0')
        return np.array([a[0]/s, a[1]/s, 1.0 - a[0]/s - a[1]/s])
    def gen_rgb_matrix(r, g, b, w):
        cols = np.column_stack([norm_xyz(r), norm_xyz(g), norm_xyz(b)])
        if abs(np.linalg.det(cols)) <= 1e-12:
            raise ValueError('四色矩阵原色共线或不可逆')
        scale = np.linalg.solve(cols, norm_xyz(w))
        return cols @ np.diag(scale)
    rw, mw = _vector(ref_white), _vector(meas_white)
    if rw[1] <= 0 or mw[1] <= 0:
        raise ValueError('四色矩阵白场亮度 Y 必须大于 0')
    m_ref = gen_rgb_matrix(ref_red, ref_green, ref_blue, rw)
    m_meas = gen_rgb_matrix(meas_red, meas_green, meas_blue, mw)
    if abs(np.linalg.det(m_meas)) <= 1e-12:
        raise ValueError('实测四色矩阵不可逆')
    arr = m_ref @ np.linalg.inv(m_meas)
    denom = float(arr[1] @ mw)
    if abs(denom) <= 1e-12:
        raise ValueError('四色矩阵白场亮度归一化分母为零')
    arr = arr * (float(rw[1]) / denom)
    return arr.tolist()


def apply_four_color_matrix(matrix, xyz):
    m = np.asarray(matrix, dtype=float)
    if m.shape != (3, 3) or not np.isfinite(m).all():
        raise ValueError('四色校正矩阵必须为有限的 3×3 矩阵')
    v = _vector(xyz)
    return (m @ v).tolist()


def delta_e_76(a,b):
    return float(np.linalg.norm(_vector(a)-_vector(b)))


def delta_e_94(measured,target):
    a,b=_vector(measured),_vector(target); dL,da,db=a-b
    C=math.hypot(b[1],b[2]); dC=math.hypot(a[1],a[2])-C
    return math.sqrt(dL*dL+(dC/(1+.045*C))**2+max(0,da*da+db*db-dC*dC)/(1+.015*C)**2)


def delta_e_2000(measured,target):
    L1,a1,b1=_vector(target); L2,a2,b2=_vector(measured)
    C1,C2=math.hypot(a1,b1),math.hypot(a2,b2); Cbar=(C1+C2)/2
    G=(1-math.sqrt(Cbar**7/(Cbar**7+25**7)))/2
    ap1,ap2=a1*(1+G),a2*(1+G); cp1,cp2=math.hypot(ap1,b1),math.hypot(ap2,b2)
    hp1,hp2=math.degrees(math.atan2(b1,ap1))%360,math.degrees(math.atan2(b2,ap2))%360
    dh=hp2-hp1
    if cp1*cp2==0: dh=0
    elif dh>180: dh-=360
    elif dh < -180: dh+=360
    if cp1*cp2==0: hbar=hp1+hp2
    elif abs(hp1-hp2)<=180: hbar=(hp1+hp2)/2
    elif hp1+hp2<360: hbar=(hp1+hp2+360)/2
    else: hbar=(hp1+hp2-360)/2
    cos=lambda d: math.cos(math.radians(d))
    dL=L2-L1; dC=cp2-cp1; dH=2*math.sqrt(cp1*cp2)*math.sin(math.radians(dh/2))
    Lbar=(L1+L2)/2; cpbar=(cp1+cp2)/2
    T=1-.17*cos(hbar-30)+.24*cos(2*hbar)+.32*cos(3*hbar+6)-.2*cos(4*hbar-63)
    SL=1+.015*(Lbar-50)**2/math.sqrt(20+(Lbar-50)**2); SC=1+.045*cpbar; SH=1+.015*cpbar*T
    RT=-2*math.sqrt(cpbar**7/(cpbar**7+25**7))*math.sin(math.radians(60*math.exp(-((hbar-275)/25)**2)))
    l,c,h=dL/SL,dC/SC,dH/SH
    return math.sqrt(max(0,l*l+c*c+h*h+RT*c*h))


def calculate_delta_e(measured,target,c,compensate_luminance=False):
    a,b=_vector(measured),_vector(target)
    if np.any(a<0) or np.any(b<0): raise ValueError('负 XYZ：保留原始值，色差无效')
    if compensate_luminance:
        if a[1]<=0 or b[1]<=0: raise ValueError('零亮度不能补偿')
        b=b*a[1]/b[1]
    w=xyY_to_xyz((*c.white_xy,c.white))
    lab_a,lab_b=xyz_to_lab(a,w),xyz_to_lab(b,w)
    luv_a,luv_b=xyz_to_luv(a,w),xyz_to_luv(b,w)
    return {'de76':delta_e_76(lab_a,lab_b),'de76_luv':delta_e_76(luv_a,luv_b),
            'de94':delta_e_94(lab_a,lab_b),'de2000':delta_e_2000(lab_a,lab_b),
            'de_itp':delta_e_itp(a,b)}


def calculate_point_gamma(signal,Y,black,white):
    if not 0<signal<1 or not 0<Y-black<white-black: return None
    return math.log((Y-black)/(white-black))/math.log(signal)


def calculate_gamma_metrics(signal, Y, config):
    """Intermediate-point metrics; endpoints and undefined logarithms stay missing."""
    validate_config(config)
    result = dict(point=None, absolute=None, effective=None)
    if not all(math.isfinite(v) for v in (signal, Y)) or not 0 < signal < 1 or Y <= 0:
        return result
    B, W = config.black, config.white
    result['effective'] = calculate_point_gamma(signal, Y, B, W)
    result['absolute'] = math.log(Y / W) / math.log(signal)
    linear = (Y - B) / (W - B)
    if config.gamma == 'bt1886':
        a = (W**(1/2.4) - B**(1/2.4))**2.4
        b = B**(1/2.4) / (W**(1/2.4) - B**(1/2.4))
        denominator = math.log(signal + b)
        point = math.log(Y / a) / denominator if abs(denominator) > 1e-14 else None
    elif linear <= 0:
        point = None
    elif config.gamma == 'srgb':
        # CalIO branches on the measured linear light, including its low-light branch.
        point = (math.log(linear) / math.log((signal + .055) / 1.055)
                 if linear > .0031308 else float(eotf(signal, config)) / linear * 2.4)
    else:
        point = math.log(linear) / math.log(signal)
    result['point'] = max(.1, point) if point is not None and math.isfinite(point) else None
    return {key: value if value is None or math.isfinite(value) else None for key, value in result.items()}


def calculate_cct(xy, method='mccamy'):
    x,y=_vector(xy,2)
    if abs(y-.1858)<1e-12: return None
    if method == 'calio':
        N = (x - 0.332) / (0.1858 - y)
        t = 437.0 * N**3 + 3601.0 * N**2 + 6831.0 * N + 5517.0
    else:
        n = (x - 0.3320) / (y - 0.1858)
        t = -449.0 * n**3 + 3525.0 * n**2 - 6823.3 * n + 5520.33
    return float(t) if 1667<=t<=25000 else None


def _area(poly):
    return abs(_signed_area(poly))


def _signed_area(poly):
    if len(poly)<3: return 0.0
    return float(sum(poly[i][0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*poly[i][1]
                     for i in range(len(poly)))/2)


def _cross(a,b,c):
    ab,ac=b-a,c-a
    return float(ab[0]*ac[1]-ab[1]*ac[0])


def _segments_intersect(a,b,c,d,eps=1e-12):
    ab_c,ab_d=_cross(a,b,c),_cross(a,b,d)
    cd_a,cd_b=_cross(c,d,a),_cross(c,d,b)
    if ((ab_c>eps and ab_d < -eps) or (ab_c < -eps and ab_d>eps)) and \
       ((cd_a>eps and cd_b < -eps) or (cd_a < -eps and cd_b>eps)):
        return True
    def on_segment(p,q,r):
        return np.all(r>=np.minimum(p,q)-eps) and np.all(r<=np.maximum(p,q)+eps)
    return any(abs(cross)<=eps and on_segment(p,q,r) for cross,p,q,r in
               ((ab_c,a,b,c),(ab_d,a,b,d),(cd_a,c,d,a),(cd_b,c,d,b)))


def _validate_polygon(points,label):
    poly=np.asarray(points,dtype=float)
    if poly.ndim!=2 or poly.shape[1]!=2 or len(poly)<3 or not np.isfinite(poly).all():
        raise ValueError(f'{label}要求至少三个有限二维坐标')
    n=len(poly)
    for i in range(n):
        for j in range(i+1,n):
            if np.linalg.norm(poly[i]-poly[j])<=1e-12:
                raise ValueError(f'{label}存在重复色点')
            if j==i+1 or (i==0 and j==n-1): continue
            if _segments_intersect(poly[i],poly[(i+1)%n],poly[j],poly[(j+1)%n]):
                raise ValueError(f'{label}轮廓自交，无法计算色域面积')
    if _area(poly)<=1e-12: raise ValueError(f'{label}面积为零或色点共线')
    return poly


def _triangulate_polygon(poly):
    """Ear clipping preserves concave CMY vertices; it does not take a hull."""
    indices=list(range(len(poly))); triangles=[]
    orient=1 if _signed_area(poly)>0 else -1
    while len(indices)>3:
        for j,current in enumerate(indices):
            previous,following=indices[j-1],indices[(j+1)%len(indices)]
            a,b,c=poly[[previous,current,following]]
            if orient*_cross(a,b,c)<=1e-12: continue
            others=[i for i in indices if i not in (previous,current,following)]
            if any(all(orient*_cross(p,q,poly[i])>=-1e-12 for p,q in ((a,b),(b,c),(c,a))) for i in others):
                continue
            triangles.append(np.array([a,b,c])); indices.pop(j)
            break
        else:
            raise ValueError('六色轮廓无法分割，请检查测量数据')
    if _area(poly[indices])>1e-12: triangles.append(poly[indices])
    return triangles


def _clip_to_convex(subject,target,orient):
    poly=list(subject)
    for i in range(len(target)):
        a,b=target[i],target[(i+1)%len(target)]
        out=[]
        for j in range(len(poly)):
            p,q=poly[j-1],poly[j]
            sp,sq=orient*_cross(a,b,p),orient*_cross(a,b,q)
            if (sp>=-1e-12)!=(sq>=-1e-12): out.append(p+(q-p)*(sp/(sp-sq)))
            if sq>=-1e-12: out.append(q)
        poly=out
    return poly


def calculate_gamut_coverage(measured_xy,target_xy,space='xy'):
    """Intersect an ordered simple measurement polygon with a convex target.

    The measurement workflow supplies six points in R,Y,G,C,B,M order. Concave
    inward secondary points are retained. Three-point callers remain supported.
    """
    measured=_validate_polygon(measured_xy,'实测色域')
    target=_validate_polygon(target_xy,'目标色域')
    if space=='uv':
        measured=np.array([xy_to_uv_prime(p) for p in measured])
        target=np.array([xy_to_uv_prime(p) for p in target])
        measured=_validate_polygon(measured,'实测 u′v′ 色域')
        target=_validate_polygon(target,'目标 u′v′ 色域')
    elif space!='xy': raise ValueError('空间必须为 xy 或 uv')
    target_area,measured_area=_area(target),_area(measured)
    orient=1 if _signed_area(target)>0 else -1
    if any(orient*_cross(target[i-1],target[i],target[(i+1)%len(target)]) < -1e-12 for i in range(len(target))):
        raise ValueError('目标色域轮廓必须为凸多边形')
    # Disjoint clipped pieces are handled as separate triangles, avoiding
    # artificial joining edges when a concave measured polygon is clipped.
    intersection=sum(_area(_clip_to_convex(t,target,orient)) for t in _triangulate_polygon(measured))
    intersection=min(target_area,measured_area,max(0.0,intersection))
    return {'coverage_percent':intersection/target_area*100,
            'area_ratio_percent':measured_area/target_area*100,'space':space,
            'measured_area':measured_area,'target_area':target_area,'intersection_area':intersection,
            'measured_polygon':measured.tolist(),'target_polygon':target.tolist(),
            'point_count':len(measured),'method':'ordered_polygon'}
