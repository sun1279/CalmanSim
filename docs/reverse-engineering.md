# Calman 分析与实现映射

## 分析边界

输入为当前目录已有的 `code/` 反编译工程、`CalIO_Architecture_and_API_Guide.md`、`calman-simulator/` 以及本机安装的 Calman。

本机 dnSpy v6.1.8 GUI 自动化与 EXE 启动器遇到控制台句柄问题。现已验证使用 `dotnet exec dnSpy-net-win64/bin/dnSpy.Console.dll` 可运行原版 CLI；通过该方式重新反编译原安装目录的 CalIO 与其他相关 DLL。初期分析还使用了已有反编译 C# 和 dnSpy 附带 dnlib 的原始 IL 核对。未执行目标程序集。新的样本哈希与导出清单见 `output/analysis/other-dlls/manifest.json`。

设备底层 `Common/calmanv5.dll` 是原生 x86 PE，无 CLR 元数据，dnSpy 无法将它直接还原为 C#。为完成用户要求的设备接口，本次额外使用只读 PE/RTTI 和 Capstone x86 反汇编；没有修改、加载、执行或复制该 DLL 到应用中。

分析样本：

- 文件版本：`5.17.6.007`
- SHA-256：`2A1E909C28FAE2407EA0DA8E7A27CC45DDB58CF198AA67DDA4207DE34177C674`
- 路径：`C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\Common\calmanv5.dll`
- 下列地址为样本首选基址 `0x10000000` 下的 VA，不适用于其他版本。

## 托管调用与计算

| 原文件 | Python 对应 |
|---|---|
| `CalIO/Cal/CalIO/CalMeter.cs` | `devices.CA410.measure_xyz` / `session.run_measurements` |
| `CalIO/Cal/CalIO/CalPatternSource.cs` | `devices.MurideoSIXG.show_pattern` |
| `CalAppPlatform/CalAppPlatform/HardwareFactory.cs` | Python 显式创建独立设备适配器 |
| `CalWinCOM/CalWinCOM/COM_HardwareFactorySource.cs`、`CalMeterCom.cs` | 确认底层 COM 边界；运行时不调用该层 |
| `Colorspace.cs`、`CalculationPoint.cs` | `color_math.calculate_target`、`calculate_delta_e` |
| `GammaPower.cs`、`Gamma_sRGB.cs`、`Gamma_ITU1886.cs` | `color_math.eotf` |
| `Formulas.cs` | XYZ/xyY、Lab、ΔE76/94/2000 |
| `ColorCheckerSource.cs`、`ColorDefinition_XnYnZn.cs`、`Utility.cs` | Pattern 定义、固定 2.2 编码及中点取偶量化 |

色域覆盖率和测得 Gamma 的汇总是本项目明确规定的计算口径，未声称与 Calman 所有报表配置完全一致。原网页数值测试作为移植对照，标准色差数据独立验证 ΔE2000。

## SIX-G 原生驱动证据

RTTI `.?AVMurideogen@@` 的类型描述地址为 `0x10b81dd0`，虚表位于 `0x10a8dce0`。可以复查：

```powershell
.\.venv\Scripts\python.exe tools/inspect_native.py "C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\Common\calmanv5.dll"
.\.venv\Scripts\python.exe tools/inspect_native.py "C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\Common\calmanv5.dll" --address 0x106922a0 --size 0x90
```

| VA | 观察 |
|---|---|
| `0x1068fe70` | 所有字节求和后取低字节的相反数，构成零和校验 |
| `0x1068ff00` | 打开串口；`0x10690011` 使用 115200 baud |
| `0x10690760` | 查询 `0x8063`，请求 11 字节，返回格式/模式索引 |
| `0x10690a60` | 串口写入及读取，原程序使用 5000 ms 等待 |
| `0x106908f0` | ACK 回调检查 13 字节响应的偏移 11 状态：0 成功、1 校验错误、2 非法命令、3 执行失败、4 当前模式无效 |
| `0x10692210` | 输入为 10 bit 时三通道右移 2 位，再进入 8 bit 路径 |
| `0x106922a0` | 18 字节 RGB 色块命令 `0x006c`：前景 RGB、背景 RGB、窗口大小、0 |
| `0x10692610` | 发生器 Pattern ID 命令 `0x0062`；传统 SIX-G 分支选自定义 RGB ID 29 |
| `0x10692150` / `0x106920f0` | `0x0063` / `0x0064` 设置格式与位深索引 |
| `0x10691d20` / `0x10692710` | `0x006f` / `0x007f` 的 SDR/HDR、Dolby 模式控制；SDR 路径传 0 |

