# 工业实验优化平台 — 架构设计 v0.2

> 状态: 草案(v0.2),基于 `docs/architecture-v0.1.md` 的第二轮评审修订版,供产品/工程三次评审。
> 本文档不涉及任何代码改动(业务代码与前端 UI 均未修改)。
> `architecture-v0.md`、`architecture-v0.1.md` 均保留作为历史基线,不删除,便于对照 diff。
> 输入依据: `architecture-v0.1.md`、vendored `baybe/` 与 `bofire/` 源码实测,以及本轮评审提出的 14 项修订要求。

---

## 0. 本次修订摘要(v0.1 → v0.2 评审对照表)

| # | 评审要求 | 落在哪一节 | 一句话概括变化 |
|---|---|---|---|
| 1 | 逐输出 `ObjectivePolicy[]` 改名为 `TargetSpec[]` | §2.6 | "每个 output 的优化方向"这一层改名为 `TargetSpec`,只描述单个目标的方向,不再叫 policy |
| 2 | 新增唯一的 Campaign 级 `ObjectivePolicy` 判别联合 | §2.7 | `SingleObjectivePolicy \| DesirabilityObjectivePolicy \| ParetoObjectivePolicy`,每个 revision 恰好一个,判别字段 `kind` |
| 3 | Desirability 显式保存 transformation/cutoffs/weights/scalarizer,删除运行时 min/max 猜测 | §2.7 §5.2 | Desirability 的缩放边界必须显式给定;缺失 → `blocking` issue;删除 v0.1 §5.2 "用历史观测 min/max 运行时估计"的降级逻辑 |
| 4 | `OptimizationPolicy` 移到 `CampaignRun`,定义类型化策略配置 | §2.3 §2.9 | Policy 从"问题定义"移到"执行"侧;`strategyConfig` 改为判别联合,不再是"只校验是否合法 JSON"的不透明 object |
| 5 | 新增 `CampaignDefinitionRevision` 不可变实体 | §2.1 §2.2 §2.3 | `CampaignDefinition` 退化为逻辑容器(`headRevisionId`);问题定义内容进入不可变的 `CampaignDefinitionRevision`;`CampaignRun.definitionRevisionId` 指向某个不可变 revision |
| 6 | `ConstraintSpec` 改为判别联合,FixedSum 只作 UI 映射 | §2.8 §5 | `LinearEqualityConstraintSpec \| LinearInequalityConstraintSpec \| CardinalityConstraintSpec`;删除 `custom` 自由文本;FixedSum 降为前端到 `LinearEquality` 的 UI 映射 |
| 7 | `executable` 改为 `ValidationResult` 的派生结果,不持久化 | §2.8 §4 | 删除 `ConstraintSpec.executable`/`parsedExpression` 持久字段;可执行性由 `validate()` 每次实时派生 |
| 8 | 修正 BayBE/BoFire 能力矩阵 | §5 | BayBE 数值离散和约束用 `DiscreteSumConstraint`;**修正 req 8 前提**:BoFire 线性约束实测支持 `ContinuousInput`+`DiscreteInput`(仅排除 Categorical),非"仅 ContinuousInput";BayBE Cardinality `min=max` 表达恰好 K |
| 9 | 新增 `Measurement.revision` / `supersedesMeasurementId` | §2.12 | 显式版本化读数,修正历史误差时新增一条 revision 并链接被取代者,活跃读数取 supersedes 链头 |
| 10 | 新增 `AwaitingMeasurements` 结果就绪状态 + 推荐前 partial-measurement gating | §2.3 §3 §4 | 新增"实验已执行、读数未齐"状态;`recommend()`/关轮前依据 `objective/backend` 的 partial-measurement 能力做门禁 |
| 11 | MVP 明确使用 SQLite 持久化 + 记录复现信息 | §1 §2.13 §7 | SQLite 从"暂时不做"移到"做";`algorithmConfig.environment` 记录 Python/torch/botorch 版本与 `dependencyLockHash` |
| 12 | 删除公共 `OptimizerAdapter.update()` | §4 | Protocol 只保留 `capabilities/validate/generate_initial_design/recommend/explain`;缓存是 Adapter 内部实现细节 |
| 13 | 修正 JSON:合法完整,`batchSize=4` 即四个 candidates,无占位串 | §8 | `inputSnapshot` 内联真实深拷贝(不再是 `<= 省略 =>` 占位文本);`candidates` 严格 4 条 |
| 14 | 保留 Agent 职责边界 | §6 | LLM 不得直接计算推荐候选、不得绕过 `validate()`、不得伪造预测值 |

> **关于 req 8 的诚实标注**:第 8 条要求文本写"BoFire 线性约束只支持 ContinuousInput"。实测 vendored 源码 `bofire/bofire/data_models/constraints/linear.py` 的 `LinearConstraint.validate_inputs` 使用 `inputs.get_keys([ContinuousInput, DiscreteInput])`,即**同时接受连续与数值离散输入,仅排除 Categorical**。本文档按源码真实行为记录(见 §5.1 与 §5 末尾"修正说明"),不照抄与源码相悖的前提;这是本轮唯一一处与要求文本不一致但有源码依据的偏差,提请评审确认。
>
> **关于本次 req 6(DiscreteSumConstraint)的诚实标注**:本次修补第 6 条要求"BayBE `DiscreteSumConstraint` 仅支持全部离散数值参数且 coefficients 全为 1"。实测源码 `baybe/baybe/constraints/discrete.py`:`numerical_only=True`(全部参数须为数值离散)确为库的硬性限制;但 `coefficients` **原生支持任意非零权重**(默认全 1),"仅全为 1"并非库限制。本文档据源码把"coefficients 全为 1"明确标注为**平台 MVP 的显式简化**(而非能力所限),`validate_definition()` 据此阻断加权和(见 §5.2 与 §5 末尾"修正说明"第 4 点),放开留待 v1。

---

## 1. MVP 边界

### 1.1 MVP 做什么

在 v0.1 基础上,本轮修订主要是模型精确化 + 两处能力范围调整(SQLite 持久化纳入、Desirability 显式化):

| 能力 | 说明 |
|---|---|
| Campaign 定义 | 创建/编辑 `CampaignDefinition`;每次编辑产生一个**不可变** `CampaignDefinitionRevision`(Parameters/Outputs/TargetSpecs/**唯一 ObjectivePolicy**/Constraints 数组) |
| 设计空间校验 | `validate()`:参数、Output/TargetSpec、唯一 `ObjectivePolicy`(含 Desirability 显式缩放边界)、`constraints` 数组每一项是否可执行(实时派生)、`constraintsConfirmed` 是否为真 |
| 初始实验设计 | `generate_initial_design()`:仅 BayBE 单后端,冷启动生成第一批候选 |
| 迭代推荐 | `recommend()`:基于 `ExperimentRun` + `Measurement` 生成下一批候选,**原生支持部分回填**,并按后端 partial-measurement 能力做 gating |
| 观测回填 | 人工录入/编辑 `Measurement`,一次一个 Output,允许陆续补齐;修正历史读数产生新 revision |
| 决策留痕 | `DecisionLog` 记录 revision 创建、约束确认、推荐生成、实验执行、测量记录/取代、Run 完成等 |
| 可复现性 | 每次 `generate_initial_design`/`recommend` 落盘完整输入快照 + 完整算法配置 + **运行环境版本 + 依赖锁 hash** |
| 持久化 | **v0.2 变更**:MVP 采用 **SQLite** 落盘(单文件、零运维),实体表结构对齐 §2 领域模型 |
| Campaign 版本化 | 通过 `CampaignDefinitionRevision.revisionNumber` 单调递增,历史 revision 不可变、只读可查 |

### 1.2 MVP 暂时不做什么

| 能力 | 原因 / 计划 |
|---|---|
| 自由文本 `custom` 约束 DSL | **v0.2 变更**:直接删除 `custom` 分支(见 §2.8)。约束改为三个具体判别联合类型,不再保留"未来 DSL 占位符"路径(解决 v0.1 §9-2 开放问题) |
| 一个 `CampaignDefinition` 对应多个 `CampaignRun` | MVP 阶段一个 `CampaignRun` 引用一个不可变 revision;`headRevisionId` 演进与多 Run 并存是 v1+ 扩展点(见 §9-1) |
| 真实分布式数据库 / 多副本 / 迁移工具 | MVP 只用单文件 SQLite;迁移到 Postgres 等是 v1+ 事项 |
| BoFire 后端接入、双后端自动选型 | 同 v0.1,能力矩阵已在 §5 修正并绑定版本 |
| LLM/Agent 自动产出推荐数值 | 同 v0.1,见 §6 |
| 化学/分子式模态 | 同 v0.1,不在范围内 |
| DoE 最优设计 | 同 v0.1;非线性约束仍不做,但线性/基数约束已进入判别联合(§2.8) |
| 迁移学习 / SHAP 深化 / 多租户 / 并发编辑 | 同 v0.1 |
| 目标权重的 UI 编辑 | Desirability 权重已进入模型(§2.7);MVP 是否开放 UI 编辑见 §9-3 |

---

## 2. 平台统一领域模型 v0.2

### 2.0 实体总览与拆分原则

v0.2 的核心结构动作是引入**不可变 revision**,把 v0.1 中 `CampaignDefinition`(带 `version` 的可变实体)进一步拆成"逻辑容器 + 不可变快照"两层,并把 `OptimizationPolicy` 从"问题定义"移到"执行"侧:

```
CampaignDefinition            ← 逻辑容器(无生命周期、无问题内容)
  └─ headRevisionId ─────────→ CampaignDefinitionRevision(当前 head)

CampaignDefinitionRevision    ← 不可变问题定义快照(revisionNumber 单调递增,内容一经创建不可改)
  ├─ ParameterSpec[]                (判别联合: Continuous | Discrete | Categorical)
  ├─ OutputSpec[]                   (测什么)
  ├─ TargetSpec[]                   (每个 output 的优化方向;v0.2 由逐输出 ObjectivePolicy 改名)
  ├─ ObjectivePolicy                (唯一;campaign 级判别联合: Single | Desirability | Pareto)
  └─ ConstraintSpec[]               (判别联合: LinearEquality | LinearInequality | Cardinality;可为空)

CampaignRun                   ← 执行状态机(引用一个不可变 revision)
  ├─ definitionRevisionId ───→ CampaignDefinitionRevision(v0.2 取代 currentDefinitionVersion)
  ├─ OptimizationPolicy             (v0.2 从 revision 移到此处;含类型化 strategyConfig)
  ├─ ExperimentRound[]
  │    └─ ExperimentRun[]
  │         └─ Measurement[]        (含 revision / supersedesMeasurementId)
  ├─ RecommendationBatch[]          (含不可变 inputSnapshot + algorithmConfig + environment)
  └─ DecisionLog[]                  (append-only)
```

**为什么再拆一层不可变 revision(对应 req 5)**:v0.1 的 `CampaignDefinition.version` 是"就地自增"的可变实体——同一条记录的 `parameters` 字段今天是 A、明天被编辑成 B,只有 `version` 号在变。这带来两个问题:(1) `RecommendationBatch.inputSnapshotDefinitionVersion` 指向的"version 3"到底是哪一刻的内容,依赖额外的历史表才能还原;(2) 无法用一个稳定 id 引用"永远不变的那一份定义"。v0.2 把每次编辑固化为一条独立、不可变的 `CampaignDefinitionRevision` 记录(有自己的 `id`),`CampaignRun` 直接用 `definitionRevisionId` 引用它。这样"这个 Run 当初基于哪一份定义跑的"是一个不可变外键,不需要靠版本号回溯重建。

**MVP 约束**:创建 Campaign 时,`CampaignDefinition`(容器)+ 第一条 `CampaignDefinitionRevision`(revisionNumber=1)+ `CampaignRun`(status=`Draft`,`definitionRevisionId` 指向 rev1)成对创建。编辑问题定义 = 追加一条新 revision 并把容器 `headRevisionId` 前移;若关联 Run 处于可编辑状态,则该 Run 的 `definitionRevisionId` rebase 到新 revision 且退回 `Draft`(见 §3)。历史 revision 永久保留、只读。1:N(一个 Definition 多个 Run)是 v1+ 开放问题(见 §9-1)。

