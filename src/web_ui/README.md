# Orca Unified Web UI

长期维护的 Orca 统一 Web 界面。参考 Wind Hub `src/web_admin` 的 Vue 3 + Element Plus、左侧菜单、浅色语义 Token、表格与响应式布局，不复制其业务代码。

## 启动

```bash
cd src/web_ui
npm install
npm run dev
npm run build
```

当前原型包含运行总览、控制监视、设备与数据、配置管理、调试工具、事件与审计、服务通信诊断。未接通业务接口的页面明确展示未接入状态，不伪造运行数据，不发送控制指令。

## 健康检查

可通过 Vite 环境变量配置同源或已正确配置 CORS 的只读健康端点：

```env
VITE_CONTROLLER_HEALTH_URL=/api/health/controller
VITE_COMMANDER_HEALTH_URL=/api/health/commander
VITE_BROKER_HEALTH_URL=/api/health/broker
```

默认端点留空，状态显示“未检测”而非虚报故障。每 15 秒探测，3 秒超时；HTTP 网络失败或非 2xx 才提示顶部 Error Bar。健康状态只能证明 HTTP 检查端点可达，不能替代业务健康、stale 判定、Commander gRPC 就绪及控制权限判断。生产端点应由 Web Server 提供聚合结果，而不是让浏览器直接访问 gRPC 或消息中间件。

## 迭代原则

- `web_ui` 为所有 Orca 组件的唯一 Web 前端，原 Admin 能力归入配置管理，不建立第二套前端。
- `web_server` 统一提供安全的查询、配置与调试 API，业务留在原组件；不因单服务故障阻断其他页面。
- 控制操作必须使用后端确认、控制权代际校验、UNKNOWN 不重传和 fail-closed，不能依赖乐观更新。
- 样式 Token 与 Wind Hub 风格保持一致，优先 Element Plus 组件；后续逐步抽出页面级组件、API 客户端、类型定义和单元测试。
- 一期重点：可用性框架、配置管理、控制监视、服务诊断、AGC 仿真试验入口。
- 二期：PFR/IR/AVC 深度调试、性能成因分析、反事实仿真和报告。

## 原型状态

目前完成基础 UI 框架及 HTTP 健康探测。控制、配置、数据、审计和试验后端尚未对接，相关操作保持只读或禁用。此版本是正式 Web 产品的可持续演进起点，而非交互已验收的完整一期产品。

## 可视化 Mock 模式

默认启用演示数据（右上角“演示”开关），所有演示数据来自 `src/mock/data.ts`：电厂指标、四类控制功能、18 台设备、32 条事件记录、3 组 AGC 试验，以及完整时序响应样本。

实时趋势、历史回放和控制效果曲线使用 ECharts，支持图例开关、游标提示和缩放。页面显式标识 `MOCK DATA`，演示数据不代表现场测量。可切换“接口”模式检查未接入状态。Mock 模式下所有危险操作仍禁用；不能通过模拟控件发送真实指令。

目前专注静态数据在五个页面的布局与交互设计，尚未对接真实 Web Server API。后续应将 Mock 数据适配为与实际 API 契约一致的独立数据源，不让演示数据进入生产构建的运行状态管理路径。
