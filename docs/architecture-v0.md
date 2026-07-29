# 工业实验优化平台 — 架构设计 v0

> 状态: 草案(v0),供产品/工程评审。本文档不涉及任何代码改动。
> 输入依据: `实验工艺优化平台_调研报告.md`(战略结论)、`comparison_report.md`(BoFire/BayBE 技术对比)、
> 现有前端原型 `frontend/src/pages/campaigns/demo-v2/*`(UI 层已验证的领域概念:
> `Parameter`/`Objective`/`ConstraintState`/`CampaignData`)。
> 本文档只定义**平台统一领域模型与接口契约**,不绑定具体后端框架(FastAPI/Django/…)、
> 不绑定具体数据库、不包含 UI 实现细节。

---

## 1. MVP 边界

### 1.1 MVP 做什么

| 能力 | 说明 |
|---|---|
| Campaign 定义 | 创建/编辑 Campaign 的 Parameters、Objectives、单条 Constraint、目标/预算元信息 |
| 设计空间校验 | `validate()`:参数合法性(名称唯一、连续上下界、离散/类别非空值)、目标合法性(名称唯一、至少一个)、约束是否已确认 |
| 初始实验设计 | `generate_initial_design()`:仅使用 **单一后端(BayBE)**,冷启动生成第一批候选 |
| 迭代推荐 | `recommend()`:基于累积 Observation 生成下一批候选(单目标 + Desirability 多目标) |
| 观测回填 | 人工录入/编辑 Observation,支持部分回填(批次内某些候选尚未完成实验) |
| 决策留痕 | 记录约束确认、参数/目标编辑、推荐生成、观测回填、Campaign 完成等关键动作到 DecisionLog |
| 可复现性 | 每次 `generate_initial_design`/`recommend` 落盘:后端名称+版本、随机种子、输入快照、输出候选 |
| Campaign 版本化 | Parameters/Objectives/Constraint 变更产生新版本,历史版本只读可查 |

### 1.2 MVP 暂时不做什么

| 能力 | 原因 / 计划 |
|---|---|
| 数据库/持久化服务落地、真实 API、前后端联调 | 本文档只定义契约;落地是下一阶段任务,当前前端仍为纯 mock 状态 |
| BoFire 后端接入、双后端自动选型 | 调研报告结论"先 A 后 B":MVP 先用 BayBE 跑通闭环,BoFire 留给需要强约束/DoE 的场景(见 §5) |
| LLM/Agent 自动产出推荐数值 | 明确禁止,见 §6;Agent 在 MVP 阶段甚至可以完全不接入,只影响交互层 |
| 化学/分子式模态(`MolecularInput`/`SubstanceParameter`) | 当前案例(环氧涂层配方)不需要;留作 v1+ 扩展点,ParameterSpec 的 `type` 枚举预留但不实现 |
| DoE 最优设计(D/A/G-optimal)、N-choose-K/非线性约束 | 仅 BoFire 支持,MVP 单后端(BayBE)阶段直接拒绝这类约束/策略请求(见 §5 拒绝矩阵) |
| 迁移学习(Transfer Learning / TaskParameter) | 需要历史项目库,MVP 阶段每个 Campaign 独立无历史迁移 |
| SHAP/可解释性图表 | `explain()` 接口预留但 MVP 可返回"未实现"错误,不阻塞主闭环 |
| 多租户、权限、审批流 | DecisionLog 记录 `actor` 字段,但不做权限校验;单用户/单团队场景 |
| 目标权重(Objective Weighting) | 前端已明确将其放入"Optional Preferences",不参与 Validation/Gating,ObjectiveSpec.weight 字段预留但后端不读取 |
| 批次并发/多人协作编辑 | 假设同一时刻一个 Campaign 只有一个操作者 |

---

## 2. 平台统一领域模型

设计原则:字段命名与前端 `types.ts` 中已验证的概念对齐(`Parameter`/`Objective`/`ConstraintState`),
但作为后端权威模型补充了 `id`/版本/审计等前端尚未建模的字段。**所有类型均为平台自有类型,
不直接引用 `bofire.*` 或 `baybe.*` 的类**(理由见 §5 末尾原则)。

类型标注使用通用记法:`string` / `number` / `boolean` / `enum(...)` / `array<T>` / `object` / `T?`(可选)。

### 2.1 CampaignSpec

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string (UUID) | ✓ | 全局唯一 |
| `version` | number (int, ≥1) | ✓ | 单调递增;Parameters/Objectives/Constraint 任一变更即 `version += 1` |
| `name` | string | ✓ | 非空,平台内唯一(同一工作区) |
| `goal` | string | — | 自由文本,人类可读的实验目标描述(对应前端 Campaign Goal 区块) |
| `status` | enum(`Draft`,`DesignSpaceValidated`,`RecommendationsPending`,`RoundClosed`,`Completed`,`Archived`) | ✓ | 见 §3 状态机;前端 UI 的三态 `Draft/Active/Completed` 是此枚举的粗粒度视图(映射见 §3.4) |
| `round` | number (int, ≥0) | ✓ | 当前实验轮次,0 表示尚未生成过推荐 |
| `batchSize` | number (int, ≥1) | ✓ | 每轮推荐的候选数量 |
| `budgetTotal` | number (int, ≥1) | ✓ | 预算实验总次数上限 |
| `budgetUsed` | number (int, ≥0) | ✓ | 已消耗的实验次数(= 已回填 Observation 数量) |
| `parameters` | array<ParameterSpec> | ✓ | 至少 1 项才能通过设计空间校验(空数组允许存在于 `Draft`) |
| `objectives` | array<ObjectiveSpec> | ✓ | 至少 1 项才能通过设计空间校验 |
| `constraint` | ConstraintSpec? | — | MVP 阶段每个 Campaign 至多 1 条约束(对齐前端 `ConstraintState`);null 表示尚未确认 |
| `createdAt` / `updatedAt` | string (ISO-8601) | ✓ | |
| `createdBy` | string | ✓ | 用户标识,MVP 不做权限校验但记录 |