所有类型均为平台自有类型,不直接引用 `bofire.*`/`baybe.*` 的类(理由见 §5 末尾)。类型记法:`string`/`number`/`boolean`/`enum(...)`/`array<T>`/`object`/`T?`(可选)。

### 2.1 CampaignDefinition(v0.2 退化为逻辑容器)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | 全局唯一,稳定不变 |
| `name` | string | ✓ | 非空,平台内唯一 |
| `goal` | string | — | 自由文本 |
| `headRevisionId` | string | ✓ | 指向当前生效的 `CampaignDefinitionRevision.id`;每次编辑产生新 revision 后前移 |
| `createdAt` | string (ISO-8601) | ✓ | 容器创建时间 |
| `createdBy` | string | ✓ | |
| `updatedAt` | string (ISO-8601) | ✓ | 最近一次 `headRevisionId` 变更时间 |

**关键变化**:v0.1 中 `CampaignDefinition` 持有 `parameters`/`outputs`/`objectivePolicies`/`constraints`/`optimizationPolicy` 与 `version`;v0.2 中这些内容全部下沉到 `CampaignDefinitionRevision`,容器只保留稳定标识(id/name/goal)与"当前 head"指针。`name` 唯一性、`goal` 描述属于容器级(跨 revision 不变的元信息)。

### 2.2 CampaignDefinitionRevision(v0.2 新增,不可变)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | 全局唯一;一经创建,所有内容字段不可变 |
| `campaignDefinitionId` | string | ✓ | 归属容器 |
| `revisionNumber` | number (int, ≥1) | ✓ | 同一容器内单调递增;取代 v0.1 的 `CampaignDefinition.version` |
| `parentRevisionId` | string? | — | 上一条 revision;rev1 为 null。构成不可变链,便于 diff/回溯 |
| `parameters` | array<ParameterSpec> | ✓ | 至少 1 项才能通过设计空间校验(见 §2.4) |
| `outputs` | array<OutputSpec> | ✓ | 至少 1 项(见 §2.5) |
| `targets` | array<TargetSpec> | ✓ | 每个 `outputId` 恰好被一个 `TargetSpec` 引用(见 §2.6) |
| `objectivePolicy` | ObjectivePolicy | ✓ | **唯一一个** campaign 级策略判别联合(见 §2.7) |
| `constraints` | array<ConstraintSpec> | ✓(可空数组) | 判别联合元素(见 §2.8),空数组表示"没有配置约束" |
| `constraintsConfirmed` | boolean | ✓ | `true`=用户已走完确认流程(即使结论是"不需要约束");`false`=尚未决定 |
| `constraintsConfirmedAt` | string (ISO-8601)? | 当 `constraintsConfirmed=true` 时必填 | |
| `createdAt` | string (ISO-8601) | ✓ | revision 固化时间 |
| `createdBy` | string | ✓ | |

**关键约束**:
- revision 内 `parameters`/`outputs`/`targets`/`constraints` 各自数组内 `id` 唯一;`parameters`/`outputs` 的 `name` 各自命名空间内唯一(大小写不敏感)。
- **设计空间校验通过的充要条件**:`parameters` 非空且每项合法 + `outputs`/`targets` 非空且每项合法 + `objectivePolicy` 合法(Desirability 必须显式给全缩放边界与权重决策,§2.7)+ `constraintsConfirmed = true` + `constraints` 数组中不存在"当前不可执行"的元素(可执行性由 `validate()` 实时派生,§2.8/§4,不再持久化)。
- revision **不可变**:任何"编辑"都不是就地改这条记录,而是以它为 `parentRevisionId` 追加一条新 revision。因此 `CampaignRun` 通过 `definitionRevisionId` 引用到的定义永远是它当初看到的那一份。

### 2.3 CampaignRun(v0.2:引用不可变 revision + 承载 OptimizationPolicy + 新增状态)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | |
| `campaignDefinitionId` | string | ✓ | 冗余外键,便于按容器聚合查询 |
| `definitionRevisionId` | string | ✓ | **v0.2 变更**:取代 v0.1 的 `currentDefinitionVersion`;指向一条不可变 `CampaignDefinitionRevision`。**首个 `RecommendationBatch` 之前**(Run 停在 `Draft`/`DesignSpaceValidated`)编辑定义可 rebase 到新 revision 并退回 `Draft`;**首个 Batch 之后此字段冻结**,改定义须 fork 新 Run(§3.6) |
| `status` | enum(`Draft`,`DesignSpaceValidated`,`RecommendationsPending`,`AwaitingMeasurements`,`RoundClosed`,`Completed`,`Archived`) | ✓ | **v0.2 新增 `AwaitingMeasurements`**(见 §3) |
| `optimizationPolicy` | OptimizationPolicy | ✓ | **v0.2 变更**:从 revision 移到此处(见 §2.9)。批次/后端/种子/类型化策略配置属于"这次怎么跑",不属于"问题是什么" |
| `round` | number (int, ≥0) | ✓ | 已生成的 `RecommendationBatch` 数量;0 表示尚未推荐 |
| `budgetTotal` | number (int, ≥1) | ✓ | 预算实验总次数上限;`status=Draft` 期间可编辑 |
| `budgetUsed` | number (int, ≥0) | ✓ | 精确定义(同 v0.1):等于该 Run 下 `ExperimentRun.status ∈ {Completed, Failed}` 的记录数;`Pending`/`Cancelled` 不计入。可从 `ExperimentRun` 表重新推导的物化计数 |
| `createdAt` / `updatedAt` | string (ISO-8601) | ✓ | |
| `createdBy` | string | ✓ | |

**关键约束**:`round`(生成过几批推荐)与 `budgetUsed`(物理执行过几次实验)是独立计数维度,不必同步增长。`OptimizationPolicy` 移到 Run 后,"同一份不可变 revision 用不同策略跑多个 Run"在模型层已经成立(MVP 仍 1 Run,能力打开留给 v1+,见 §9-1)。

### 2.4 ParameterSpec(判别联合类型,同 v0.1)

判别字段 `type`;后端三分(Continuous/Discrete/Categorical)而非前端二分,理由同 v0.1(`Discrete` 取值是可排序数值,`Categorical` 是无序标签)。

```
type ParameterSpec = ContinuousParameterSpec | DiscreteParameterSpec | CategoricalParameterSpec

interface ParameterSpecBase { id: string; name: string; unit?: string; description?: string }

interface ContinuousParameterSpec extends ParameterSpecBase {
  type: 'Continuous'
  bounds: { lower: number; upper: number; stepsize?: number }   // lower < upper;stepsize 为 v1+ 预留
}
interface DiscreteParameterSpec extends ParameterSpecBase {
  type: 'Discrete'
  values: number[]   // 至少 1 个,去重排序
}
interface CategoricalParameterSpec extends ParameterSpecBase {
  type: 'Categorical'
  values: string[]   // 至少 1 个非空白值,去重,大小写敏感
}
```

**必填规则**:`name` 缺失/重复(忽略大小写)→ 拒绝;`Continuous` 的 `bounds` 缺失或 `lower >= upper` → 拒绝;`Discrete` 的 `values` 为空或含非数值 → 拒绝;`Categorical` 的 `values` 全空白 → 拒绝。`Molecular`/`Substance` 不是本联合的合法 variant,`validate()` 以"未知 type"报错。

### 2.5 OutputSpec(同 v0.1)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | Campaign 内唯一 |
| `name` | string | ✓ | 非空,Campaign 内唯一 |
| `unit` | string | — | 可为空字符串 |
| `description` | string | — | 可为空字符串 |

只描述"这是一个会被测量的量",不含优化方向信息。校验:`outputs.length === 0` → 拒绝。

### 2.6 TargetSpec(v0.2:由逐输出 `ObjectivePolicy` 改名,对应 req 1)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `outputId` | string | ✓ | 引用 `OutputSpec.id`;每个 `outputId` 恰好被一个 `TargetSpec` 引用(见 §9-10) |
| `direction` | enum(`Maximize`,`Minimize`) | ✓ | `Target`/`CloseToTarget` 为 v1+ 预留 |
| `targetValue` | number? | — | 仅 `direction='Target'` 时使用,v1+ 预留 |

**改名动机(req 1)**:v0.1 把"每个输出往哪个方向优化"叫做 `ObjectivePolicy`,与"整个 campaign 用什么标量化/多目标策略"混用了同一个名字。v0.2 把逐输出这一层降级为纯粹的 `TargetSpec`(只声明单个目标的方向,类比 BayBE 的 `NumericalTarget` / BoFire 的 `MaximizeObjective`),并把"campaign 级策略"这一真正的 policy 语义独立出来放到 §2.7 的唯一 `ObjectivePolicy`。注意:`weight` 字段从这里**移除**——权重只在 `DesirabilityObjectivePolicy.entries[].weight` 中出现(§2.7),避免"权重挂在哪里"的二义性。

**必填规则**:`objectivePolicy` 引用的 target 必须存在;`targets.length === 0` → 拒绝(至少一个优化目标)。

### 2.7 ObjectivePolicy(v0.2 新增:唯一的 campaign 级判别联合,对应 req 2/3)

每条 `CampaignDefinitionRevision` 恰好持有**一个** `objectivePolicy`,判别字段 `kind`:

```
type ObjectivePolicy =
  | SingleObjectivePolicy
  | DesirabilityObjectivePolicy
  | ParetoObjectivePolicy

interface SingleObjectivePolicy {
  kind: 'Single'
  targetId: string                 // 恰好引用一个 TargetSpec;要求 targets.length === 1
}

interface DesirabilityObjectivePolicy {
  kind: 'Desirability'
  entries: DesirabilityEntry[]      // 每个 TargetSpec 对应一条,长度 === targets.length
  weightingMode: 'explicit' | 'equal'
  scalarizer: 'MEAN' | 'GEOM_MEAN'  // 默认 GEOM_MEAN(与 BayBE DesirabilityObjective 默认一致)
}

interface DesirabilityEntry {
  targetId: string
  transformation: 'NormalizedRamp'  // MVP 仅此一种;Target/Triangular/Bell 移到 v1(见下)
  cutoffs: { lower: number; upper: number }  // 显式缩放边界,不允许运行时猜测
  weight: number                    // weightingMode='equal' 时由校验器强制全部相等
}

interface ParetoObjectivePolicy {
  kind: 'Pareto'
  targetIds: string[]               // ≥2,引用多个 TargetSpec;无标量化,产出 Pareto 前沿
}
```

**Desirability 显式化(对应 req 3,删除运行时 min/max 猜测)**:
- v0.1 §5.2 曾允许"`DesirabilityObjective` 缩放边界未提供时,Adapter 用目标历史观测的 min/max 运行时估计"。**v0.2 删除该降级路径**。理由:用历史观测 min/max 现算缩放边界,会让"同一组参数、同一份定义"在不同轮次因历史数据不同而得到不同 desirability,破坏可复现性,且边界随数据漂移会悄悄改变优化目标。
- `DesirabilityEntry.cutoffs` 必须**显式**给出 `lower`+`upper`(MVP 的 `NormalizedRamp` 用一段线性斜坡把目标值归一化到 [0,1])。缺失或非法(如 `lower >= upper`)→ `validate_definition()` 返回 `severity='blocking'` issue,`generate_initial_design()`/`recommend()` 拒绝执行。
- `weightingMode='equal'` 是一个**显式决策**(等权),不是"没填权重"的默认兜底;此时所有 `entries[].weight` 必须相等(校验器强制),持久化时仍写出具体相等值,保证快照自解释。
- `direction`(Maximize/Minimize)来自被引用的 `TargetSpec`;`NormalizedRamp` 据此决定哪一端 cutoff 归一为 1(好)、哪一端归一为 0。
- **MVP 变换范围收窄(本次 req 5)**:MVP 目标只保留 `Maximize`/`Minimize`(§2.6)+ 唯一变换 `NormalizedRamp(lower, upper)`。v0.1 曾列出的 `TRIANGULAR`/`BELL` 变换及 `direction='Target'`/`CloseToTarget`(需要 `peak`/`targetValue` 的"越接近某值越好"语义)**整体移到 v1**。理由:线性斜坡的心智模型最简单、与 BayBE `NumericalTarget` 的 bounds 归一化直接对应,先把"显式缩放边界 + 权重 + scalarizer"这套骨架跑通;三角/钟形/靶值在建模上都要额外的 peak 语义与 UI,留到 v1 再开(见 §9-3)。`cutoffs` 因此不再需要 `peak` 字段。

