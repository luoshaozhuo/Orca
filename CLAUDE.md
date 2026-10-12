# Orca Claude Code / Codex 执行入口

默认中文。开始仓库任务先读取 `ai_shared/rules/routing.md`。

## 环境

使用 Conda 环境 `orca`（Python 3.12）。

```bash
conda activate orca
python -m pip install -e '.[dev]'
python -m pytest -q
```

Claude SessionStart 使用 `ai_shared/agent_config/activate-env.sh`；Codex 使用 `.codex/config.toml` 的 BASH_ENV。确保 `CONDA_DEFAULT_ENV=orca`，否则停止工作，不擅自切换到其他解释器。若本地仓库不在 `/home/luosh/Orca`，先更新 Codex BASH_ENV 绝对路径。

## 流程与规则

`origin/main → task branch → 修改 → 定向验证 → commit → push → PR → 用户合并`。不在 main 开发或 push，不 force push，不绕过安全 Hook。规则位于 `ai_shared/rules/`，Skill 位于 `ai_shared/agent_config/skills/`，Hook 位于 `ai_shared/agent_config/hooks/`。

## 安全边界

Orca 当前只有演示骨架，禁止连接真实设备。生产Ⅰ区为 control/commissioning，管理区为 evaluation/optimization，管理区禁止直接控制生产设备。
