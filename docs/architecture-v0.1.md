# 工业实验优化平台 — 架构设计 v0.1

> 状态: 草案(v0.1),基于架构评审对 `docs/architecture-v0.md` 的修订版,供产品/工程二次评审。
> 本文档不涉及任何代码改动(业务代码与前端 UI 均未修改)。
> `architecture-v0.md` 保留作为历史基线,不删除,便于对照 diff。
> 输入依据: `实验工艺优化平台_调研报告.md`、`comparison_report.md`、现有前端原型
> `frontend/src/pages/campaigns/demo-v2/*`,以及本次评审提出的 12 项修订要求。

---

## 0. 本次修订摘要(评审对照表)

| # | 评审要求 | 落在哪一节 | 一句话概括变化 |
|---|---|---|---|
| 1 | 拆分 CampaignDefinition / CampaignRun | §2.1 §2.2 §3 | 问题定义(可版本化)与执行状态(有生命周期)拆成两个实体,不再合并进一个 `CampaignSpec.status` |
| 2 | constraints 从一开始定义为数组 | §2.1 §2.6 | `ConstraintSpec` 单条字段改为 `constraints: ConstraintSpec[]` |
| 3 | 删除 `no-constraint`,空数组表示无约束 | §2.1 §2.6 | 新增 `constraintsConfirmed` 显式确认标志,区分"未决定"与"确认无约束" |
| 4 | custom 未解析为可执行约束必须阻止推荐 | §2.6 §4 | `ValidationIssue` 新增 `severity`,custom 不可执行时为 `blocking`,不再是 warning |
| 5 | ParameterSpec 改判别联合类型 | §2.3 | `Continuous \| Discrete \| Categorical` 三个独立接口,`Discrete.values` 改为 `number[]` |
| 6 | OutputSpec 与 ObjectivePolicy 分开 | §2.4 §2.5 | "测什么"与"怎么优化"拆成两个实体 |
| 7 | 新增 OptimizationPolicy | §2.7 | 批次大小/后端选择/种子策略/算法超参数独立建模 |
| 8 | ExperimentRun 与 Measurement 分开 | §2.9 §2.10 | "执行一次实验"与"记录一条读数"拆开,原生支持部分回填 |
| 9 | 推荐记录保存不可变输入快照 + 完整算法配置 | §2.11 | `RecommendationBatch.inputSnapshot`(深拷贝)+ `algorithmConfig`(完整可复现配置) |
| 10 | 修正能力矩阵,绑定明确版本和 Strategy | §5 | 绑定 CHANGELOG 版本号 + vendored commit + 具体 Strategy/Constraint 类名 |
| 11 | 修正预算/状态机/JSON 示例矛盾 | §2.2 §3 §8 | 明确 `budgetUsed` 计算口径;状态机主体改为 `CampaignRun`;JSON 示例改为自洽叙事 |
| 12 | 仅修改设计文档 | 全文 | 本次未改动 `frontend/` 任何文件 |

---

## 1. MVP 边界

### 1.1 MVP 做什么

与 v0 基本一致,以下为本次修订带来的内部形态调整(不扩大能力范围,只是模型更准确):

| 能力 | 说明 |
|---|---|
| Campaign 定义 | 创建/编辑 `CampaignDefinition`(Parameters/Outputs/ObjectivePolicies/**Constraints 数组**/OptimizationPolicy) |
| 设计空间校验 | `validate()`:参数合法性、Output/ObjectivePolicy 合法性、**constraints 数组每一项是否 `executable`**、`constraintsConfirmed` 是否为真 |
| 初始实验设计 | `generate_initial_design()`:仅 BayBE 单后端,冷启动生成第一批候选 |
| 迭代推荐 | `recommend()`:基于 `ExperimentRun` + `Measurement` 生成下一批候选,**原生支持部分回填**(不要求同一个 ExperimentRun 的所有 Output 都已测量) |
| 观测回填 | 人工录入/编辑 `Measurement`,一次一个 Output,允许陆续补齐 |
| 决策留痕 | `DecisionLog` 记录 Definition 编辑、约束确认、推荐生成、实验执行、测量记录、Run 完成等 |
| 可复现性 | 每次 `generate_initial_design`/`recommend` 落盘**完整输入快照 + 完整算法配置**(不再只是版本号引用) |
| Campaign 版本化 | `CampaignDefinition.version` 单调递增,历史版本只读可查 |

### 1.2 MVP 暂时不做什么

在 v0 基础上更新两点(其余不变):

| 能力 | 原因 / 计划 |
|---|---|
| `custom` 约束的正式 DSL 解析器/求值器 | 仍不做;但本次修订把"未解析"的后果从 warning 提升为 **blocking**(§2.6 §4),体验上更严格,产品需要重新评估 `custom` 选项的定位(见 §9 开放问题 2) |
| 一个 `CampaignDefinition` 对应多个 `CampaignRun`(重复运行、对比后端) | MVP 阶段严格 1:1(同创建、同生命周期);1:N 是拆分后自然打开的 v1+ 扩展点(见 §9 开放问题 1) |
| 数据库/持久化服务落地、真实 API、前后端联调 | 同 v0 |
| BoFire 后端接入、双后端自动选型 | 同 v0,能力矩阵已在 §5 修正并绑定明确版本 |
| LLM/Agent 自动产出推荐数值 | 同 v0,见 §6 |
| 化学/分子式模态 | 同 v0,不在本次范围内(用户已明确标注为粘贴错误) |
| DoE 最优设计、非线性/N-choose-K 约束 | 同 v0,但 §5.3 新增一条修正:BayBE 的 Cardinality 约束与 N-choose-K 语义部分重叠,不再是"完全不支持" |
| 迁移学习 / SHAP / 多租户权限 / 批次并发编辑 | 同 v0 |
| 目标权重 | 同 v0,字段位置从 `ObjectiveSpec.weight` 迁移到 `ObjectivePolicy.weight`,语义不变(仍不启用) |

---

## 2. 平台统一领域模型 v0.1

### 2.0 实体总览与拆分原则

本次修订核心动作是把 v0 中过度合并的几个实体重新拆分,原则是:**一个实体只承担一类会随不同原因变化的信息**。拆分后的实体清单:

```
CampaignDefinition   ← 问题定义(版本化,无生命周期状态)
  ├─ ParameterSpec[]        (判别联合类型:Continuous | Discrete | Categorical)
  ├─ OutputSpec[]           (测什么)
  ├─ ObjectivePolicy[]      (怎么优化,引用 OutputSpec)
  ├─ ConstraintSpec[]       (数组,可为空)
  └─ OptimizationPolicy     (批次/后端/种子/算法超参数)

CampaignRun           ← 执行状态(有生命周期状态机,引用一个 CampaignDefinition)
  ├─ ExperimentRound[]
  │    └─ ExperimentRun[]
  │         └─ Measurement[]   (可部分回填)
  ├─ RecommendationBatch[]     (含不可变输入快照 + 完整算法配置)
  └─ DecisionLog[]             (append-only)
```

**MVP 约束**:创建一个 Campaign 时,`CampaignDefinition`(version=1)与 `CampaignRun`(status=`Draft`)总是成对创建,严格 1:1,生命周期同步销毁/归档。因此 `ExperimentRound`/`ExperimentRun`/`RecommendationBatch`/`DecisionLog` 统一以 `campaignRunId` 作为归属外键;需要追溯到问题定义时,通过 `CampaignRun.campaignDefinitionId` 间接查询即可,不需要再挂一份 `campaignDefinitionId`。1:N 是 v1+ 的开放问题(见 §9-1)。

所有类型均为平台自有类型,不直接引用 `bofire.*`/`baybe.*` 的类(理由见 §5 末尾原则)。类型标注记法同 v0:`string`/`number`/`boolean`/`enum(...)`/`array<T>`/`object`/`T?`(可选)。