**校验规则**:`kind='Single'` 要求 `targets.length===1`;`kind='Desirability'` 要求 `entries` 覆盖全部 target 且一一对应;`kind='Pareto'` 要求 `targetIds.length>=2` 且均存在。`kind` 与 `targets` 数量矛盾(如 Single 却有 3 个 target)→ blocking。

### 2.8 ConstraintSpec(v0.2:判别联合,对应 req 6/7)

判别字段 `kind`,删除 v0.1 的 `choice='fixed-sum'|'custom'` 与自由文本 `custom`:

```
type ConstraintSpec =
  | LinearEqualityConstraintSpec
  | LinearInequalityConstraintSpec
  | CardinalityConstraintSpec

interface ConstraintBase { id: string; resolvedAt?: string }

interface LinearEqualityConstraintSpec extends ConstraintBase {
  kind: 'LinearEquality'
  parameterIds: string[]     // 至少 2 项,引用 ParameterSpec.id
  coefficients: number[]     // 与 parameterIds 等长
  rhs: number                // Σ coefficients[i] * value(parameterIds[i]) = rhs
}
interface LinearInequalityConstraintSpec extends ConstraintBase {
  kind: 'LinearInequality'
  parameterIds: string[]     // 至少 2 项
  coefficients: number[]
  operator: '<=' | '>='
  rhs: number                // Σ ... (operator) rhs
}
interface CardinalityConstraintSpec extends ConstraintBase {
  kind: 'Cardinality'
  parameterIds: string[]     // 至少 2 项
  minCardinality: number     // ≥0
  maxCardinality: number     // ≥ minCardinality 且 ≤ parameterIds.length
}
```

**FixedSum 只作为 UI 映射(req 6)**:前端"固定和(如 Resin+Hardener=100)"不再是一个后端约束类型。前端把它映射为 `LinearEqualityConstraintSpec`:`parameterIds=[resin,hardener]`、`coefficients=[1,1]`、`rhs=100`。后端只认识判别联合三类,不认识 `fixed-sum` 这个词。

**删除 `custom` 自由文本(req 6)**:v0.1 保留的 `choice='custom'` + `customExpression` 自由文本(需未来 DSL 解析)整体删除,解决 v0.1 §9-2 开放问题。用户想表达的线性/基数约束现在都有具体类型;超出这三类的需求(非线性、跨组)在 MVP 阶段由 `validate()` 明确拒绝(§5.3),而不是塞进一个易卡住的自由文本框。

**`executable`/`parsedExpression` 删除,改为派生(req 7)**:v0.1 在 `ConstraintSpec` 上持久化 `executable`(boolean)与 `parsedExpression`。v0.2 **不持久化**任何可执行性字段。约束是否"当前可执行"由 `validate()` 每次实时派生:检查 `parameterIds` 是否仍存在于当前 revision 的 `parameters`、类型是否匹配后端能力、`coefficients` 长度是否对齐、Cardinality 边界是否合法(`0 <= min <= max <= len`,且非"min=0 且 max=len"的空约束)。派生结果进入 `ValidationResult.issues`(见 §4),不写回实体。理由:持久化的布尔标记会与实际参数状态脱节(参数被删/改类型后标记未更新即成谎报),让"可执行性"永远是当前定义的纯函数更安全。

**阻断规则(承接 v0.1 req 4)**:只要某条约束当前不可执行(引用了已删除参数、类型不被后端支持等),`validate()` 必须产出至少一条 `severity='blocking'` issue,`generate_initial_design()`/`recommend()` 以 `ValidationError` 拒绝。

### 2.9 OptimizationPolicy(v0.2:移到 CampaignRun + 类型化 strategyConfig,对应 req 4)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `backendName` | string | ✓ | MVP 固定 `"baybe"` |
| `batchSize` | number (int, ≥1) | ✓ | 每轮默认候选数;`generate_initial_design()`/`recommend()` 可显式传参覆盖本次调用(不回写) |
| `seedPolicy` | enum(`Fixed`,`AutoGenerated`) | ✓ | |
| `seedValue` | number (int)? | 当 `seedPolicy='Fixed'` 时必填 | |
| `strategyConfig` | StrategyConfig | ✓ | **v0.2 变更**:类型化判别联合,不再是"只校验是否合法 JSON"的不透明 object |

```
type StrategyConfig = TwoPhaseMetaConfig | BotorchConfig

interface TwoPhaseMetaConfig {
  kind: 'TwoPhaseMeta'
  initialRecommender: 'RandomRecommender' | 'FPSRecommender'   // 冷启动阶段
  switchAfter: number            // int ≥1,累计观测达到后切换到 BO 阶段
  remainSwitched: boolean
  acquisitionFunction: 'qLogEI' | 'qLogNEHVI' | 'qLogNParEGO'  // BO 阶段采集函数
}

interface BotorchConfig {
  kind: 'Botorch'
  acquisitionFunction: 'qLogEI' | 'qLogNEHVI' | 'qLogNParEGO'
}
```

**移动理由(req 4)**:v0.1 把 `OptimizationPolicy` 放在 `CampaignDefinition`(问题定义)里,意味着"换个 batchSize/换个采集函数"要污染问题定义版本。v0.2 把它移到 `CampaignRun`——"用什么策略跑"属于执行决策,与"问题是什么"正交。

**类型化理由(req 4)**:v0.1 的 `recommenderConfig` 是不透明 object,只做"是否合法 JSON"校验,用户/Agent 能塞入后端不支持甚至危险的超参。v0.2 用判别联合把合法字段固定下来,`validate()` 可逐字段校验(如 `switchAfter>=1`、`acquisitionFunction` 在枚举内、多目标场景才允许 `qLogNEHVI`/`qLogNParEGO`)。解决 v0.1 §9-11 开放问题的主体部分。`strategyConfig.kind` 缺省时由 Adapter 依 `capabilities()` 选默认(BayBE 路径默认 `TwoPhaseMeta` + `RandomRecommender`)。

### 2.10 ExperimentRound(同 v0.1)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `roundNumber` | number (int, ≥1) | ✓ | 与 `RecommendationBatch.roundNumber` 一一对应 |
| `recommendationBatchId` | string | ✓ | |
| `experimentRunIds` | array<string> | ✓ | 初始为空,随执行增长 |
| `openedAt` | string (ISO-8601) | ✓ | |
| `closedAt` | string (ISO-8601)? | — | |
| `status` | enum(`Open`,`Closed`) | ✓ | |

**关闭条件(v0.2 细化)**:该轮全部 `ExperimentRun.status ∈ {Completed, Failed, Cancelled}`(物理执行完/放弃),**且**通过 partial-measurement gating(§3/§4)——即"结果已就绪到足以支撑下一轮 `recommend()`"。这对应新的 `AwaitingMeasurements` 中间态。

### 2.11 ExperimentRun(同 v0.1)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `experimentRoundId` | string | ✓ | |
| `recommendationCandidateId` | string? | — | 回填自某候选则关联;人工补充实验可为空 |
| `parameterValues` | object (map: `ParameterSpec.id` → string \| number) | ✓ | 每个已配置参数必须有值 |
| `status` | enum(`Pending`,`Completed`,`Failed`,`Cancelled`) | ✓ | 语义同 v0.1;`Completed`=物理执行完,不代表读数已齐 |
| `executedAt` | string (ISO-8601)? | `status ∈ {Completed,Failed}` 时必填 | |
| `executedBy` | string? | `status ∈ {Completed,Failed}` 时必填 | |
| `notes` | string | — | |

### 2.12 Measurement(v0.2:新增 revision / supersedesMeasurementId,对应 req 9)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `experimentRunId` | string | ✓ | |
| `outputId` | string | ✓ | 引用 `OutputSpec.id` |
| `value` | number | ✓ | |
| `status` | enum(`Valid`,`Invalid`) | ✓ | `Invalid`:异常读数,存档但不参与拟合 |
| `revision` | number (int, ≥1) | ✓ | **v0.2 新增**:同一 `(experimentRunId, outputId)` 下读数的版本序号,首条为 1 |
| `supersedesMeasurementId` | string? | — | **v0.2 新增**:若本条修正/取代了旧读数,指向被取代者的 `id`;首条为 null,构成不可变取代链 |
| `recordedAt` | string (ISO-8601) | ✓ | |
| `recordedBy` | string | ✓ | |
| `notes` | string? | — | |

**版本化读数规则(req 9)**:v0.1 用"同 `(experimentRunId, outputId)` 允许多条,取时间最新的一条 Valid"隐式表达修正,靠时间戳排序脆弱且无法表达"这条明确取代那条"。v0.2 显式化:
- 修正历史误差时,**新增**一条 `revision = 旧.revision + 1`、`supersedesMeasurementId = 旧.id` 的记录,而不是改旧记录(旧记录永久保留供审计)。
- **活跃读数**(参与 `recommend()` 输入构建)= 每个 `(experimentRunId, outputId)` 取"没有被任何其它记录 supersede 的、`status='Valid'` 的链头"。
- 取代动作写 `DecisionLog(action='MeasurementSuperseded')`。

### 2.13 RecommendationBatch(v0.2:algorithmConfig 新增 environment,对应 req 11)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `roundNumber` | number (int, ≥1) | ✓ | |
| `generatedAt` | string (ISO-8601) | ✓ | |
| `inputSnapshot` | object | ✓ | 生成时 `{parameters, outputs, targets, objectivePolicy, constraints}` 的**深拷贝** + 全部参与拟合的活跃 `ExperimentRun`/`Measurement` 内联拷贝(引用 `definitionRevisionId` 但内容内联,不靠外部回溯) |
| `algorithmConfig` | object | ✓ | 见下(含 `environment`) |
| `candidates` | array<RecommendationCandidate> | ✓ | 长度 = 本次实际 `batchSize` |
| `status` | enum(`Proposed`,`PartiallyExecuted`,`FullyExecuted`,`Superseded`) | ✓ | |

```
algorithmConfig = {
  backendName, backendVersion, backendCommit,
  strategyKind,                 // 'TwoPhaseMeta' | 'Botorch',对齐 OptimizationPolicy.strategyConfig.kind
  hyperparameters: object,      // 从类型化 strategyConfig 展开的实际取值
  acquisitionFunction, seed,    // seed 不允许 null;自动生成也须回写实际值
  environment: {                // v0.2 新增(req 11),支撑复现
    pythonVersion,              // "3.11.15"
    torchVersion,               // "2.13.0+cpu"
    botorchVersion,             // "0.18.1"
    dependencyLockHash          // "sha256:...",锁文件内容哈希
  }
}
```

`RecommendationCandidate` 子对象:`id`、`parameterValues`(map)、`predictedMean`(map)?、`predictedSd`(map)?、`desirability`(number)?。冷启动初始设计无代理模型时,预测字段为 null 是合法且诚实的。

**谁创建 `RecommendationBatch`(本次 req 2)**:Adapter **不**创建、不持久化 `RecommendationBatch`。Adapter 只返回纯 `RecommendationResult`(`candidates` + `algorithmConfig` + 可选 `diagnostics`,见 §4.1)。**Application Service** 负责把 result 组装成 `RecommendationBatch`:分配 `id`、计算 `roundNumber`(初始设计=1,迭代=`run.round+1`)、`generatedAt=now`、构建 `inputSnapshot`(对 revision + 参与拟合的活跃 `ExperimentRun`/`Measurement` 做全量深拷贝)、置 `status='Proposed'`,再在同一事务内落盘并执行状态转换与 `DecisionLog`(§4.1 时序)。这样"深拷贝快照、事务、状态机"这些平台关注点集中在服务层,Adapter 保持无副作用的纯计算,换后端不影响编排。

