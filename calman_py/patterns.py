"""
内置测试色块库 (Pattern Library)
源自 CalIO.Analytical.Colorspace.ColorCheckerSource (ClassicCC_Source)
包含：
- 白场 / 黑场 / 10%–90% 灰阶 (Signal & Grayscale)
- 100% RGB / CMY 原色与二次色 (Primary & Secondary)
- Classic 色块集合：22 个 (4 个相对亮度灰 + 18 个经典色块)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional

CLASSIC_COLORCHECKER_RAW = [
    ("dark-skin", "Dark Skin", "深肤色", 0.405119, 0.362530, 0.096774),
    ("light-skin", "Light Skin", "浅肤色", 0.379756, 0.357031, 0.353705),
    ("blue-sky", "Blue Sky", "蓝天", 0.249396, 0.266854, 0.189130),
    ("foliage", "Foliage", "叶绿", 0.338784, 0.433265, 0.132836),
    ("blue-flower", "Blue Flower", "蓝花", 0.267688, 0.253140, 0.235775),
    ("bluish-green", "Bluish Green", "蓝绿", 0.261653, 0.359045, 0.425252),
    ("orange", "Orange", "橙色", 0.512087, 0.410373, 0.287229),
    ("purplish-blue", "Purplish Blue", "紫蓝", 0.213095, 0.186377, 0.115692),
    ("moderate-red", "Moderate Red", "中红", 0.461291, 0.312073, 0.187204),
    ("purple", "Purple", "紫色", 0.288075, 0.217532, 0.064716),
    ("yellow-green", "Yellow Green", "黄绿", 0.378520, 0.496473, 0.436288),
    ("orange-yellow", "Orange Yellow", "橙黄", 0.473379, 0.443246, 0.433456),
    ("blue", "Blue", "蓝色", 0.186955, 0.133934, 0.060722),
    ("green", "Green", "绿色", 0.306493, 0.495107, 0.234403),
    ("red", "Red", "红色", 0.547377, 0.317462, 0.114731),
    ("yellow", "Yellow", "黄色", 0.447920, 0.475618, 0.597462),
    ("magenta", "Magenta", "品红", 0.371346, 0.241770, 0.187509),
    ("cyan", "Cyan", "青色", 0.196190, 0.266985, 0.193415),
]

PATTERNS: List[Dict[str, Any]] = [
    {"id": "white", "name": "White", "label": "100% 白场", "type": "rgb", "group": "signal", "rgb": (1.0, 1.0, 1.0)},
    {"id": "black", "name": "Black", "label": "0% 黑场", "type": "rgb", "group": "signal", "rgb": (0.0, 0.0, 0.0)},
    *[
        {
            "id": f"gray-{n}",
            "name": f"{n}% Gray",
            "label": f"{n}% 信号灰阶",
            "type": "rgb",
            "group": "signal",
            "rgb": (n / 100.0, n / 100.0, n / 100.0),
        }
        for n in (10, 20, 30, 40, 50, 60, 70, 80, 90)
    ],
    *[
        {
            "id": f"full-{pid}",
            "name": f"100% {pname}",
            "label": f"100% {plabel}",
            "type": "rgb",
            "group": "primary",
            "rgb": rgb,
        }
        for pid, pname, plabel, rgb in (
            ("red", "Red", "红 (R)", (1.0, 0.0, 0.0)),
            ("green", "Green", "绿 (G)", (0.0, 1.0, 0.0)),
            ("blue", "Blue", "蓝 (B)", (0.0, 0.0, 1.0)),
            ("cyan", "Cyan", "青 (C)", (0.0, 1.0, 1.0)),
            ("magenta", "Magenta", "品红 (M)", (1.0, 0.0, 1.0)),
            ("yellow", "Yellow", "黄 (Y)", (1.0, 1.0, 0.0)),
        )
    ],
    *[
        {
            "id": f"cc-gray-{n}",
            "name": f"Gray {n}",
            "label": f"{n}% 相对白亮度灰",
            "type": "relativeGray",
            "group": "classic",
            "Yn": n / 100.0,
        }
        for n in (35, 50, 65, 80)
    ],
    *[
        {
            "id": f"cc-{cid}",
            "name": cname,
            "label": clabel,
            "type": "xyYn",
            "group": "classic",
            "x": x,
            "y": y,
            "Yn": Yn,
        }
        for cid, cname, clabel, x, y, Yn in CLASSIC_COLORCHECKER_RAW
    ],
]

PATTERN_MAP: Dict[str, Dict[str, Any]] = {p["id"]: p for p in PATTERNS}

# Boundary order differs from acquisition order (white, RGB, CMY).
GAMUT_BOUNDARY_IDS = ('full-red','full-yellow','full-green','full-cyan','full-blue','full-magenta')


def get_pattern_by_id(pattern_id: str) -> Optional[Dict[str, Any]]:
    return PATTERN_MAP.get(pattern_id)


GRAYSCALE_SWEEP_IDS: List[str] = [
    "black",
    "gray-10",
    "gray-20",
    "gray-30",
    "gray-40",
    "gray-50",
    "gray-60",
    "gray-70",
    "gray-80",
    "gray-90",
    "white",
]

GAMUT_SWEEP_IDS: List[str] = [
    "full-red",
    "full-green",
    "full-blue",
    "full-cyan",
    "full-magenta",
    "full-yellow",
    "white",
]

COLORCHECKER_SWEEP_IDS: List[str] = [
    p["id"] for p in PATTERNS if p["group"] == "classic"
]


def build_grayscale_patterns(count, config):
    """Equally spaced signal codes, including black and white (not equal Y)."""
    from .color_math import signal_range
    low, high = signal_range(config)
    if type(count) is not int or not 2 <= count <= high - low + 1:
        raise ValueError(f'灰阶点数要求 2..{high-low+1}，包含黑、白端点')
    patterns = []
    for i in range(count):
        code = low + round(i * (high-low) / (count-1))
        signal = (code-low) / (high-low)
        key = 'black' if i == 0 else 'white' if i == count-1 else f'gray-code-{config.bit_depth}-{config.range}-{code}'
        name = 'Black' if i == 0 else 'White' if i == count-1 else f'{100*signal:.4f}% Gray'
        patterns.append({'id':key,'name':name,'label':f'灰阶 {i+1}/{count} · RGB {code}',
                         'type':'rgb','group':'grayscale','rgb':(signal,signal,signal),
                         'exact_codes':[code,code,code],
                         'code_format':{'bit_depth':config.bit_depth,'range':config.range}})
    return patterns


def build_measurement_patterns(mode, config, gray_count=17):
    if mode == 'grayscale':
        return build_grayscale_patterns(gray_count, config)
    if mode == 'gamut':
        return [dict(PATTERN_MAP[k]) for k in ('white','full-red','full-green','full-blue',
                                               'full-cyan','full-magenta','full-yellow')]
    if mode == 'accuracy':
        return [dict(p) for p in PATTERNS]
    raise ValueError('未知测量模式')
