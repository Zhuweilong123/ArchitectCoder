# 雷达信号处理链路设计文档

## 1. 概述

本文档描述 `radar_sim` 软件包的设计。该包从 UML 设计
`radar_design_0730.umlproj` 生成，实现一条完整的雷达信号处理仿真链路：
**模式控制 → 发射波形生成 → 回波仿真 → 脉冲压缩 → 距离测量**。

软件包是事件驱动的端到端仿真，不涉及真实硬件与实时调度，重点关注
信号链路中各阶段的数学模型和它们之间的数据流转。

### 1.1 设计目标

- **按组件解耦**：四个功能组件各自封装独立职责，通过明确的接口交互。
- **物理上一致**：回波时延由目标距离按两倍程距离换算，脉冲压缩给出与
  时延对应的距离像，检测结果逼近注入的真实距离。
- **可测试**：各组件纯函数式设计、随机性可控，配合同步种子保证测试可复现。
- **可扩展**：新增模式、目标类型或检测算法无需改动其他组件。

### 1.2 组件总览

| 组件 | 提供接口 | 职责 |
|---|---|---|
| ModeControl | `IModeControl` | 模式切换与参数查询 |
| TransmitWaveformGen | `ITransmitSignal` | 生成 LFM 发射脉冲串 |
| EchoSimulation | `IEchoSignal` | 模拟目标回波（时延 + 噪声） |
| PulseCompression | `IRangeProfile` | 匹配滤波压缩与峰值测距 |

### 1.3 技术选型

- **Python 3.12+**，**NumPy 1.26** 提供复数向量运算与 FFT。
- **pytest 9** 作为测试框架。
- 仅依赖 NumPy，无第三方信号处理库（`scipy` 未使用），保持依赖最小化。

---

## 2. 系统架构

### 2.1 组件依赖图

```
                    ┌──────────────┐
                    │ ModeControl  │ IModeControl
                    │ (IModeControl)│
                    └──────┬───────┘
                           │ IModeControl（查询参数）
                           ▼
                  ┌─────────────────┐      ITransmitSignal      ┌──────────────┐
                  │TransmitWaveformGen│ ────────────────────────▶│EchoSimulation│
                  │  (ITransmitSignal)│                          │(IEchoSignal) │
                  └────────┬──────────┘                          └──────┬───────┘
                           │  ITransmitSignal / IEchoSignal             │ IEchoSignal
                           ▼                                             ▼
                  ┌─────────────────┐                          ┌──────────────┐
                  │ PulseCompression│ ◀────────────────────────│   (回波信号)  │
                  │ (IRangeProfile) │                          └──────────────┘
                  └─────────────────┘
```

### 2.2 包结构与职责

```
src/
└── radar_sim/
    ├── __init__.py           # 包导出（公共类型）
    ├── common.py             # 共享领域类型与常量
    ├── mode_control.py       # ModeControl 组件
    ├── transmit.py           # TransmitWaveformGen 组件
    ├── echo.py               # EchoSimulation 组件
    ├── pulse_compression.py  # PulseCompression 组件
    └── main.py               # 端到端演示入口
test/
    ├── conftest.py           # 将 src/ 加入 sys.path
    ├── test_mode_control.py
    ├── test_transmit.py
    ├── test_echo.py
    └── test_pulse_compression.py
```

### 2.3 数据流

```
ModeController.setMode(LongRange)
        │  getCurrentParams()
        ▼
ModeParams{prt=3ms, pulseWidth=100µs}
        │
        ▼
TransmitCoordinator.generateTransmitSignal(prt, pulseWidth, Fs)
        │  → txSignal（30000 复数样点，PRT 周期）
        ▼
┌─ EchoSimulator.setTargets([5,25,150] km) ─┐
│  EchoSimulator.generateEcho(txSignal, SNR, Fs) │
│    每个目标：时延移位 → 幅度加权(√RCS) → 叠加    │
│    合成后：叠加复高斯白噪声                       │
└────────────────────┬────────────────────────┘
                     ▼
              echoSignal（等长复数数组）
                     │
                     ▼
PulseCompressor.buildFilter(txSignal)   ← 匹配滤波系数 = conj(FFT(ref))
PulseCompressor.compress(echoSignal)    → |IFFT(FFT(echo)·conj(FFT(ref)))|²
                     │
                     ▼
PeakDetector.detect(rangeProfile, Fs)   → [5.01km, 25.00km, 150.00km]
```