**关键约束**:`parameters`、`objectives` 内部元素的 `id` 在各自数组内必须唯一;
`name` 在各自数组内必须唯一(大小写不敏感,与前端 `parameterUtils.ts`/`objectiveUtils.ts` 校验规则一致)。

### 2.2 ParameterSpec

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | Campaign 内唯一 |
| `name` | string | ✓ | 非空,Campaign 内唯一 |
| `type` | enum(`Continuous`,`Discrete`,`Categorical`) | ✓ | 判别字段;`Molecular`/`Substance` 为 v1+ 预留值,MVP 校验层直接拒绝 |
| `unit` | string | — | 可为空字符串 |
| `description` | string | — | 可为空字符串 |
| `bounds` | object `{ lower: number, upper: number, stepsize: number? }` | 当 `type = Continuous` 时必填 | `lower < upper`;`stepsize` 为 v1+ 预留(离散化连续参数) |
| `values` | array<string> | 当 `type ∈ {Discrete, Categorical}` 时必填 | 去除空白后至少 1 个非空值;`Discrete` 隐含数值可排序,`Categorical` 无序 |

**必填规则**(与前端 `getParameterIssues` 一致):
- `name` 缺失 → 拒绝;`name` 重复(忽略大小写)→ 拒绝。
- `type = Continuous` 且 `lower/upper` 缺失或 `lower >= upper` → 拒绝。
- `type ∈ {Discrete, Categorical}` 且 `values` 全为空白 → 拒绝。

### 2.3 ObjectiveSpec

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | Campaign 内唯一 |
| `name` | string | ✓ | 非空,Campaign 内唯一 |
| `direction` | enum(`Maximize`,`Minimize`) | ✓ | `Target`/`CloseToTarget` 为 v1+ 预留值 |
| `unit` | string | — | 可为空字符串 |
| `description` | string | — | 可为空字符串 |
| `weight` | number? | — | **MVP 不启用**(对应前端 Optional Preferences);字段存在但校验/推荐逻辑不读取 |
| `targetValue` | number? | — | 仅 `direction = Target` 时使用,v1+ 预留 |

**必填规则**(与前端 `getObjectiveIssues`/`areObjectivesValid` 一致):
- `name` 缺失 → 拒绝;`name` 重复 → 拒绝。
- Campaign 级校验:`objectives.length === 0` → 拒绝(至少保留一个目标)。

### 2.4 ConstraintSpec

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `choice` | enum(`fixed-sum`,`no-constraint`,`custom`) | ✓ | 对齐前端 `ConstraintChoice` |
| `involvedParameterIds` | array<string> | 当 `choice = fixed-sum` 时必填 | 引用 `ParameterSpec.id`,至少 2 项,且均为 `Continuous` 类型 |
| `targetSum` | number | 当 `choice = fixed-sum` 时必填 | MVP 固定语义为 "涉及参数之和 = targetSum"(环氧涂层案例中为 100) |
| `customExpression` | string | 当 `choice = custom` 时必填,非空 | **MVP 阶段为不透明文本**,平台不解析/不求值,仅存档展示(见 §9 开放问题) |
| `resolvedAt` | string (ISO-8601)? | — | 用户做出选择的时间戳,用于 DecisionLog 关联 |

**必填规则**:`choice` 为 `null`(未选择)视为约束未解决,`isConstraintResolved()` 返回 false,
与前端 `constraintUtils.ts` 语义一致。MVP 每个 Campaign 至多一条 `ConstraintSpec`;
多约束(数组化)为 v1+ 扩展点,届时 `CampaignSpec.constraint` 改为 `constraints: ConstraintSpec[]`。

