# 部署资产

- `windows/install.ps1`：首次配置、依赖安装、Nginx 校验和可选防火墙规则。
- `windows/start.ps1`：启动 Server、视频 WebSocket sidecar 与 Web 网关。
- `windows/stop.ps1`：停止本项目的上位机进程。
- `windows/verify.ps1`：验证 Nginx、HTTP 健康检查和本地监听端口。

完整操作顺序见 `docs/WINDOWS_SERVER_WEB_DEPLOYMENT.md`。