### 2.1 CampaignDefinition

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | 全局唯一 |
| `version` | number (int, ≥1) | ✓ | 单调递增;`parameters`/`outputs`/`objectivePolicies`/`constraints`/`optimizationPolicy` 任一变更即 `+= 1` |
| `name` | string | ✓ | 非空,平台内唯一 |
| `goal` | string | — | 自由文本 |
| `parameters` | array<ParameterSpec> | ✓ | 至少 1 项才能通过设计空间校验 |
| `outputs` | array<OutputSpec> | ✓ | 至少 1 项才能通过设计空间校验 |
| `objectivePolicies` | array<ObjectivePolicy> | ✓ | MVP 阶段长度必须等于 `outputs.length`,且每个 `outputId` 被恰好一个 policy 引用(严格 1:1,见 §9-10) |
| `constraints` | array<ConstraintSpec> | ✓(可为空数组) | **v0.1 变更**:从"至多 1 条、可为 null"改为数组,空数组表示"当前没有配置任何约束" |
| `constraintsConfirmed` | boolean | ✓ | **v0.1 新增**。`true` 表示用户已经过确认流程(即使确认结果是"不需要约束");`false` 表示尚未决定。取代了 v0 中 `ConstraintSpec.choice = 'no-constraint'` 的语义角色(见 §2.6 说明) |
| `constraintsConfirmedAt` | string (ISO-8601)? | 当 `constraintsConfirmed=true` 时必填 | |
| `optimizationPolicy` | OptimizationPolicy | ✓ | 见 §2.7 |
| `createdAt` / `updatedAt` | string (ISO-8601) | ✓ | |
| `createdBy` | string | ✓ | |

**关键约束**:
- `parameters`/`outputs`/`objectivePolicies`/`constraints` 各自数组内 `id` 唯一;`parameters`/`outputs` 的 `name` 各自命名空间内唯一(大小写不敏感)。
- **设计空间校验通过的充要条件**:`parameters` 非空且每项合法 + `outputs`/`objectivePolicies` 非空且每项合法 + `constraintsConfirmed = true` + `constraints` 数组中不存在 `executable = false` 的元素。
- `CampaignDefinition` **没有** `status` 字段——是否可编辑取决于关联的 `CampaignRun.status`(见 §3.3 权限表),这是与 v0 最大的结构性差异。

### 2.2 CampaignRun

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | |
| `campaignDefinitionId` | string | ✓ | MVP 阶段 1:1,见 §2.0 |
| `currentDefinitionVersion` | number (int) | ✓ | 指向当前生效的 `CampaignDefinition.version`;Definition 被编辑且本 Run 处于可编辑状态时(§3.3)随之 `+= 1` |
| `status` | enum(`Draft`,`DesignSpaceValidated`,`RecommendationsPending`,`RoundClosed`,`Completed`,`Archived`) | ✓ | 生命周期状态机主体,见 §3 |
| `round` | number (int, ≥0) | ✓ | 已生成的 `RecommendationBatch` 数量;0 表示尚未生成过推荐 |
| `batchSize` | — | — | **v0.1 变更**:已迁移到 `OptimizationPolicy.batchSize`,`CampaignRun` 不再重复持有 |
| `budgetTotal` | number (int, ≥1) | ✓ | 预算实验总次数上限,创建时设定,`status=Draft` 期间可编辑 |
| `budgetUsed` | number (int, ≥0) | ✓ | **精确定义(v0.1 新增,修正 req 11)**:等于该 Run 下 `ExperimentRun.status ∈ {Completed, Failed}` 的记录数量。`Pending`/`Cancelled` 不计入(尚未消耗资源,或已被主动撤销)。本质上是可从 `ExperimentRun` 表重新推导的物化计数,任何 `ExperimentRun.status` 变更必须在同一操作内同步更新它 |
| `createdAt` / `updatedAt` | string (ISO-8601) | ✓ | |
| `createdBy` | string | ✓ | |

**关键约束**:`round` 与 `budgetUsed` 是两个独立的计数维度——`round` 数的是"生成过几批推荐",`budgetUsed` 数的是"物理执行过几次实验"。二者不必同步增长(例如刚生成完第 1 轮推荐、尚未执行任何候选时,`round=1` 且 `budgetUsed=0` 是完全自洽的合法状态,不是矛盾;v0 的 JSON 示例对此表达不清晰,本次在 §8 改为分阶段叙事以消除歧义)。

### 2.3 ParameterSpec(判别联合类型)

**v0.1 变更**:从"单一接口 + 条件必填字段"改为判别联合类型,判别字段为 `type`。与前端 `types.ts` 中 `Parameter = ContinuousParameter | ValuesParameter` 的二分组合不同,后端采用三分,是因为前端把 `Discrete`/`Categorical` 在表单层合并成同一形状(`values: string[]`)是 UI 便利性选择,后端作为权威模型需要更精确的语义:`Discrete` 的取值本质是可排序数值,`Categorical` 的取值是无序标签。二者在领域模型层不应共享同一字段类型。前端的 `ValuesParameter` 在映射到后端时,依据 `type` 落到 `DiscreteParameterSpec` 或 `CategoricalParameterSpec` 之一,不产生语义漂移。

```
type ParameterSpec =
  | ContinuousParameterSpec
  | DiscreteParameterSpec
  | CategoricalParameterSpec

interface ParameterSpecBase {
  id: string
  name: string
  unit?: string
  description?: string
}

interface ContinuousParameterSpec extends ParameterSpecBase {
  type: 'Continuous'
  bounds: { lower: number; upper: number; stepsize?: number }  // lower < upper;stepsize 为 v1+ 预留
}

interface DiscreteParameterSpec extends ParameterSpecBase {
  type: 'Discrete'
  values: number[]   // 至少 1 个,自动去重排序(v0.1 变更:原为 string[],现改为语义正确的 number[])
}

interface CategoricalParameterSpec extends ParameterSpecBase {
  type: 'Categorical'
  values: string[]   // 至少 1 个非空白值,去重,大小写敏感
}
```

**必填规则**(与前端 `getParameterIssues` 保持一致,并按判别分支细化):
- `name` 缺失 → 拒绝;`name` 重复(忽略大小写)→ 拒绝。
- `type = 'Continuous'`:`bounds` 缺失,或 `lower >= upper` → 拒绝。
- `type = 'Discrete'`:`values` 为空数组,或存在非数值 → 拒绝。
- `type = 'Categorical'`:`values` 全为空白 → 拒绝。
- `Molecular`/`Substance` 不是本联合类型的合法分支(不是"预留枚举值",而是根本不存在这个 variant),对应 `validate()` 层直接以"未知 type"报错,比 v0"枚举值存在但被拒绝"更严格地在类型层面排除了这个分支。

### 2.4 OutputSpec(新增,从原 ObjectiveSpec 拆出"测什么")

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | Campaign 内唯一 |
| `name` | string | ✓ | 非空,Campaign 内唯一 |
| `unit` | string | — | 可为空字符串 |
| `description` | string | — | 可为空字符串 |

`OutputSpec` 只描述"这是一个会被测量的量",不包含任何优化方向信息。这是本次拆分的核心动机:同一个被测量的量,在不同场景下可能只是监控指标(v1+)、也可能是被 Maximize/Minimize 的优化目标,把"测什么"和"怎么优化"分开后,这两类需求可以独立演化(MVP 阶段仍强制 1:1,见 §2.5)。

**必填规则**:`name` 缺失或重复 → 拒绝;Campaign 级校验:`outputs.length === 0` → 拒绝。