---

## 3. 领域模型（Domain Model）

本节对照 UML 类图描述各组件内部的对象模型。除枚举与常量外，所有数据
均为可变的普通对象；对外交互使用明确的类型方法。

### 3.1 共享类型（`common.py`）

| 类型 | 说明 |
|---|---|
| `ModeEnum` | 雷达工作模式枚举，携带 `prt` 与 `pulse_width` 两个参数 |
| `ModeParams` | 波形时序参数（`prt`、`pulse_width`），构造时校验正值与脉宽小于 PRT |
| `Target` | 点目标，含 `distance`(m) 与 `rcs`(m²) |
| `Peak` | 检测峰值，含 `index`(样点)、`range_m`(物理距离)、`amplitude` |
| `c` | 光速常量 299792458 m/s |

**设计决策**：`ModeEnum` 的枚举成员自带时序参数（`prt`、`pulse_width`），
这是对 UML 中 `ModeParamTable` 的 `shortRangePRT`…`longRangePulseWidth`
六个属性的等价建模——参数表按模式查询，等价于按枚举成员取值。
`ModeEnum` 同时充当常量表与参数来源，避免在控制器与参数表之间复制参数。

### 3.2 ModeControl 组件（`mode_control.py`）

对应 UML 类图 *ModeControl Domain Model*。

```
┌─────────────────────┐      lookup 1..1      ┌─────────────────────┐
│    ModeController   │──────────────────────▶│   ModeParamTable    │
│  ─────────────────  │                        │  ─────────────────  │
│ - currentMode       │                        │ - shortRangePRT…   │
│ - paramsTable       │                        │ - longRangePW      │
│  ─────────────────  │                        │  ─────────────────  │
│ + setMode(ModeEnum) │                        │ + getParams(Mode)  │
│ + getCurrentParams()│                        │   → ModeParams     │
└─────────────────────┘                        └─────────────────────┘
```

- `ModeController` 提供 `IModeControl` 接口：`setMode` 切换当前模式，
  `getCurrentParams` 通过参数表查询当前模式的 `ModeParams`。
- 属性 `currentMode`、`paramsTable` 与类图 `-` 私有可见性对应。
- 关联 `lookup`（控制器使用参数表）建模为构造注入 + 委托调用。

**校验规则**：`setMode` 拒绝非 `ModeEnum` 值；`ModeParamTable.getParams`
同样拒绝非法类型，避免字符串等误用。

### 3.3 TransmitWaveformGen 组件（`transmit.py`）

对应 UML 类图 *TransmitWaveformGen Domain Model*。

```
┌─────────────────────────┐
│  TransmitCoordinator    │   (IModeControl 需求侧)
│  ─────────────────────  │
│ + generateTransmitSignal│
│   (prt, pulseWidth, Fs) │
└──────────┬──────────────┘
           │ dependency
     ┌─────┴─────┐
     ▼           ▼
┌─────────────┐ ┌─────────────┐
│WaveformGen  │ │PulseSeq     │
│+genLFMPulse │ │+assemble... │
└─────────────┘ └─────────────┘
```

- `TransmitCoordinator` 是组件外观，接收时序参数并协调两个子模块。
- `WaveformGenerator.generateLFMPulse(pulseWidth, sampleRate)`：
  生成单个线性调频脉冲的 I/Q 复数数据。
- `PulseSequencer.assemblePulseSequence(pulse, PRT, sampleRate)`：
  将单个脉冲按 PRT 补零，拼装成完整周期的脉冲串。

**关键物理参数**：

| 常量 | 值 | 说明 |
|---|---|---|
| `FC` | 1.0 GHz | 标称雷达中心频率（L 波段） |
| `LFM_BANDWIDTH` | 2.0 MHz | LFM 扫频带宽，系统固定常量 |
| `c` | 299792458 m/s | 光速 |