### 2.5 Observation

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignId` | string | ✓ | |
| `experimentRoundId` | string | ✓ | 所属实验轮次 |
| `recommendationCandidateId` | string? | — | 若该观测回填自某条推荐候选则关联;人工额外补充实验时可为空 |
| `parameterValues` | object (map: `ParameterSpec.id` → string \| number) | ✓ | 每个已配置参数必须有值;类型校验依据对应 `ParameterSpec.type` |
| `objectiveValues` | object (map: `ObjectiveSpec.id` → number) | ✓ | 每个已配置目标必须有实测值才能标记为 `Recorded` |
| `status` | enum(`Pending`,`Recorded`,`Invalid`) | ✓ | `Pending`:候选已生成待实验;`Recorded`:已回填;`Invalid`:实验失败/异常值,不参与下一轮拟合但保留存档 |
| `recordedAt` | string (ISO-8601)? | 当 `status = Recorded` 时必填 | |
| `recordedBy` | string? | 当 `status = Recorded` 时必填 | |
| `notes` | string | — | |

### 2.6 RecommendationBatch

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignId` | string | ✓ | |
| `roundNumber` | number (int, ≥1) | ✓ | |
| `generatedAt` | string (ISO-8601) | ✓ | |
| `backendName` | string | ✓ | 例如 `"baybe"`(MVP 固定值) |
| `backendVersion` | string | ✓ | 例如 `"0.11.3"`,来自运行环境包版本,不手工填写 |
| `seed` | number (int)? | ✓(若后端支持随机种子则必填,否则为 null 并在 `notes` 说明) | 保证同输入可复现同输出 |
| `campaignSpecVersion` | number | ✓ | 引用生成该批次时的 `CampaignSpec.version`,用于复现性回溯 |
| `candidates` | array<RecommendationCandidate> | ✓ | 长度 = 请求的 `batchSize`(除非后端因搜索空间过小截断,需在 `notes` 说明) |
| `status` | enum(`Proposed`,`PartiallyExecuted`,`FullyExecuted`,`Superseded`) | ✓ | `Superseded`:该批次未执行完就生成了新一轮推荐(允许但需 DecisionLog 留痕) |

`RecommendationCandidate` 子对象:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | |
| `parameterValues` | object (map) | ✓ | 与 `Observation.parameterValues` 同构 |
| `predictedMean` | object (map: `ObjectiveSpec.id` → number)? | — | 后端若提供预测均值(如 BayBE/BoFire 的 `_pred`) |
| `predictedSd` | object (map)? | — | 预测标准差(不确定度) |
| `desirability` | number? | — | 多目标期望度综合分,仅当使用 Desirability 组合时提供 |

### 2.7 ExperimentRound

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignId` | string | ✓ | |
| `roundNumber` | number (int, ≥1) | ✓ | 与 `RecommendationBatch.roundNumber` 一一对应 |
| `recommendationBatchId` | string | ✓ | |
| `observationIds` | array<string> | ✓ | 初始为空,随回填增长 |
| `openedAt` | string (ISO-8601) | ✓ | |
| `closedAt` | string (ISO-8601)? | — | 全部候选 `status ∈ {Recorded, Invalid}` 时可关闭 |
| `status` | enum(`Open`,`Closed`) | ✓ | |

### 2.8 DecisionLog

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---|---|
| `id` | string | ✓ | |
| `campaignId` | string | ✓ | |
| `timestamp` | string (ISO-8601) | ✓ | |
| `actor` | string | ✓ | 用户 id,或 `"agent:<name>"`,或 `"system"` |
| `action` | enum(`ParameterEdited`,`ObjectiveEdited`,`ConstraintProposed`,`ConstraintConfirmed`,`DesignSpaceValidated`,`DesignSpaceValidationFailed`,`InitialDesignGenerated`,`RecommendationRequested`,`ObservationRecorded`,`RoundClosed`,`CampaignCompleted`,`CampaignArchived`) | ✓ | |
| `campaignSpecVersion` | number | ✓ | 该动作发生时的 spec 版本 |
| `payload` | object | — | 动作相关的最小快照,例如 `ConstraintConfirmed` 记录 `{ choice, targetSum }`;`ParameterEdited` 记录 `{ before, after }` |
| `relatedEntityId` | string? | — | 关联的 `RecommendationBatch.id` / `ExperimentRound.id` / `Observation.id` 等 |

**不可变性**:DecisionLog 只追加(append-only),不允许更新或删除已写入的条目——这是可复现性的基础。

---

## 3. Campaign 生命周期状态机

### 3.1 状态定义

| 状态 | 含义 |
|---|---|
| `Draft` | Campaign 正在被定义,Parameters/Objectives/Constraint 尚未通过校验,或校验后又被编辑 |
| `DesignSpaceValidated` | `validate()` 通过:参数/目标合法且约束已确认;尚未生成过任何推荐 |
| `RecommendationsPending` | 存在一个 `status = Proposed` 或 `PartiallyExecuted` 的 `RecommendationBatch`,等待观测回填 |
| `RoundClosed` | 当前轮次全部候选已回填(`Recorded`/`Invalid`),等待下一步决策(继续推荐或结束) |
| `Completed` | 用户主动结束 Campaign(达到目标或预算耗尽),只读 |
| `Archived` | 归档,完全只读,不出现在默认列表 |

### 3.2 状态转换

```
Draft --validate() 通过--> DesignSpaceValidated
Draft --validate() 失败--> Draft (停留,附带失败原因)

DesignSpaceValidated --编辑 Parameters/Objectives/Constraint--> Draft (spec version += 1)
DesignSpaceValidated --generate_initial_design() 成功--> RecommendationsPending (round=1)

RecommendationsPending --全部候选回填完成--> RoundClosed
RecommendationsPending --编辑 Parameters/Objectives/Constraint--> 拒绝(见 3.3);
    若确需修改,须先将当前 Batch 标记 Superseded 并转入 RoundClosed,再回到 Draft

RoundClosed --recommend() 成功--> RecommendationsPending (round += 1)
RoundClosed --编辑 Parameters/Objectives/Constraint--> Draft (spec version += 1,历史轮次数据保留但标记"跨版本")
RoundClosed --mark_completed()--> Completed