### 2.14 DecisionLog(v0.2:action 枚举更新)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `timestamp` | string (ISO-8601) | ✓ | |
| `actor` | string | ✓ | 用户 id / `"agent:<name>"` / `"system"` |
| `action` | enum(见下) | ✓ | |
| `definitionRevisionId` | string | ✓ | **v0.2 变更**:取代 v0.1 的 `campaignDefinitionVersion`,直接指向不可变 revision |
| `payload` | object | — | |
| `relatedEntityId` | string? | — | |

**`action` 枚举(v0.2)**:`CampaignCreated`、`DefinitionRevisionCreated`(取代 v0.1 `CampaignDefinitionEdited`,每次编辑固化一条新 revision)、`ConstraintsConfirmed`、`DesignSpaceValidated`、`DesignSpaceValidationFailed`、`InitialDesignGenerated`、`RecommendationRequested`、`ExperimentRunExecuted`、`MeasurementRecorded`、`MeasurementSuperseded`(**v0.2 新增**,对应 §2.12)、`RoundClosed`、`RoundAborted`(**本次新增**,对应 §3.5 `abort_round()`)、`RunCompleted`、`RunArchived`、`RunReopened`。**append-only**,不可更新或删除。

---

## 3. CampaignRun 生命周期状态机(v0.2:新增 AwaitingMeasurements)

状态机主体仍是 `CampaignRun.status`;`CampaignDefinitionRevision` 不可变、无状态;`CampaignDefinition` 容器无状态。是否可编辑取决于关联 `CampaignRun` 的状态(见 §3.3)。

### 3.1 状态定义

| 状态 | 含义 |
|---|---|
| `Draft` | 关联 revision 尚未通过设计空间校验;或校验通过后定义被编辑(`definitionRevisionId` rebase 到新 revision) |
| `DesignSpaceValidated` | `validate()` 通过(parameters/outputs/targets/唯一 objectivePolicy 合法,Desirability 缩放边界显式且合法,`constraintsConfirmed=true`,无不可执行约束);尚未生成推荐 |
| `RecommendationsPending` | 存在 `status ∈ {Proposed, PartiallyExecuted}` 的 `RecommendationBatch`,等待 `ExperimentRun` 物理执行 |
| `AwaitingMeasurements` | **v0.2 新增**:本轮全部 `ExperimentRun.status ∈ {Completed,Failed,Cancelled}`(物理执行完),但活跃 `Measurement` 尚未就绪到可支撑下一轮 `recommend()`(见 §3.5 gating) |
| `RoundClosed` | 本轮结果已就绪(通过 gating),`ExperimentRound` 已关闭,等待下一步决策 |
| `Completed` | 用户主动结束该 Run,只读 |
| `Archived` | 归档,完全只读,不出现在默认列表 |

### 3.2 状态转换

```
Draft --validate_definition() 通过--> DesignSpaceValidated
Draft --validate_definition() 失败--> Draft (停留,附 blocking issue 列表)

DesignSpaceValidated --编辑定义(追加新 revision)--> Draft (首个 Batch 前,definitionRevisionId rebase 到新 revision)
DesignSpaceValidated --generate_initial_design() 成功--> RecommendationsPending (round=1;此后定义/策略冻结,见 §3.6)

RecommendationsPending --全部 ExperimentRun ∈ {Completed,Failed,Cancelled}--> AwaitingMeasurements
RecommendationsPending --abort_round()--> RoundClosed (未完成候选置 Cancelled,Batch 置 Superseded)
RecommendationsPending --编辑定义/策略--> 拒绝(见 §3.6);如需变更,先 abort_round() 再 fork 新 Run

AwaitingMeasurements --回填 Measurement 直到 assess_readiness().ready 且 close_round()--> RoundClosed
AwaitingMeasurements --close_round(discard_incomplete=true) 丢弃残缺行后通过 gating--> RoundClosed
AwaitingMeasurements --abort_round() 放弃本轮--> RoundClosed
AwaitingMeasurements --回填中,ready=false--> AwaitingMeasurements (停留)

RoundClosed --recommend() 成功--> RecommendationsPending (round += 1)
RoundClosed --编辑定义/策略--> fork 新 CampaignRun(§3.6);当前 Run 定义/策略已冻结,不回 Draft
RoundClosed --mark_completed()--> Completed

Completed --reopen()(显式管理操作,写 RunReopened)--> RoundClosed
Completed --archive()--> Archived
DesignSpaceValidated / RoundClosed --archive()--> Archived
(Draft / RecommendationsPending / AwaitingMeasurements 不可直接归档,须先完成或退回)

Archived --(终态,无出边)--
```

### 3.3 各状态允许的操作

| 操作 | Draft | DesignSpaceValidated | RecommendationsPending | AwaitingMeasurements | RoundClosed | Completed | Archived |
|---|---|---|---|---|---|---|---|
| 编辑定义(追加 revision) | ✓ | ✓(退回 Draft) | ✗ | ✗ | ✓(fork 新 Run,§3.6) | ✗ | ✗ |
| 编辑/确认 constraints | ✓ | ✓(退回 Draft) | ✗ | ✗ | ✓(fork 新 Run,§3.6) | ✗ | ✗ |
| `validate_definition()` | ✓ | ✓(空操作) | ✓(只读) | ✓(只读) | ✓(只读) | ✓(只读) | ✓(只读) |
| `generate_initial_design()` | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 执行 `ExperimentRun` | ✗ | ✗ | ✓ | ✗(轮次执行已完) | ✗ | ✗ | ✗ |
| 回填/取代 `Measurement` | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ |
| `close_round([discard_incomplete])`(gating 门禁) | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| `abort_round()`(放弃本轮,§3.5) | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ |
| `recommend()` | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `explain()` | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `mark_completed()` | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `archive()` | ✗ | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ |
| 查看历史 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**关键点**:`Measurement` 回填在 `RecommendationsPending` 与 `AwaitingMeasurements` 两个状态都允许(实验陆续测完);`recommend()` 只在 `RoundClosed` 允许,而进入 `RoundClosed` 必须通过 `close_round()` 的 `assess_readiness()` gating,或经 `close_round(discard_incomplete=true)`/`abort_round()` 显式逃生(§3.5);首个 Batch 后定义/策略冻结,`RoundClosed` 下"编辑定义"实为 fork 新 Run(§3.6)。

### 3.4 与前端三态的映射

| 前端状态 | 对应后端状态 |
|---|---|
| `Draft` | `Draft`、`DesignSpaceValidated` |
| `Active` | `RecommendationsPending`、`AwaitingMeasurements`、`RoundClosed` |
| `Completed` | `Completed`、`Archived` |

### 3.5 结果就绪门禁(assess_readiness,对应 req 10 + 本次 req 3/7)

`AwaitingMeasurements → RoundClosed`(以及随后 `recommend()`)前,由 `assess_readiness(revision, optimization_policy, experiment_runs, measurements)`(§4)**实时判定**,而非比对静态能力常量(v0.1 的 `supportsPartialMeasurements`/`minObservationsForRecommend` 已删除,见 §4):

- Adapter 依**实际的** `objectivePolicy` 决定"完整行"口径:对每个已 `Completed` 的 `ExperimentRun` 组装一行"参数值 + 各 target 活跃读数";Desirability/Pareto 需其覆盖的全部 target 均有活跃 Valid 读数才算完整行,Single 只需被引用的那个 target。MVP BayBE 标量化 Desirability 不接受缺失 target,故"只测 2/4 目标"的行被排除——这正是 §8 示例停在 `AwaitingMeasurements` 的原因。
- Adapter 依 `strategyConfig` 决定最小可用观测数(TwoPhaseMeta 冷启动阶段与 BO 阶段阈值不同),返回 `{ ready, usableRowCount, issues }`。
- `close_round()` 门禁:`ready=false` 时拒绝关轮并透传 `issues`("有效观测不足,请补齐读数、取消候选或作废本轮")。
- Agent/LLM 不得绕过该门禁强制关轮(§6)。

**避免 AwaitingMeasurements 死锁(本次 req 7)**:若个别候选永远测不出(实验失败/样品报废/成本过高而放弃),`ready` 会一直为 false,Run 卡死在 `AwaitingMeasurements`。为此提供两条**显式**逃生路径,均须真实用户触发并留痕,Agent 不得代为决定(§6):

- `abort_round()`:**放弃当前轮**。把本轮未完成的 `ExperimentRun` 置 `Cancelled`、`RecommendationBatch.status='Superseded'`、`ExperimentRound` 关闭且标记"无有效产出",Run 回到 `RoundClosed`(可据已有完整行再 `recommend()`,或 `mark_completed()`)。写 `DecisionLog(action='RoundAborted')`。
- `close_round(discard_incomplete=true)`:在"已有足够完整行、只是个别候选读数残缺"时,**显式丢弃残缺观测行**(它们不进入 `recommend()` 入模数据,但作为历史 `Measurement` 永久保留),用剩余完整行通过 gating 正常关轮。写 `DecisionLog(action='RoundClosed', payload.discardedIncomplete=true)`。

二者区别:`abort_round()` 放弃整轮、不产出下一轮输入;`close_round(discard_incomplete)` 正常关轮、只排除残缺行。是否再提供"强制关轮忽略 gating"的 `force` 口子留待产品定(§9-5)。

### 3.6 首个 RecommendationBatch 后的不可变性(本次 req 4)

一旦某 `CampaignRun` 生成了**第一个** `RecommendationBatch`(即 `generate_initial_design()` 成功、Run 进入过 `RecommendationsPending`),该 Run 的 `definitionRevisionId` 与 `OptimizationPolicy` 即**冻结、不可再修改**。理由:`RecommendationBatch.inputSnapshot`/`algorithmConfig` 已把"用哪份定义、哪套策略、哪个种子/环境"钉死为可复现事实;若允许回改 Run 的定义或策略,后续轮次会与已落盘的历史快照口径不一致,破坏"同一 Run 内前后轮可比"的前提。

- **想改定义**(参数/目标/约束/objectivePolicy):追加一条新的 `CampaignDefinitionRevision`,并**新建一个 `CampaignRun`** 引用它;旧 Run 连同其 revision、Batch、ExperimentRun、Measurement 全部保留只读。
- **想改策略**(`OptimizationPolicy`:batchSize/seed/strategyConfig):同样**新建 Run**(可复用同一 revision,配不同 policy 做对比,见 §9-1)。
- **尚未产生任何 Batch 之前**(Run 停在 `Draft`/`DesignSpaceValidated`):编辑定义仍按 §3.2 rebase 当前 Run 的 `definitionRevisionId`——此时 Run 还没对任何快照做出可复现承诺,rebase 无损。

即:`definitionRevisionId`/`OptimizationPolicy` 的可变窗口是"Run 诞生到首个 Batch 之前";其后一律 fork 新 Run。

---

## 4. OptimizerAdapter 统一接口(v0.2:删除 update() + validate 拆分 + 纯 RecommendationResult)

> 术语约定:本次将 v0.1 的单一 `validate()` 拆为 `validate_definition()`(定义/约束/Desirability 校验)与 `validate_run()`(执行侧策略校验)。本文其余小节仍以 `validate()` 泛指"设计空间校验"时,均指 `validate_definition()`;凡涉及 `strategyConfig`/`OptimizationPolicy` 的校验属 `validate_run()`。

