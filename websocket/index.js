const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const WebSocket = require('ws');
const { spawn } = require('child_process');

function fileExists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

function resolveFromPath(command) {
  const pathValue = process.env.PATH || process.env.Path || '';
  const pathEntries = pathValue.split(path.delimiter).filter(Boolean);
  const extensions = process.platform === 'win32'
    ? (process.env.PATHEXT || '.EXE;.CMD;.BAT;.COM').split(';')
    : [''];

  for (const dir of pathEntries) {
    for (const ext of extensions) {
      const candidate = path.join(dir, `${command}${ext}`);
      if (fileExists(candidate)) {
        return candidate;
      }
    }
  }

  return null;
}

function resolveFFmpegCommand() {
  const configuredPath = process.env.FFMPEG_PATH || process.env.FFMPEG;
  if (configuredPath && fileExists(configuredPath)) {
    return configuredPath;
  }

  const commandName = process.platform === 'win32' ? 'ffmpeg' : 'ffmpeg';
  const pathResolved = resolveFromPath(commandName);
  if (pathResolved) {
    return pathResolved;
  }

  if (process.platform === 'win32') {
    const winGetLink = path.join(
      os.homedir(),
      'AppData',
      'Local',
      'Microsoft',
      'WinGet',
      'Links',
      'ffmpeg.exe'
    );

    if (fileExists(winGetLink)) {
      return winGetLink;
    }
  }

  return null;
}

const ffmpegCommand = resolveFFmpegCommand();
if (!ffmpegCommand) {
  console.error(
    '未找到 FFmpeg。请安装 FFmpeg，或在环境变量中设置 FFMPEG_PATH 指向 ffmpeg 可执行文件。'
  );
  process.exit(1);
}

console.log(`使用 FFmpeg: ${ffmpegCommand}`);

function readPort(name, fallback) {
  const value = Number.parseInt(process.env[name] || '', 10);
  return Number.isInteger(value) && value > 0 && value <= 65535 ? value : fallback;
}

const unifiedBase = (process.env.RTSP_UNIFIED_BASE || '').replace(/\/$/, '');
const camera1Url = process.env.RTSP_URL_CAMERA1 || (unifiedBase ? `${unifiedBase}/cam01` : '');
const camera2Url = process.env.RTSP_URL_CAMERA2 || (unifiedBase ? `${unifiedBase}/cam02` : '');
if (!camera1Url || !camera2Url) {
  console.error('请在 websocket/.env 中设置 RTSP_URL_CAMERA1 和 RTSP_URL_CAMERA2。');
  process.exit(1);
}

const wsHost = process.env.WS_HOST || '127.0.0.1';

function startMqttWebSocketProxy() {
  const listenPort = readPort('MQTT_WS_PORT', 1884);
  const targetHost = process.env.MQTT_TCP_HOST || '127.0.0.1';
  const targetPort = readPort('MQTT_TCP_PORT', 1883);
  const proxy = new WebSocket.Server({
    port: listenPort,
    host: wsHost,
    handleProtocols(protocols) {
      return protocols.has('mqtt') ? 'mqtt' : false;
    }
  });

  proxy.on('connection', (ws) => {
    const tcp = net.createConnection({ host: targetHost, port: targetPort });
    let closed = false;
    const closeBoth = () => {
      if (closed) return;
      closed = true;
      if (!tcp.destroyed) tcp.destroy();
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close();
    };

    tcp.on('data', (chunk) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(chunk, { binary: true });
    });
    tcp.on('error', (error) => {
      console.error(`MQTT TCP连接失败 ${targetHost}:${targetPort}:`, error.message);
      closeBoth();
    });
    tcp.on('close', closeBoth);
    ws.on('message', (data) => {
      if (!tcp.destroyed) tcp.write(data);
    });
    ws.on('close', closeBoth);
    ws.on('error', closeBoth);
  });

  console.log(`MQTT WebSocket代理已启动: ws://${wsHost}:${listenPort} -> mqtt://${targetHost}:${targetPort}`);
  return proxy;
}

const mqttProxyServer = startMqttWebSocketProxy();