### 2.5 ObjectivePolicy(新增,从原 ObjectiveSpec 拆出"怎么优化")

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `outputId` | string | ✓ | 引用 `OutputSpec.id`;MVP 阶段每个 `outputId` 恰好被一个 `ObjectivePolicy` 引用(见 §9-10) |
| `direction` | enum(`Maximize`,`Minimize`) | ✓ | `Target`/`CloseToTarget` 为 v1+ 预留值 |
| `weight` | number? | — | MVP 不启用(对应前端 Optional Preferences) |
| `targetValue` | number? | — | 仅 `direction='Target'` 时使用,v1+ 预留 |

**必填规则**:与前端 `getObjectiveIssues`/`areObjectivesValid` 语义一致,只是"名称唯一"的校验现在发生在 `OutputSpec` 层(因为 `ObjectivePolicy` 本身没有 `name`)。Campaign 级校验:`objectivePolicies.length === 0` → 拒绝(至少保留一个优化目标)。

### 2.6 ConstraintSpec(数组元素,v0.1 结构变更)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `choice` | enum(`fixed-sum`,`custom`) | ✓ | **v0.1 变更**:删除 `no-constraint`(见下方说明) |
| `involvedParameterIds` | array<string> | 当 `choice='fixed-sum'` 时必填 | 引用 `ParameterSpec.id`,至少 2 项,且均为 `Continuous` 类型 |
| `targetSum` | number | 当 `choice='fixed-sum'` 时必填 | 涉及参数之和 = `targetSum` |
| `customExpression` | string | 当 `choice='custom'` 时必填,非空 | 不透明原始文本 |
| `parsedExpression` | object \| null | — | **v0.1 新增**。若该 `customExpression` 已被解析为结构化可执行表达式(具体语法待 §9-2 决定),则非 null;否则为 null |
| `executable` | boolean | ✓(计算得出,建议持久化避免重算) | `choice='fixed-sum'` 时,只要 `involvedParameterIds`/`targetSum` 合法且引用的参数仍存在于当前 `parameters` 中,恒为 `true`;`choice='custom'` 时,仅当 `parsedExpression` 非 null 且其引用的参数在当前 `parameters` 中仍然存在、类型匹配时为 `true`,否则为 `false` |
| `resolvedAt` | string (ISO-8601)? | — | 该条约束被用户设定/确认的时间戳 |

**删除 `no-constraint` 的语义迁移说明(对应 req 2/3)**:v0 中 `choice='no-constraint'` 是一个"内容为空但代表已确认"的哨兵对象,用来区分"用户还没打开约束对话框"(`choice=null`)与"用户确认了不需要约束"(`choice='no-constraint'`)。v0.1 把这两种情况改用两个正交维度表达:
- **是否已经决定** → `CampaignDefinition.constraintsConfirmed`(布尔)
- **决定的内容是什么** → `CampaignDefinition.constraints`(数组,可为空)

`constraints = []` 且 `constraintsConfirmed = true` 表示"确认无约束";`constraints = []` 且 `constraintsConfirmed = false` 表示"还没决定"——语义与 v0 完全等价,但不再需要一个"内容为空的约束对象"占位,`choice` 枚举因此可以只保留真正会产生约束逻辑的两个值。

**阻断规则(对应 req 4,relatvie 于 v0 的行为变更)**:v0 中 `custom` 约束未被优化器解析时,`validate()` 只产生 warning 级 issue,允许通过并进入推荐环节。**v0.1 改为**:只要 `constraints` 数组中存在任意一项 `executable = false`,`validate()` 的结果中必须包含至少一条 `severity = 'blocking'` 的 issue,且 `generate_initial_design()`/`recommend()` 必须以 `ValidationError` 拒绝执行——不再存在"允许通过但警告"的路径。理由:一个用户以为生效、但实际未参与优化计算的约束,产生的推荐结果可能系统性违反用户意图,这类错误的代价远高于"多一步确认摩擦"。

### 2.7 OptimizationPolicy(新增,对应 req 7)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `backendName` | string | ✓ | MVP 固定为 `"baybe"` |
| `strategyClassName` | string? | — | 若省略,由 Adapter 按 `capabilities()` 选择默认策略(例如 BayBE 路径默认 `TwoPhaseMetaRecommender`) |
| `batchSize` | number (int, ≥1) | ✓ | 每轮推荐的默认候选数量;`generate_initial_design()`/`recommend()` 调用时可显式传参覆盖本次调用(不回写此字段) |
| `seedPolicy` | enum(`Fixed`,`AutoGenerated`) | ✓ | |
| `seedValue` | number (int)? | 当 `seedPolicy='Fixed'` 时必填 | |
| `recommenderConfig` | object | — | 后端/策略相关的不透明超参数,例如 `{ "switch_after": 10, "remain_switched": true }`;MVP 阶段只做"是否为合法 JSON"级别的校验(见 §9-11) |
| `coldStartStrategy` | string? | — | v1+ 预留(如需在 BayBE/BoFire 之间为冷启动单独选路由) |

拆分理由:把"批次多大""用哪个后端/策略""随机种子怎么定"这类和**算法执行方式**相关的决策,从"问题本身是什么"(parameters/outputs/objectivePolicies/constraints)中独立出来。这为 v1+ "同一个问题定义,尝试不同优化策略并对比效果"打开了建模空间,而不需要重新定义整个 Campaign(MVP 阶段仍是 1 个 Definition 配 1 个 Policy 配 1 个 Run,见 §2.0)。

### 2.8 ExperimentRound

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | **v0.1 改名**(原 `campaignId`) |
| `roundNumber` | number (int, ≥1) | ✓ | 与 `RecommendationBatch.roundNumber` 一一对应 |
| `recommendationBatchId` | string | ✓ | |
| `experimentRunIds` | array<string> | ✓ | **v0.1 改名**(原 `observationIds`);初始为空,随执行增长 |
| `openedAt` | string (ISO-8601) | ✓ | |
| `closedAt` | string (ISO-8601)? | — | 关闭条件见下 |
| `status` | enum(`Open`,`Closed`) | ✓ | |

**关闭条件(v0.1 更新)**:该轮次关联的所有 `ExperimentRun.status ∈ {Completed, Failed, Cancelled}` 时才可关闭(不再是 v0 中"Observation 全部 Recorded/Invalid"的旧语义)。

### 2.9 ExperimentRun(从 Observation 拆出"执行"部分,对应 req 8)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `experimentRoundId` | string | ✓ | |
| `recommendationCandidateId` | string? | — | 若回填自某条推荐候选则关联;人工额外补充实验时可为空 |
| `parameterValues` | object (map: `ParameterSpec.id` → string \| number) | ✓ | 每个已配置参数必须有值 |
| `status` | enum(`Pending`,`Completed`,`Failed`,`Cancelled`) | ✓ | 语义见下 |
| `executedAt` | string (ISO-8601)? | 当 `status ∈ {Completed, Failed}` 时必填 | |
| `executedBy` | string? | 当 `status ∈ {Completed, Failed}` 时必填 | |
| `notes` | string | — | |

**`status` 语义**:
- `Pending`:候选已生成,物理实验尚未执行。
- `Completed`:物理实验已完成执行——**不代表全部 `Measurement` 都已录入**(见 §2.10 的部分回填规则)。是否标记为 `Completed` 由用户显式操作决定。
- `Failed`:实验执行失败/异常(设备故障、原料问题等),仍计入 `budgetUsed`(消耗了资源)。
- `Cancelled`:用户主动放弃执行该候选,不计入 `budgetUsed`。