```python
class OptimizerAdapter(Protocol):

    def capabilities(self) -> AdapterCapabilities:
        """无输入。返回该后端静态能力描述。"""

    def validate_definition(self, revision: CampaignDefinitionRevision) -> ValidationResult:
        """
        输入: 一条不可变 CampaignDefinitionRevision(parameters/outputs/targets/
              objectivePolicy/constraints/constraintsConfirmed)。仅校验"问题定义"本身,
              不涉及任何执行侧策略(OptimizationPolicy)。
        输出: ValidationResult { ok: bool, issues: list[ValidationIssue] }。
        ValidationIssue { code: str, message: str,
                          severity: 'blocking' | 'warning', relatedEntityId: str | None }。
        ok = (issues 中不存在 severity='blocking' 的项)。
        校验内容包含:
          - constraintsConfirmed 是否为真;
          - 每条 constraint 的"当前可执行性"(v0.2 实时派生,不读持久字段):参数是否存在、
            类型是否被后端支持、coefficients 是否对齐、Cardinality 边界是否合法;
          - Desirability 缩放边界(cutoffs)是否显式且合法(缺失/非法 → blocking,对应 §2.7);
          - objectivePolicy.kind 与 targets 数量/类型是否自洽。
        不产生副作用,不调用底层库的重计算逻辑。对应 §3.2 中 Draft → DesignSpaceValidated 门禁。
        """

    def validate_run(
        self, revision: CampaignDefinitionRevision,
        optimization_policy: OptimizationPolicy,
    ) -> ValidationResult:
        """
        输入: 已通过 validate_definition() 的 revision + 该 Run 的 OptimizationPolicy。
        输出: 同 validate_definition() 的 ValidationResult 结构。
        校验内容(仅执行侧,不重复 definition 校验):
          - strategyConfig 是否为受支持的判别分支且字段合法(TwoPhaseMeta / Botorch);
          - strategyConfig / acquisitionFunction 与 objectivePolicy.kind 是否相容
            (Pareto 需 qLogNEHVI/qLogNParEGO;Single/Desirability 需标量采集函数);
          - batchSize ≥ 1;seedPolicy='Fixed' 时 seedValue 必填;
          - backendName 是否为当前 Adapter 所属后端。
        v0.2 拆分动机(对应本次 req 1):v0.1 的单一 validate() 把"定义是否合法"与"这套执行
        策略是否可用"混在一起,但二者触发时机不同(定义校验是 Draft 门禁,策略校验在生成推荐前),
        且 OptimizationPolicy 已移到 CampaignRun(§2.9)。拆开后 Draft→DesignSpaceValidated 只
        依赖 validate_definition(),不会因"还没配策略"被卡住;generate_initial_design()/
        recommend() 之前再跑 validate_run(),职责边界清晰。不产生副作用。
        """

    def generate_initial_design(
        self, revision: CampaignDefinitionRevision,
        optimization_policy: OptimizationPolicy,
        batch_size: int | None = None, seed: int | None = None,
    ) -> RecommendationResult:
        """
        输入: 已通过 validate_definition()+validate_run() 的 revision + 该 Run 的 OptimizationPolicy;
              batch_size 省略则取 optimization_policy.batchSize(本次覆盖不回写);
              seed 省略则按 optimization_policy.seedPolicy 处理。
        输出: 纯 RecommendationResult(见 §4.1);Adapter 只做计算,不持久化、不改状态、
              不生成 roundNumber/inputSnapshot——这些由 Application Service 组装(§4.1)。
        前置: 关联 Run 无历史 ExperimentRun;revision 校验通过。
        错误: ValidationError / UnsupportedFeatureError / ComputationError。
        """

    def recommend(
        self, revision: CampaignDefinitionRevision,
        optimization_policy: OptimizationPolicy,
        experiment_runs: list[ExperimentRun], measurements: list[Measurement],
        batch_size: int | None = None, seed: int | None = None,
    ) -> RecommendationResult:
        """
        输入: revision + OptimizationPolicy + 截至当前的 ExperimentRun(status='Completed') +
              Measurement(活跃 Valid,取 supersedes 链头)。Adapter 内部按 partial-measurement
              能力(§3.5)决定缺读数行是入模还是排除。
        输出: 纯 RecommendationResult;不含 roundNumber、不落盘——由 Application Service
              据 run.round + 1 组装并持久化为 RecommendationBatch(见 §4.1)。
        错误: ValidationError / UnsupportedFeatureError / InsufficientDataError / ComputationError。
        """

    def assess_readiness(
        self, revision: CampaignDefinitionRevision,
        optimization_policy: OptimizationPolicy,
        experiment_runs: list[ExperimentRun], measurements: list[Measurement],
    ) -> ReadinessResult:
        """
        输入: 同 recommend() 的数据切片(不含 batch_size/seed)。
        输出: ReadinessResult { ready: bool, usableRowCount: int,
                                issues: list[ValidationIssue] }。
        语义(对应本次 req 3/7):不再依赖任何静态 capability 常量(v0.1 的
        supportsPartialMeasurements / minObservationsForRecommend 已删除),而是基于**实际的**
        objectivePolicy、strategyConfig 与当前数据实时计算"是否够开下一轮":
          - 依 objectivePolicy 决定"完整行"口径:Desirability/Pareto 需其覆盖的全部 target 均有
            活跃 Valid 读数;Single 仅需被引用的那个 target;
          - 依 strategyConfig 决定最小可用观测数:如 TwoPhaseMeta 在 switchAfter 之前仍可由
            initialRecommender 产出、不强求 BO 量级观测;进入 BO 阶段则需要足量完整行;
          - usableRowCount = 依上述口径可入模的完整观测行数。
        close_round() 门禁(§3.5)据此判定,而非比对某个后端级魔法阈值。不产生副作用。
        """

    def explain(
        self, revision: CampaignDefinitionRevision,
        experiment_runs: list[ExperimentRun], measurements: list[Measurement],
    ) -> ExplanationResult:
        """输出: ExplanationResult { featureImportance: dict, notes: str }。"""
```

**删除 `update()`(req 12)**:v0.1 的公共 `update()`(更新后端内部状态缓存的可选钩子)从 Protocol **移除**。理由:它把"是否缓存、缓存什么、何时失效"这类纯粹的后端内部性能实现细节泄露到平台公共接口,调用方需要知道"先 update 再 recommend 才快"这种隐式时序契约。v0.2 规定:任何缓存(已拟合代理模型、增量数据等)只能是各 Adapter 实现内部的私有细节,`recommend()` 自身负责"拿到全量 `experiment_runs`+`measurements` 就能算",缓存命中与否对调用方透明。平台层不感知、不依赖、不调度缓存。

**错误类型体系**:不变(`ValidationError`/`UnsupportedFeatureError`/`InsufficientDataError`/`ComputationError`/`BackendUnavailableError`)。

`AdapterCapabilities`(v0.2 更新):

| 字段 | 类型 | 说明 |
|---|---|---|
| `backendName` / `backendVersion` / `backendCommit` | string | `backendCommit` 记录 vendored 源码 git commit(见 §5.0) |
| `supportedParameterTypes` | array<enum> | `["Continuous","Discrete","Categorical"]` |
| `supportedObjectiveDirections` | array<enum> | `["Maximize","Minimize"]` |
| `supportedObjectivePolicies` | array<enum> | **v0.2 新增**:`["Single","Desirability","Pareto"]` 的子集 |
| `supportedConstraintKinds` | array<enum> | **v0.2 变更**:取代 v0.1 `supportedConstraintChoices`;取值 `["LinearEquality","LinearInequality","Cardinality"]` 的子集 |
| `supportsMultiObjective` / `supportsExplain` / `supportsOptimalDesign` | boolean | 不变 |
| `maxDiscreteCombinationEstimate` | number? | 不变 |

`supportsCardinalityConstraint`(v0.1 布尔)已并入 `supportedConstraintKinds`(是否含 `"Cardinality"`),不再单列。

**删除 `supportsPartialMeasurements` 与 `minObservationsForRecommend`(本次 req 3)**:这两个静态能力常量从 `AdapterCapabilities` **移除**。理由:"能不能用不完整的观测开下一轮"不是后端的静态属性,而取决于**具体的** objectivePolicy(Desirability/Pareto 要求全部目标齐全、Single 只需单目标)、strategyConfig(TwoPhaseMeta 冷启动阶段与 BO 阶段的最小观测量不同)与**当前实际数据**。把它压成一个布尔 + 一个魔法阈值,会在"同一后端、不同 objective/strategy/数据"时给出错误门禁。改由 `assess_readiness(revision, optimization_policy, experiment_runs, measurements)` 每次实时计算 `{ ready, usableRowCount, issues }`(见上)。`close_round()`(§3.5)直接消费该结果。

### 4.1 Adapter 输出边界与 Application Service 职责(本次 req 2/8)

**Adapter 只返回纯计算结果 `RecommendationResult`,不碰持久化与状态**:

```
interface RecommendationResult {
  candidates: RecommendationCandidate[]   // 与 §2.13 candidate 同构:parameterValues + 预测字段
  algorithmConfig: AlgorithmConfig         // 后端自报:backendName/Version/Commit、strategyKind、
                                           // hyperparameters、acquisitionFunction、seed(实际使用值)、
                                           // environment(python/torch/botorch 版本 + dependencyLockHash)
  diagnostics?: { usableRowCount?: number; notes?: string }
}

interface ReadinessResult {
  ready: boolean
  usableRowCount: number
  issues: ValidationIssue[]
}
```

`RecommendationResult` 里**没有** `id`、`roundNumber`、`generatedAt`、`inputSnapshot`、`status`——这些是持久化/编排关注点,由 Application Service 负责。

**Application Service 编排 generate_initial_design()/recommend() 的完整时序**(单个事务):
1. 前置校验:`validate_definition(revision)` 与 `validate_run(revision, optimization_policy)` 均 `ok`,否则 `ValidationError` 中止,不进入 Adapter。
2. 调 Adapter 得到 `RecommendationResult`(Adapter 内部完成建模/采样,纯函数式)。
3. **候选结果后校验(见下,req 8)**:任一候选不合法 → `ResultValidationError`,**不持久化、不改状态**。
4. 组装 `RecommendationBatch`:分配 `id`;`roundNumber = 1`(初始设计)或 `run.round + 1`;`generatedAt = now`;`inputSnapshot` = 对当前 revision + 截至此刻的 `priorExperimentRuns`/`priorMeasurements` 做**全量深拷贝**(§2.13);`algorithmConfig` = 取自 result;`candidates` = 取自 result;`status='Proposed'`。
5. 在同一事务内持久化 Batch、新建/推进 `ExperimentRound`,并执行状态转换(§3.2:`DesignSpaceValidated→RecommendationsPending` 或 `RoundClosed→RecommendationsPending`,`round += 1`)。
6. 追加 `DecisionLog`(`InitialDesignGenerated` / `RecommendationGenerated`,`actor=真实用户 id`)。

事务边界与状态转换的持久化实现见 §7 与 backend/persistence。Adapter 不感知事务、不写库、不发状态事件——这样"换后端"只替换第 2 步,编排逻辑不动。

**候选结果后校验(本次 req 8)**:Adapter 返回候选后、组装 Batch 前,由一个**与后端无关的确定性校验器**(backend/domain/validation)对每个 candidate 逐项检查,任一失败即整批拒绝(宁可拒绝也不落盘一批越界/违约候选):

| 检查项 | 规则 | 失败处理 |
|---|---|---|
| 类型 | 每个 `parameterValues[k]` 与其 `ParameterSpec.type` 匹配(Continuous→number;Discrete→数值;Categorical→字符串);键集合恰好等于 revision 的参数集合(不缺不多) | `ResultValidationError`(blocking) |
| 边界 | Continuous 落在 `[lower, upper]`;声明 `stepsize` 时按步长对齐(容差 `1e-9`) | 同上 |
| 允许值 | Discrete 值 ∈ 声明 `values`;Categorical 值 ∈ 声明 `categories` | 同上 |
| 约束 | 每个候选满足全部 `ConstraintSpec`:LinearEquality `|Σcᵢxᵢ − rhs| ≤ tol`;LinearInequality 按 operator;Cardinality 非零分量数落在 `[min,max]` | 同上 |
| 重复候选 | 同一批内两候选参数向量在容差内相同 → 视为重复;可选:与本 Run 已执行点重合也告警 | blocking(批内重复)/ warning(与历史重合) |

该校验器与 `validate_definition()` 中的"约束可执行性"复用同一套约束求值逻辑,只是作用对象不同(前者校验候选点是否满足约束,后者校验约束本身是否可执行)。Agent 不得绕过该校验直接写入候选(§6)。

---

## 5. BayBE 与 BoFire 能力映射(v0.2:约束/目标判别联合对齐 + 修正,对应 req 8)

### 5.0 版本锚点