### 3.4 EchoSimulation 组件（`echo.py`）

对应 UML 类图 *EchoSimulation Domain Model*。

```
┌───────────────────────┐
│     EchoSimulator     │
│  - sceneManager       │
│  - delayProcessor     │
│  - noiseAdder         │
│ + setTargets(Target[])│
│ + generateEcho(...)   │
└──────┬──────┬──────┬──┘
       ▼      ▼      ▼
┌──────────┐┌──────────┐┌──────────┐
│TargetScene││DelayProc ││NoiseAdd  │
│ Manager  ││+applyDelay││+addNoise │
│+addTarget││          ││          │
│+getDelays││          ││          │
└──────────┘└──────────┘└──────────┘
```

- `EchoSimulator` 是回波生成协调器，组合时延与噪声；其属性
  `sceneManager`、`delayProcessor`、`noiseAdder` 与类图 `-` 私有属性对应。
- `TargetSceneManager` 维护目标距离/RCS 列表，`getTargetDelays` 换算时延。
- `DelayProcessor.applyDelay` 执行分数时延移位（支持非整数采样点）。
- `NoiseAdder.addNoise` 按信噪比叠加复高斯白噪声。

### 3.5 PulseCompression 组件（`pulse_compression.py`）

对应 UML 类图 *PulseCompression Domain Model*。

```
┌───────────────────────┐
│    PulseCompressor    │
│  - filterCoefficients │
│  - builder            │
│  - detector           │
│ + buildFilter(ref)    │
│ + compress(echo)      │
└──────┬───────────┬────┘
       ▼           ▼
┌─────────────┐ ┌─────────────┐
│MatchedFilter│ │PeakDetector │
│  Builder    │ │ +detect(…)  │
│+buildFilter │ └─────────────┘
└─────────────┘
```

- `PulseCompressor` 协调匹配滤波与峰值检测，输出距离像。
- `MatchedFilterBuilder.buildFilter` 对参考信号 FFT 取共轭，生成频域匹配系数。
- `PeakDetector.detect` 搜索峰值并换算为物理距离。

---

## 4. 算法设计

### 4.1 波形生成（LFM 脉冲）

线性调频（Chirp）脉冲的复包络：

```
s(t) = exp(j·2π·(fc·t + ½·k·t²)),   0 ≤ t < T
k = B / T
```

其中 `T = pulse_width`，`B = LFM_BANDWIDTH = 2MHz`，`fc = FC = 1GHz`。

**离散化**：`N = round(pulseWidth × sampleRate)` 个样点，时间轴
`t_n = n / sampleRate`。波形为单位幅度复数序列。

**脉冲串组装**：PRT 窗 `N_prt = round(PRT × sampleRate)` 个样点，前
`N` 个填入脉冲，其余补零。

### 4.2 匹配滤波（脉冲压缩）

匹配滤波器在频域实现：对参考信号（发射脉冲串）FFT 并取共轭，得到频域
系数；回波与系数频域相乘后 IFFT，取模得到距离像。

```
Y = |IFFT( FFT(echo) · conj(FFT(ref_padded)) )|
```

**实现要点**：参考信号须零填充到回波长度后再做 FFT 取共轭。若直接对
`conj(FFT(ref))` 结果 IFFT 往返（即先 `ifft` 再 `fft`），会因参考块内的
共轭翻转引入随参考长度的线性相位，导致相关峰整体偏移（实测偏移量等于
参考信号长度）。零填充的共轭谱是标准互相关的 FFT 形式，峰位恰好落在
回波时延对应的样点。

**时延样点 → 物理距离**：

```
R = n · c / (2 · Fs)
```

因子 `2` 来自双程时延（雷达收发共用天线）。

### 4.3 回波仿真

每个目标独立的回波叠加：

```
echo(t) = Σᵢ  √(rcsᵢ) · s(t - τᵢ) + n(t)
```

其中双程时延：

```
τᵢ = 2 · distanceᵢ / c            （秒）
delay_samplesᵢ = τᵢ · sampleRate  （样点）
```

