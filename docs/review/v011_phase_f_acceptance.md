# Orca Controller v0.11 Phase B–F 验收报告

日期：2026-10-13　分支：`refactor/controller-v011-production-quality`
基线：`docs/review/v011_phase_a_baseline.md`（commit 3d55662）

本报告只记录相对 Phase A 基线的状态变化与证据；未列出的条目维持基线结论。

## 1. 需求追踪矩阵状态更新

| ID | 基线 | 现状态 | 证据 |
|---|---|---|---|
| CTRL-MODE-006 | PARTIAL | IMPLEMENTED | AGC/AVC 并行闭环：单任务 P/Q 联合规划与联合分配（tests/unit/test_reactive_planning_cycle.py、tests/system/test_pq_dispatch_chain.py） |
| REF-003 | PARTIAL | PARTIAL | `event_id` 已投入使用并参与去重；`quality`/`authority` 仍以快照门禁与 authorized_sources 承担，无独立字段（设计允许等效语义，保持 PARTIAL 待设计确认） |
| REF-008 | MISSING | IMPLEMENTED | 同一非空 event_id 多需求在 `CoordinationContext` 构造即拒绝（src/core/domain/coordination.py；tests/unit/test_coordination_events.py） |
| CTL-002 | MISSING | IMPLEMENTED | `AgcMode.LIMIT` + `ActiveLimitLaw`：上限取 min(可用, 限值)、下限取 max(可用, 限值)，候选为 ABSOLUTE 语义，不追踪限值（src/core/application/control_laws.py；tests/unit/test_active_limit_mode.py） |
| CTL-003 | MISSING | IMPLEMENTED | AVC 三模式：`ReactivePowerController`（定无功/定功率因数复用无功管线）+ `VoltageReactiveLaw`（定电压 PI，饱和方向积分冻结）；pf→Q 映射在 \|p\|<1e-3 MW 判定不适定（src/core/application/reactive_power_controller.py；tests/unit/test_reactive_power_controller.py） |

## 2. 架构问题（A 类）处置

| ID | 处置 | 说明 |
|---|---|---|
| A1 | 已解决 | 删除 `active_power_planner.py`/`power_allocator.py`/`joint_plant_allocator.py`/`joint_pq_validator.py` 及专属测试（commit cee801c） |
| A2 | 未解决（记录决策） | 旧 noop 链仍是非规划任务的活动路径，删除需重写 Runtime 非规划分支；留待独立任务，避免在功能重构中混入行为变更 |
| A3 | 已解决 | 删除 `core/contracts.py`、`algorithms/passthrough.py`、`control/__main__.py` |
| A4 | 已解决 | 无功候选进入联合分配；`PlanningSnapshot.reactive` + `reactive_controllers`（commit 32e0800） |
| A5 | 已解决 | `ControlRuntime.output_owners()/planning_cycle_port/planning_task_ids`；application 不再访问私有成员 |
| A6 | 部分解决 | 任务状态收敛为 `TaskState` 枚举（STOPPED/RUNNING）；FAULT 态未引入——异常已记录于 `reasons` 并复位控制状态，引入第三态需设计确认语义 |
| A7 | 已解决 | mypy 38→0（含 `OrcaConfig.wind_hub` 默认工厂真实缺陷修复）、ruff 23→0、CI 触发分支更新 |
| A8 | 未解决 | `ControlAuthority` 状态机与审计，P2，留待后续 |
| A9 | 未解决 | `InternalReferenceService` 接入 Runtime 需频率测点与 PFR/IR 事件状态机（触发/持续/退出/恢复，设计 §9）；为避免无事件逻辑的简化实现违反设计，留待独立功能任务 |
| A10 | 未解决 | `combine_power_references` 仍仅测试使用；语义冲突风险记录，未删除（被参考生成测试依赖） |

## 3. 测试问题（T 类）处置

| ID | 处置 | 说明 |
|---|---|---|
| T1 | 已解决 | tests/{unit,integration,system,reliability,support} 分层完成 |
| T2 | 已解决 | tests/system/test_pq_dispatch_chain.py：发布→真实 Redis→快照→规划→联合分配→安全下发→Mock Commander 全链路断言 |
| T3 | 已解决 | tests/reliability：UNKNOWN 不重传、权限撤销/计划过期零写入、200 周期浸泡冒烟 |
| T4 | 已解决 | pytest-cov 接入，单元覆盖率 80%（src，排除 pb2） |
| T5 | 已解决 | CI 三段：静态检查 / 单元+覆盖率 / 集成+系统+可靠性（托管 Redis）；分支 main + 本分支 + PR |
| T6 | 维持 | Wind Hub 设备协议端到端测试 NOT_EXECUTED（Wind Hub 不在本仓库），不虚构 |
| T7 | 未解决 | 控制性能仿真需设备动态模型，独立任务 |

## 4. 安全边界复核

- 未启用对真实现场设备的自动写入：`write_enabled` 默认 False，dispatcher 安装要求全部任务停止且写入关闭；系统/可靠性测试指令只到达 Mock Commander。
- UNKNOWN 不自动重传：响应丢失故障注入确认服务端恰好一次写入，dispatcher 无重试逻辑（tests/reliability/test_dispatch_failure_injection.py）。
- 断线后不重发上一次指令：`CommanderClient.write_once` 单次提交、`wait_for_ready=False`，不重连补发。
- 测试未为便利移除安全联锁；权限撤销与计划过期用例断言零写入。

## 5. 质量门

- 321 测试通过（288 unit + 28 integration + 1 system + 4 reliability），真实 Redis（scripts/start_redis.sh 托管容器），无 fakeredis 用于集成层。
- ruff 0 错误；mypy 0 错误（65 源文件）。
- 单元覆盖率 80%（src，排除 pb2 生成代码）。

## 6. 遗留工作（建议后续任务）

1. A9：PFR/IR 内部参考接入 Runtime（需频率测点配置与事件状态机，设计 §9）。
2. A2：旧 noop 执行链归并（需重写非规划任务路径的行为等价性验证）。
3. A8：ControlAuthority 状态机与审计字段。
4. T7：设备动态模型与控制性能仿真。
5. REF-003：`quality`/`authority` 独立字段语义待设计确认。
6. A6：FAULT 态语义待设计确认。
7. pb2 重新生成（6.33 gencode）后移除 `rpc/__init__.py` 兼容加载序列。