两库以 vendored 源码树形式存在(`PYTHONPATH` 引用,未走标准 `pip install`),`importlib.metadata`/`__version__` 解析为字面量 `"unknown"`。采用两独立可核实来源交叉引用:

| 项 | BayBE | BoFire |
|---|---|---|
| CHANGELOG.md 最新声明版本 | `0.15.0`(2026-06-11) | `0.4.1`(2026-06-16) |
| vendored 源码 git commit(评审验证时) | `b939e3588aad832856c33ac055c5510f7cb76f96`(2026-07-27) | `58f01b2e9d2129e61a1d1f9f17980b0bbb98e5a6`(2026-07-27) |
| 关键依赖约束 | 经 `BotorchRecommender` 依赖 botorch | `0.4.0` 起 Breaking:要求 `botorch >= 0.18.1` |
| 本地已确认可运行环境 | Python 3.11.15 / torch 2.13.0+cpu / botorch 0.18.1(micromamba env `bo_examples`,两库共用) | 同左 |

平台实际集成时应改为标准 pip 依赖并锁定版本,届时 `pip show baybe`/`pip show bofire` 是唯一权威来源,取代本表;`algorithmConfig.environment.dependencyLockHash`(§2.13)记录锁文件哈希,`backendCommit` 记录实际构建。

### 5.1 可直接转换(判别联合逐类对齐)

| 平台模型(v0.2) | BayBE(`0.15.0` / `b939e358`) | BoFire(`0.4.1` / `58f01b2e`,botorch≥0.18.1) |
|---|---|---|
| `ContinuousParameterSpec` | `NumericalContinuousParameter` | `ContinuousInput` |
| `DiscreteParameterSpec`(`number[]`) | `NumericalDiscreteParameter` | `DiscreteInput` |
| `CategoricalParameterSpec` | `CategoricalParameter` | `CategoricalInput` |
| `TargetSpec`(单方向) | `NumericalTarget`(`minimize`/`maximize`) | `ContinuousOutput` + `MaximizeObjective`/`MinimizeObjective` |
| `SingleObjectivePolicy` | `SingleTargetObjective` | 单目标 `SoboStrategy` |
| `ParetoObjectivePolicy` | `ParetoObjective` | `MoboStrategy`(qNEHVI)/ `QparegoStrategy` |
| `LinearEqualityConstraintSpec`(连续参数) | `ContinuousLinearConstraint`(`operator='='`,`coefficients`,`rhs`) | `LinearEqualityConstraint` |
| `LinearInequalityConstraintSpec`(连续参数) | `ContinuousLinearConstraint`(`operator ∈ {'>=','<='}`) | `LinearInequalityConstraint` |

> BayBE `ContinuousLinearConstraint.operator` 实测校验集合为 `["=", ">=", "<="]`(源码 `baybe/baybe/constraints/continuous.py`),故等式/不等式共用同一个类、以 `operator` 区分。

### 5.2 仅部分支持(需能力探测 + 降级)

| 平台特性 | BayBE | BoFire | 降级/处理方式 |
|---|---|---|---|
| `DesirabilityObjectivePolicy` | `DesirabilityObjective`(`weights`,`scalarizer`,默认 `GEOM_MEAN`)+ 每 target 的 `NumericalTarget` 显式 `bounds` + 变换 | 无一等 Desirability 对象;需用加权标量化或改走 Pareto(`MoboStrategy`),映射非平凡 | **v0.2 变更(req 3)**:缩放边界(cutoffs)必须来自 `DesirabilityEntry.cutoffs` 显式值,**删除** v0.1 "未提供时用历史观测 min/max 运行时估计"的降级路径;缺失即 `blocking`,不再静默现算 |
| `LinearEqualityConstraintSpec` / `LinearInequalityConstraintSpec`(**数值离散**参数,如离散配比之和) | **`DiscreteSumConstraint`**(`numerical_only=True`;以 `ThresholdCondition` 表达和的相等/不等阈值;`coefficients` 源码支持任意非零权重、默认全 1;源码 `baybe/baybe/constraints/discrete.py`),而非 `ContinuousLinearConstraint` | `LinearEqualityConstraint`(见 §5 末尾修正:BoFire 线性约束**同时**接受 `DiscreteInput`) | **本次 req 6 限制**:平台 MVP 仅当"引用参数**全部为数值离散**且 `coefficients` **全为 1**(纯无权求和)"时映射到 `DiscreteSumConstraint`;否则 `validate_definition()` 阻断。注:coefficients 全 1 是**平台 MVP 简化**、非 BayBE 限制(见 §5 末尾修正 4) |
| `CardinalityConstraintSpec` | `ContinuousCardinalityConstraint` / `DiscreteCardinalityConstraint`(`min_cardinality` 默认 0、`ge(0)`;`max_cardinality` 默认 = 参数数) | `NChooseKConstraint`(`min_count`/`max_count`/`none_also_valid`) | **v0.2(req 8)**:`min=max=K` 表达"**恰好 K 个非零**"(BayBE 校验器只拒绝 `min>max`,故 `min==max` 合法);另注意 BayBE 拒绝 `min=0 且 max=参数数`(等于无约束)与 `max>参数数` |
| 初始实验设计(冷启动) | 无专门 DoE;靠 `TwoPhaseMetaRecommender` 随机/`FPSRecommender` 阶段 | `DoEStrategy`(默认 `DOptimalityCriterion(formula="fully-quadratic")`,另 A/G/K/I + SpaceFilling) | MVP 单后端(BayBE)`generate_initial_design()` 走随机/空间填充;`supportsOptimalDesign=true`(接入 BoFire 后)可路由 |
| 可解释性 `explain()` | `insights/shap.py` | `surrogates/feature_importance.py` | 依赖已拟合代理模型且观测达阈值,否则 `InsufficientDataError` |

### 5.3 必须拒绝或降级

| 平台特性 | 处理方式 | 理由 |
|---|---|---|
| 线性约束涉及 `Categorical` 参数 | 两后端均 `UnsupportedFeatureError`;`validate()` 出 `blocking` issue | BayBE `ContinuousLinearConstraint` 仅连续;BoFire `LinearConstraint.validate_inputs` 用 `get_keys([ContinuousInput, DiscreteInput])`,**排除** Categorical |
| 混合连续+离散的单条线性约束(BayBE 路径) | MVP(BayBE-only):`validate()` 拒绝并提示"BayBE 请按参数类型拆分为连续线性或离散和约束" | BayBE 无"混合连续离散线性"单一类;BoFire 的 `LinearEqualityConstraint` 可混合(接受两类),接入后可放开 |
| 超出线性/基数的约束(非线性、跨组、复杂 N-choose-K) | MVP `validate()` 返回 `UnsupportedFeatureError` | v0.2 删除 `custom` 自由文本后,这类需求没有承接类型,必须显式拒绝而非静默丢弃(静默丢弃危害 > 拒绝) |
| `ParameterSpec` 的 `Molecular`/`Substance` | 非合法 variant,`validate()` 报"未知参数类型" | 未纳入 MVP(§1.2) |
| 目标数量超过后端实测规模(如 >6) | `validate()` 出 `warning`,不强制阻断;底层抛 `ComputationError` 原样透传 | 两库无文档化硬上限,但过多目标显著降低代理质量 |

**修正说明(对应 req 8 的三点,均以 vendored 源码为准)**:
1. **BayBE 数值离散和约束 = `DiscreteSumConstraint`**(不是 `ContinuousLinearConstraint`):离散参数的"取值之和满足阈值/相等"由 `DiscreteSumConstraint` 的 `ThresholdCondition` 表达。已在 §5.2 对齐。
2. **BoFire 线性约束并非"只支持 ContinuousInput"**:源码 `bofire/bofire/data_models/constraints/linear.py` 中 `LinearConstraint.validate_inputs` 为 `inputs.get_keys([ContinuousInput, DiscreteInput])`,**同时接受连续与数值离散,仅排除 Categorical**,且 `coefficients` 要求 `min_length=2`。故 req 8 文本"BoFire 线性约束只支持 ContinuousInput"与源码不符,本文档按源码记录并在此显式标注偏差(见 §0 末尾)。
3. **BayBE Cardinality 可表达"恰好 K"**:`AbstractCardinalityConstraint` 校验器(`baybe/baybe/constraints/base.py`)只在 `min_cardinality > max_cardinality` 时报错,故 `min == max == K` 合法,即"恰好 K 个非零";另会拒绝 `max > 参数数` 与 `min=0 且 max=参数数`(后者等价无约束)。
4. **`DiscreteSumConstraint` 的 "coefficients 全为 1" 是平台决策,非库限制(对应本次 req 6)**:源码 `baybe/baybe/constraints/discrete.py` 中 `DiscreteSumConstraint.coefficients: tuple[float, ...]`,其校验器(第 119–135 行)只要求"长度等于参数数 且 每项非零",默认全 1(`_default_coefficients`)——即 BayBE **原生支持任意非零权重的加权和**。本次 req 6 要求"仅 coefficients 全为 1、否则 validate 阻断",是**平台 MVP 的显式简化**(先只放行最直观的等权求和,如配比之和 = 100),并非 BayBE 能力所限;放开加权和留待 v1。真正属于 BayBE 硬性限制的是 `numerical_only=True`——全部参数须为数值离散,否则库在构造期即抛 `ValueError`(源码 `constraints/validation.py:95`)。据此,`validate_definition()` 对离散和约束的阻断条件为:任一参数非数值离散,**或** 任一 `coefficients[i] != 1`。

**不变原则**:平台领域模型不直接依赖两库内部类型;转换只发生在各自 Adapter 的私有映射层。

---

## 6. Agent 的职责边界(v0.2,对应 req 14)

**Agent 可以做的**:
1. **自然语言 → 结构化草稿**:把用户描述解析为一份 `CampaignDefinitionRevision` 草稿(含 parameters/outputs/targets/objectivePolicy/constraints),但草稿需人工在 UI 确认后才固化为正式 revision。
2. **发现缺失信息**:对比草稿与 `validate()` 的 `issues`(含 `severity`),生成"缺什么"提示——尤其提示 Desirability 缺少显式 cutoffs、约束引用了不存在的参数等 `blocking` 项。
3. **请求用户确认**:约束的每一条选择、参数/目标的模糊描述,必须生成显式确认选项。
4. **调用确定性工具**:只能调用 `OptimizerAdapter` 方法或平台服务层 CRUD API。
5. **解释推荐结果**:把 `RecommendationBatch.candidates` 的预测字段与 `explain()` 结果翻译成自然语言。

**Agent 不能做的(架构强制)**:
- **不能绕过 `validate()` 直接写入 `RecommendationBatch`/`ExperimentRun`/`Measurement`**——唯一产生候选点的路径是调用 `generate_initial_design()`/`recommend()`。
- **不能自行计算/编造推荐候选的 `parameterValues`**,也不能伪造或修改 `predictedMean`/`predictedSd`/`desirability`。
- **不能代替用户确认任何一条 `constraints`**;约束"当前是否可执行"只能来自 `validate()` 的实时派生结果(§2.8/§4),不能由 Agent 的自然语言理解直接断言。
- **不能绕过 partial-measurement gating 强制 `close_round()`**(§3.5)。
- **不能代替用户提交/取代 `Measurement`**——`recordedBy` 必须是真实用户标识。
- **不能修改已写入的 `DecisionLog` 条目**。
- Agent 生成的 revision 草稿在固化生效前,必须经一次显式用户确认动作产生 `DecisionLog`(`action='DefinitionRevisionCreated'`,`actor=真实用户 id`),不能以 `actor="agent:*"` 身份直接产生对 `validate()`/`generate_initial_design()` 生效的 revision。

服务层对外只暴露一组 API,UI 和 Agent 是这组 API 的两个不同调用方。

---

## 7. 数据持久化与可复现性设计(v0.2:SQLite + 环境锁,对应 req 11)

