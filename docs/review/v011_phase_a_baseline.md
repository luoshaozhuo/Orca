# Orca Controller v0.11 Phase A 基线审查报告

日期：2026-10-13
分支：`refactor/controller-v011-production-quality`
基线：`origin/main` = `d6a82f9`（任务书所给 `d6a82f9` 经 fetch 确认为最新；设计文档 §12.1 所述 `237936b` 已过时，文档基线标注问题记入文档偏差）
规范文件核查：`符号体系.md`、`Python代码规范.md`、`软件系统建模图形与表达规范.md`、`报告模板.md` **在仓库中不存在**（已全库检索），按要求记录缺失，不虚构其内容；实际遵守 `ai_shared/rules/coding.md`、`testing.md`、`comments.md`。

## 1. 基线验证结果

| 检查 | 结果 |
|---|---|
| 单元测试（非 integration） | 249 PASS |
| 集成测试（真实 Redis 容器 `orca-redis` + 模拟 gRPC Server） | 28 PASS |
| Ruff（src+tests，含生成代码目录） | 23 错误（可自动修复，主要是生成代码/导入排序） |
| mypy（src 全量） | 38 错误，13 个文件 |
| CI 工作流 | 触发分支仍为旧开发分支 `feat/controller-v0-11-coordination`，需重设计 |

## 2. 当前代码结构（有效生产代码）

- `core/domain`：`config`（Pydantic 严格校验）、`control_value`（物理量/阶段契约）、`reference`（参考仲裁）、`coordination`（协调契约）、`control_authority`（投退/闭锁/手动互锁）、`control_law`、`power_allocation`、`device_capability`（P-Q 圆/SOC）、`device_ramp`、`capability_snapshot`、`safe_dispatch`、`decision`（旧链）。
- `core/application`：`reference_manager`、`reference_generators`（PFR/IR）、`internal_reference`、`control_pipeline`(+factory/signal/trajectory/laws)、`control_coordinator`、`active_power_controller`、`active_power_reference`、`joint_power_allocator`、`read_only_planning_cycle`、`safe_plan_dispatcher`、`dispatch_mapping`、`validation`；**仅测试使用**：`active_power_planner`、`power_allocator`、`joint_plant_allocator`、`joint_pq_validator`。
- `control`：`application`（装配/IPC）、`runtime`（周期调度/任务生命周期）、`cycle`+`decision`（旧 noop 执行链）、`__main__`（纯演示）。
- `infrastructure`：`redis/source`、`redis/planning_snapshot`、`wind_hub/commander|collector|watchdog`、`config/yaml`。
- `algorithms`：`noop`（旧链策略）、`passthrough`（仅演示）；`core/contracts.py`（仅演示与 `__main__`）。
- `commissioning`/`evaluation`/`optimization`：空包。

## 3. 需求追踪矩阵（状态：IMPLEMENTED / PARTIAL / MISSING / INCORRECT / NEEDS_CONFIRMATION）

### 第 5 章 控制任务与运行模式

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| CTRL-MODE-001 | 任务生命周期 STOPPED/READY/RUNNING/FAULT | PARTIAL | `ControlRuntime.states` 为裸字符串，仅 STOPPED/RUNNING，无 READY/FAULT，无类型化状态机 |
| CTRL-MODE-002 | 功能投退 IN_SERVICE/OUT_OF_SERVICE | IMPLEMENTED | `ControlAuthority`；但缺 `CONFIGURED/REQUESTED` 中间态（§7.4） |
| CTRL-MODE-003 | 闭锁 UNBLOCKED/BLOCKED，按 plant/function/resource/point 分级 | PARTIAL | `blocks` 按 function 记录原因；无 plant/resource/point 级闭锁范围 |
| CTRL-MODE-004 | 响应阶段 IDLE/RESPONDING/RECOVERING（PFR/IR 投入≠输出） | MISSING | 无响应阶段状态机；投入即视为可贡献 |
| CTRL-MODE-005 | 控制权 AUTO/MANUAL/LOCAL/NONE 单写权 | PARTIAL | `AuthorityMode` 仅 AUTO/MANUAL；设备点所有权 `_authority` 按 task 记录，无 LOCAL/NONE |
| CTRL-MODE-006 | 功能组合并行（AGC+AVC 不互斥，PFR+IR 按协调规则） | PARTIAL | 协调层正确；但无功控制链整体缺失，AGC/AVC 并行无法闭环验证 |
| CTRL-MODE-007 | 手动全场接管互锁事务（§7.4.1/7.4.2 顺序） | PARTIAL | `enter_manual` 校验投出与写权释放；无"冻结自动投入请求"与逐步拒绝原因报告 |
| CTRL-MODE-008 | 单设备人工旁路（设备×控制量，实测仍计入平衡） | PARTIAL | `p_auto/q_auto` 授权标志参与分配；无运行期旁路事务接口与重分配触发 |
| CTRL-MODE-009 | FREE 模式 | MISSING | 无 FREE 语义建模 |
| CTRL-MODE-010 | 投退事务审计（操作者、时间、前后状态、原因、代际） | MISSING | 无审计记录结构 |
| CTRL-MODE-011 | 状态迁移带控制权代际版本 | MISSING | `planning_generation` 为静态配置，无运行期代际递增 |

