# Orca Controller v0.11 最终整改与生产软件质量验收报告

日期：2026-10-14　分支：`refactor/controller-v011-production-quality`
基线：`docs/review/v011_phase_a_baseline.md`（commit 3d55662）、`docs/review/v011_phase_f_acceptance.md`（commit e4d3246）

本报告为 v0.11 整改最终验收。结论先行：A2/A8/A9/T7 四项遗留全部闭环；质量门全绿（396 测试、覆盖率 86% 分支、安全关键模块分支覆盖 ≥95%、ruff/mypy 对 src 与 tests 零错误）；Wind Hub 设备协议（Modbus/ADS/IEC104）测试与中长期浸泡按约定标注 NOT_EXECUTED，未虚构。

## A. Git 审计

- 全部工作在既有分支 `refactor/controller-v011-production-quality` 上完成，未新建分支、未直接改动 main、未 force push、未执行合并。
- 本轮整改提交（自 Phase F 报告之后）：
  - `1c433f6` A2 归并旧 NoOp 控制执行链
  - `dd9eed0` A9 内部 PFR/IR 参考接入与事件状态机
  - `4d1695f` A8 ControlAuthority 五态状态机与投退审计
  - `021d0be` T7 合成设备模型闭环仿真入口
  - `108715d` 协议级故障恢复与批量执行语义补全
  - `d24d551` 规划-下发运行场景矩阵
  - `5ed6cb1` 链路级短时浸泡与资源监控
  - `f8682f2` 安全关键链路覆盖率补齐与 IR 系统测试确定性化
- 所有提交以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾。

## B. 设计项闭环（A2/A8/A9/T7）

定义以 Phase F 报告 §2/§3 为准，未从编号臆测含义。

| ID | 定义（Phase F） | 本轮处置 | 证据 |
|---|---|---|---|
| A2 | 旧 noop 执行链归并 | 已解决 | `src/` 中无 noop 残留（全仓检索为零）；非规划任务路径并入只读校验链，删除未意外放开任何设备写入（write_enabled 默认 False 不变，见 §C/§D） |
| A8 | ControlAuthority 状态机与审计字段 | 已解决 | 五态状态机（OUT_OF_SERVICE/REQUESTED/IN_SERVICE/BLOCKED/MANUAL）+ 投退审计记录（动作、操作者、理由）；越权投退、带阻断投运、无理由阻断均被拒绝（tests/unit/test_control_authority.py） |
| A9 | PFR/IR 内部参考接入 Runtime（频率测点 + 事件状态机，设计 §9） | 已解决 | `frequency_response.py`：RocofEstimator 滑窗最小二乘（缺窗/时序异常即无效）+ PFR/IR 事件状态机（IDLE→RESPONDING→RECOVERING→IDLE，事件期间 event_id 与基线冻结，输入失效复位并停发内部参考）；经 InternalReferenceService → ReferenceManager → 快照 → 规划 → 安全下发全链路（tests/unit/test_frequency_response*.py 20 项 + tests/system/test_frequency_response_chain.py 3 项，覆盖死区、触发、持续、事件期间基准变化、退出、PFR+IR 叠加与独立退出） |
| T7 | 设备动态模型与控制性能仿真 | 已解决（入口级） | 合成设备模型闭环仿真入口（commit 021d0be，tests/system）；定位为仿真入口而非完整并网性能评估，见 §E 风险 |

## C. 架构复核

- 单一控制执行链：DataPublisher → Redis → RedisPlanningSnapshotSource → ReadOnlyPlanningCycle → ControlCoordinator → JointPowerAllocator → SafePlanDispatcher → CommanderClient。不存在第二条并行维护的控制链。
- 安全边界维持：write_enabled 默认 False；dispatcher 安装要求授权且绑定唯一注册物理输出；不可行分配在规划周期即失败关闭（`planning allocation blocked`），不进入下发；UNKNOWN 闩锁后不重传；权限撤销/代际不匹配/快照过期/空计划均零写入。
- 观测性与可追溯：task→cycle→generation→reference→control→coordination→allocation→command_id 链路中，代际（generation）贯穿快照、分配与下发门禁；command_id 单次生成、单次提交、不重发；事件生命周期以 event_id 标识并参与协调去重；Runtime 周期异常隔离为 `reasons` 记录并复位控制状态，下发失败与 in-flight 取消均触发 UNKNOWN 闩锁并记录 warning 日志。任务停止（stop_task）、运行时重启（Runtime stop/start）与进程重启三者下，UNKNOWN 闩锁均为fail-closed：前两者保留审计与阻断状态，进程重启后 write_enabled 回到默认关闭，需重新走看门狗验证流程，不会静默恢复写入。
- 遗留记录（非本轮阻断项）：REF-003 quality/authority 独立字段语义待设计确认；A6 FAULT 态语义待设计确认；A10 `combine_power_references` 仅测试使用，语义冲突风险已记录。