// 配置两个 RTSP 源。地址只从本机环境读取，不在仓库中保存凭据。
const configs = [
  {
    id: 'camera1',
    wsPort: readPort('WS_PORT_CAMERA1', 8089),
    rtspUrl: camera1Url,
    ffmpegCommand,
    ffmpegOptions: [
      '-i', '${rtspUrl}',
      '-f', 'mpegts',
      '-codec:v', 'mpeg1video',
      '-b:v', '800k',
      '-r', '25',
      '-s', '640x480',  // 调整为适合小窗口的分辨率
      '-bf', '0',

      // 音频参数
      '-codec:a', 'mp2',
      '-b:a', '128k',
      '-ac', '2',
      '-ar', '44100',

      '-muxdelay', '0.1',
      '-'
    ]
  },
  {
    id: 'camera2',
    wsPort: readPort('WS_PORT_CAMERA2', 8090),
    rtspUrl: camera2Url,
    ffmpegCommand,
    ffmpegOptions: [
      '-i', '${rtspUrl}',
      '-f', 'mpegts',
      '-codec:v', 'mpeg1video',
      '-b:v', '800k',
      '-r', '25',
      '-s', '320x240',
      '-bf', '0',
      '-muxdelay', '0.1',
      '-'
    ]
  }
];

// 为每个配置创建WebSocket服务器和FFmpeg进程
const servers = [];

configs.forEach(config => {
  const wss = new WebSocket.Server({ port: config.wsPort, host: wsHost });
  console.log(`WebSocket服务器已启动 ${config.id}: ws://${wsHost}:${config.wsPort}`);

  let ffmpegProcess = null;
  const clients = new Set();

  // 启动FFmpeg转码
  function startFFmpeg() {
    if (ffmpegProcess) return;

    const args = config.ffmpegOptions.map(opt =>
      opt === '${rtspUrl}' ? config.rtspUrl : opt
    );

    ffmpegProcess = spawn(config.ffmpegCommand, args, {
      stdio: ['ignore', 'pipe', 'inherit']
    });

    ffmpegProcess.stdout.on('data', (data) => {
      // 转发给所有连接的客户端
      clients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) {
          client.send(data);
        }
      });
    });

    ffmpegProcess.on('error', (err) => {
      console.error(`FFmpeg错误 (${config.id}):`, err);
      if (err && err.code === 'ENOENT') {
        console.error(`FFmpeg不可用 (${config.id})，已停止自动重试。`);
        ffmpegProcess = null;
        return;
      }
      restartFFmpeg();
    });

    ffmpegProcess.on('close', (code) => {
      console.log(`FFmpeg进程退出 (${config.id})，代码: ${code}`);
      restartFFmpeg();
    });
  }

  // 重启FFmpeg
  function restartFFmpeg() {
    if (ffmpegProcess) {
      ffmpegProcess.kill();
      ffmpegProcess = null;
    }
    setTimeout(startFFmpeg, 1000);
  }

  // 客户端连接处理
  wss.on('connection', (ws) => {
    console.log(`新客户端连接 (${config.id})`);
    clients.add(ws);

    // 如果没有FFmpeg进程，则启动
    if (!ffmpegProcess) {
      startFFmpeg();
    }

    // 客户端断开处理
    ws.on('close', () => {
      console.log(`客户端断开 (${config.id})`);
      clients.delete(ws);

      // 如果没有客户端连接，关闭FFmpeg
      if (clients.size === 0 && ffmpegProcess) {
        ffmpegProcess.kill();
        ffmpegProcess = null;
      }
    });

    // 错误处理
    ws.on('error', (err) => {
      console.error(`WebSocket错误 (${config.id}):`, err);
      clients.delete(ws);
    });
  });

  servers.push({
    id: config.id,
    wss: wss,
    ffmpegProcess: ffmpegProcess
  });
});

// 进程退出清理
process.on('SIGINT', () => {
  console.log('正在关闭服务器...');
  servers.forEach(server => {
    if (server.ffmpegProcess) server.ffmpegProcess.kill();
    server.wss.close();
  });
  mqttProxyServer.close();
  process.exit();
});