### 第 6 章 总体流程

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| CTRL-FLOW-001 | 三层主链 Reference→Control→Allocation | IMPLEMENTED | `active_power_reference`→`ActivePowerController`→`JointPowerAllocator`（仅有功链） |
| CTRL-FLOW-002 | P/Q 双管线并行、共享能力快照 | PARTIAL | `JointPowerAllocator` 支持双目标；`ReadOnlyPlanningCycle` 恒传 `q_target=None`，无功管线未接线 |
| CTRL-FLOW-003 | 周期边界确定性执行次序（§6.3 1-9） | IMPLEMENTED | `runtime._run_task`+`ReadOnlyPlanningCycle`+`SafePlanDispatcher`；整批预校验、逐条重验、UNKNOWN 不重传均有证据 |
| CTRL-FLOW-004 | 跳过过期周期、不积压 | IMPLEMENTED | `deadline = loop.time() + interval` |
| CTRL-FLOW-005 | 核心数据对象契约（表 6-4） | PARTIAL | 多数存在；`ControlOutcome` 无 UUID/提交时间/设备反馈结构（`DispatchReceipt` 有 command_ids/confirmed） |
| CTRL-FLOW-006 | 故障分支语义（表 6-5） | PARTIAL | 参考失效/陈旧/UNKNOWN 路径有；模式切换残留、隐式回退等需系统测试证实 |
| CTRL-FLOW-007 | 算法执行管线可配置（表 6-1） | PARTIAL | signal/trajectory/law 可配；状态估计（RoCoF）、MPC 未实现（设计列为候选，非 [确定]） |

### 第 7 章 Reference

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| REF-001 | 五类来源 GRID/FFR/THIRD_PARTY/INTERNAL/MANUAL | IMPLEMENTED | `ReferenceSource` |
| REF-002 | 八种参考用途契约（表 7-2，purpose↔quantity↔kind） | IMPLEMENTED | `_PURPOSE_CONTRACT` |
| REF-003 | 标准化字段（表 7-3：timestamp/valid_until/quality/authority/sequence/generation/event_id） | PARTIAL | `quality`/`authority` 无独立字段（以 authorized_sources 与快照门禁承担）；`event_id` 有字段无去重逻辑 |
| REF-004 | 仲裁 FIXED/PRIMARY_BACKUP/PRIORITY/EXPLICIT/COMPOSE | PARTIAL | 仅 FIXED/PRIMARY_BACKUP；其余三种 MISSING |
| REF-005 | 主备切换需授权确认 | IMPLEMENTED | `select_source(authorized=)`+`confirmed_source` |
| REF-006 | 失效策略 BLOCK/ZERO_AND_BLOCK、禁止隐式回退与旧值维持 | PARTIAL | 参考过期即不可用（等效 BLOCK）；无 `invalid_action` 配置与 ZERO_AND_BLOCK 语义 |
| REF-007 | 逆序/旧代际拒绝 | IMPLEMENTED | `publish_many` generation/sequence/timestamp 排序 |
| REF-008 | 同事件去重（PFR/IR event_id 不重复贡献） | MISSING | 无事件去重 |
| REF-009 | 内部 PFR 生成（式 7-1/7-2） | IMPLEMENTED | `generate_pfr_reference`（含死区、上下限、符号） |
| REF-010 | 内部 IR 生成（式 7-3） | PARTIAL | 生成器存在；RoCoF 估计（质量校验+滤波）缺失，且服务未接入 Runtime 周期 |
| REF-011 | 内部 VR 生成（式 7-4） | MISSING | 无 |
| REF-012 | 内部/外部同事件只允许一个有效贡献 | MISSING | 无内外双算防护 |
| REF-013 | 参考切换对有状态算法的影响（模式代际变化重置状态） | PARTIAL | `required_generation` 校验；来源切换不自动重置 pipeline 状态 |
| REF-014 | 组合配置 `composition`/`interlocks` 静态校验（§7.3 YAML 契约） | MISSING | 无 reference channels YAML 契约（现由 controllers.references 承担 FIXED/PRIMARY_BACKUP） |
| REF-015 | 诊断信息（期望/实际来源、投入申请、拒绝原因、代际、事件编号） | MISSING | status 输出无参考诊断 |