## D. 测试验收

- 规模：396 通过 / 0 失败（unit 341 + integration 32 + system 14 + reliability 9），全量含覆盖率运行 50 s。
- 协议级（真实服务，非纯 mock 网络行为）：真实 Redis 托管容器（ACL、批量、时标、质量位、丢失、过期、畸形报文、断连、服务端停止/重启恢复、并发一致性、关闭重连无残留）；Commander gRPC mock 覆盖 UNKNOWN 不重传、部分成功批次、重复 command_id、停止期间 in-flight 取消、同端口重启不补发；Collector gRPC mock 覆盖重启后 revision 演进可见。
- 系统级场景矩阵：AGC 定值/限值模式、AVC 三模式（定无功/定功率因数/定电压）、PFR、IR、PFR+IR 叠加、P/Q 联合、SOC 窗口越界失败关闭、SOC 能量裕度约束、权限撤销零写入、能力变化（p_auto=False 失败关闭）、超能力目标饱和不超调、授权源切换（PRIMARY_BACKUP，无自动回退）。
- 故障注入与浸泡：UNKNOWN 闩锁、权限撤销、计划过期零写入；链路级短时浸泡 300 周期 + 资源监控（FD 数、RSS、asyncio 任务数、连接、时延均值/p99、UNKNOWN 计数），断言无泄漏。
- 覆盖率（`--cov=src --cov-branch`）：TOTAL 86%；安全关键模块分支覆盖：control_authority 100%、safe_dispatch 100%、safe_plan_dispatcher 100%、control/runtime 96.25%（≥95% 门槛）。未删除任何有效断言、未降低门槛。
- 静态门：ruff 对 src/ 与 tests/ 零错误（仓库内唯一 PIE810 位于 ai_shared hook，经 `git show origin/main` 验证为基线遗留，非本轮引入）；mypy src 零错误。
- NOT_EXECUTED（按约定如实标注，未虚构）：
  1. Wind Hub 设备协议 Modbus/ADS/IEC104 端到端测试——本仓库仅有 gRPC 与 Redis 实现，无任何 Modbus/ADS/IEC104 代码路径，无物可测。
  2. 中期/夜间级浸泡——仅执行 PR 级短时浸泡（300 周期）；中期（小时级）与夜间（天级）档位未在 CI 执行，浸泡测试文档中已注明。

## E. 风险与验收边界

1. 软件功能验收 ≠ 工程化质量验收 ≠ 现场投运验收。本轮完成的是软件功能与代码质量层：控制语义、安全门禁、故障语义、覆盖率与静态门。工程化质量（长期稳定性、资源极限、部署硬化）仅有短时浸泡与单元级证据；现场投运（真机联调、涉网试验）完全不在本轮范围。
2. T7 为合成设备模型的闭环仿真入口，不是设备级动态特性验证；控制性能指标（调节速率、超调、一次调频合格率）需真实或半实物模型，建议独立任务。
3. runtime 剩余 3 个未覆盖分支均为 in-flight 取消的防御性路径（266->271、283、291-292），其语义已由可靠性层的 in-flight 取消测试在 dispatcher 级覆盖；如需 100% 分支需注入精确的取消时序，收益低、脆性高，建议保持现状。
4. REF-003 与 A6 依赖设计确认，未擅自实现；确认前维持等效语义与两态模型。
5. IR 系统测试已确定性化（虚拟时标），全量运行下重复 3 次无抖动；但 RoCoF 估计本质依赖真实时标质量，现场频率测点时标抖动需在投运前以现场数据复估阈值。
