# 安装版 Gamma / 色准计算核对

2026-11-07 修正状态：下文“当前 Python”描述的是审计时的修正前状态。现已实现三种 Gamma 与算术平均、黑白参考重算、黑场统计开关；原拟合保留为独立指标。使用与兼容规则见 [README-Python.md](../README-Python.md)。设备与采样补充见 [other-dll-audit.md](other-dll-audit.md)。

分析对象：`C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\CalIO.dll`。

- 程序集版本：5.17.6.7。
- MVID：`ae6981a4-7025-4f84-b6a4-152f147bbe0e`。
- SHA-256：`249FBF034BFAB26F9CDAC925BFBC1F09BCC295359B681614495E8F081F47DECE`。
- 通过 dnSpy 附带的 dnlib 直接读取安装 DLL 的元数据和 IL，不执行目标程序集。已有 C# 反编译文件用于阅读，关键调用和分支用安装版 IL 核对。
- 原始 IL 摘录：installed-calio-gamma-colour.il.txt（本地分析产物，不随仓库发布）。本次仅分析，未修改 Python 的计算或界面。

## 结论与优先级

当前 Python 的 SDR 目标路径、共同参考白、ΔE76/94/2000 和亮度补偿的主要公式吻合。需要优先对齐的是 **Gamma 指标的定义、汇总方式、黑白参考值策略，以及黑场是否参与色差统计**。公式一致不能保证不同设置下的报表一致。

| 优先级 | 事项 | 安装版行为 | 当前 Python |
|---|---|---|---|
| 高 | 逐点 Gamma | 同时提供 EOTF 相关的 `GammaPoint` 和直接对数的 `GammaAbsolute` | 只有统一的扣黑有效 Gamma |
| 高 | 汇总 Gamma | `TotalGamma` 是有效中间点 `GammaPoint` 的算术平均 | 对数域通过原点的最小二乘拟合 |
| 高 | 黑白参考值 | 可选择实测或手动，变化时触发目标与结果更新 | Gamma 使用实测黑白；目标与 ΔE 使用手动配置 |
| 中 | 色差统计点集 | `IncludeBlackInAvgMax` 控制黑场是否计入平均/最大 | 所有有效记录均参与 |
| 中 | 比较字段 | 普通 ΔE00、亮度补偿 ΔE00、JND/ICtCp、跨公式 dEMax 等是不同字段 | 明确显示普通 ΔE00 和 ΔE00 LC |

## 1. GammaPoint 与 GammaAbsolute 应分开

核对入口：

- `CalculationPoint.GetGammaPoint`：token `06001271`。
- `CalculationPoint.GetGammaAbsolute`：`06001275`。
- `Formulas.GammaAbsolute`：`060022C5`。
- `GammaBlackCompBase.PointGamma`：`060013FF`。
- `Gamma_sRGB.DoPointGamma`：`0600145C`。
- `Gamma_ITU1886.PointGamma`：`0600144D`。

对中间灰阶，`GammaAbsolute` 直接计算 `log(Y/MaxY) / log(s)`。`GammaPoint` 则交给当前 EOTF 对象：

- Power：先按所选黑白参考做黑位范围转换，再求对数。
- sRGB：高亮分段用 `log(l) / log((s+0.055)/1.055)`，`l` 是黑位范围转换后的线性光；低亮分段另有处理。
- BT.1886：令 `a=(W^(1/2.4)-B^(1/2.4))^2.4`、`b=B^(1/2.4)/(W^(1/2.4)-B^(1/2.4))`，采用 `log(Y/a)/log(s+b)`。

当前工具的公式始终为 `log((Y-B)/(W-B))/log(s)`，定义是扣黑后的有效幂指数。它可保留为独立指标，但不能直接用来替代以上所有 `GammaPoint`。

按安装版公式演算的示例（理论信号 s=0.5，未进行码值量化，不是硬件读数）：

| 完美符合的 EOTF | 黑/白 nit | Y nit | 当前有效 Gamma | 安装版 GammaPoint |
|---|---|---:|---:|---:|
| sRGB | 0 / 100 | 21.404114 | 2.224040 | 2.400000 |
| BT.1886 | 0.1 / 100 | 21.604911 | 2.215819 | 2.400000 |

因此在 sRGB/BT.1886 下看到当前曲线不是常数 2.4，并不能据此判定显示器偏离目标 EOTF。需要核对正在比较的字段。

## 2. TotalGamma 不是 Gamma 拟合

`LuminanceRampCollection.get_TotalGamma`（`060014F9`）遍历已测量点，排除黑、白，求 `GammaPoint` 的算术平均。`get_TotalGammaAbsolute`（`060014FA`）对 `GammaAbsolute` 做相同处理。没有中间有效点时会返回目标值。