Completed --reopen()(显式管理操作)--> RoundClosed
Completed --archive()--> Archived
DesignSpaceValidated / RoundClosed --archive()--> Archived (Draft/RecommendationsPending 不可直接归档,须先完成或退回)

Archived --(终态,无出边)--
```

### 3.3 各状态允许的操作

| 操作 | Draft | DesignSpaceValidated | RecommendationsPending | RoundClosed | Completed | Archived |
|---|---|---|---|---|---|---|
| 编辑 Parameters/Objectives | ✓ | ✓(触发退回 Draft) | ✗(需先处理未完成批次) | ✓(触发退回 Draft) | ✗ | ✗ |
| 确认/修改 Constraint | ✓ | ✓(触发退回 Draft) | ✗ | ✓(触发退回 Draft) | ✗ | ✗ |
| `validate()` | ✓ | ✓(空操作,已通过) | ✓(只读检查) | ✓(只读检查) | ✓(只读) | ✓(只读) |
| `generate_initial_design()` | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `recommend()` | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| 回填 Observation | ✗ | ✗ | ✓ | ✗(轮次已关闭,只读) | ✗ | ✗ |
| `explain()` | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| `mark_completed()` | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `archive()` | ✗ | ✓ | ✗ | ✓ | ✓ | ✗ |
| 查看历史(DecisionLog/历史轮次) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

### 3.4 与前端三态的映射

前端 `CampaignData.status: 'Draft' | 'Active' | 'Completed'` 是本状态机的粗粒度投影:

| 前端状态 | 对应后端状态 |
|---|---|
| `Draft` | `Draft`、`DesignSpaceValidated` |
| `Active` | `RecommendationsPending`、`RoundClosed` |
| `Completed` | `Completed`、`Archived`(前端如需区分可后续再拆) |

---

## 4. OptimizerAdapter 统一接口

所有后端(BayBE/BoFire/未来其他引擎)必须实现同一接口;平台服务层与 Agent 工具层
只与 `OptimizerAdapter` 交互,不直接 import 具体后端库。

```python
class OptimizerAdapter(Protocol):

    def capabilities(self) -> AdapterCapabilities:
        """无输入。返回该后端静态能力描述,用于校验前置拦截与前端能力提示。"""

    def validate(self, campaign: CampaignSpec) -> ValidationResult:
        """
        输入: 完整 CampaignSpec(parameters/objectives/constraint)。
        输出: ValidationResult { ok: bool, issues: list[ValidationIssue] }。
        不产生副作用,不调用底层优化库的重计算逻辑,仅做结构/语义校验
        (含"该后端是否支持这个 spec 中用到的特性",见 capabilities())。
        错误类型: 不抛异常,所有问题收敛进 ValidationResult.issues。
        """

    def generate_initial_design(
        self, campaign: CampaignSpec, batch_size: int, seed: int | None = None
    ) -> RecommendationBatch:
        """
        输入: 已通过 validate() 的 CampaignSpec,batch_size,可选 seed。
        输出: RecommendationBatch(roundNumber=1)。
        前置条件: campaign 无历史 Observation。
        错误类型:
          - ValidationError: 传入未校验通过的 spec
          - UnsupportedFeatureError: spec 使用了该后端不支持的参数/约束/目标类型
          - ComputationError: 底层库计算异常(数值失败、搜索空间为空等)
        """

    def recommend(
        self,
        campaign: CampaignSpec,
        observations: list[Observation],
        batch_size: int,
        seed: int | None = None,
    ) -> RecommendationBatch:
        """
        输入: CampaignSpec + 截至当前的全部有效 Observation(status=Recorded)。
        输出: RecommendationBatch(roundNumber = campaign.round + 1)。
        错误类型:
          - ValidationError
          - UnsupportedFeatureError
          - InsufficientDataError: 观测数量不足以拟合代理模型(如 BoFire 冷启动 ask() 报错场景)
          - ComputationError
        """

    def update(self, campaign: CampaignSpec, observations: list[Observation]) -> None:
        """
        输入: CampaignSpec + 新增/修正的 Observation。
        输出: 无返回值;用于"仅更新后端内部状态缓存(若后端支持增量,如 BayBE Campaign.add_measurements)
        而不立即触发推荐"的场景,是 recommend() 的可选性能优化钩子,MVP 可用 no-op 实现
        (每次 recommend() 都重建状态)。
        错误类型: ValidationError、ComputationError。
        """

    def explain(
        self, campaign: CampaignSpec, observations: list[Observation]
    ) -> ExplanationResult:
        """
        输入: CampaignSpec + Observation。
        输出: ExplanationResult { featureImportance: dict, notes: str }(如 SHAP)。
        错误类型: NotImplementedError(MVP 阶段允许直接声明不支持,由 capabilities() 提前标注)。
        """
