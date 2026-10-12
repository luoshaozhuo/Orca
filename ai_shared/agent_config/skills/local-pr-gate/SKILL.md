---
name: local-pr-gate
description: Run targeted Orca integration and simulation regression checks when warranted by changed code.
---
# Local PR Gate

对于控制安全、调试旁路、风光储设备模型、仿真闭环等高风险变更，除 Fast Gate 外按现有测试执行相关 integration/system/simulation 测试。

```bash
python -m pytest -q tests
```

尚未建设的专项测试如实标记 NOT_EXECUTED，不能将 smoke test 认定为涉网性能验收通过。