Python 的 `session.summary()` 使用 `sum(log(s)*log(l))/sum(log(s)^2)`，会对低信号点赋予更大的隐含权重。例如两个信号点 s=0.25、0.75，逐点指数分别为 2.0、2.6，算术平均为 2.3，对数拟合为 2.024772。

这两种统计回答的问题不同。对齐 Calman 时应新增明确命名的平均指标，原“Gamma 拟合”可以继续单独显示。

## 3. 黑白参考会同时影响目标与色差

`CalculationPoint.UpdateMeasures`（`0600129C`）在读取黑/白场后调用 `SetReadMin` / `SetReadMax`。`Colorspace.OnMaxChanged`（`06001320`）依据 `UseMeasuredWhite` 选择实测或手动白场；`OnMinChanged`（`06001322`）依据 `UseMeasuredBlack` 选择黑位。目标变化后会通知并重新计算。

`BaseOptions.set_GammaFormula`（`060029F8`）还会参考 Gamma 对象的 `UsesMeasuredBlack` 属性：BT.1886 在黑位目标为 0 的相应切换分支启用实测黑位，sRGB 的相应分支关闭实测黑位；Power 对象返回空值，保留选择。不能把“所有 EOTF 永远扣除实测黑场”当成统一的原版行为。

当前 Python 的 Gamma 从实测黑白归一化，而目标 XYZ 和色差参考白仍使用 `TargetConfig.black/white`。这种模式是明确的工具设计，但与启用实测参考的 Calman 会产生可见差异。

例如色度完全正确的 120 nit 白场，对固定 100 nit 目标计算，当前 ΔE00 约为 4.034118；若使用 120 nit 作为会话白场参考并相应更新目标，则该白场的误差为零。

**参考白色度仍是选定目标色域的白点。** `Colorspace.White_X/Y/Z`（`06001308`～`0600130A`）读取目标白点坐标。不能将“使用实测白场亮度”直接解释为“把实测白色度也作为 Lab 参考白”，否则可能掩盖白点色偏。

## 4. 色差内核与 LC 处理

- `Formulas.DeltaE2000`（六参数重载 `06002289`）使用 kL/kC/kH=1。
- `Formulas.DeltaE1994`（`06002285`）使用目标色度作为 C 权重参考，K1=0.045、K2=0.015；当前 Python 的参数方向一致。
- Lab 使用会话归一化 XYZ 与目标参考白。Python 用绝对 XYZ 和相应绝对参考白，二者在比例一致时等价。
- `CalculationPoint.Update_XYZ_Derived`（`0600129F`）将目标 XYZ 等比例缩放到实测亮度，再计算亮度补偿色差。当前 `calculate_delta_e(..., compensate_luminance=True)` 与此处理等价。

普通 ΔE00 包含亮度误差，LC 的含义是去除当前点的亮度差异。LC 不等于只比较 xy，也不能与普通 ΔE00 混用。

比较“最大色差”时应对应具体字段：当前最大 ΔE00 对应 `CollectionMax_dE2000`；原版 `dEMax` 是多种色差公式结果的最大值，不等于当前序列中最大的 ΔE00。

## 5. 平均与最大色差的黑场开关

核对 `XYZ_CalDataSet.CalcAvg`（`06001A8F`）与 `CalcMax`（`06001AAA`）以及 `MeasureCollection.get_DeltaEAverage2000`（`06001550`）：均只统计已设置点，并根据 `IncludeBlackInAvgMax` 决定是否排除黑场。

Python 当前平均和最大 ΔE00 统计全部有效记录，没有对应开关。即使每个点的色差完全相同，点集不同仍会产生不同的平均值；黑场是最大误差点时，最大值也会不同。不同报表必须使用相同的 Pattern 集合比较。

## 6. 目标值与预处理的核对条件

- `CalculationPoint.UpdateTargets`（`060012A4`）从实际 RGB 码值反算信号后生成目标；不能直接把 ColorChecker 原始 xyY 当最终目标。当前 Python 已遵守此路径。
- 内置 SDR ColorChecker 的 RGB 生成使用固定 1/2.2 编码，再按当前 EOTF 生成最终目标；自定义集合可能选择 DynamicGamma。当前 39 点沿用内置 SDR 路径。
- 原版 `UpdateMeasures` 在归一化前可能施加环境光偏移和屏幕 xy/Y 修正；当前工具没有这些修正。与 Calman 对照时应关闭它们或明确记录。
- 原版多处把 NaN 或特殊端点转为零/目标值，`GammaPoint` 还存在 0.1 下限；Python 对无效读数或无法求 Gamma 的端点采用缺失值。没有必要把原版容错输出当成有效测量去复刻。
- 后续若加入 SG Lab 色块，应保留其定义白点 D50；当前 39 点没有使用 SG 集合。

建议下一次功能修改优先处理：GammaPoint/Absolute/平均值区分；实测/手动黑白参考模式与会话重算；黑场参与平均和最大色差的开关。实施前需要按项目约定提交具体改动计划。
