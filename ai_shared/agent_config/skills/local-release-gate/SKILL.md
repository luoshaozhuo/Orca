---
name: local-release-gate
description: Check Orca's real packaging and code quality before a candidate release.
---
# Local Release Gate

```bash
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest -q
python -m pip check
```

发布生产Ⅰ区版本前另检查最小依赖、安全校验、试验权限、版本回退和审批；不把最小骨架视为可投运软件。
