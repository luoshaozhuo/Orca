# Orca

新能源并网场站级协调控制系统（早期可运行代码骨架，**禁止接入真实设备**）。

- 生产Ⅰ区：`control`、`commissioning`。
- 管理区：`evaluation`、`optimization`。
- 共享：`core`、`algorithms`、`infrastructure`。
- 测量和设备命令通信由 wind-hub 负责。

源码模块直接放置在 `src/` 下，不设置 `src/orca/` 包层级。

## Conda 环境

```bash
conda create -n orca python=3.12 -y
conda activate orca
python -m pip install -e '.[dev]'
python -m pytest -q
python -m control
```

> 仅用于开发与测试；不包含已验收的控制安全逻辑。禁止向公开仓库提交现场数据、凭据、配置与设备清单。
