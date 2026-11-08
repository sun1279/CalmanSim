"""Explicit analysis policy, XYZ correction, reference selection and sample aggregation."""
from dataclasses import dataclass, asdict, replace
import math
import numpy as np
from .color_math import validate_config


@dataclass(frozen=True)
class MeasurementOptions:
    black_reference: str = 'manual'
    white_reference: str = 'manual'
    include_black: bool = False
    gamma_metric: str = 'point'
    sample_count: int = 1
    sample_interval: float = 0.0
    end_black: bool = False
    correction_name: str = ''
    correction_matrix: tuple = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    correction_offset: tuple = (0., 0., 0.)
    legacy: bool = False

    def __post_init__(self):
        if self.black_reference not in ('manual', 'measured', 'auto') or self.white_reference not in ('manual', 'measured'):
            raise ValueError('无效黑白参考来源')
        if self.gamma_metric not in ('point', 'absolute', 'effective'):
            raise ValueError('无效 Gamma 指标')
        if any(type(v) is not bool for v in (self.include_black, self.end_black, self.legacy)):
            raise ValueError('无效测量开关')
        if type(self.sample_count) is not int or not 1 <= self.sample_count <= 100:
            raise ValueError('每点采样次数必须为 1..100')
        if not math.isfinite(self.sample_interval) or not 0 <= self.sample_interval <= 60:
            raise ValueError('采样间隔必须为 0..60 秒')
        matrix = np.asarray(self.correction_matrix, dtype=float)
        offset = np.asarray(self.correction_offset, dtype=float)
        if matrix.shape != (3, 3) or offset.shape != (3,) or not np.isfinite(matrix).all() or not np.isfinite(offset).all():
            raise ValueError('校正要求有限的 3×3 矩阵和三项偏移')
        if not isinstance(self.correction_name, str) or len(self.correction_name) > 200:
            raise ValueError('无效校正名称')
        object.__setattr__(self, 'correction_matrix', tuple(tuple(row) for row in matrix.tolist()))
        object.__setattr__(self, 'correction_offset', tuple(offset.tolist()))

    def to_dict(self):
        return asdict(self)


def correct_xyz(xyz, options):
    raw = np.asarray(xyz, dtype=float)
    if raw.shape != (3,) or not np.isfinite(raw).all():
        raise ValueError('读数必须为三个有限 XYZ')
    corrected = np.asarray(options.correction_matrix) @ raw + options.correction_offset
    if not np.isfinite(corrected).all():
        raise ValueError('XYZ 校正结果溢出')
    return corrected.tolist()


def resolve_references(config, records, options):
    black_mode = options.black_reference
    if black_mode == 'auto':
        black_mode = 'measured' if config.gamma == 'bt1886' else 'manual'
    modes = {'black': black_mode, 'white': options.white_reference}
    values = {'black': config.black, 'white': config.white}
    missing = []
    for key, mode in modes.items():
        if mode == 'measured':
            record = next((r for r in records if r['pattern']['id'] == key), None)
            if record is None:
                missing.append(key)
            else:
                xyz = correct_xyz(record['raw_xyz'], options)
                if min(record['raw_xyz']) < 0 or any(min(s['xyz'])<0 for s in record.get('samples',[])) or min(xyz) < 0 or (key == 'white' and xyz[1] <= 0):
                    missing.append(key)
                else:
                    values[key] = xyz[1]
    state = {'ready': not missing, 'sources': modes, 'missing': missing,
             'black': values['black'] if 'black' not in missing else None,
             'white': values['white'] if 'white' not in missing else None, 'message': ''}
    if missing:
        state['message'] = '等待实测参考：' + '、'.join('黑场' if k == 'black' else '白场' for k in missing)
        return None, state
    if values['black'] >= values['white']:
        state.update(ready=False, message='参考无效：黑场必须低于白场')
        return None, state
    return validate_config(replace(config, **values)), state


def aggregate_samples(samples):
    if not samples:
        raise ValueError('缺少采样')
    values = np.asarray([s['xyz'] for s in samples], dtype=float)
    if values.shape != (len(samples), 3) or not np.isfinite(values).all():
        raise ValueError('采样 XYZ 无效')
    # Negative readings are retained, never silently filtered away.
    return {'xyz': values.mean(axis=0).tolist(), 'sample_count': len(samples),
            'xyz_stddev': values.std(axis=0, ddof=1 if len(samples) > 1 else 0).tolist()}