### 第 8 章 Control

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| CTL-001 | AGC 定值跟踪（direct/P/PI+前馈） | IMPLEMENTED | `control_laws.py`+测试 |
| CTL-002 | AGC 限值模式：边界内释放出力，不追踪上限 | MISSING | 无 ACTIVE_LIMIT 控制语义（`ReferencePurpose.ACTIVE_LIMIT` 仅契约） |
| CTL-003 | AVC 定电压/定无功/定功率因数三模式 | MISSING | 无电压/无功控制律；功率因数→无功映射缺失 |
| CTL-004 | PI 抗积分饱和（回算/冻结） | IMPLEMENTED | `PIControlLaw`（achievable 回算+饱和方向冻结） |
| CTL-005 | 输出限幅与变化率约束（式 8-3） | IMPLEMENTED | `_result` 限幅、`RampTrajectory` 非对称限速 |
| CTL-006 | 低通滤波（式 8-2），无效输入不伪装有效 | IMPLEMENTED | `LowPassSignal`；快照质量门禁在前 |
| CTL-007 | RoCoF 估计（带时标/时间窗/有效标志） | MISSING | 无状态估计器 |
| CTL-008 | 算法配置校验 | IMPLEMENTED | `SignalStageConfig/TrajectoryStageConfig/ControlLawStageConfig` 严格校验 |
| CTL-009 | 运行期算法替换仅周期边界、无扰切换、失败回滚/闭锁 | PARTIAL | `replace_task_pipeline` 要求 STOPPED（比设计更保守，可接受）；无运行期热切换 |
| CTL-010 | PFR/IR 不套用 AGC 滤波/斜坡 | IMPLEMENTED | 管线按 controller 独立配置 |
| CTL-011 | 算法异常安全行为（状态复位） | IMPLEMENTED | pipeline/controller 异常即 reset |

### 第 9 章 Allocation

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| ALC-001 | 风/光/储/SVG 能力模型 | IMPLEMENTED | `DeviceTechnology`+`DevicePQCapability` |
| ALC-002 | 动态可调上下限（非静态铭牌） | PARTIAL | 边界来自配置静态值+实测 current；无设备动态可用裕度测点接入 |
| ALC-003 | P/Q 联合约束（式 9-3/9-4，厂家曲线优先） | IMPLEMENTED（简化圆）/PARTIAL | `JointPowerAllocator` Dykstra 投影；厂家 P-Q 曲线接口 MISSING |
| ALC-004 | SOC 充放电方向与效率 | IMPLEMENTED | `StorageEnergyLimits` |
| ALC-005 | 设备爬坡率 | IMPLEMENTED | `DeviceRampLimits` |
| ALC-006 | 加权裕度迭代分配（式 9-5～9-9） | IMPLEMENTED | `JointPowerAllocator`（注：`power_allocator.py` 为重复实现，仅测试使用） |
| ALC-007 | 分配残差、饱和原因上送 | PARTIAL | `p_residual/q_residual/reason`；不可行时上抛，无饱和设备清单 |
| ALC-008 | 人工旁路设备实测仍计入平衡 | INCORRECT 风险 | `JointPowerAllocator` 以设备求和为平衡口径；旁路设备固定 current 计入，正确；但见 ALC-010 |
| ALC-009 | 独立 P/Q 控制权限 | IMPLEMENTED | `p_authorized/q_authorized` |
| ALC-010 | 并网点目标≠设备功率简单求和（P_loss/P_aux，式 3-2/9-1/9-2） | MISSING | 无损耗/站用电模型；当前以设备 sum 近似 PCC，**不得宣称精确并网点平衡** |
| ALC-011 | 离散无功设备（投切/档位、动作间隔、次数、闭锁） | MISSING | 无 |
| ALC-012 | 设备离线/不可用 | PARTIAL | 质量/陈旧门禁；无显式 UNAVAILABLE 可控状态（表 9-1 六态未建模） |
| ALC-013 | 设备能力变化后重新分配 | IMPLEMENTED | 每周期新快照重算 |

