

### 2.1 前台启动
```bash
cd /opt/loonginx-client
/opt/loonginx-client/.venv/bin/python /opt/loonginx-client/main.py
```
特点：

- 日志直接显示在终端
- 报错最直观
- 适合首次排查 MQTT、PLC、配置和串口问题

### 2.2 后台启动

如果只是临时在后台挂起运行，可以这样：

```bash
cd /opt/loonginx-client
nohup /opt/loonginx-client/.venv/bin/python /opt/loonginx-client/main.py > /opt/loonginx-client/logs/stdout.log 2>&1 &
```
然后查看进程：

```bash
ps -ef | grep main.py
```

查看输出日志：

```bash
tail -f /opt/loonginx-client/logs/stdout.log
```

特点：

- 简单直接
- 适合临时后台运行
- 但不适合长期托管，不支持规范的状态管理和自动重启

## 3. 如何停止程序

### 3.1 前台运行时停止

前台运行时，直接按：

```bash
Ctrl+C
```

这是最直接的停止方式。

### 3.2 后台运行时停止

先找到进程：

```bash
ps -ef | grep '/opt/loonginx-client/main.py'
```

然后优先优雅停止：

```bash
kill <PID>
```

如果进程没有退出，再强制停止：

```bash
kill -9 <PID>
```

建议优先使用普通 `kill`，因为：

- 它会让 Python 进程有机会执行清理逻辑
- `kill -9` 会直接终止，可能丢失最后日志或状态

## 4. 如何设置开机自启

推荐使用 `systemd`。

### 4.1 创建服务文件

```bash
cat >/etc/systemd/system/loonginx-client.service <<'EOF'
[Unit]
Description=Loonginx Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/loonginx-client
EnvironmentFile=-/opt/loonginx-client/.env
ExecStart=/opt/loonginx-client/.venv/bin/python /opt/loonginx-client/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 4.2 启用并启动

```bash
systemctl daemon-reload
systemctl enable loonginx-client
systemctl start loonginx-client
```

### 4.3 查看服务状态

```bash
systemctl status loonginx-client --no-pager
```

### 4.4 查看服务日志

```bash
journalctl -u loonginx-client -n 100 --no-pager
```

持续查看：

```bash
journalctl -u loonginx-client -f
```

### 4.5 重启、停止、取消自启

重启：

```bash
systemctl restart loonginx-client
```

停止：

```bash
systemctl stop loonginx-client
```

取消开机自启：

```bash
systemctl disable loonginx-client
```


