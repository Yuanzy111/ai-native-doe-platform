# BoFire vs BayBE 对比报告

面向"把贝叶斯优化 / 实验设计能力封装成内部平台工具"的调研。两个项目均为开源的
贝叶斯优化 / 实验设计（DoE + BO）框架，底层都用 **BoTorch / GPyTorch / PyTorch**，
但在数据建模、状态管理、序列化和工程定位上有明显差异。

> 运行环境：micromamba 环境 `bo_examples`（Python 3.11，CPU 版 torch 2.13 + botorch 0.18.1）。
> 两个项目均**未安装、未改动源码**，通过 `PYTHONPATH` 指向本地源码运行。

---

## 1. 核心数据模型

### BoFire —— Pydantic 数据模型 与 功能实现 双层分离
- `bofire/data_models/`：全部是可序列化的 **Pydantic** 模型（`type` 字段做多态判别）。
- `bofire/strategies/`、`surrogates/`、`kernels/` …：对应的功能实现，通过 `mapper.py`
  把数据模型映射成可执行对象。
- 这种"声明式 spec + 功能实现"的分层是为了 **REST API / 前后端分离**：一个优化问题可以
  完整地表示成 JSON，服务端再 `map()` 成可运行的策略。

关键对象层级：

| 概念 | 类 | 说明 |
|------|----|------|
| 输入特征 | `ContinuousInput` / `DiscreteInput` / `CategoricalInput` / `MolecularInput` | 支持连续/离散/类别/分子编码 |
| 输出特征 | `ContinuousOutput`（携带 `objective`） | 输出即目标载体 |
| 目标 | `MaximizeObjective` / `MinimizeObjective` / `CloseToTargetObjective` / `Sigmoid*` … | 目标与输出解耦 |
| 约束 | `LinearEquality/Inequality`、`Nonlinear*`、`NChooseK`、`Interpoint` … | 丰富的约束体系 |
| 问题域 | `Domain = Inputs + Outputs + Constraints` | 整个优化问题的容器 |
| 策略 spec | `SoboStrategy`、`MoboStrategy`、`RandomStrategy`、`DoEStrategy` … | 仅是超参 spec |

### BayBE —— attrs 值对象 + 单一有状态 Campaign
- 所有领域对象用 **attrs**（`@define(frozen=True)`）构建，是**不可变值对象**。
- **`Campaign` 是唯一的有状态类**（`@define` 可变）；其余对象构造后即冻结/无状态。
- 序列化用 **cattrs + `SerialMixin`**，多态反序列化靠抽象基类上的 `"type"` 字段。

关键对象层级：

| 概念 | 类 | 说明 |
|------|----|------|
| 参数 | `NumericalContinuousParameter` / `NumericalDiscreteParameter` / `CategoricalParameter` / `SubstanceParameter` | Substance 需要 rdkit（化学编码）|
| 搜索空间 | `SearchSpace.from_product(...)` / `from_dataframe(...)` | 连续 + 离散子空间 |
| 目标 | `NumericalTarget`（现代接口默认 maximize，`minimize=True` 反向） | 目标变换（Bell/Triangular…）内置 |
| 目标组合 | `SingleTargetObjective` / `DesirabilityObjective` / `ParetoObjective` | 单/多目标 |
| 约束 | 连续约束 + 离散约束 | 相对 BoFire 更精简 |
| 推荐器 | `TwoPhaseMetaRecommender`（默认）、`BotorchRecommender`、`RandomRecommender` … | 探索阶段→贝叶斯阶段自动切换 |
| 活动 | `Campaign = SearchSpace + Objective + Recommender` | 唯一持有测量数据的对象 |

**一句话区别**：BoFire 把"问题"和"求解器"都建模成可序列化 spec，靠 mapper 落地；
BayBE 把不可变的领域对象组合进一个有状态的 `Campaign`，状态集中在一处。

---

## 2. 优化策略