### 第 10 章 Coordination

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| COO-001 | default/sum/priority/exclusive/directional 五算法 | IMPLEMENTED | `ControlCoordinator`+测试 |
| COO-002 | 权重非负有限、未列功能默认 1、不归一化 | IMPLEMENTED | 配置校验 |
| COO-003 | 同级并列默认拒绝（reject） | IMPLEMENTED | `_priority`/`_exclusive` |
| COO-004 | rules 精确集合匹配、重复拒绝 | IMPLEMENTED | 配置校验+`_resolve_policy` |
| COO-005 | 参数按算法严格校验（未知字段拒绝） | IMPLEMENTED | `validate_for` |
| COO-006 | AGC 绝对目标基准转换（式 10-1，P_AGC,entry 锁定） | MISSING | AGC 需求未以"PFR 事件进入时锁定的 AGC 参考"为基准；当前 AGC 经控制律输出 candidate 直接作 base |
| COO-007 | 同向/反向判定（乘积符号，零非同向） | IMPLEMENTED | `_default`/`_directional` |
| COO-008 | 设备本地自主贡献识别与去重 | MISSING | 无 |
| COO-009 | 抑制需求继续跟踪、不回放历史、恢复重验 | PARTIAL | contributions 记录 requested/accepted/reason；恢复路径无专项状态机 |
| COO-010 | 规则歧义/缺基准/单位不一致拒绝，不退化默认求和 | IMPLEMENTED | 契约校验+异常上抛 |
| COO-011 | 冲突审计字段完整（算法、权重、命中规则、基准、代际） | PARTIAL | `CoordinationResult` 含 algorithm/matched_rule/contributions；缺基准与代际记录 |
| COO-012 | P/Q 分别写点但联合能力与时序检查 | IMPLEMENTED | 联合分配+整批预校验 |

### Runtime / Safe Dispatch（第 6、10 章安全约束）

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| RUN-001 | 周期调度、单调时间、超时跳过 | IMPLEMENTED | `_run_task` |
| RUN-002 | 输入快照冻结与代际一致性 | IMPLEMENTED | `DeviceCapabilitySnapshot` 混代拒绝 |
| RUN-003 | stop_task 与执行中写入竞态：UNKNOWN 锁存 | IMPLEMENTED | `_inflight_dispatch`+取消路径闭锁 |
| RUN-004 | 任务停止后继续下发阻断 | IMPLEMENTED | `permitted` 逐条重验 |
| RUN-005 | Redis 失联不使用过期数据发新指令 | IMPLEMENTED | `ready` 门禁+快照时效；集成测试覆盖 |
| RUN-006 | Commander 失联/UNKNOWN 不重传 | IMPLEMENTED | `SafePlanDispatcher`+`CommanderClient.write_once` |
| RUN-007 | 第 N 条失败：停止后续、保留已执行审计、不虚报整体成功 | IMPLEMENTED | `DispatchReceipt(command_ids, confirmed)` |
| RUN-008 | 写入后控制权变化逐条重验 | IMPLEMENTED | `_output_owner` 每条复核 |
| RUN-009 | 代际变化拒绝旧计划 | IMPLEMENTED | generation 校验 |
| RUN-010 | 看门狗配置变化重新验证 | IMPLEMENTED | revision 比对+映射重验 |
| RUN-011 | 异常不传播为静默、资源释放 | IMPLEMENTED | reasons 记录+finally 清理 |
| RUN-012 | 闭锁清除需人工授权、不自动补发 | IMPLEMENTED | `clear_dispatch_block(authorized=)` |

### 第 11～12 章 架构

| ID | 需求 | 状态 | 证据/缺口 |
|---|---|---|---|
| ARC-001 | Domain 纯领域、Infrastructure 隔离 I/O | IMPLEMENTED | 结构符合 |
| ARC-002 | 算法插件不接触 Redis/Commander | IMPLEMENTED | pipeline/allocator 纯计算 |
| ARC-003 | 算法注册/版本/参数 schema | PARTIAL | 按名称装配+严格参数校验；无注册表与版本元数据（设计为建议性目录结构） |
| ARC-004 | 消除重复/死代码 | MISSING | 见第 4 节清单 |