**MVP 持久化(v0.2 变更)**:采用 **SQLite** 单文件数据库(零运维、便于随实验数据打包与备份)。实体表与 §2 领域模型一一对应:`campaign_definition`(容器)、`campaign_definition_revision`(不可变,`(campaignDefinitionId, revisionNumber)` 唯一)、`campaign_run`、`experiment_round`、`experiment_run`、`measurement`(含 `revision`/`supersedes_measurement_id`)、`recommendation_batch`(`input_snapshot`/`algorithm_config` 存 JSON blob)、`decision_log`(append-only)。迁移到 Postgres 等是 v1+ 事项(§1.2)。

| 可复现性要素 | 落在哪个模型 | 说明 |
|---|---|---|
| 问题定义版本 | `CampaignDefinitionRevision`(不可变)+ `CampaignRun.definitionRevisionId` 外键 | v0.2 核心:引用的是"永不改变的那一份",不再靠 version 号回溯重建 |
| 每轮输入数据 | `RecommendationBatch.inputSnapshot` 内联全量深拷贝 | 权威来源是快照本身;`definitionRevisionId` 仅作索引 |
| 算法名称与版本 | `RecommendationBatch.algorithmConfig`(`backendName`/`backendVersion`/`backendCommit`/`strategyKind`/`hyperparameters`/`acquisitionFunction`) | |
| **运行环境**(v0.2 新增) | `algorithmConfig.environment`:`pythonVersion`/`torchVersion`/`botorchVersion`/`dependencyLockHash` | 支撑"同环境同输入 → 同候选"的复现;`dependencyLockHash` 为锁文件内容哈希 |
| 随机种子 | `algorithmConfig.seed` | 必须记录实际使用值,不允许"种子未知";自动生成也须回写 |
| 推荐结果 | `RecommendationBatch.candidates` | |
| 读数修正轨迹 | `Measurement.revision` / `supersedesMeasurementId` | 修正即追加,旧读数永久保留(§2.12) |
| 用户确认记录 | `DecisionLog`(append-only) | `action='ConstraintsConfirmed'` 的 payload 记录完整 constraints 数组快照 + `constraintsConfirmed` |

**版本化与历史数据**:`CampaignRun` 因编辑定义退回 `Draft` 时会 rebase 到一条**新的**不可变 revision;已产生的 `ExperimentRound`/`RecommendationBatch`/`ExperimentRun`/`Measurement` 不删除、不迁移,永久关联到产生它们时的 `definitionRevisionId`。跨 revision 复用规则见 §9-7(参数 `id` 存在且类型未变则复用,否则标记 `Invalid`)。

---

## 8. 环氧涂层案例 — 完整合法 JSON 示例(v0.2,对应 req 13)

叙事时间线:`CampaignDefinition` 容器指向不可变 revision 3(已通过校验、约束已确认);`CampaignRun` 处于 `AwaitingMeasurements`——第 1 轮 4 个候选(`batchSize=4`)已全部物理执行(`budgetUsed=4`),前 3 个候选四项目标全部测完,第 4 个候选只测了 2 项(cost / actual-curing-time 待补),在 Desirability(不支持部分测量)口径下尚未通过 gating,故停在 `AwaitingMeasurements`;Run 1 的 hardness 读数演示了一次修正(rev1=76.0 被 rev2=78.4 取代)。`round=1` 与 `budgetUsed=4` 互不矛盾。

```json
{
  "campaignDefinition": {
    "id": "campaigndef-epoxy-coating-001",
    "name": "Epoxy Coating Optimization",
    "goal": "优化环氧涂层配方,希望硬度高、不易脆、固化快、成本低。",
    "headRevisionId": "campaigndefrev-epoxy-003",
    "createdAt": "2026-07-20T02:10:00Z",
    "createdBy": "user-li-wei",
    "updatedAt": "2026-07-22T06:05:00Z"
  },

  "campaignDefinitionRevision": {
    "id": "campaigndefrev-epoxy-003",
    "campaignDefinitionId": "campaigndef-epoxy-coating-001",
    "revisionNumber": 3,
    "parentRevisionId": "campaigndefrev-epoxy-002",
    "parameters": [
      { "id": "param-resin-ratio", "type": "Continuous", "name": "Resin Ratio", "unit": "%", "description": "", "bounds": { "lower": 60, "upper": 85, "stepsize": null } },
      { "id": "param-hardener-ratio", "type": "Continuous", "name": "Hardener Ratio", "unit": "%", "description": "", "bounds": { "lower": 15, "upper": 40, "stepsize": null } },
      { "id": "param-curing-temperature", "type": "Continuous", "name": "Curing Temperature", "unit": "°C", "description": "", "bounds": { "lower": 80, "upper": 160, "stepsize": null } },
      { "id": "param-curing-time", "type": "Continuous", "name": "Curing Time", "unit": "min", "description": "", "bounds": { "lower": 20, "upper": 120, "stepsize": null } }
    ],
    "outputs": [
      { "id": "output-hardness", "name": "Hardness", "unit": "", "description": "" },
      { "id": "output-brittleness", "name": "Brittleness", "unit": "", "description": "" },
      { "id": "output-cost", "name": "Cost", "unit": "", "description": "" },
      { "id": "output-actual-curing-time", "name": "Actual Curing Time", "unit": "min", "description": "" }
    ],
    "targets": [
      { "id": "target-hardness", "outputId": "output-hardness", "direction": "Maximize", "targetValue": null },
      { "id": "target-brittleness", "outputId": "output-brittleness", "direction": "Minimize", "targetValue": null },
      { "id": "target-cost", "outputId": "output-cost", "direction": "Minimize", "targetValue": null },
      { "id": "target-actual-curing-time", "outputId": "output-actual-curing-time", "direction": "Minimize", "targetValue": null }
    ],
    "objectivePolicy": {
      "kind": "Desirability",
      "weightingMode": "explicit",
      "scalarizer": "GEOM_MEAN",
      "entries": [
        { "targetId": "target-hardness", "transformation": "NormalizedRamp", "cutoffs": { "lower": 50, "upper": 90 }, "weight": 0.4 },
        { "targetId": "target-brittleness", "transformation": "NormalizedRamp", "cutoffs": { "lower": 5, "upper": 25 }, "weight": 0.3 },
        { "targetId": "target-cost", "transformation": "NormalizedRamp", "cutoffs": { "lower": 20, "upper": 60 }, "weight": 0.2 },
        { "targetId": "target-actual-curing-time", "transformation": "NormalizedRamp", "cutoffs": { "lower": 20, "upper": 120 }, "weight": 0.1 }
      ]
    },
    "constraints": [
      { "id": "constraint-resin-hardener-sum", "kind": "LinearEquality", "parameterIds": ["param-resin-ratio", "param-hardener-ratio"], "coefficients": [1, 1], "rhs": 100, "resolvedAt": "2026-07-22T06:05:00Z" }
    ],
    "constraintsConfirmed": true,
    "constraintsConfirmedAt": "2026-07-22T06:05:00Z",
    "createdAt": "2026-07-22T06:04:00Z",
    "createdBy": "user-li-wei"
  },

  "campaignRun": {
    "id": "campaignrun-epoxy-coating-001",
    "campaignDefinitionId": "campaigndef-epoxy-coating-001",
    "definitionRevisionId": "campaigndefrev-epoxy-003",
    "status": "AwaitingMeasurements",
    "optimizationPolicy": {
      "id": "optpolicy-epoxy-001",
      "backendName": "baybe",
      "batchSize": 4,
      "seedPolicy": "Fixed",
      "seedValue": 42,
      "strategyConfig": {
        "kind": "TwoPhaseMeta",
        "initialRecommender": "RandomRecommender",
        "switchAfter": 10,
        "remainSwitched": true,
        "acquisitionFunction": "qLogEI"
      }
    },
    "round": 1,
    "budgetTotal": 12,
    "budgetUsed": 4,
    "createdAt": "2026-07-20T02:10:00Z",
    "updatedAt": "2026-07-30T09:26:00Z",
    "createdBy": "user-li-wei"
  },

  "experimentRound": {
    "id": "round-epoxy-001-r1",
    "campaignRunId": "campaignrun-epoxy-coating-001",
    "roundNumber": 1,
    "recommendationBatchId": "batch-epoxy-001-r1",
    "experimentRunIds": ["exprun-r1-1", "exprun-r1-2", "exprun-r1-3", "exprun-r1-4"],
    "openedAt": "2026-07-29T09:40:05Z",
    "closedAt": null,
    "status": "Open"
  },

  "recommendationBatch": {
    "id": "batch-epoxy-001-r1",
    "campaignRunId": "campaignrun-epoxy-coating-001",
    "roundNumber": 1,
    "generatedAt": "2026-07-29T09:40:05Z",
    "inputSnapshot": {
      "definitionRevisionId": "campaigndefrev-epoxy-003",
      "parameters": [
        { "id": "param-resin-ratio", "type": "Continuous", "name": "Resin Ratio", "unit": "%", "description": "", "bounds": { "lower": 60, "upper": 85, "stepsize": null } },
        { "id": "param-hardener-ratio", "type": "Continuous", "name": "Hardener Ratio", "unit": "%", "description": "", "bounds": { "lower": 15, "upper": 40, "stepsize": null } },
        { "id": "param-curing-temperature", "type": "Continuous", "name": "Curing Temperature", "unit": "°C", "description": "", "bounds": { "lower": 80, "upper": 160, "stepsize": null } },
        { "id": "param-curing-time", "type": "Continuous", "name": "Curing Time", "unit": "min", "description": "", "bounds": { "lower": 20, "upper": 120, "stepsize": null } }
      ],
      "outputs": [
        { "id": "output-hardness", "name": "Hardness", "unit": "", "description": "" },
        { "id": "output-brittleness", "name": "Brittleness", "unit": "", "description": "" },
        { "id": "output-cost", "name": "Cost", "unit": "", "description": "" },
        { "id": "output-actual-curing-time", "name": "Actual Curing Time", "unit": "min", "description": "" }
      ],
      "targets": [
        { "id": "target-hardness", "outputId": "output-hardness", "direction": "Maximize", "targetValue": null },
        { "id": "target-brittleness", "outputId": "output-brittleness", "direction": "Minimize", "targetValue": null },
        { "id": "target-cost", "outputId": "output-cost", "direction": "Minimize", "targetValue": null },
        { "id": "target-actual-curing-time", "outputId": "output-actual-curing-time", "direction": "Minimize", "targetValue": null }
      ],
      "objectivePolicy": {
        "kind": "Desirability",
        "weightingMode": "explicit",
        "scalarizer": "GEOM_MEAN",
        "entries": [
          { "targetId": "target-hardness", "transformation": "NormalizedRamp", "cutoffs": { "lower": 50, "upper": 90 }, "weight": 0.4 },
          { "targetId": "target-brittleness", "transformation": "NormalizedRamp", "cutoffs": { "lower": 5, "upper": 25 }, "weight": 0.3 },
          { "targetId": "target-cost", "transformation": "NormalizedRamp", "cutoffs": { "lower": 20, "upper": 60 }, "weight": 0.2 },
          { "targetId": "target-actual-curing-time", "transformation": "NormalizedRamp", "cutoffs": { "lower": 20, "upper": 120 }, "weight": 0.1 }
        ]
      },
      "constraints": [
        { "id": "constraint-resin-hardener-sum", "kind": "LinearEquality", "parameterIds": ["param-resin-ratio", "param-hardener-ratio"], "coefficients": [1, 1], "rhs": 100, "resolvedAt": "2026-07-22T06:05:00Z" }
      ],
      "priorExperimentRuns": [],
      "priorMeasurements": []
    },
    "algorithmConfig": {
      "backendName": "baybe",
      "backendVersion": "0.15.0",
      "backendCommit": "b939e3588aad832856c33ac055c5510f7cb76f96",
      "strategyKind": "TwoPhaseMeta",
      "hyperparameters": { "initialRecommender": "RandomRecommender", "switchAfter": 10, "remainSwitched": true },
      "acquisitionFunction": "qLogEI",
      "seed": 42,
      "environment": {
        "pythonVersion": "3.11.15",
        "torchVersion": "2.13.0+cpu",
        "botorchVersion": "0.18.1",
        "dependencyLockHash": "sha256:3f8a9c2e5b1d7046f9a2c8e4b6d0135792ace4680bdf13579ace2468bdf01357"
      }
    },
    "status": "FullyExecuted",
    "candidates": [
      { "id": "cand-r1-1", "parameterValues": { "param-resin-ratio": 72.5, "param-hardener-ratio": 27.5, "param-curing-temperature": 120, "param-curing-time": 60 }, "predictedMean": null, "predictedSd": null, "desirability": null },
      { "id": "cand-r1-2", "parameterValues": { "param-resin-ratio": 65, "param-hardener-ratio": 35, "param-curing-temperature": 95, "param-curing-time": 90 }, "predictedMean": null, "predictedSd": null, "desirability": null },
      { "id": "cand-r1-3", "parameterValues": { "param-resin-ratio": 80, "param-hardener-ratio": 20, "param-curing-temperature": 140, "param-curing-time": 45 }, "predictedMean": null, "predictedSd": null, "desirability": null },
      { "id": "cand-r1-4", "parameterValues": { "param-resin-ratio": 68, "param-hardener-ratio": 32, "param-curing-temperature": 110, "param-curing-time": 75 }, "predictedMean": null, "predictedSd": null, "desirability": null }
    ]
  },

  "experimentRuns": [
    { "id": "exprun-r1-1", "campaignRunId": "campaignrun-epoxy-coating-001", "experimentRoundId": "round-epoxy-001-r1", "recommendationCandidateId": "cand-r1-1", "parameterValues": { "param-resin-ratio": 72.5, "param-hardener-ratio": 27.5, "param-curing-temperature": 120, "param-curing-time": 60 }, "status": "Completed", "executedAt": "2026-07-29T14:20:00Z", "executedBy": "user-li-wei", "notes": "" },
    { "id": "exprun-r1-2", "campaignRunId": "campaignrun-epoxy-coating-001", "experimentRoundId": "round-epoxy-001-r1", "recommendationCandidateId": "cand-r1-2", "parameterValues": { "param-resin-ratio": 65, "param-hardener-ratio": 35, "param-curing-temperature": 95, "param-curing-time": 90 }, "status": "Completed", "executedAt": "2026-07-29T15:05:00Z", "executedBy": "user-li-wei", "notes": "" },
    { "id": "exprun-r1-3", "campaignRunId": "campaignrun-epoxy-coating-001", "experimentRoundId": "round-epoxy-001-r1", "recommendationCandidateId": "cand-r1-3", "parameterValues": { "param-resin-ratio": 80, "param-hardener-ratio": 20, "param-curing-temperature": 140, "param-curing-time": 45 }, "status": "Completed", "executedAt": "2026-07-29T15:40:00Z", "executedBy": "user-li-wei", "notes": "" },
    { "id": "exprun-r1-4", "campaignRunId": "campaignrun-epoxy-coating-001", "experimentRoundId": "round-epoxy-001-r1", "recommendationCandidateId": "cand-r1-4", "parameterValues": { "param-resin-ratio": 68, "param-hardener-ratio": 32, "param-curing-temperature": 110, "param-curing-time": 75 }, "status": "Completed", "executedAt": "2026-07-29T16:30:00Z", "executedBy": "user-li-wei", "notes": "成本与实际固化时长尚在测试,预计次日补齐;因此本轮停留在 AwaitingMeasurements。" }
  ],

  "measurements": [
    { "id": "meas-r1-1-hardness-v1", "experimentRunId": "exprun-r1-1", "outputId": "output-hardness", "value": 76.0, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T14:22:00Z", "recordedBy": "user-li-wei", "notes": "初测读数,后发现硬度计校准偏差,已被 v2 取代。" },
    { "id": "meas-r1-1-hardness-v2", "experimentRunId": "exprun-r1-1", "outputId": "output-hardness", "value": 78.4, "status": "Valid", "revision": 2, "supersedesMeasurementId": "meas-r1-1-hardness-v1", "recordedAt": "2026-07-29T18:10:00Z", "recordedBy": "user-li-wei", "notes": "校准后复测,取代 v1。" },
    { "id": "meas-r1-1-brittleness", "experimentRunId": "exprun-r1-1", "outputId": "output-brittleness", "value": 12.1, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T14:22:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-1-cost", "experimentRunId": "exprun-r1-1", "outputId": "output-cost", "value": 42.5, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:15:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-1-act", "experimentRunId": "exprun-r1-1", "outputId": "output-actual-curing-time", "value": 58, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:15:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-2-hardness", "experimentRunId": "exprun-r1-2", "outputId": "output-hardness", "value": 71.2, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T15:07:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-2-brittleness", "experimentRunId": "exprun-r1-2", "outputId": "output-brittleness", "value": 15.8, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T15:07:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-2-cost", "experimentRunId": "exprun-r1-2", "outputId": "output-cost", "value": 38.0, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:20:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-2-act", "experimentRunId": "exprun-r1-2", "outputId": "output-actual-curing-time", "value": 88, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:20:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-3-hardness", "experimentRunId": "exprun-r1-3", "outputId": "output-hardness", "value": 82.7, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T15:42:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-3-brittleness", "experimentRunId": "exprun-r1-3", "outputId": "output-brittleness", "value": 9.4, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T15:42:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-3-cost", "experimentRunId": "exprun-r1-3", "outputId": "output-cost", "value": 47.2, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:25:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-3-act", "experimentRunId": "exprun-r1-3", "outputId": "output-actual-curing-time", "value": 44, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-30T09:25:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-4-hardness", "experimentRunId": "exprun-r1-4", "outputId": "output-hardness", "value": 74.9, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T16:32:00Z", "recordedBy": "user-li-wei", "notes": null },
    { "id": "meas-r1-4-brittleness", "experimentRunId": "exprun-r1-4", "outputId": "output-brittleness", "value": 13.6, "status": "Valid", "revision": 1, "supersedesMeasurementId": null, "recordedAt": "2026-07-29T16:32:00Z", "recordedBy": "user-li-wei", "notes": "cost / actual-curing-time 尚未测得,故本 ExperimentRun 的观测行不完整。" }
  ],

  "decisionLog": [
    { "id": "log-0001", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-20T02:10:00Z", "actor": "user-li-wei", "action": "CampaignCreated", "definitionRevisionId": "campaigndefrev-epoxy-001", "payload": { "name": "Epoxy Coating Optimization" }, "relatedEntityId": "campaigndef-epoxy-coating-001" },
    { "id": "log-0002", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-22T06:04:00Z", "actor": "user-li-wei", "action": "DefinitionRevisionCreated", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "revisionNumber": 3, "parentRevisionId": "campaigndefrev-epoxy-002" }, "relatedEntityId": "campaigndefrev-epoxy-003" },
    { "id": "log-0003", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-22T06:05:00Z", "actor": "user-li-wei", "action": "ConstraintsConfirmed", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "constraintsConfirmed": true, "constraints": [{ "kind": "LinearEquality", "parameterIds": ["param-resin-ratio", "param-hardener-ratio"], "coefficients": [1, 1], "rhs": 100 }] }, "relatedEntityId": "constraint-resin-hardener-sum" },
    { "id": "log-0004", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-22T06:05:01Z", "actor": "user-li-wei", "action": "DesignSpaceValidated", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "ok": true, "issues": [] }, "relatedEntityId": null },
    { "id": "log-0005", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-29T09:40:05Z", "actor": "user-li-wei", "action": "InitialDesignGenerated", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "batchSize": 4, "backendName": "baybe", "backendVersion": "0.15.0", "seed": 42 }, "relatedEntityId": "batch-epoxy-001-r1" },
    { "id": "log-0006", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-29T16:30:00Z", "actor": "user-li-wei", "action": "ExperimentRunExecuted", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "status": "Completed" }, "relatedEntityId": "exprun-r1-4" },
    { "id": "log-0007", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-29T14:22:00Z", "actor": "user-li-wei", "action": "MeasurementRecorded", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "outputId": "output-hardness", "revision": 1 }, "relatedEntityId": "meas-r1-1-hardness-v1" },
    { "id": "log-0008", "campaignRunId": "campaignrun-epoxy-coating-001", "timestamp": "2026-07-29T18:10:00Z", "actor": "user-li-wei", "action": "MeasurementSuperseded", "definitionRevisionId": "campaigndefrev-epoxy-003", "payload": { "outputId": "output-hardness", "from": "meas-r1-1-hardness-v1", "to": "meas-r1-1-hardness-v2", "oldValue": 76.0, "newValue": 78.4 }, "relatedEntityId": "meas-r1-1-hardness-v2" }
  ]
}
```