### 2.10 Measurement(从 Observation 拆出"读数"部分,支持部分回填,对应 req 8)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `experimentRunId` | string | ✓ | |
| `outputId` | string | ✓ | 引用 `OutputSpec.id` |
| `value` | number | ✓ | |
| `status` | enum(`Valid`,`Invalid`) | ✓ | `Invalid`:读数异常/失败,保留存档但不参与拟合 |
| `recordedAt` | string (ISO-8601) | ✓ | |
| `recordedBy` | string | ✓ | |
| `notes` | string? | — | |

**部分回填规则(v0.1 核心修正)**:一个 `ExperimentRun` 可以先有 0 条、再逐步增加到 N 条(N = 当前 `outputs.length`)`Measurement`,不要求一次性提交全部读数——这是相对 v0 `Observation.objectiveValues` 模型的关键修正:v0 要求所有已配置目标的值一次性提供才能标记为 `Recorded`,无法表达"某些理化指标测试周期明显长于其他指标,先出结果先记"的真实实验场景。

**必填规则**:同一 `(experimentRunId, outputId)` 组合下允许存在多条 `Measurement`(重新测量/修正历史误差),取"时间最新的一条 `status='Valid'`"记录参与 `recommend()` 的输入构建;历史记录不删除,保留审计轨迹。

### 2.11 RecommendationBatch(扩展:不可变输入快照 + 完整算法配置,对应 req 9)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | |
| `roundNumber` | number (int, ≥1) | ✓ | |
| `generatedAt` | string (ISO-8601) | ✓ | |
| `inputSnapshot` | object | ✓ | **v0.1 新增**。生成该批次时 `{parameters, outputs, objectivePolicies, constraints}` 的**深拷贝**,以及当时全部参与拟合的 `ExperimentRun`(`status='Completed'`)+ `Measurement`(`status='Valid'`,取最新)数据的内联拷贝。即使之后 `CampaignDefinition` 被编辑产生新版本,该快照永远不变 |
| `inputSnapshotDefinitionVersion` | number | ✓ | 仅作索引/查询提示;**权威数据是 `inputSnapshot` 本身**,不是这个版本号(v0 中反过来,只存版本号、靠回溯重建输入——这是本次修正的重点) |
| `algorithmConfig` | object | ✓ | **v0.1 新增**。`{ backendName, backendVersion, backendCommit?, strategyClassName, hyperparameters: object, acquisitionFunction?, seed }`。目标是"给定同样的 `inputSnapshot` + `algorithmConfig`,理论上应产出相同候选"的复现粒度;`seed` 不允许为 null(若后端自动生成种子,必须在生成后立刻回写实际使用的值) |
| `candidates` | array<RecommendationCandidate> | ✓ | 结构不变(见下) |
| `status` | enum(`Proposed`,`PartiallyExecuted`,`FullyExecuted`,`Superseded`) | ✓ | 不变 |

`RecommendationCandidate` 子对象(不变):`id`、`parameterValues`(map)、`predictedMean`(map)?、`predictedSd`(map)?、`desirability`(number)?。

### 2.12 DecisionLog(action 枚举更新)

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignRunId` | string | ✓ | **v0.1 改名**(原 `campaignId`);见 §2.0 关于 1:1 归属外键的说明 |
| `timestamp` | string (ISO-8601) | ✓ | |
| `actor` | string | ✓ | 用户 id,或 `"agent:<name>"`,或 `"system"` |
| `action` | enum(见下) | ✓ | |
| `campaignDefinitionVersion` | number | ✓ | **v0.1 改名**(原 `campaignSpecVersion`) |
| `payload` | object | — | |
| `relatedEntityId` | string? | — | |

**`action` 枚举(v0.1 更新)**:
`CampaignDefinitionEdited`(取代原 `ParameterEdited`/`ObjectiveEdited`,`payload` 中用 `{ field: 'parameters'|'outputs'|'objectivePolicies'|'optimizationPolicy', before, after }` 区分具体哪个数组变化)、`ConstraintProposed`、`ConstraintsConfirmed`(原 `ConstraintConfirmed` 改为复数,`payload` 记录完整 `constraints` 数组快照 + `constraintsConfirmed` 布尔值)、`DesignSpaceValidated`、`DesignSpaceValidationFailed`、`InitialDesignGenerated`、`RecommendationRequested`、`ExperimentRunExecuted`(新增)、`MeasurementRecorded`(取代原 `ObservationRecorded`)、`RoundClosed`、`RunCompleted`(原 `CampaignCompleted`)、`RunArchived`(原 `CampaignArchived`)。

**不可变性不变**:append-only,不允许更新或删除已写入的条目。

---

## 3. CampaignRun 生命周期状态机(v0.1 重新定位)

**核心修正说明**:本状态机描述的是 `CampaignRun.status`,**不是** `CampaignDefinition` 的属性。`CampaignDefinition` 本身没有 `status` 字段,只有 `version`;它当前是否可编辑,取决于关联的 `CampaignRun` 现在处于哪个状态(见 §3.3)。v0 把"问题定义正在被编辑"和"这个 Campaign 还没开始跑"合并进同一个 `Draft` 状态,导致"是否存在一次正在进行的执行"与"哪个 Definition 版本是权威的"两件事无法独立追踪——这是本次评审要求拆分的直接动因,状态机层面的体现即在此。

### 3.1 状态定义

| 状态 | 含义 |
|---|---|
| `Draft` | 该 `CampaignRun` 关联的 `CampaignDefinition` 尚未通过设计空间校验;或校验通过后 Definition 又被编辑(`currentDefinitionVersion` 发生变化) |
| `DesignSpaceValidated` | `validate()` 通过:parameters/outputs/objectivePolicies 合法,且 `constraintsConfirmed=true`,且 `constraints` 数组中不存在 `executable=false` 的元素;尚未生成过任何推荐 |
| `RecommendationsPending` | 存在一个 `status ∈ {Proposed, PartiallyExecuted}` 的 `RecommendationBatch`,等待 `ExperimentRun` 执行与 `Measurement` 回填 |
| `RoundClosed` | 当前轮次全部 `ExperimentRun.status ∈ {Completed, Failed, Cancelled}`,等待下一步决策 |
| `Completed` | 用户主动结束该 Run,只读 |
| `Archived` | 归档,完全只读,不出现在默认列表 |

### 3.2 状态转换

```
Draft --validate() 通过--> DesignSpaceValidated
Draft --validate() 失败--> Draft (停留,附带失败原因,含 blocking issue 列表)

DesignSpaceValidated --编辑 CampaignDefinition(parameters/outputs/objectivePolicies/constraints/optimizationPolicy)-->
    Draft (CampaignDefinition.version += 1,CampaignRun.currentDefinitionVersion 同步更新)
DesignSpaceValidated --generate_initial_design() 成功--> RecommendationsPending (round=1)

RecommendationsPending --全部 ExperimentRun.status ∈ {Completed,Failed,Cancelled}--> RoundClosed
RecommendationsPending --编辑 CampaignDefinition--> 拒绝(见 3.3);
    若确需修改,须先将当前 Batch 标记 Superseded 并将全部未执行候选置为 Cancelled 以转入 RoundClosed,再回到 Draft

RoundClosed --recommend() 成功--> RecommendationsPending (round += 1)
RoundClosed --编辑 CampaignDefinition--> Draft (version += 1;历史 ExperimentRound/RecommendationBatch/ExperimentRun/Measurement 保留,标记"跨版本",见 §7)
RoundClosed --mark_completed()--> Completed

Completed --reopen()(显式管理操作)--> RoundClosed
Completed --archive()--> Archived
DesignSpaceValidated / RoundClosed --archive()--> Archived (Draft/RecommendationsPending 不可直接归档,须先完成或退回)