## 4. 架构问题清单（按严重程度）

| # | 级别 | 问题 |
|---|---|---|
| A1 | P1 | 死代码：`active_power_planner.py`、`power_allocator.py`、`joint_plant_allocator.py`、`joint_pq_validator.py` 仅测试使用，与 `joint_power_allocator`/`read_only_planning_cycle` 职责重复 |
| A2 | P1 | 旧 noop 执行链（`control/cycle.py`、`control/decision.py`、`core/domain/decision.py`、`port/strategy.py`、`algorithms/noop.py`）与新规划链并存，同一 Runtime 两条控制路径 |
| A3 | P2 | 演示代码混入生产包：`core/contracts.py`、`algorithms/passthrough.py`、`control/__main__.py` |
| A4 | P1 | 无功控制链缺失：`ReadOnlyPlanningCycle` 恒 `q_target=None`，AVC 三模式整体 MISSING |
| A5 | P2 | `ControlApplication` 访问 `runtime._authority`、`_can_write_task`、`_planning_cycle`、`_planning_tasks` 私有成员（mypy 错误来源之一） |
| A6 | P2 | Runtime 任务状态为裸字符串，无 FAULT 态，异常后无状态区分 |
| A7 | P2 | mypy 38 错误、Ruff 23 错误；CI 触发分支过期 |
| A8 | P2 | `ControlAuthority` 缺 REQUESTED/BLOCKED 状态机与响应阶段；无审计 |
| A9 | P3 | `InternalReferenceService` 未接入 Runtime 周期，PFR/IR 内部参考只能由测试驱动 |
| A10 | P3 | `combine_power_references`（`control_value.py`）仅测试使用，且与设计"候选求和式不得作为默认执行规则"存在语义冲突风险 |

## 5. 测试问题清单

| # | 问题 |
|---|---|
| T1 | 目录未按 `testing.md` 层级组织（unit/integration/system/reliability）；全部平铺于 `tests/` 根 |
| T2 | 无 system 测试（完整链路 模拟数据→Redis→Runtime→Reference→Control→Coordination→Allocation→SafeDispatch→Commander 模拟服务端 未端到端断言） |
| T3 | 无 reliability/soak/并发竞态测试目录；部分竞态由单元测试覆盖（dispatch_runtime_interlock） |
| T4 | 无覆盖率统计与门禁 |
| T5 | CI 触发分支为旧开发分支；无分层 Fast/Integration/System/Reliability |
| T6 | Wind Hub 不在本仓库，第二层设备协议（Modbus/ADS/IEC104）端到端测试 **NOT_EXECUTED**，需记录限制；不得虚构 |
| T7 | 控制性能仿真（设备动态模型、阶跃响应指标）缺失 |

## 6. 风险排序与实施计划

P0（安全相关，先行）：无 —— 现有写入链（SafePlanDispatcher）安全语义完整且默认关闭写入，未发现未授权写入或 UNKNOWN 重传路径。

P1：A4（AVC 缺失）、COO-006（AGC 基准转换）、REF-008/012（事件去重/内外双算）、CTL-002（限值模式）、A1/A2/A3（死代码与双链）、T5（CI）。

P2：CTRL-MODE-001/003/004/010/011（状态机与审计）、REF-004/006（仲裁与失效策略）、ALC-010（损耗模型声明）、T1/T2/T3/T4（测试体系）。

P3：REF-011（VR）、ALC-011（离散设备）、REF-015（诊断）、T7（性能仿真）、A9。

实施顺序：Phase B 先补 P1 功能（AVC 链、限值模式、事件去重、AGC 基准转换）并同步测试；Phase C 清理 A1/A2/A3 与私有访问；Phase D 重建测试体系与 CI；Phase E/F 收尾。

## 7. 设计文档内部偏差记录（不擅自修改设计语义）

1. §12.1 所述 main 基线 SHA（`237936b`）已过时，实际 `d6a82f9`；属于文档基线标注滞后，不影响技术语义。
2. §7.4.1 "这里按用户提出的'AGV'理解为 `AVC`" 为批注性文字遗留，建议后续版本清理（仅记录，不修改）。
3. ALC-010：在损耗模型缺失期间，软件只能宣称"设备交流侧功率求和口径"的分配，不能宣称并网点精确平衡；已在矩阵中标注，相关 README/注释不得夸大。
