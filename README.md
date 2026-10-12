# Orca

新能源并网场站级协调控制系统（早期可运行代码骨架，**禁止接入真实设备**）。

- 生产Ⅰ区：`control`、`commissioning`。
- 管理区：`evaluation`、`optimization`。
- 共享：`core`、`algorithms`、`infrastructure`。
- 测量和设备命令通信由 wind-hub 负责。

详见 [架构设计](docs/架构设计.md)。

## Conda 环境

```bash
conda create -n orca python=3.12 -y
conda activate orca
python -m pip install -e '.[dev]'
python -m pytest -q
python -m orca
```

> 仅用于开发与测试；不包含已验收的控制安全逻辑。禁止向公开仓库提交现场数据、凭据、配置与设备清单。