```

**错误类型体系**(平台自定义异常,不透传底层库异常):

| 异常 | 触发场景 |
|---|---|
| `ValidationError` | spec 结构/语义不合法(缺字段、重名、越界) |
| `UnsupportedFeatureError` | spec 使用了当前后端 `capabilities()` 未声明支持的特性 |
| `InsufficientDataError` | 观测数据不足以支撑推荐计算 |
| `ComputationError` | 底层数值计算失败(非平台可预期的输入问题) |
| `BackendUnavailableError` | 后端进程/依赖未就绪(导入失败、环境缺依赖) |

`AdapterCapabilities` 描述对象(用于 §5 能力矩阵的机器可读版本):

| 字段 | 类型 | 说明 |
|---|---|---|
| `backendName` / `backendVersion` | string | |
| `supportedParameterTypes` | array<enum> | 如 `["Continuous","Discrete","Categorical"]` |
| `supportedObjectiveDirections` | array<enum> | 如 `["Maximize","Minimize"]` |
| `supportedConstraintChoices` | array<enum> | 如 `["fixed-sum","no-constraint"]`(注意 `custom` 是否可被解析求值) |
| `supportsMultiObjective` | boolean | |
| `supportsExplain` | boolean | |
| `supportsOptimalDesign` | boolean | 是否支持 D/A/G-optimal 等严谨实验设计 |
| `minObservationsForRecommend` | number (int) | 冷启动阈值 |
| `maxDiscreteCombinationEstimate` | number? | 高基数离散笛卡尔积护栏(对齐 BayBE `estimate_product_space_size`) |

---

## 5. BayBE 与 BoFire 能力映射

**平台领域模型不允许直接依赖两个库的内部类型。** 转换只发生在各自 Adapter 实现内部的
私有映射层(例如 `BaybeAdapter._to_baybe_parameter(ParameterSpec) -> baybe.parameters.*`),
`CampaignSpec`/`ParameterSpec`/… 本身不 import `bofire`/`baybe`。这样做的原因:
(1) 两库均为 Apache-2.0 第三方依赖,版本演进不应传导到平台核心模型;
(2) 保留"平台模型可同时映射到两个甚至未来第三个后端"的能力,而不是被某一库的建模选择锁死
(例如 BayBE 的 attrs 不可变对象 vs BoFire 的 Pydantic spec 双层分离——这是两种不同的架构哲学,
平台模型需要是两者的公共上位抽象)。

### 5.1 可直接转换(两后端均直接支持)

| 平台模型 | BayBE | BoFire |
|---|---|---|
| `ParameterSpec(type=Continuous)` | `NumericalContinuousParameter` | `ContinuousInput` |
| `ParameterSpec(type=Discrete)` | `NumericalDiscreteParameter` | `DiscreteInput` |
| `ParameterSpec(type=Categorical)` | `CategoricalParameter` | `CategoricalInput` |
| `ObjectiveSpec(direction=Maximize/Minimize)`,单目标 | `NumericalTarget` + `SingleTargetObjective` | `ContinuousOutput` + `MaximizeObjective`/`MinimizeObjective` |
| `ConstraintSpec(choice=fixed-sum)`,仅涉及连续参数 | 连续线性等式约束 | `LinearEqualityConstraint` |
| `ConstraintSpec(choice=no-constraint)` | 不建约束 | 不建约束 |

### 5.2 仅部分支持(需要能力探测 + 降级策略)

| 平台特性 | BayBE | BoFire | 降级/处理方式 |
|---|---|---|---|
| 多目标(Desirability/Pareto) | `DesirabilityObjective`(需额外提供每个目标的缩放范围,平台当前 `ObjectiveSpec` 无此字段,MVP 用参数默认区间近似)/ `ParetoObjective` | `MoboStrategy`(qNEHVI/qParEGO,原生多目标,无需额外缩放输入) | BayBE 路径下 Desirability 的缩放边界若未提供,Adapter 使用目标历史观测的 min/max 做运行时估计,并在 `RecommendationBatch.notes` 标注"缩放边界为运行时估计,非用户指定" |
| 初始实验设计(冷启动) | 无专门 DoE 模块,靠 `TwoPhaseMetaRecommender` 的随机阶段 | `DoEStrategy`(D/A/G/I/K-optimal) | MVP 单后端(BayBE)时 `generate_initial_design()` 一律走随机/空间填充策略,**不做**严谨最优设计;若后续接入 BoFire,`capabilities().supportsOptimalDesign=true` 时平台可选择路由到 BoFire |
| 可解释性(`explain()`) | `insights/shap.py`(SHAP/LIME/MAPLE) | `surrogates/feature_importance.py`(SHAP/ARD/置换) | 两者均"部分支持":依赖已拟合代理模型且观测量达到阈值,否则返回 `InsufficientDataError` |
| 迁移学习 | `TaskParameter` 原生支持,一等公民 | 多任务 GP,存在但非一等公民 | MVP 均不启用(§1.2);v1+ 若启用,优先路由到 BayBE |

### 5.3 必须拒绝或降级的情况

| 平台特性 | 处理方式 | 理由 |
|---|---|---|
| `ConstraintSpec(choice=fixed-sum)` 涉及 `Discrete`/`Categorical` 参数 | BayBE 路径:`UnsupportedFeatureError`;BoFire 路径:可支持(混合约束) | BayBE 的线性约束主要面向连续子空间;MVP 单后端(BayBE)时直接在 `validate()` 阶段拒绝并给出明确 issue,而不是静默降级 |
| 非线性约束 / N-choose-K 约束 | 两库中仅 BoFire 支持;MVP(BayBE-only)阶段 `validate()` 直接返回 `UnsupportedFeatureError`,不允许静默丢弃约束 | 静默丢弃约束会让推荐结果违反用户意图,危害比拒绝更大 |
| `ConstraintSpec(choice=custom)` 的 `customExpression` | 两库均**不**自动解析平台的不透明文本约束;MVP 阶段该约束仅作展示存档,**不传入优化后端**,`validate()` 对此类 Campaign 返回 `issues=["custom expression not enforced by optimizer, proceed with caution"]` 但允许通过(warning 级别,非阻断) | 与前端现状一致(`ConstraintDialog` 目前也只做本地状态保存,未接后端);避免"看起来生效但实际未生效"的静默错误,平台必须显式提示 |
| `ParameterSpec(type=Molecular/Substance)` | MVP 阶段 `validate()` 直接拒绝(`type` 不在 `capabilities().supportedParameterTypes` 内) | 未纳入 MVP 范围(§1.2);两库均支持但需要 rdkit 依赖与编码策略选型,留待专门设计 |
| 目标数量超过后端 Desirability/Pareto 实际验证过的规模(如 >6 个目标) | Adapter 在 `validate()` 阶段发出 warning-级 issue,不强制阻断,但 `generate_initial_design`/`recommend` 若底层抛 `ComputationError` 需原样透传并在 UI 提示"目标过多导致计算失败" | 两库均无文档化的目标数量硬上限,但工程经验上过多目标会显著降低代理模型质量 |

---

## 6. Agent 的职责边界

沿用调研报告 §4 的结论,在架构层面固化为**不可绕过的调用路径**约束:

**Agent 可以做的**:
1. **自然语言 → 结构化草稿**:把用户的自然语言描述解析为 `CampaignSpec` 草稿(parameters/objectives/constraint 的候选值),写入 `Draft` 状态,但草稿仍需人工在 UI 确认后才生效。
2. **发现缺失信息**:对比草稿与 `validate()` 的 `issues` 列表,生成人类可读的"缺什么"提示(即前端 Copilot 面板的 Missing Information)。
3. **请求用户确认**:约束选择、参数/目标的模糊描述("大概 60-85 度"→"60-85")必须生成显式确认选项,不能自动落定为最终值。
4. **调用确定性工具**:Agent 的"工具调用"只能是 `OptimizerAdapter` 接口方法或平台服务层的 CRUD API,不能有第二条写入路径。
5. **解释推荐结果**:把 `RecommendationBatch.candidates` 的 `predictedMean`/`predictedSd`/`desirability` 翻译成自然语言解读,以及调用 `explain()` 结果做特征重要性解读。

**Agent 不能做的(架构强制,而非仅靠 Prompt 约束)**:
- **不能绕过 `validate()` 直接写入 `RecommendationBatch` 或 `Observation`**——Agent 的工具集里没有"直接插入候选点"这个操作;唯一产生候选点的路径是调用 `generate_initial_design()`/`recommend()`,与人工从 UI 操作走的是同一段服务层代码。
- **不能伪造或修改 `predictedMean`/`predictedSd`/`desirability`**——这些字段只能来自 Adapter 的返回值,Agent 的解释文本是独立字段(如对话消息),绝不回写覆盖 `RecommendationCandidate` 的数值字段。
- **不能代替用户确认约束或提交观测**——`ConstraintSpec.resolvedAt`/`Observation.recordedBy` 必须是真实用户标识;Agent 生成的草稿在落库前必须经过一次"用户确认"的 DecisionLog 事件,`actor` 字段才能从 `agent:*` 变为真实用户 id。
- **不能修改已生成的 DecisionLog 条目**(§2.8 的 append-only 规则对 Agent 同样生效)。

架构落地方式:服务层对外只暴露一组 API(CRUD + `validate`/`generate_initial_design`/`recommend`/`update`/`explain`),
UI 和 Agent 是这组 API 的两个不同调用方,而不是 Agent 拥有一套"绕过校验的后门 API"。

---

## 7. 数据持久化与可复现性设计

任意一次推荐结果,必须能够回答"用什么算法、什么版本、什么随机种子、基于哪些历史数据、
谁确认了什么"。落地方式:

| 可复现性要素 | 落在哪个模型 | 说明 |
|---|---|---|
| Campaign 版本 | `CampaignSpec.version` | 每次 Parameters/Objectives/Constraint 变更 +1;`RecommendationBatch.campaignSpecVersion` 记录生成时所处的版本,可精确回溯"这批推荐是基于哪个版本的问题定义" |
| 每轮输入数据 | `ExperimentRound.observationIds` + 关联的 `Observation`(`status=Recorded`) | `recommend()` 的输入是"截至该轮次为止全部 `Recorded` 状态的 Observation",这份输入集合本身通过 `ExperimentRound` 边界可重建,不需要额外快照表 |
| 算法名称和版本 | `RecommendationBatch.backendName` / `backendVersion` | 版本号来自运行环境实际安装的包版本(如 `pip show baybe` 结果),不是手工填写,防止漂移 |
| 随机种子 | `RecommendationBatch.seed` | 若后端支持显式种子(BayBE/BoFire 底层 BoTorch 均可控制 `torch.manual_seed`),Adapter 必须记录实际使用的种子值;若某次调用未传种子由后端自动生成,Adapter 必须把自动生成的种子值回写进返回对象,不允许"种子未知" |
| 推荐结果 | `RecommendationBatch.candidates`(含预测均值/方差/期望度) | 原样存档,不因后续重新计算而覆盖历史批次 |
| 用户确认记录 | `DecisionLog`(append-only) | 约束确认、参数/目标编辑、推荐接受、观测回填等动作各自产生一条不可变日志 |

**版本化与历史数据的关系**:当 Campaign 从 `RoundClosed` 因编辑 Parameters/Objectives 退回 `Draft`
产生新版本时,已产生的 `ExperimentRound`/`RecommendationBatch`/`Observation` 不会被删除或迁移,
它们永久关联到产生时的 `campaignSpecVersion`。跨版本继续推荐时,`recommend()` 的实现需要决定
是否复用旧版本下的 Observation(MVP 简化规则:只要 `parameterValues` 涉及的参数集合在新旧版本中
的 `id` 均存在且类型未变,则该 Observation 视为有效可复用;否则标记为 `Invalid` 并在 DecisionLog
记录一条 `ObservationRecorded` 的修正说明)。

---

## 8. 环氧涂层案例 — 完整 JSON 示例

对齐当前前端 mock 数据(`frontend/src/pages/campaigns/demo-v2/mockData.ts`):
4 个连续参数(Resin Ratio / Hardener Ratio / Curing Temperature / Curing Time)、
4 个目标(Hardness↑ / Brittleness↓ / Cost↓ / Actual Curing Time↓)、
Resin+Hardener 之和固定为 100% 的约束。

```json
{
  "campaign": {
    "id": "campaign-epoxy-coating-001",
    "version": 3,
    "name": "Epoxy Coating Optimization",
    "goal": "优化环氧涂层配方,希望硬度高、不易脆、固化快、成本低。",
    "status": "RecommendationsPending",
    "round": 1,
    "batchSize": 4,
    "budgetTotal": 12,
    "budgetUsed": 0,
    "createdAt": "2026-07-20T02:10:00Z",
    "updatedAt": "2026-07-29T09:40:00Z",
    "createdBy": "user-li-wei",
    "parameters": [
      {
        "id": "param-resin-ratio",
        "name": "Resin Ratio",
        "type": "Continuous",
        "unit": "%",
        "description": "",
        "bounds": { "lower": 60, "upper": 85, "stepsize": null }
      },
      {
        "id": "param-hardener-ratio",
        "name": "Hardener Ratio",
        "type": "Continuous",
        "unit": "%",
        "description": "",
        "bounds": { "lower": 15, "upper": 40, "stepsize": null }
      },
      {
        "id": "param-curing-temperature",
        "name": "Curing Temperature",
        "type": "Continuous",
        "unit": "°C",
        "description": "",
        "bounds": { "lower": 80, "upper": 160, "stepsize": null }
      },
      {
        "id": "param-curing-time",
        "name": "Curing Time",
        "type": "Continuous",
        "unit": "min",
        "description": "",
        "bounds": { "lower": 20, "upper": 120, "stepsize": null }
      }
    ],
    "objectives": [
      { "id": "objective-hardness", "name": "Hardness", "direction": "Maximize", "unit": "", "description": "", "weight": null, "targetValue": null },
      { "id": "objective-brittleness", "name": "Brittleness", "direction": "Minimize", "unit": "", "description": "", "weight": null, "targetValue": null },
      { "id": "objective-cost", "name": "Cost", "direction": "Minimize", "unit": "", "description": "", "weight": null, "targetValue": null },
      { "id": "objective-actual-curing-time", "name": "Actual Curing Time", "direction": "Minimize", "unit": "", "description": "", "weight": null, "targetValue": null }
    ],
    "constraint": {
      "id": "constraint-resin-hardener-sum",
      "choice": "fixed-sum",
      "involvedParameterIds": ["param-resin-ratio", "param-hardener-ratio"],
      "targetSum": 100,
      "customExpression": null,
      "resolvedAt": "2026-07-22T06:05:00Z"
    }
  },

  "experimentRound": {
    "id": "round-epoxy-001-r1",
    "campaignId": "campaign-epoxy-coating-001",
    "roundNumber": 1,
    "recommendationBatchId": "batch-epoxy-001-r1",
    "observationIds": [],
    "openedAt": "2026-07-29T09:40:05Z",
    "closedAt": null,
    "status": "Open"
  },

  "recommendationBatch": {
    "id": "batch-epoxy-001-r1",
    "campaignId": "campaign-epoxy-coating-001",
    "roundNumber": 1,
    "generatedAt": "2026-07-29T09:40:05Z",
    "backendName": "baybe",
    "backendVersion": "0.11.3",
    "seed": 42,
    "campaignSpecVersion": 3,
    "status": "Proposed",
    "candidates": [
      {
        "id": "cand-r1-1",
        "parameterValues": {
          "param-resin-ratio": 72.5,
          "param-hardener-ratio": 27.5,
          "param-curing-temperature": 120,
          "param-curing-time": 60
        },
        "predictedMean": null,
        "predictedSd": null,
        "desirability": null
      },
      {
        "id": "cand-r1-2",
        "parameterValues": {
          "param-resin-ratio": 65,
          "param-hardener-ratio": 35,
          "param-curing-temperature": 95,
          "param-curing-time": 90
        },
        "predictedMean": null,
        "predictedSd": null,
        "desirability": null
      }
    ]
  },

  "observationExample": {
    "id": "obs-r1-1",
    "campaignId": "campaign-epoxy-coating-001",
    "experimentRoundId": "round-epoxy-001-r1",
    "recommendationCandidateId": "cand-r1-1",
    "parameterValues": {
      "param-resin-ratio": 72.5,
      "param-hardener-ratio": 27.5,
      "param-curing-temperature": 120,
      "param-curing-time": 60
    },
    "objectiveValues": {
      "objective-hardness": 78.4,
      "objective-brittleness": 12.1,
      "objective-cost": 34.2,
      "objective-actual-curing-time": 58.0
    },
    "status": "Recorded",
    "recordedAt": "2026-07-30T03:20:00Z",
    "recordedBy": "user-li-wei",
    "notes": ""
  },

  "decisionLogExamples": [
    {
      "id": "log-0001",
      "campaignId": "campaign-epoxy-coating-001",
      "timestamp": "2026-07-22T06:05:00Z",
      "actor": "user-li-wei",
      "action": "ConstraintConfirmed",
      "campaignSpecVersion": 2,
      "payload": { "choice": "fixed-sum", "targetSum": 100, "involvedParameterIds": ["param-resin-ratio", "param-hardener-ratio"] },
      "relatedEntityId": "constraint-resin-hardener-sum"
    },
    {
      "id": "log-0002",
      "campaignId": "campaign-epoxy-coating-001",
      "timestamp": "2026-07-22T06:05:01Z",
      "actor": "user-li-wei",
      "action": "DesignSpaceValidated",
      "campaignSpecVersion": 3,
      "payload": { "ok": true, "issues": [] },
      "relatedEntityId": null
    },
    {
      "id": "log-0003",
      "campaignId": "campaign-epoxy-coating-001",
      "timestamp": "2026-07-29T09:40:05Z",
      "actor": "user-li-wei",
      "action": "InitialDesignGenerated",
      "campaignSpecVersion": 3,
      "payload": { "batchSize": 4, "backendName": "baybe", "backendVersion": "0.11.3", "seed": 42 },
      "relatedEntityId": "batch-epoxy-001-r1"
    }
  ]
}
```

---

## 9. 开放问题(需产品负责人决定)

1. **单约束 vs 多约束**:当前设计 MVP 阶段每个 Campaign 只支持一条 `ConstraintSpec`(对齐前端现状)。
   若近期就有"多条独立约束"的真实需求(例如同时存在配比约束 + 某工艺参数上限约束),
   需要提前把 `constraint` 字段改为 `constraints: ConstraintSpec[]`,这是一个破坏性的模型变更,
   越早决定越省迁移成本。
2. **`custom` 约束表达式是否需要正式 DSL**:目前定义为"不透明文本,仅展示存档,不传入优化后端"。
   如果产品要求自定义约束真正参与优化计算,需要设计一套受限表达式语法(例如仅支持
   `a*x1 + b*x2 <= c` 这类线性表达式)并配套解析器/求值器,这是一块独立的工作量,需要单独排期。
3. **目标权重(Objective Weighting)何时启用**:`ObjectiveSpec.weight` 字段已预留,但 MVP 明确不读取。
   需要产品确认:一旦启用,权重语义是"Desirability 组合权重"还是"仅用于排序展示",这会决定
   落到 BayBE `DesirabilityObjective` 时的具体映射方式。
4. **BoFire 接入时间线**:当前所有"需要 BoFire 才能支持"的特性(强约束、DoE 最优设计)在 MVP 阶段
   直接拒绝。需要产品明确这类需求的优先级和时间点,以便规划双后端抽象层(调研报告称为路线 B)
   何时启动。
5. **部分批次回填后能否手动结束轮次**:当前状态机要求"全部候选回填完成"才能从
   `RecommendationsPending` 进入 `RoundClosed`。如果实验中途发现某个候选不可执行(设备故障/
   原料不足),是否允许用户主动把未完成的候选标记为 `Invalid` 以提前关闭轮次?需要产品给出
   业务规则。
6. **`Completed` 之后的 `reopen()` 权限**:谁有权限重新打开一个已完成的 Campaign?是否需要审批?
   MVP 阶段暂定为"任何有该 Campaign 访问权的用户均可操作",但这可能不符合实际治理要求。
7. **跨版本 Observation 复用规则的最终确认**:§7 末尾给出的"参数 id 存在且类型未变则复用,否则标记
   Invalid"是工程上的简化规则,需要产品/算法负责人确认这不会引入误导性的推荐结果(尤其是当
   参数的取值范围在新版本中被收窄或放宽时,旧 Observation 是否仍然"有效"存在争议空间)。
8. **化学/分子式模态的实际优先级**:本次会话中曾提及未来可能加入分子式相关模态
   (`MolecularInput`/`SubstanceParameter`),但该请求已被用户明确标注为"粘贴错误"、不属于本次
   范围。是否是真实的中期需求、优先级如何,需要产品负责人后续单独确认后再纳入路线图。
9. **多后端场景下的自动选型规则**:v1+ 引入 BoFire 后,当一个 Campaign 的特性同时被两个后端部分
   支持(例如多目标但同时有非线性约束),应由系统自动选择后端,还是由用户显式指定?自动选型
   的优先级规则(能力覆盖优先 / 性能优先 / 用户历史偏好优先)需要产品给出裁决依据。
