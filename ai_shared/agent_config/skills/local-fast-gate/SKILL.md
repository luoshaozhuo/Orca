---
name: local-fast-gate
description: Run Orca's basic relevant Python checks on changed product code.
---
# Local Fast Gate

当前 Orca 仅提供 Python 骨架。产品代码变更默认执行：

```bash
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest -q
```

按项目实际配置补充 `python -m mypy src`。仅治理文档/Agent 规则变更时只进行定向校验。不得调用 wind-hub 独有的 scripts/ci_gate.py。