Archived --(终态,无出边)--
```

### 3.3 各状态允许的操作

| 操作 | Draft | DesignSpaceValidated | RecommendationsPending | RoundClosed | Completed | Archived |
|---|---|---|---|---|---|---|
| 编辑 `CampaignDefinition`(parameters/outputs/objectivePolicies/optimizationPolicy) | ✓ | ✓(触发退回 Draft) | ✗(需先处理未完成批次) | ✓(触发退回 Draft) | ✗ | ✗ |
| 编辑 `constraints` 数组 / 设置 `constraintsConfirmed` | ✓ | ✓(触发退回 Draft) | ✗ | ✓(触发退回 Draft) | ✗ | ✗ |
| `validate()` | ✓ | ✓(空操作,已通过) | ✓(只读检查) | ✓(只读检查) | ✓(只读) | ✓(只读) |
| `generate_initial_design()` | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `recommend()` | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| 执行 `ExperimentRun`(状态转 Completed/Failed/Cancelled) | ✗ | ✗ | ✓ | ✗(轮次已关闭,只读) | ✗ | ✗ |
| 回填 `Measurement` | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| `explain()` | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| `mark_completed()` | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `archive()` | ✗ | ✓ | ✗ | ✓ | ✓ | ✗ |
| 查看历史(DecisionLog/历史轮次) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

### 3.4 与前端三态的映射

不变,只是数据来源从 `CampaignSpec.status` 改为 `CampaignRun.status`:

| 前端状态 | 对应后端状态 |
|---|---|
| `Draft` | `Draft`、`DesignSpaceValidated` |
| `Active` | `RecommendationsPending`、`RoundClosed` |
| `Completed` | `Completed`、`Archived` |

---

## 4. OptimizerAdapter 统一接口(v0.1 更新签名)

```python
class OptimizerAdapter(Protocol):

    def capabilities(self) -> AdapterCapabilities:
        """无输入。返回该后端静态能力描述。"""

    def validate(
        self, definition: CampaignDefinition
    ) -> ValidationResult:
        """
        输入: 完整 CampaignDefinition(parameters/outputs/objectivePolicies/constraints/
              constraintsConfirmed/optimizationPolicy)。
        输出: ValidationResult { ok: bool, issues: list[ValidationIssue] }。
        ValidationIssue { code: str, message: str, severity: 'blocking' | 'warning',
                           relatedEntityId: str | None }   # severity 为 v0.1 新增字段
        ok = (issues 中不存在 severity='blocking' 的项)。warning 不阻断 ok=True。
        校验内容新增: constraintsConfirmed 是否为真;constraints 数组中每一项 executable
        是否为真(custom 未解析 → blocking issue,对应 §2.6 阻断规则)。
        不产生副作用,不调用底层优化库的重计算逻辑。
        """

    def generate_initial_design(
        self, definition: CampaignDefinition, batch_size: int | None = None, seed: int | None = None
    ) -> RecommendationBatch:
        """
        输入: 已通过 validate() 的 CampaignDefinition;batch_size 若省略则取
              definition.optimizationPolicy.batchSize(本次调用可覆盖,但不回写 policy);
              seed 若省略则按 optimizationPolicy.seedPolicy 处理。
        输出: RecommendationBatch(roundNumber=1),含完整 inputSnapshot 与 algorithmConfig。
        前置条件: 关联 CampaignRun 无历史 ExperimentRun;definition.constraintsConfirmed=true
                  且不存在 executable=false 的约束(否则不应进入此方法,由 validate() 前置拦截)。
        错误类型:
          - ValidationError: 传入未校验通过的 definition,或存在 executable=false 的约束
          - UnsupportedFeatureError: definition 使用了该后端不支持的参数/约束/目标类型
          - ComputationError
        """

    def recommend(
        self,
        definition: CampaignDefinition,
        experiment_runs: list[ExperimentRun],
        measurements: list[Measurement],
        batch_size: int | None = None,
        seed: int | None = None,
    ) -> RecommendationBatch:
        """
        输入: CampaignDefinition + 截至当前的 ExperimentRun(status='Completed') 列表 +
              Measurement(status='Valid') 列表。Adapter 内部按 experimentRunId 把两个列表
              拼装成"参数值 + 目标值"表格(每个 outputId 取最新一条 Valid Measurement)。
        输出: RecommendationBatch(roundNumber = run.round + 1)。
        错误类型:
          - ValidationError
          - UnsupportedFeatureError
          - InsufficientDataError: 有效观测不足以拟合代理模型
          - ComputationError
        """

    def update(
        self, definition: CampaignDefinition,
        experiment_runs: list[ExperimentRun], measurements: list[Measurement],
    ) -> None:
        """仅更新后端内部状态缓存的可选性能优化钩子;MVP 可用 no-op 实现。参数类型同 recommend()。"""

    def explain(
        self, definition: CampaignDefinition,
        experiment_runs: list[ExperimentRun], measurements: list[Measurement],
    ) -> ExplanationResult:
        """输出: ExplanationResult { featureImportance: dict, notes: str }。参数类型同 recommend()。"""