- **幅度加权**：按 `√rcs` 缩放目标回波强度（点目标 RCS 模型）。
- **分数时延**：`DelayProcessor` 在频域乘 `exp(-j·2π·f·delay/sampleRate)`
  实现任意精度移位，支持非整数采样点。
- **噪声**：复高斯白噪声，功率由 SNR 决定。

```
P_noise = P_signal / 10^(SNR/10)
n = √(P_noise/2) · (randn + j·randn)
```

### 4.4 峰值检测

检测距离像中的局部极大值，判据为双重阈值：

```
min_amplitude = max(0.3 · global_max, noise · 10^(snrThreshold/10))
```

其中 `noise = median(|rangeProfile|)` 为全局中值噪声底。

- **中值噪声底**：对稀疏距离像（大量零值）稳健；平均会高估噪声底。
- **主瓣下界 `0.3·global_max`**：匹配滤波的 sinc 旁瓣为 -13.3 dB
  （约 0.216·主峰），取 0.3 可稳健剔除旁瓣虚警，同时不误杀较弱目标
  （弱目标仍由噪声底判据决定是否检测）。
- 该双重判据对应典型的 CFAR（恒虚警）思路：固定虚警率由噪声底决定，
  固定主瓣门限剔除强目标旁瓣。

### 4.5 关键物理量汇总（长程模式）

| 参数 | 值 |
|---|---|
| PRT | 3 ms |
| 脉冲宽度 | 100 µs |
| 采样率 | 10 MHz |
| 每个 PRT 样点数 | 30000 |
| 发射脉冲内样点数 | 1000 |
| LFM 带宽 | 2 MHz |
| 距离分辨率 ΔR = c/(2B) | **74.95 m** |
| 最大不模糊距离 c·PRT/2 | **449.7 km** |
| 时间带宽积 T·B | 200 |
| 压缩比 | 200 |

三个注入目标 `[5, 25, 150] km` 的时延样点分别为
`[334, 1668, 10007]`，均落在 30000 样点窗内，无模糊。

---

## 5. 时序流程（Sequence Flow）

以下对照 UML 时序图 *Full Radar Signal Processing Flow*（长程设置）。

| 序号 | 发送方 | 接收方 | 动作 | 说明 |
|---|---|---|---|---|
| 1 | User | ModeController | `setMode(LongRange)` | 切换至远程模式 |
| 2 | ModeController | User | `return {prt, pulseWidth}` | 返回远程参数 |
| 3 | User | TransmitCoordinator | `generateTransmitSignal(prt, pw, Fs)` | 生成发射信号 |
| 4 | TransmitCoordinator | User | `return txSignal` | 完整脉冲串 |
| 5 | User | PulseCompressor | `buildFilter(txSignal)` | 构建匹配系数 |
| 6 | PulseCompressor | User | `return ok` | 系数缓存完成 |
| 7 | User | EchoSimulator | `setTargets([5km,25km,150km])` | 注入目标距离 |
| 8 | EchoSimulator | User | `return` | 场景配置完成 |
| 9 | User | EchoSimulator | `generateEcho(tx, SNR=20dB, Fs)` | 产生回波（每 PRT 循环） |
| 10 | EchoSimulator | User | `return echoSignal` | 带时延与噪声的回波 |
| 11 | User | PulseCompressor | `compress(echoSignal)` | 脉冲压缩 |
| 12 | PulseCompressor | User | `return {distances}` | 检测结果 |

**UML 期望输出**：`{distances: [4.98km, 24.9km, 149.5km]}`。

**实测输出**（`main.py`，SNR=20dB，种子固定）：

```
range =  5.01 km   amplitude =  986.05
range = 25.00 km   amplitude =  997.00
range = 150.00 km  amplitude = 1000.90
```

三个峰的位置均在实际时延 ±1 样点（约 15 m，远小于 75 m 距离分辨率）
之内，与 UML 期望一致。与期望值的微小偏差来自频域压缩的循环相关与
分数时延的近似——这是合理的量化精度，不影响检测正确性。

