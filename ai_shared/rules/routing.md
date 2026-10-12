# Orca Coding Agent 路由

任务开始读取 `CLAUDE.md` 和本文件，根据任务类型最少读取相关规则。

- Python 开发：`coding.md`，注释额外读 `comments.md`。
- 测试任务：`testing.md`。
- 前端任务（目前尚未创建前端应用）：`frontend.md`。
- 产品代码：`local-fast-gate`；涉及设备边界、控制安全或仿真闭环时按需 `local-pr-gate`。
- 发布：`local-release-gate`；硬件性能资格：`local-qualification`。
- CI 失败：`ci-fix-loop`；合并：`merge-branch`；治理更新：`rule-update`。

Orca 目前没有 wind-hub 的 `scripts/dev.py`、`scripts/ci_scope.py`、`scripts/ci_gate.py`，不要引用或假定其存在。依据实际变更选择可运行的测试，记录 NOT_EXECUTED。
