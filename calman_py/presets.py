"""
色域、参考白点与仿真显示面板预设
源自 CalIO.Analytical.Colorspace (Colorspace.cs / CieIlluminants.cs)
"""

from __future__ import annotations
from typing import Dict, Any

WHITE_POINTS: Dict[str, Dict[str, Any]] = {
    "d65": {"id": "d65", "name": "D65 · Calman 标准", "xy": (0.312713, 0.329016)},
    "d50": {"id": "d50", "name": "D50 · 印刷标准", "xy": (0.345669, 0.358496)},
    "cinema": {"id": "cinema", "name": "DCI P3 影院白点", "xy": (0.314000, 0.351000)},
    "d93": {"id": "d93", "name": "D93 · 演播室冷白", "xy": (0.283150, 0.297110)},
}

GAMUTS: Dict[str, Dict[str, Any]] = {
    "rec709": {
        "id": "rec709",
        "name": "Rec.709 / sRGB",
        "primaries": ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)),
        "white": WHITE_POINTS["d65"]["xy"],
    },
    "p3": {
        "id": "p3",
        "name": "P3-D65 (Display P3)",
        "primaries": ((0.68, 0.32), (0.265, 0.69), (0.15, 0.06)),
        "white": WHITE_POINTS["d65"]["xy"],
    },
    "dcip3": {
        "id": "dcip3",
        "name": "DCI-P3 (SMPTE RP 431-2)",
        "primaries": ((0.68, 0.32), (0.265, 0.69), (0.15, 0.06)),
        "white": WHITE_POINTS["cinema"]["xy"],
    },
    "rec2020": {
        "id": "rec2020",
        "name": "ITU-R BT.2020 (Rec.2020)",
        "primaries": ((0.708, 0.292), (0.17, 0.797), (0.131, 0.046)),
        "white": WHITE_POINTS["d65"]["xy"],
    },
    "adobe": {
        "id": "adobe",
        "name": "Adobe RGB (1998)",
        "primaries": ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)),
        "white": WHITE_POINTS["d65"]["xy"],
    },
    "ntsc1953": {
        "id": "ntsc1953",
        "name": "NTSC 1953 原色 / D65",
        "primaries": ((0.67, 0.33), (0.21, 0.71), (0.14, 0.08)),
        "white": WHITE_POINTS["d65"]["xy"],
    },
}

# 用于无物理探头连接时的仿真面板特性配置
SIMULATED_DISPLAY_PROFILES: Dict[str, Dict[str, Any]] = {
    "uncalibrated": {
        "id": "uncalibrated",
        "name": "出厂未校准（偏冷 / Gamma 2.08）",
        "primaries": ((0.630, 0.334), (0.307, 0.588), (0.152, 0.064)),
        "white_xy": (0.3045, 0.3195),
        "gamma_exponent": 2.08,
        "black_nit": 0.08,
        "white_nit": 103.5,
        "channel_gains": (0.96, 0.99, 1.05),
        "noise_sigma": 0.00025,
    },
    "reference": {
        "id": "reference",
        "name": "理想参考（完全跟随目标）",
        "primaries": None,  # 跟随当前目标色域微扰
        "white_xy": None,   # 跟随当前目标白点微扰
        "gamma_exponent": None,
        "black_nit": None,
        "white_nit": None,
        "channel_gains": (1.000, 1.000, 1.000),
        "noise_sigma": 0.00015,
    },
    "wide_unclamped": {
        "id": "wide_unclamped",
        "name": "广色域未限幅（原生 P3）",
        "primaries": ((0.676, 0.321), (0.268, 0.684), (0.151, 0.061)),
        "white_xy": (0.3112, 0.3274),
        "gamma_exponent": 2.22,
        "black_nit": 0.05,
        "white_nit": 101.2,
        "channel_gains": (1.01, 1.00, 0.99),
        "noise_sigma": 0.00020,
    },
    "warm_aged": {
        "id": "warm_aged",
        "name": "老化背光（偏暖 / 黑位抬升）",
        "primaries": ((0.625, 0.338), (0.312, 0.580), (0.155, 0.068)),
        "white_xy": (0.3260, 0.3410),
        "gamma_exponent": 2.34,
        "black_nit": 0.22,
        "white_nit": 88.0,
        "channel_gains": (1.04, 0.99, 0.92),
        "noise_sigma": 0.00030,
    },
}