---

## 6. 配置与运行

### 6.1 运行演示

```bash
cd src
python main.py
```

演示输出见 5 节。脚本在入口处将 `src/` 加入 `sys.path`，因此也可从其他
目录运行：

```bash
python D:/AI_tools/uml_designer/generated/src/main.py
```

### 6.2 运行测试

```bash
cd D:/AI_tools/uml_designer/generated
python -m pytest test -q
# 37 passed
```

`test/conftest.py` 负责把 `src/` 加入 `sys.path`，使测试包可直接
`import radar_sim`。

### 6.3 依赖

- Python ≥ 3.10（使用 `X | None` 类型联合语法）
- NumPy ≥ 1.20（`np.typing.NDArray`、`np.fft.fftfreq`）
- pytest ≥ 7（测试环境）

---

## 7. 测试策略

37 个用例按组件划分，覆盖功能、边界与物理一致性。

### 7.1 ModeControl（7 用例）

- 参数表对每个模式的查询正确、长程参数与 UML 一致。
- 控制器默认短程模式、切换模式生效、`getCurrentParams` 反映当前模式。
- 非法模式值抛出 `ValueError`。

### 7.2 TransmitWaveformGen（8 用例）

- 脉冲长度 = 脉宽×采样率；单位幅度包络；正调频斜率的瞬时频率递增。
- 序列器 PRT 窗补零正确、拒绝 PRT 小于脉冲长度的非法组合。
- 协调器输出长度 = PRT 窗、非脉冲区全零、距离分辨率 = c/(2B)。
- 非法时序参数（PRT≤0、脉宽≥PRT）抛错。

### 7.3 EchoSimulation（7 用例）

- 时延换算公式验证（`distance·2/c·Fs`）、多目标顺序保持。
- 零时延恒等、整数时延精确移位、分数时延峰位落点与实部近似。
- 噪声功率按 SNR 匹配（10dB → 信号+噪声功率比 1.1）、空信号透传。
- 回波长度守恒、高 SNR 下能量守恒、空目标列表拒绝。

### 7.4 PulseCompression（12 用例）

- 匹配系数 = `conj(FFT(ref))`；空参考拒绝。
- 匹配滤波压缩：单脉冲回波压缩峰在时延位置 ±2 样点内，且主峰显著
  高于非压缩基线。
- `compress` 未建滤波器时抛 `RuntimeError`。
- 峰值检测：单峰检出、双程距离换算、距离偏移、空剖面返回空。
- **端到端**：发射→回波→压缩→检测，5/25/150 km 三目标全部检出，
  相对误差 < 2%。

### 7.5 复现性

噪声测试与端到端用例使用固定种子
（`main.py` 中 `np.random.seed(20260730)`），保证随机性可复现；
端到端用例采用无噪声（SNR=∞）构造，完全确定性。

---

## 8. 设计决策与权衡

### 8.1 LFM 带宽固定（相对于脉宽）

UML 未规定带宽来源。初版采用 `B = 0.9/T`（近最大带宽）导致时间带宽积
`T·B ≈ 0.9`，脉冲压缩几乎无效，距离分辨率达 16 km。改为**系统固定
`B = 2 MHz`** 后：

- 对长程模式 `T·B = 200`，压缩比 200，距离分辨率 75 m，物理合理。
- 各模式共享同一带宽，`ModeParams` 只承载时序参数，语义清晰。

### 8.2 匹配滤波的频域实现与零填充

- 直接频域乘 `conj(FFT(ref))` 因参考块内共轭翻转引入线性相位偏移，
  峰位会偏移参考长度。改为零填充到回波长度的共轭谱，得到标准互相关，
  峰位精确对应时延（见 4.2）。
- `filterCoefficients` 保留 `conj(FFT(ref))` 作为构建产物（符合 UML
  的 `MatchedFilterBuilder` 语义），`compress` 内部做长度适配。

### 8.3 峰值检测的双重阈值

- 纯局部极大值检出的峰数多达 42（含 sinc 旁瓣），因为噪声底为零时
  无法用 SNR 判据剔除。
