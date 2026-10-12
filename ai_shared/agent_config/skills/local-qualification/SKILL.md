---
name: local-qualification
description: Run authorized Orca hardware, control-performance, or long-soak qualification tests only when a suitable environment exists.
---
# Local Qualification

必须用户明确要求、具备测试环境与控制权限后才执行现场设备响应试验、性能验收或长稳测试。禁止自动连接真实设备或发送危险指令。环境不足时记录 NOT_EXECUTED，不使用 mock 结果冒充真实硬件测试。