---

## 9. 开放问题(需产品负责人决定)

1. **一个 `CampaignDefinition` : 多个 `CampaignRun`(1:N)何时开放**:v0.2 已在模型层就绪(`OptimizationPolicy` 移到 Run、Run 引用不可变 revision),"同一份 revision 用不同 `OptimizationPolicy` 跑多个 Run 并对比"技术上已成立。是否近期开放?若开放,`CampaignRun` 是否需要 `label`/`name` 区分同一定义下的多个 Run?
2. **`weightingMode='equal'` 的默认与 UI 呈现**:等权是显式决策(§2.7)。产品是否希望新建 Desirability 时默认 `equal`,还是强制用户逐项填写权重(避免"以为调了权重其实没调")?
3. **Desirability 变换的 v1 放开时机**:MVP 只保留 `NormalizedRamp(lower, upper)`(§2.7,本次 req 5),前端只暴露上下界两个输入。`TRIANGULAR`/`BELL` 与 `direction='Target'`/`CloseToTarget`(需 `peak`/`targetValue` 的"越接近某值越好"语义)已推迟到 v1。v1 何时开、是否需要一个"目标模式"切换器与对应 UI,需产品负责人定。
4. **`ObjectivePolicy.kind` 的默认推断**:当用户配了多个 target 时,系统默认建议 `Desirability` 还是 `Pareto`?二者对用户心智模型差异较大(标量化 vs 前沿),需要产品定默认与引导文案。
5. **`AwaitingMeasurements` 下是否允许"强制关轮"**:当前 gating 要求有效观测行数达阈值才能 `close_round()`。是否需要一个显式 `close_round(force=true)`,允许在部分候选读数缺失(或被 Cancelled)时强制进入下一轮?需要产品给出业务规则与风险提示文案。
6. **`Completed` 之后 `reopen()` 的权限与审计**:谁能 reopen、是否需要二次确认(已有 `DecisionLog.action='RunReopened'` 记录动作,权限策略待定)。
7. **跨 revision 的 `ExperimentRun`/`Measurement` 复用规则最终确认**:参数 `id` 存在且类型未变则复用、否则标记 `Invalid`——此规则需产品/算法负责人最终拍板(尤其"连续 bounds 收窄导致历史点越界"如何处理)。
8. **BoFire 接入时间线**:能力矩阵已按源码修正(§5,含 BoFire 线性约束接受数值离散的更正)。接入排期决定"混合连续+离散线性约束"何时从 MVP 的拒绝转为支持。
9. **多后端场景下的自动选型规则**:当同时具备 BayBE/BoFire 时,依据 `capabilities()` 的哪些字段(约束类型、Desirability 支持、最优设计)自动选路由。
10. **`OutputSpec` : `TargetSpec` 是否放开 1:1**:是否允许"纯监控、无 target"的 Output,或一个 Output 被多个 target 引用?当前 MVP 强制严格 1:1。
11. **`req 8` 与源码的偏差确认**:文档已按 vendored 源码记录"BoFire 线性约束同时接受 ContinuousInput 与 DiscreteInput(仅排除 Categorical)",与 req 8 文本"只支持 ContinuousInput"不一致(§0 末尾、§5 修正说明)。请确认以源码为准。
12. **BayBE Cardinality 替代 N-choose-K 的判定边界**:`min=max=K` 可表达"恰好 K 个非零"(§5.2),但复杂/跨组 N-choose-K 仍需 BoFire。BayBE-only MVP 阶段哪些 N-choose-K 需求降级路由到 Cardinality、哪些直接拒绝,需算法负责人给边界。
13. **`dependencyLockHash` 的生成口径**:哈希对象是 `pip freeze` 全量、还是 lock 文件(如 `uv.lock`/`poetry.lock`)?决定复现校验的严格程度与跨机稳定性。