发送帧结构由常量写入和校验调用重建为：`AA 00 00`、4 字节小端长度（payload 字节数 + 5）、2 字节小端命令、payload、校验字节。请求查询示例为 `AA0000060000006380006D`。

**证据级别须区分：** 请求组包、命令编号、8 bit 降位路径及 ACK 状态偏移来自静态指令；回复帧沿用相同包头/长度/命令回显及零和校验是本驱动采用的严格约定，尚未取得实机抓包确认。若固件回复不同，驱动会报错，不把未知回复当成成功。传统 SIX-G/GEN2 为目标型号，不扩展宣称支持 SIX-G 8K/Metal 或 SEVEN。

未确定的 HDMI 量化范围标志与分辨率控制未猜测实现；由用户在发生器端预设。Python 的 Full/Limited 决定传输码值和目标计算。

## CA410

通信依据 [Konica Minolta CA410 通信规范 v1.12](https://www.konicaminolta.jp/instruments/media/ca-410_communicationspecifications_jpn.pdf) 的串口设置、命令流程、测量条件和扩展 XYZ 响应。Python 使用原始 XYZ，完整保留响应与非零 OK 状态警告；错误不会转成有效测量。

## 完成与待验证

完成：独立 Python 计算、两种串口适配器、模拟设备、序列调度、Qt 界面、图表、会话持久化、CSV、取消续测及离线测试。

待实机：CA410 枚举及零校准、低亮度积分超时；SIX-G 固件回复格式、输出格式索引语义、HDMI 码值与窗口比例；逐点与运行中的 Calman 比对。模拟结果仅检验软件流程。

## 页签与测量序列修复核对

直接读取 `C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\CalIO.dll` 得到程序集版本 `5.17.6.7`。`GrayscaleCollection.GetColorspaceIndexFromCollectionIndex` 的 IL 为三次 `ldarg.1; conv.u2` 后调用 `Colorspace.EncodeRGB_Index`，确认灰阶索引的 R/G/B 相同。`Utility.GetBitFromPercent` 的三参数入口选择 `RoundToEven`；底层 IL 调用 `Math.Round(..., MidpointRounding)`，同时保留先整数百分比取整的另一入口。

本项目据此将灰阶序列显式保存为 `exact_codes`，并记录位深、信号范围；计算目标值时直接反解这些码值，避免先将 6.25% 等信号取整为 6% 再量化。点数包含黑、白两个端点，范围内等间隔取样；17/33 等具体点数属于用户要求的工具行为，不宣称就是 Calman 的全部默认档位。

UI 图表页签现在同时选择测量模式：色域固定白＋RGB＋CMY，灰阶亮度与 Gamma 共用等通道序列，色准使用现有 39 点。不同模式及点数分别保留当前会话，测量任务启动后锁定其 Pattern 快照；切图表不会改变正在发送的序列。

## 六点色域面积

按用户确认的口径，面积统计由 RGB 三角形改为六色实测点按 R→Y→G→C→B→M 连线的有序多边形。该定义是本工具要求，不宣称复现 Calman 所有色域报表算法。白色仅作为第七个测量点，不作为多边形顶点。

完整面积采用鞋带公式；覆盖率采用六点实测多边形与六点理论目标轮廓的交集。保留向内偏移的 CMY 所形成的凹陷，不取凸包。对凹多边形进行耳切三角剖分，每个三角形再对凸目标轮廓裁剪并累加交集面积，支持裁剪后分成多个不相连的区域。六点缺失、无有效色度、重复顶点或边界自交时不输出面积，也不使用三点结果替代。

xy 与 u′v′ 分别在对应坐标中计算，JSON 汇总保存点数、算法标记、六点坐标、实测/目标/交集面积及两种比例。界面轮廓从同一份六点统计结果绘制；加载旧会话时根据原始六色 XYZ 重算，不沿用文件中的旧三点汇总。