| | BoFire | BayBE |
|---|--------|-------|
| 单目标 BO | `SoboStrategy`（及 Additive/Multiplicative 变体） | `BotorchRecommender` + `SingleTargetObjective` |
| 多目标 BO | `MoboStrategy`、`QparegoStrategy`（qNEHVI / qParEGO） | `ParetoObjective` + `BotorchRecommender` |
| 无模型基线 | `RandomStrategy` | `RandomRecommender` |
| 实验设计 DoE | `DoEStrategy`（D/A/G-最优等准则）、`FractionalFactorial` | 无专门 DoE 模块（靠初始随机推荐器） |
| 采集函数 | `qLogEI`、`qLogNEI`、`qLogNEHVI` …（BoTorch） | builder 模式组合 acqf |
| 迁移学习/多保真 | `MultiTaskGP`、`TaskInput`、多保真策略 | `task` 参数 + Transfer Learning（专有术语） |
| 代理模型 | 单/多任务 GP、随机森林、MLP、（可选）ENTMOOT 树模型 | GP 为主，可选自定义 surrogate / ONNX |
| 阶段编排 | `StepwiseStrategy`（多策略按条件切换） | `TwoPhaseMetaRecommender`（初期→贝叶斯自动切换） |

- **BoFire** 的强项是 **约束优化 + DoE**：cvxpy/pymoo/pyomo 支撑复杂约束与配比
  （mixture / N-choose-K）问题，还内置 SHAP 可解释性。
- **BayBE** 的强项是 **实验闭环易用性 + 化学/配方场景**：substance 编码、desirability
  多目标、迁移学习一等公民，Campaign 一个对象即闭环。

---

## 3. 运行流程

### BoFire：`Domain` → 数据模型 spec → `map()` → `ask/tell`
```python
domain = Domain(inputs=Inputs(features=[...]), outputs=Outputs(features=[...]))
strategy_dm = SoboStrategy(domain=domain, acquisition_function=qLogNEI())  # 仅 spec
strategy = strategies.map(strategy_dm)                                     # 落地为可执行
strategy.tell(experiments=history_df)   # 用历史实验拟合代理模型
candidates = strategy.ask(candidate_count=2)  # 推荐下一批（含预测均值/方差/desirability）
```
- `tell` 是**全量**：每次传入到目前为止的全部实验，重新拟合。
- `ask` 返回的 DataFrame 带 `<out>_pred / <out>_sd / <out>_des` 三列（预测/不确定度/期望度）。

### BayBE：`SearchSpace + Objective` → `Campaign` → `recommend/add_measurements`
```python
searchspace = SearchSpace.from_product([p1, p2])
objective = SingleTargetObjective(target=NumericalTarget(name="yield"))
campaign = Campaign(searchspace=searchspace, objective=objective)  # 唯一状态对象
rec = campaign.recommend(batch_size=3)          # 推荐（DataFrame）
rec["yield"] = run_experiments(rec)             # 填入实测目标列
campaign.add_measurements(rec)                  # 增量累加进 Campaign 状态
rec2 = campaign.recommend(batch_size=3)         # 第二轮已条件化于全部历史
```
- `add_measurements` 是**增量**：数据累积在 `campaign.measurements` 里，无需每轮重传历史。

### 两个最小示例的实测输出（本次已运行验证）

BoFire（`bofire/examples_minimal/sobo_ask_tell.py`）——两轮 ask/tell，推荐点向真值峰
(0.7, 0.3) 靠拢，返回带不确定度的候选：
```
=== Round 1: ask(2) ===
  conc_A   conc_B  yield_pred  yield_sd  yield_des
1.000000 0.475041    0.952798   0.07501   0.952798
0.279998 0.338607    0.912416   0.08830   0.912416
```

BayBE（`baybe/examples_minimal/campaign_two_rounds.py`）——两轮 recommend/add_measurements，
第二轮条件化于第一轮数据，找到 yield≈0.93 的更优点：
```
=== Campaign state ===
batches done   : 2
measurements   : 6
=== Best experiment so far ===
conc_A 0.80  conc_B 0.20  yield 0.930192  BatchNr 2
```

---

## 4. 输入 / 输出 / 状态管理

| 维度 | BoFire | BayBE |
|------|--------|-------|
| **输入** | `Domain`（Pydantic）+ 历史实验 `DataFrame`（含 `valid_<out>` 有效性标记列） | `SearchSpace` + `Objective` + 实测 `DataFrame`（目标列命名与 target 同名） |
| **输出** | 候选 `DataFrame`：输入列 + `<out>_pred`、`<out>_sd`、`<out>_des` | 推荐 `DataFrame`：参数列（离散空间会给出网格上的具体配置，附 `BatchNr/FitNr`） |
| **状态载体** | **策略对象内部**；`tell` 每次全量重拟合（策略本身可视为无持久状态，历史由调用方持有） | **`Campaign` 单一对象**；`add_measurements` 增量累积，`n_batches_done / measurements` 可查 |
| **状态外化/持久化** | 数据模型 `model_dump()` ↔ JSON 往返（严格逐字段一致），Domain/Strategy spec 可存库；实验数据由调用方管理 | `Campaign.to_json()/from_json()`（含 `version` 字段），DataFrame 以 pickle+base64 编码进 JSON，**整个活动可原样存取** |
| **多态反序列化** | Pydantic `TypeAdapter` + `type` 判别 | cattrs 钩子 + 抽象基类 `type` 字段 |