```

**错误类型体系**:不变(`ValidationError`/`UnsupportedFeatureError`/`InsufficientDataError`/`ComputationError`/`BackendUnavailableError`)。

`AdapterCapabilities` 描述对象更新:

| 字段 | 类型 | 说明 |
|---|---|---|
| `backendName` / `backendVersion` / `backendCommit` | string | `backendCommit` 为 v0.1 新增,记录 vendored 源码 git commit(见 §5 版本锚点说明) |
| `supportedParameterTypes` | array<enum> | `["Continuous","Discrete","Categorical"]` |
| `supportedObjectiveDirections` | array<enum> | `["Maximize","Minimize"]` |
| `supportedConstraintChoices` | array<enum> | **v0.1 变更**:只会是 `["fixed-sum","custom"]` 的子集,`no-constraint` 不再是一种"能力声明"(它不是一种约束类型,而是数组的空状态,不需要声明是否支持) |
| `supportsMultiObjective` / `supportsExplain` / `supportsOptimalDesign` | boolean | 不变 |
| `minObservationsForRecommend` | number (int) | 不变 |
| `maxDiscreteCombinationEstimate` | number? | 不变 |
| `supportsCardinalityConstraint` | boolean | **v0.1 新增**,对应 §5.2 新发现的 BayBE Cardinality 约束能力 |

---

## 5. BayBE 与 BoFire 能力映射(v0.1,绑定明确版本)

### 5.0 版本锚点(对应 req 10)

两库在本次评审所使用的开发环境中均以 vendored 源码树形式存在(通过 `PYTHONPATH` 引用,未走标准 `pip install`),因此 `importlib.metadata`/`__version__` 无法解析出规范的 semver(实测返回字面量 `"unknown"`/`"Unknown"`)。为了让"明确的测试版本"这一要求可验证,采用两个独立可核实来源的交叉引用,而不是手工编造版本号(v0 中 `backendVersion: "0.11.3"` 属于臆测值,已在本次修订中纠正):

| 项 | BayBE | BoFire |
|---|---|---|
| CHANGELOG.md 最新声明版本 | `0.15.0`(2026-06-11) | `0.4.1`(2026-06-16) |
| vendored 源码 git commit(评审验证时) | `b939e3588aad832856c33ac055c5510f7cb76f96`(2026-07-27) | `58f01b2e9d2129e61a1d1f9f17980b0bbb98e5a6`(2026-07-27) |
| 关键依赖约束 | 经 `BotorchRecommender` 依赖 botorch | `0.4.0` 起 **Breaking**:要求 `botorch >= 0.18.1` |
| 本地已确认可运行环境 | Python 3.11.15 / torch 2.13.0+cpu / botorch 0.18.1(micromamba env `bo_examples`,两库共用) | 同左 |

**方法论说明**:该表不是"正式发布版本号"意义上的权威来源。平台实际集成时应改为标准 pip 依赖(写入 `requirements.txt`/`pyproject.toml` 并锁定版本),届时 `pip show baybe`/`pip show bofire` 的结果才是唯一权威来源,取代本表;`backendCommit` 字段(§4 `AdapterCapabilities`)在那之后仍可保留,作为"实际运行的具体构建"的额外审计信息。

### 5.1 可直接转换(两后端均直接支持)

| 平台模型 | BayBE(锚定 changelog `0.15.0` / commit `b939e358`) | BoFire(锚定 changelog `0.4.1` / commit `58f01b2e`,需 botorch≥0.18.1) |
|---|---|---|
| `ContinuousParameterSpec` | `NumericalContinuousParameter` | `ContinuousInput` |
| `DiscreteParameterSpec`(`values: number[]`) | `NumericalDiscreteParameter` | `DiscreteInput` |
| `CategoricalParameterSpec` | `CategoricalParameter` | `CategoricalInput` |
| `ObjectivePolicy`(单目标) | `NumericalTarget` + `SingleTargetObjective` | `ContinuousOutput` + `MaximizeObjective`/`MinimizeObjective` |
| `ConstraintSpec(choice='fixed-sum')`,仅涉及连续参数 | `ContinuousLinearConstraint`(`operator='='`, `coefficients`, `rhs=targetSum`) | `LinearEqualityConstraint` |

(`no-constraint` 一行已删除——它不再是一个 `choice`,不需要映射。)

### 5.2 仅部分支持(需要能力探测 + 降级策略)

| 平台特性 | BayBE | BoFire | 降级/处理方式 |
|---|---|---|---|
| 多目标(Desirability/Pareto) | `DesirabilityObjective`(`weights`,`scalarizer` 默认 `GEOM_MEAN`)/ `ParetoObjective` | `MoboStrategy`(qNEHVI)/ `QparegoStrategy` | BayBE 路径下 `DesirabilityObjective` 缩放边界未提供时,Adapter 用目标历史观测的 min/max 运行时估计,并在 `RecommendationBatch.algorithmConfig.hyperparameters.notes` 标注"缩放边界为运行时估计" |
| 初始实验设计(冷启动) | 无专门 DoE 模块,靠 `TwoPhaseMetaRecommender`(`switch_after`/`remain_switched`)的随机阶段;或 `BotorchRecommender` | `DoEStrategy`(criterion 默认 `DOptimalityCriterion(formula="fully-quadratic")`,可选 D/A/G/K/I-Optimality + SpaceFilling 共 6 种) | MVP 单后端(BayBE)时 `generate_initial_design()` 走随机/空间填充,不做严谨最优设计;`capabilities().supportsOptimalDesign=true` 时(即接入 BoFire 后)可路由 |
| 可解释性(`explain()`) | `insights/shap.py`(SHAP/LIME/MAPLE) | `surrogates/feature_importance.py`(SHAP/ARD/置换) | 依赖已拟合代理模型且观测量达到阈值,否则 `InsufficientDataError` |
| 迁移学习 | `TaskParameter` 原生一等公民 | 多任务 GP,存在但非一等公民 | MVP 均不启用 |
| **约束基数限制(N-choose-K 近似,v0.1 新增发现)** | `ContinuousCardinalityConstraint`/`DiscreteCardinalityConstraint`(`min_cardinality`/`max_cardinality`) | `NChooseKConstraint`(原生、更贴近"恰好 K 个非零"的语义) | **v0.1 修正**:v0 曾把"N-choose-K/非线性约束"整体归入"BayBE 完全不支持,必须拒绝"(§5.3)。本次评审核实 BayBE 存在 Cardinality 约束,语义与 N-choose-K 部分重叠(表达"最多/至少 K 个参数非零",但不保证"恰好 K 个"及更复杂的分组逻辑),应从"完全拒绝"上调为"部分支持",具体是否可以在 BayBE-only MVP 阶段把简单的 N-choose-K 需求降级路由到 Cardinality 约束,需要算法负责人确认(见 §9-12) |

### 5.3 必须拒绝或降级的情况

| 平台特性 | 处理方式 | 理由 |
|---|---|---|
| `ConstraintSpec(choice='fixed-sum')` 涉及 `Discrete`/`Categorical` 参数 | BayBE 路径:`UnsupportedFeatureError`(`ContinuousLinearConstraint` 仅支持连续参数);BoFire 路径:可支持(混合约束) | MVP 单后端(BayBE)时 `validate()` 阶段直接拒绝并给出明确 `severity='blocking'` issue |
| 复杂/分组化的 N-choose-K 语义(超出 Cardinality 约束表达能力的部分,例如"恰好 K 个"或跨组约束) | 两库中仅 BoFire `NChooseKConstraint` 完整支持;MVP(BayBE-only)阶段 `validate()` 直接返回 `UnsupportedFeatureError` | 静默丢弃约束的危害远大于拒绝(见下一条同理) |
| `ConstraintSpec(choice='custom')` 且 `executable=false` | **v0.1 变更(对应 req 4)**:`validate()` 返回 `severity='blocking'` issue,`generate_initial_design()`/`recommend()` 直接拒绝执行,不再是 v0 中"允许通过但 warning" | 避免"看起来生效但实际未生效"的约束产生系统性偏离用户意图的推荐结果 |
| `ParameterSpec` 的 `Molecular`/`Substance` 分支 | 不是判别联合类型的合法 variant,`validate()` 报"未知参数类型"错误 | 未纳入 MVP 范围(§1.2) |
| 目标数量超过后端 Desirability/Pareto 实际验证过的规模(如 >6 个) | `validate()` 阶段发出 `severity='warning'` issue,不强制阻断;若底层抛 `ComputationError` 原样透传 | 两库均无文档化硬上限,但工程经验上过多目标显著降低代理模型质量 |

**不变的原则**:平台领域模型不允许直接依赖两个库的内部类型;转换只发生在各自 Adapter 实现内部的私有映射层(理由同 v0:两库均为第三方依赖,版本演进不应传导到平台核心模型;平台模型需要是两种不同架构哲学——BayBE 的 attrs 不可变对象 vs BoFire 的 Pydantic spec 双层分离——的公共上位抽象)。

---

## 6. Agent 的职责边界(v0.1 更新实体名称)

**Agent 可以做的**(与 v0 相同,仅更新引用的实体名):
1. **自然语言 → 结构化草稿**:把用户的自然语言描述解析为 `CampaignDefinition` 草稿,但草稿仍需人工在 UI 确认后才生效。
2. **发现缺失信息**:对比草稿与 `validate()` 的 `issues` 列表(含 `severity` 区分),生成"缺什么"提示。
3. **请求用户确认**:`constraints` 数组中每一条的选择、参数/目标的模糊描述,必须生成显式确认选项。
4. **调用确定性工具**:只能调用 `OptimizerAdapter` 方法或平台服务层的 CRUD API。
5. **解释推荐结果**:把 `RecommendationBatch.candidates` 的预测字段翻译成自然语言,以及调用 `explain()` 结果做解读。

**Agent 不能做的(架构强制)**:
- **不能绕过 `validate()` 直接写入 `RecommendationBatch`、`ExperimentRun` 或 `Measurement`**——唯一产生候选点的路径是调用 `generate_initial_design()`/`recommend()`。
- **不能伪造或修改 `predictedMean`/`predictedSd`/`desirability`**。
- **不能代替用户确认 `constraints` 数组中的任何一条**,也不能自行把某条 `custom` 约束的 `executable` 标记为 `true`(该标记只能来自 Adapter 对 `parsedExpression` 的确定性校验结果,不能由 Agent 的自然语言理解结果直接写入)。
- **不能代替用户提交 `Measurement`**——`Measurement.recordedBy` 必须是真实用户标识。
- **不能修改已生成的 `DecisionLog` 条目**。
- **v0.1 新增**:Agent 生成的 `CampaignDefinition` 草稿在写入生效前,必须先经过一次显式用户确认动作产生 `DecisionLog`(`action='CampaignDefinitionEdited'`,`actor=真实用户 id`),不能以 `actor="agent:*"` 的身份直接产生对后续 `validate()`/`generate_initial_design()` 生效的 Definition 版本。

架构落地方式不变:服务层对外只暴露一组 API,UI 和 Agent 是这组 API 的两个不同调用方。

---

## 7. 数据持久化与可复现性设计(v0.1 更新)

| 可复现性要素 | 落在哪个模型 | 说明 |
|---|---|---|
| Campaign 版本 | `CampaignDefinition.version` + `CampaignRun.currentDefinitionVersion` 指针 | `RecommendationBatch.inputSnapshotDefinitionVersion` 仅作索引提示,**权威来源是 `inputSnapshot` 本身**(v0.1 核心修正,对应 req 9;v0 中只存版本号,依赖回溯重建输入) |
| 每轮输入数据 | `RecommendationBatch.inputSnapshot` 内联全量深拷贝 | 不再仅依赖"通过 `ExperimentRound` 边界重建"这种间接方式;间接重建方式仍可用于交叉校验,但不是唯一来源 |
| 算法名称和版本 | `RecommendationBatch.algorithmConfig`(`backendName`/`backendVersion`/`backendCommit`/`strategyClassName`/`hyperparameters`/`acquisitionFunction`) | 版本号来自运行环境实际安装包(或本文档 §5.0 的双重锚点方案,过渡期使用) |
| 随机种子 | `algorithmConfig.seed` | 规则不变:必须记录实际使用值,不允许"种子未知" |
| 推荐结果 | `RecommendationBatch.candidates` | 不变 |
| 用户确认记录 | `DecisionLog`(append-only),`action='ConstraintsConfirmed'` | `payload` 记录完整 `constraints` 数组快照 + `constraintsConfirmed` 布尔值(而不是单条约束) |

**`budgetUsed` 与 `round` 的独立性说明(消解 v0 遗留的疑似矛盾,对应 req 11)**:`round` 数的是"生成过几批 `RecommendationBatch`",`budgetUsed` 数的是"物理执行过几次 `ExperimentRun`"。两者是完全独立的计数维度,不要求同步增长——`round=1` 且 `budgetUsed=0` 是"刚生成完第一轮推荐、尚未执行任何候选"的合法状态,不是矛盾。`budgetUsed` 的精确计算口径见 §2.2,本质上是可从 `ExperimentRun` 表直接重新推导的物化计数。

**版本化与历史数据的关系**:与 v0 相同,`CampaignRun` 从 `RoundClosed` 因编辑 `CampaignDefinition` 退回 `Draft` 产生新版本时,已产生的 `ExperimentRound`/`RecommendationBatch`/`ExperimentRun`/`Measurement` 不会被删除或迁移,永久关联到产生时的 `currentDefinitionVersion`。跨版本复用规则见 §9-7(实体名称已更新,规则本身不变:参数 `id` 存在且类型未变则复用,否则标记 `Invalid`)。

---

## 8. 环氧涂层案例 — 完整 JSON 示例(v0.1,自洽叙事)

叙事时间线:`CampaignDefinition` v3 已通过校验并确认约束;`CampaignRun` 当前处于 `RecommendationsPending`,已生成第 1 轮推荐(4 个候选,`batchSize=4`);其中 1 个候选已经物理执行完毕并测量完 2 个目标(部分回填示例),其余 3 个仍 `Pending`;因此 `budgetUsed=1`(不是 0),与 `round=1` 互不矛盾。

```json
{
  "campaignDefinition": {
    "id": "campaigndef-epoxy-coating-001",
    "version": 3,
    "name": "Epoxy Coating Optimization",
    "goal": "优化环氧涂层配方,希望硬度高、不易脆、固化快、成本低。",
    "createdAt": "2026-07-20T02:10:00Z",
    "updatedAt": "2026-07-29T09:35:00Z",
    "createdBy": "user-li-wei",
    "parameters": [
      { "id": "param-resin-ratio", "name": "Resin Ratio", "type": "Continuous", "unit": "%", "description": "", "bounds": { "lower": 60, "upper": 85, "stepsize": null } },
      { "id": "param-hardener-ratio", "name": "Hardener Ratio", "type": "Continuous", "unit": "%", "description": "", "bounds": { "lower": 15, "upper": 40, "stepsize": null } },
      { "id": "param-curing-temperature", "name": "Curing Temperature", "type": "Continuous", "unit": "°C", "description": "", "bounds": { "lower": 80, "upper": 160, "stepsize": null } },
      { "id": "param-curing-time", "name": "Curing Time", "type": "Continuous", "unit": "min", "description": "", "bounds": { "lower": 20, "upper": 120, "stepsize": null } }
    ],
    "outputs": [
      { "id": "output-hardness", "name": "Hardness", "unit": "", "description": "" },
      { "id": "output-brittleness", "name": "Brittleness", "unit": "", "description": "" },
      { "id": "output-cost", "name": "Cost", "unit": "", "description": "" },
      { "id": "output-actual-curing-time", "name": "Actual Curing Time", "unit": "min", "description": "" }
    ],
    "objectivePolicies": [
      { "id": "policy-hardness", "outputId": "output-hardness", "direction": "Maximize", "weight": null, "targetValue": null },
      { "id": "policy-brittleness", "outputId": "output-brittleness", "direction": "Minimize", "weight": null, "targetValue": null },
      { "id": "policy-cost", "outputId": "output-cost", "direction": "Minimize", "weight": null, "targetValue": null },
      { "id": "policy-actual-curing-time", "outputId": "output-actual-curing-time", "direction": "Minimize", "weight": null, "targetValue": null }
    ],
    "constraints": [
      {
        "id": "constraint-resin-hardener-sum",
        "choice": "fixed-sum",
        "involvedParameterIds": ["param-resin-ratio", "param-hardener-ratio"],
        "targetSum": 100,
        "customExpression": null,
        "parsedExpression": null,
        "executable": true,
        "resolvedAt": "2026-07-22T06:05:00Z"
      }
    ],
    "constraintsConfirmed": true,
    "constraintsConfirmedAt": "2026-07-22T06:05:00Z",
    "optimizationPolicy": {
      "id": "optpolicy-epoxy-001",
      "backendName": "baybe",
      "strategyClassName": "TwoPhaseMetaRecommender",
      "batchSize": 4,
      "seedPolicy": "Fixed",
      "seedValue": 42,
      "recommenderConfig": { "switch_after": 10, "remain_switched": true },
      "coldStartStrategy": null
    }
  },

  "campaignRun": {
    "id": "campaignrun-epoxy-coating-001",
    "campaignDefinitionId": "campaigndef-epoxy-coating-001",
    "currentDefinitionVersion": 3,
    "status": "RecommendationsPending",
    "round": 1,
    "budgetTotal": 12,
    "budgetUsed": 1,
    "createdAt": "2026-07-20T02:10:00Z",
    "updatedAt": "2026-07-29T14:22:00Z",
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
    "inputSnapshotDefinitionVersion": 3,
    "inputSnapshot": {
      "parameters": "<= campaignDefinition.parameters 在 2026-07-29T09:40:05Z 时刻的深拷贝,此处省略重复内容 =>",
      "outputs": "<= 同上,outputs 深拷贝 =>",
      "objectivePolicies": "<= 同上,objectivePolicies 深拷贝 =>",
      "constraints": "<= 同上,constraints 深拷贝 =>",
      "priorExperimentRuns": [],
      "priorMeasurements": []
    },
    "algorithmConfig": {
      "backendName": "baybe",
      "backendVersion": "0.15.0",
      "backendCommit": "b939e3588aad832856c33ac055c5510f7cb76f96",
      "strategyClassName": "TwoPhaseMetaRecommender",
      "hyperparameters": { "switch_after": 10, "remain_switched": true },
      "acquisitionFunction": null,
      "seed": 42
    },
    "status": "PartiallyExecuted",
    "candidates": [
      {
        "id": "cand-r1-1",
        "parameterValues": { "param-resin-ratio": 72.5, "param-hardener-ratio": 27.5, "param-curing-temperature": 120, "param-curing-time": 60 },
        "predictedMean": null,
        "predictedSd": null,
        "desirability": null
      },
      {
        "id": "cand-r1-2",
        "parameterValues": { "param-resin-ratio": 65, "param-hardener-ratio": 35, "param-curing-temperature": 95, "param-curing-time": 90 },
        "predictedMean": null,
        "predictedSd": null,
        "desirability": null
      }
    ]
  },

  "experimentRunExample_completedWithPartialMeasurements": {
    "id": "exprun-r1-1",
    "campaignRunId": "campaignrun-epoxy-coating-001",
    "experimentRoundId": "round-epoxy-001-r1",
    "recommendationCandidateId": "cand-r1-1",
    "parameterValues": { "param-resin-ratio": 72.5, "param-hardener-ratio": 27.5, "param-curing-temperature": 120, "param-curing-time": 60 },
    "status": "Completed",
    "executedAt": "2026-07-29T14:20:00Z",
    "executedBy": "user-li-wei",
    "notes": "固化后立即测得硬度与脆性;成本与实际固化时长指标尚在测试中,预计次日补齐。"
  },

  "measurementExamples": [
    {
      "id": "meas-r1-1-hardness",
      "experimentRunId": "exprun-r1-1",
      "outputId": "output-hardness",
      "value": 78.4,
      "status": "Valid",
      "recordedAt": "2026-07-29T14:22:00Z",
      "recordedBy": "user-li-wei"
    },
    {
      "id": "meas-r1-1-brittleness",
      "experimentRunId": "exprun-r1-1",
      "outputId": "output-brittleness",
      "value": 12.1,
      "status": "Valid",
      "recordedAt": "2026-07-29T14:22:00Z",
      "recordedBy": "user-li-wei"
    }
  ],

  "decisionLogExamples": [
    {
      "id": "log-0001",
      "campaignRunId": "campaignrun-epoxy-coating-001",
      "timestamp": "2026-07-22T06:05:00Z",
      "actor": "user-li-wei",
      "action": "ConstraintsConfirmed",
      "campaignDefinitionVersion": 2,
      "payload": { "constraintsConfirmed": true, "constraints": [{ "choice": "fixed-sum", "targetSum": 100, "involvedParameterIds": ["param-resin-ratio", "param-hardener-ratio"] }] },
      "relatedEntityId": "constraint-resin-hardener-sum"
    },
    {
      "id": "log-0002",
      "campaignRunId": "campaignrun-epoxy-coating-001",
      "timestamp": "2026-07-22T06:05:01Z",
      "actor": "user-li-wei",
      "action": "DesignSpaceValidated",
      "campaignDefinitionVersion": 3,
      "payload": { "ok": true, "issues": [] },
      "relatedEntityId": null
    },
    {
      "id": "log-0003",
      "campaignRunId": "campaignrun-epoxy-coating-001",
      "timestamp": "2026-07-29T09:40:05Z",
      "actor": "user-li-wei",
      "action": "InitialDesignGenerated",
      "campaignDefinitionVersion": 3,
      "payload": { "batchSize": 4, "backendName": "baybe", "backendVersion": "0.15.0", "seed": 42 },
      "relatedEntityId": "batch-epoxy-001-r1"
    },
    {
      "id": "log-0004",
      "campaignRunId": "campaignrun-epoxy-coating-001",
      "timestamp": "2026-07-29T14:20:00Z",
      "actor": "user-li-wei",
      "action": "ExperimentRunExecuted",
      "campaignDefinitionVersion": 3,
      "payload": { "status": "Completed" },
      "relatedEntityId": "exprun-r1-1"
    },
    {
      "id": "log-0005",
      "campaignRunId": "campaignrun-epoxy-coating-001",
      "timestamp": "2026-07-29T14:22:00Z",
      "actor": "user-li-wei",
      "action": "MeasurementRecorded",
      "campaignDefinitionVersion": 3,
      "payload": { "outputIds": ["output-hardness", "output-brittleness"], "note": "部分回填,cost/actual-curing-time 待补" },
      "relatedEntityId": "exprun-r1-1"
    }
  ]
}
```

---

## 9. 开放问题(需产品负责人决定)

1. **CampaignDefinition : CampaignRun 是否长期保持 1:1**(v0 开放问题 1 已通过本次修订解决:`constraints` 已改为数组)。拆分后自然衍生出新问题:是否需要在近期支持"同一个问题定义多次独立运行/对比不同后端或不同 `OptimizationPolicy`"(1:N)?这决定了 `CampaignRun` 是否需要一个区分同一 Definition 下多个 Run 的 `label`/`name` 字段。
2. **`custom` 约束的定位需要重新明确**:v0.1 把"未解析 = 阻断推荐"变成硬约束(原为 warning),用户体验上更"卡"。需要产品明确:`custom` 选项是"为未来 DSL 占位的严肃能力",还是应该被更多具体化的约束类型(例如显式支持不等式和别的线性组合)替代掉,减少用户走进这条容易卡住的路径?
3. **目标权重何时启用**(不变,字段位置迁移到 `ObjectivePolicy.weight`):权重语义是"Desirability 组合权重"还是"仅用于排序展示"?
4. **BoFire 接入时间线**(不变)。
5. **部分批次能否手动结束轮次**:`ExperimentRun.status='Cancelled'` 部分回答了这个问题(用户可将某个候选标记为 Cancelled 以不再等待它),但"取消单个候选"与"强制关闭整轮"仍是两个粒度。是否需要一个显式的 `close_round(force=true)` 操作,允许在仍有 `Pending` `ExperimentRun` 的情况下强制关闭轮次?需要产品给出业务规则。
6. **`Completed` 之后的 `reopen()` 权限**(不变)。
7. **跨版本 `ExperimentRun`/`Measurement` 复用规则的最终确认**(实体名称已从 Observation 更新,规则本身不变,仍需产品/算法负责人确认)。
8. **化学/分子式模态的实际优先级**(不变,已明确标注为本次会话中的粘贴错误,不属于当前范围)。
9. **多后端场景下的自动选型规则**(不变)。
10. **(新增)`OutputSpec` 与 `ObjectivePolicy` 是否需要放开 1:1 限制**:是否允许一个 `OutputSpec` 被多个 `ObjectivePolicy` 引用(例如同一测量值同时参与两种不同优化视角),或允许存在"纯监控、无 Policy"的 Output(不参与优化计算,只是记录展示)?当前 MVP 强制严格 1:1。
11. **(新增)`OptimizationPolicy.recommenderConfig` 是否需要按 backend/strategy 做 JSON Schema 强校验**,避免用户或 Agent 传入后端不支持、甚至危险的超参数组合。当前 MVP 阶段该字段是不透明 object,只做"是否为合法 JSON"级别的校验。
12. **(新增)BayBE 的 Cardinality 约束能否在多大程度上替代 N-choose-K 需求**:本次评审发现两者语义接近但不完全等价(§5.2)。是否可以在 BayBE-only MVP 阶段,把满足"至多/至少 K 个参数非零"这类简单场景的 N-choose-K 需求降级路由到 Cardinality 约束,而不是一律拒绝?需要算法负责人给出判定边界。
