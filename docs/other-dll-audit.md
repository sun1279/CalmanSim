# 安装目录其他 DLL 核对

日期：2026-11-07。此次为静态分析和 Python 实现对照，未修改应用代码，未连接或控制硬件。

后续修正状态：下文记录审计当时的实现差异。现已补入可选 XYZ 软件矩阵、CA410 同步/速度及身份和状态查询、重复采样、配置快照、可选结束回黑和旧会话兼容。SIX-G HDMI 范围/时序读回、APL、完整探头匹配与延时自动优化仍未实现，保持明确的验证边界。使用说明见 [README-Python.md](../README-Python.md)。

## 样本与方法

直接读取 `C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\` 中的安装文件，用本地原版 dnSpy 的 CLI 反编译：

```powershell
& 'C:\Program Files\dotnet\dotnet.exe' exec `
  'C:\Projects\CalmanSim\dnSpy-net-win64\bin\dnSpy.Console.dll' `
  --no-color --no-resources --no-resx --no-baml --threads 1 `
  -o 'C:\Projects\CalmanSim\output\analysis\other-dlls\CalWinCOM' `
  'C:\Program Files (x86)\Portrait Displays\Calman 5 for Business\CalWinCOM.dll'
```

共导出 12 个其他程序集，并重新导出 CalIO 以追踪实际调用。13 次导出的退出码均为 0。文件清单、SHA-256、C# 文件数见 manifest.json（本地分析产物，不随仓库发布）。退出码仅表示导出完成，不表示所有方法均已逐一审计。

| DLL | 此次检查的内容 |
|---|---|
| CalAppPlatform | 探头、发生器接口，曝光、同步、单位、硬件能力 |
| CalWinCOM | 以上接口如何传入底层 COM 驱动 |
| Interop.CALMANV5Lib | COM 接口边界，确认哪些操作仍在原生驱动中 |
| CalMAN_Workflow | 插入色块配置、探头匹配流程、设置默认值 |
| CalUI | 设置界面调用入口、延时优化命令、探头 Profile 管理 |
| ClientPlatform | 另一套客户端数学和低亮度处理函数，核对调用关系 |
| Common/CASDK2Net | SDK 托管包装中的探头状态、同步、量程和平均接口 |
| CalBinding、CalErr | 绑定和错误基础结构；本次检查范围内未发现另一套色差内核 |
| Cal_DB_Interface、Cal_DB_Source、Cal_DB_SQLite | 数据与目标配置持久化；本次检查范围内未发现另一套 Gamma/色差内核 |
| CalIO（复核） | 将其他 DLL 暴露的选项追踪至实际显示、采样和计算入口 |

输出位于 `output/analysis/other-dlls/`，包含 dnSpy 的方法 token 与 RVA 注释。没有加载运行 Calman 的目标程序集。CASDK2Net 是混合托管/原生包装，托管方法中的原生调用并未因此还原成完整 C#。

## 主要发现

### 1. 探头 Profile 与 CA410 校准通道是两层设置，优先级高

证据链：

- `CalMAN_Workflow/MeterProfilerVM.cs` 的 `UpdateProfileFromMeasures`（token `06000D18`）按 FCCM 或 Bodner 生成 Profile。
- `CalWinCOM/CalMeterCom.cs` 的 `SetMatrix`、`SetBodnerMatrices` 将矩阵交给原生层；`UpdateCalibrationTableXYZ` 是另一条设备校准表操作。
- `CalIO/CalMeter.cs` 的 `CurrentProfile`、`SetProfileXYZ`（`06001F28`）、`SetProfileBodnerMatrices`（`06001F29`）确认 Profile 会进入采样路径。

Python 的 `MCH,<channel>` 选择设备内部校准通道；读取后没有另加 Calman 软件 Profile。不能将两者视作同一个设置。对照时，Calman 若启用了非恒等 Profile，输出的 XYZ 即可能与 Python 不同，后面的色域、Gamma、色差都会随之不同。

建议：先记录探头身份、内部校准通道和 Profile 是否启用。需要软件校正时，保留设备读数 `raw_xyz`，另存校正后 XYZ，所有派生统计使用明确选择的同一层读数；防止设备通道与软件矩阵重复施加同一校正。无需先实现完整双探头匹配界面。

### 2. 信号范围还会传入发生器，优先级高

`CalWinCOM/CalSourceCOM.cs` 的 `SetRange(bool isVideo)`（`06000140`）调用原生 `SetRange(1/0)`。`CalIO/CalPatternSource.cs` 初始化及测量选项变化时均传递范围；`CommandRGB` 则另传 RGB 码值、位深和窗口参数。

Python 的 Full/Limited 已影响 RGB 码值、背景和理论目标，但 `MurideoSIXG.configure_output()` 没有发送或读取 HDMI 量化范围配置，目前依赖发生器面板预设。这一限制原先已记录，此次由其他 DLL 的调用边界进一步确认。

关键区别：码值 16–235 与 HDMI 的量化范围标志是两个状态。只改软件选项不足以证明链路一致。托管 `SetRange` 的存在也不能证明 SIX-G 每个型号都支持相同串口命令；具体映射仍须查原生分支或设备协议。

建议：尽早记录发生器实际输出模式、位深、范围和时序；能够可靠读回的项目应做一致性检查，未实现的项目明确由面板预设。不要猜测一个串口命令。

### 3. 校准、同步和“已连接”应分开，优先级高

`CalWinCOM/CalMeterCom.cs` 暴露 `RequiresInit`、`IsCalibrated`、`BlackInitInteval`、`RemainingBlackInit`、`IsSynced`。`CalIO/CalMeter.IsReady`（`06001F22`）在设备要求初始化时检查校准与同步；`Initialize` 会按设备能力进行暗校准或同步。

`Common/CASDK2Net` 的探头包装还暴露 `get_ZeroCalStatus`、`get_CurrDeltaTemp`、`get_LastMeasDeltaTemp`、连接状态和序列号接口。这证明 SDK 有这些查询能力，不能据此认定当前 CA410 串口协议可直接使用同名命令。

Python 当前连接完成即可开始测量；零校准为手动按钮。非零 OK 状态保留为通用警告，但没有更细的校准有效性判断，也未保存校准时间、探头序列号或温差状态。

建议：依据 CA410 官方串口规范确认查询与状态含义后，区分“串口可用”“探头可测”“需要零校准”。不能把所有警告统一升级为致命错误，也不能把通信成功直接作为校准有效的证明。

### 4. 同步模式、频率和曝光是独立参数，优先级高至中

`CalAppPlatform/MeterSyncModes.cs` 提供 Off、On、Auto、External、AIO，实际可用模式由设备能力筛选。`CalWinCOM/CalMeterCom.cs` 分别传递同步类型、刷新率、常规曝光、低亮度曝光和触发阈值。`MeterProfilerVM.ExecuteReadCommand` 切换探头模式后还会恢复同步类型和频率。

Python 当前连接固定发送 `SCS,4,<Hz>`，相当于固定选择一种 CA410 同步模式；只提供频率输入。`FSC` 速度虽在适配器中可配置，界面未暴露，实际使用默认 0；常规/低亮度采样策略没有独立设置。

建议：先补上本机 CA410 明确支持的同步选择、速度及有效配置记录。Calman 通用枚举与 CA410 的 SCS 数值不可直接一一对应；不同设备的全部模式也不应无条件放进 CA410 下拉框。

### 5. 低亮度不只是延长串口超时，优先级中

`CalWinCOM/CalMeterCom.cs` 的 `LowLightHandlerEnabled`、`LowLightExposureMode`、`LowLightTriggerLevel`、`StandardExposure` 对应不同原生设置；CalIO 初始化会恢复这些设置。

`ClientPlatform/Formulas.LowLightHandler`（`06000106`）还存在多样本 XYZ 平均、按偏离量筛选及最少合格样本数判断。但在本次导出的托管程序集里没有找到它的调用者，不能声称这是当前 CA410 驱动实际使用的低亮度算法。

Python 每个色块只执行一次 `MES,2`，没有独立重复采样、稳定性统计或按亮度切换采样策略。增加 timeout 只延长等待，不等同于增加积分或重复样本。

建议：若加入多次测量，先在 XYZ 层求平均，再计算 xy、Gamma 和色差，同时保留每个样本及有效样本数。对超时或错误状态继续按错误处理，不把失败样本记为零值；暂不照搬未确认调用的 ClientPlatform 过滤公式。

### 6. 固定 0.5 秒延时只是默认值，优先级中

`CalUI/UI_SourceSettings.OptimizeDelay` 以及 `CalMAN_Workflow/CubeAutoCalSetup` 调用 `OptimziePatternDelayCommand`。重新导出的 CalIO 表明，该命令先取得白、绿、品红的稳定参考，每种参考读取 10 次，再测试较短延时，按重复测量与色差判断结果调整。

`CalPatternSource.PatternIOCompleted`（`06002137`）在切换色块等条件下等待设置的延时，随后才发出完成信号。Python 已在发生器应答后等待用户设置的 settle 时间，基础顺序正确；缺少的是延时验证和优化能力。

建议：保留可手动设置的延时；首次接实机后增加独立的稳定性诊断。优化过程会显示额外测试颜色，应作为用户主动运行的工具，不应偷偷插入灰阶正式序列。

### 7. 窗口、APL、插入色块与结束状态会影响可比性，优先级中

`CalMAN_Workflow/AppColorspaceSettings.cs` 定义两种插入方式：按秒数和按显示色块次数，均可配置持续时间、灰阶强度，默认关闭。CalIO 的 `BlackFrameInsertion`（`0600212E`）实际按配置强度显示等 RGB 的全场色块，并不必然是 0% 黑色。

`CalPatternSource` 另有普通窗口、自定义窗口、APL 编码分支。Python 当前只支持百分比窗口和固定黑背景，没有恒定 APL 背景或插入策略。对照设备时应匹配窗口及背景，不能只匹配中心 RGB。

`MeterCommand.ForceBlackAfterRead` 是原版已有的流程选项，探头匹配流程会使用。Python 完成、取消时没有恢复发生器画面的操作，会留下最后一个已显示色块。这是可配置的流程差异，不能推断 Calman 所有测量结束后都必然回黑。

建议：维持当前正式序列规则：灰阶只显示等 RGB，色域只测白和 RGB/CMY 七点，色准 39 点。将 APL、插入和结束画面作为明确的可选设置；辅助显示不加入测量点数与色差统计。窗口百分比的实机语义仍需验证，不宣称已确定为边长比或面积比。

### 8. 会话需要保存有效设备状态，优先级中

Calman 的 Profile 流程保留或恢复探头模式、同步类型、频率，并将 Profile 与探头身份关联。Python 会话已经保存串口、通道、频率、窗口、延时和原始响应，但缺少探头/发生器型号与序列号、固件、实际同步模式和速度、软件校正标识、发生器有效范围及时序。

建议：保存应用实际采用且能够确认的配置快照；读不到的状态标为未知。续测时对照身份与关键配置，避免只因 COM 口相同就认为测量条件相同。串口底层字节日志与设备返回原文仍有价值，应继续保留。

## 未发现需要直接移植的另一套计算内核

本次检查的其他 DLL 大多负责 UI、流程、接口或持久化，主要计算仍回到 CalIO。ClientPlatform 另有数学函数，但未确认与当前目标采样路径关联的函数不作为移植依据；不能仅因其函数名与 Gamma、sRGB 或低亮度相关就覆盖现有算法。

上次发现的 GammaPoint/GammaAbsolute、平均 Gamma、实测黑白参考、黑场参与色差统计等差异仍存在，详见 [gamma-colour-audit.md](gamma-colour-audit.md)。此次没有把这些已知事项当成新发现。

## 建议顺序与验证边界

1. 先补测量条件的可追溯性：设备身份、校准/同步状态、采样速度、发生器输出配置。
2. 对齐上一份报告里的 Gamma 与黑白参考口径，避免同一会话不同图表采用不同参考。
3. 再加入可选探头软件校正、重复采样与稳定性诊断。
4. 最后扩展 APL、插入、结束画面等流程选项，保持现有 7/17或33/39 点定义。

此次只读静态证据不能证明实机串口交互正确。SIX-G ACK 格式和命令语义、CA410 模式映射与状态查询、窗口实际含义仍须通过官方协议或设备读回/抓包确认。未将原生厂商 DLL 声称为已完整反编译，也未因 SDK 暴露了某接口就声称当前 CA410 串口适配器已支持它。