**关键差异**：
- BayBE 的**状态是自包含的**——一个 `Campaign` 对象（可序列化为单个 JSON）就装下了搜索
  空间、目标、推荐器配置**和全部历史测量**，天然适合"存档→恢复→继续推荐"。
- BoFire 把**问题定义**（Domain/Strategy spec）与**实验数据**分离，spec 极适合入库和
  REST 传输，但历史数据需要平台侧自己存储、每轮全量 `tell` 回去。

---

## 5. 适合封装成平台工具的能力

### 两者都值得封装的通用能力
1. **"定义问题 → 推荐下一批 → 回填结果"闭环 API**：统一成 `POST /suggest` + `POST /observe`。
2. **批量推荐**（batch_size / candidate_count）：天然支持并行实验。
3. **代理模型不确定度输出**：BoFire 直接给 `_sd`；可用于主动学习/风险控制的平台展示。
4. **多目标 / desirability**：两者都支持，可做成平台里的"多目标权衡"配置项。

### BoFire 特别适合封装的能力
- **约束化实验设计 / 配方优化**：mixture、N-choose-K、线性/非线性约束——化工、材料配比
  场景的刚需；`DoEStrategy`（D/A/G-最优）可做成"实验设计生成器"工具。
- **纯 spec 化的问题定义**：Domain / Strategy 全部可 JSON 序列化，非常契合
  "低代码表单 → 生成优化问题 → 服务端执行"的平台形态（这正是其架构初衷）。
- **SHAP 可解释性**：可作为平台的"因素重要性"分析模块。
- **`StepwiseStrategy`**：把"先随机探索 N 轮再切贝叶斯"编排成可配置流水线。

### BayBE 特别适合封装的能力
- **Campaign 单对象状态机**：一个 `to_json/from_json` 就能实现"实验活动"的存档、克隆、
  断点续跑——平台侧持久化成本极低，非常适合做成"实验项目"实体。
- **化学 / 物质编码**（SubstanceParameter + MORDRED/RDKit）：药物、催化剂、配方筛选场景
  开箱即用。
- **迁移学习 / 多臂老虎机 / 多保真**：作为一等公民，适合封装成"复用历史项目经验"的高级功能。
- **`TwoPhaseMetaRecommender` 自动阶段切换**：新项目冷启动无需人工配置探索轮数。

---

## 6. 选型建议（供平台设计参考）

| 若平台侧重…… | 更契合 |
|--------------|--------|
| REST/前后端分离、问题定义强序列化、复杂约束与 DoE | **BoFire** |
| 实验活动全状态自包含、存档续跑、化学配方与迁移学习 | **BayBE** |
| 单目标/多目标通用 BO 闭环 | 两者皆可，接口都简洁 |

两者定位互补而非替代：**BoFire 更像"可序列化的优化问题 + 求解器工厂"，BayBE 更像
"自包含、可存档的实验活动对象"**。若要统一平台，建议在其上再抽象一层与框架无关的
`suggest/observe` 接口，将 BoFire 的 Domain-spec 和 BayBE 的 Campaign 分别作为后端适配器，
而**不**去改动或合并两个上游项目。

---

## 附：复现方式

```bash
cd /data/yzy/industrial_optimization

# BoFire 最小示例（首次运行会触发 numba 编译，稍慢；缓存已预热后 <60s）
micromamba run -n bo_examples env PYTHONPATH=$PWD/bofire \
    python bofire/examples_minimal/sobo_ask_tell.py

# BayBE 最小示例
micromamba run -n bo_examples env PYTHONPATH=$PWD/baybe \
    python baybe/examples_minimal/campaign_two_rounds.py
```

参考输出分别见 `bofire/examples_minimal/expected_output.txt`、
`baybe/examples_minimal/expected_output.txt`。