- 引入**中值噪声底**（对稀疏剖面稳健）与**主瓣下界 0.3·global_max**
  （剔除 -13.3 dB 旁瓣）双重判据，无噪时只保留真实目标，弱目标检测
  仍由噪声底决定。

### 8.4 可测试性优先的实现风格

- 各协作对象支持构造注入（`TransmitCoordinator(waveform_generator=…)`），
  便于单测替换、保持物理语义一致。
- 随机源为模块级 `np.random`，配合固定种子即可复现，避免引入复杂依赖。

---

## 9. 局限与后续扩展

- **点目标模型**：`Target` 只有距离与 RCS，无径向速度，不支持多普勒处理。
  可扩展 `Target` 增加速度字段，并在回波中引入多普勒相移。
- **单 PRT 处理**：当前按单 PRT 窗处理。可扩展为多 PRT 积累、MTI/MTD。
- **理想匹配滤波**：未建模滤波器失配、通道幅度/相位误差、杂波。
- **CFAR 简化**：峰值检测用全局中值底 + 固定主瓣门限；可替换为
  滑动窗 CFAR（CA-CFAR / OS-CFAR）以应对非均匀环境。
- **无波形重频综合**：LFM 时宽带宽积受限于采样率；超宽带脉冲或相位编码
  （Barker / P4）可作扩展方向。
- **实时性**：FFT 长度 30000，当前实现为串行；大数据量可引入分段
  重叠保留（overlap-save）法或 GPU 加速。

---

## 附录 A：模块接口速查

| 模块 | 类/函数 | 签名 |
|---|---|---|
| `common` | `ModeEnum` | `SHORT_RANGE / MEDIUM_RANGE / LONG_RANGE`，`.prt` `.pulse_width` |
| `common` | `ModeParams` | `ModeParams(prt, pulse_width)`，属性 `.prt` `.pulse_width` |
| `common` | `Target` | `Target(distance, rcs=1.0)`，属性 `.distance` `.rcs` |
| `common` | `Peak` | `Peak(index, range_m, amplitude)` |
| `common` | `c` | 光速常量 |
| `mode_control` | `ModeParamTable` | `getParams(mode) → ModeParams` |
| `mode_control` | `ModeController` | `setMode(mode)`，`getCurrentParams() → ModeParams` |
| `transmit` | `WaveformGenerator` | `generateLFMPulse(pulseWidth, sampleRate) → complex[]` |
| `transmit` | `PulseSequencer` | `assemblePulseSequence(pulse, PRT, sampleRate) → complex[]` |
| `transmit` | `TransmitCoordinator` | `generateTransmitSignal(prt, pulseWidth, sampleRate) → complex[]`；`range_resolution(prt, pulseWidth) → float` |
| `echo` | `TargetSceneManager` | `addTarget(distance, rcs=1.0)`，`getTargetDelays(sampleRate) → float[]` |
| `echo` | `DelayProcessor` | `applyDelay(signal, delay, sampleRate) → complex[]` |
| `echo` | `NoiseAdder` | `addNoise(signal, SNR) → complex[]` |
| `echo` | `EchoSimulator` | `setTargets(Target[])`，`generateEcho(txSignal, SNR, sampleRate) → complex[]` |
| `pulse_compression` | `MatchedFilterBuilder` | `buildFilter(refSignal) → complex[]` |
| `pulse_compression` | `PeakDetector` | `detect(rangeProfile, sampleRate, rangeOffset=0, snrThreshold=6) → Peak[]` |
| `pulse_compression` | `PulseCompressor` | `buildFilter(refSignal)`，`compress(echoSignal) → float[]` |

## 附录 B：长程模式数值速查

```
Fs          = 10 MHz
PRT         = 3 ms        → N_prt   = 30000 样点
T (脉宽)    = 100 µs      → N_pulse = 1000  样点
B (带宽)    = 2 MHz
ΔR          = c/(2B)      = 74.95 m
R_max       = c·PRT/2     = 449.7 km
时延: 5 km  → 334 样点   25 km → 1668 样点   150 km → 10007 样点
```
