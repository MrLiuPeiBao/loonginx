"""封装 Paho MQTT 下位机，提供线程安全的发布与订阅能力。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import paho.mqtt.client as mqtt

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MQTTMessageContext:
    """封装 MQTT 消息上下文信息。"""

    topic: str
    payload: bytes
    qos: int
    retain: bool

    def json(self) -> Optional[dict]:
        """将消息 payload 解析为 JSON。"""
        try:
            return json.loads(self.payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


MQTTMessageHandler = Callable[[MQTTMessageContext], None]


class MQTTManager:
    """管理 MQTT 连接、主题订阅与消息分发。"""

    def __init__(self, settings: Settings):
        """初始化 MQTT 下位机。

        Args:
            settings (Settings): 全局配置。
        """
        self._settings = settings
        self._enabled = bool(settings.mqtt_enabled)
        self._client = self._build_client(settings)

        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._handlers: Dict[str, MQTTMessageHandler] = {}
        self._loop_thread: Optional[threading.Thread] = None
        self._last_disconnect: Optional[tuple[int, float]] = None

    def _build_client(self, settings: Settings) -> mqtt.Client:
        base_client_id = (settings.app_name or 'sensor_server').strip() or 'sensor_server'
        client = mqtt.Client(client_id=f'{base_client_id}-{os.getpid()}')
        client.enable_logger(logger)
        client.reconnect_delay_set(min_delay=5, max_delay=60)
        username = settings.mqtt_username
        if username:
            client.username_pw_set(username, settings.mqtt_password or None)
        else:
            client.username_pw_set(None)
        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        return client

    # -- Lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        """建立 MQTT 连接并启动网络循环。

        Notes:
            - 该方法是幂等的：重复调用不会启动多个网络线程。
            - 使用 `connect_async + loop_forever(retry_first_connection=True)`，可在 Broker
              未就绪时持续重试，避免“启动时连接失败后永远不再恢复”的问题。
        """
        if not self._enabled:
            return
        with self._lock:
            if self._loop_thread and self._loop_thread.is_alive():
                return

            try:
                self._connect_async()
            except Exception as exc:  # pragma: no cover - 依赖网络
                logger.error('Failed to connect MQTT broker %s:%s: %s', self._settings.mqtt_broker, self._settings.mqtt_port, exc)
                return

            self._loop_thread = threading.Thread(
                target=lambda: self._client.loop_forever(retry_first_connection=True),
                name='mqtt-loop',
                daemon=True,
            )
            self._loop_thread.start()
            logger.info('MQTT network loop started for %s:%s', self._settings.mqtt_broker, self._settings.mqtt_port)

    def connect_foreground(self) -> bool:
        """Connect for callers that drive the MQTT network loop themselves."""
        if not self._enabled:
            return True
        if self._connected.is_set():
            return True
        with self._lock:
            if self._connected.is_set():
                return True
            broker = self._settings.mqtt_broker
            port = int(self._settings.mqtt_port)
            try:
                self._client.connect(broker, port, keepalive=60)
            except Exception as exc:  # pragma: no cover - depends on broker
                logger.warning('Failed to connect MQTT broker %s:%s: %s', broker, port, exc)
                return False
            logger.info('MQTT foreground connection opened for %s:%s', broker, port)
            return True

    def loop_once(self, timeout: float = 0.1) -> int:
        if not self._enabled:
            return mqtt.MQTT_ERR_SUCCESS
        return int(self._client.loop(timeout=max(0.0, float(timeout))))

    def disconnect(self) -> None:
        """关闭 MQTT 连接并停止网络循环。"""
        with self._lock:
            if not self._enabled:
                return
            if not self._loop_thread:
                return
            self._client.disconnect()
            self._connected.clear()
            logger.info('MQTT disconnect requested')

    def apply_settings(self, settings: Settings, *, start_loop_thread: bool = True) -> None:
        """Apply new settings and reconnect to broker if needed."""
        with self._lock:
            self._enabled = bool(settings.mqtt_enabled)
            rebuild = (self._settings.app_name or '') != (settings.app_name or '')
            self._settings = settings
            if rebuild:
                self._client = self._build_client(settings)
                self._loop_thread = None
                self._connected.clear()
            else:
                username = settings.mqtt_username
                if username:
                    self._client.username_pw_set(username, settings.mqtt_password or None)
                else:
                    self._client.username_pw_set(None)
            if not self._enabled:
                self._connected.clear()
                self._loop_thread = None
                return
        # Force reconnect with updated broker/port
        try:
            self._client.disconnect()
        except Exception:
            pass
        try:
            self._connect_async()
        except Exception:
            logger.exception('Failed to refresh MQTT connection')
        with self._lock:
            if start_loop_thread and not (self._loop_thread and self._loop_thread.is_alive()):
                self._loop_thread = threading.Thread(
                    target=lambda: self._client.loop_forever(retry_first_connection=True),
                    name='mqtt-loop',
                    daemon=True,
                )
                self._loop_thread.start()

    # -- Publish & Subscribe ------------------------------------------------
    def register_handler(self, topic: str, handler: MQTTMessageHandler, qos: int = 0) -> None:
        """注册主题处理器，并在连接后自动订阅。

        Args:
            topic (str): 要订阅的主题。
            handler (MQTTMessageHandler): 消息处理函数。
            qos (int, optional): 订阅 QoS，默认为 0。
        """
        with self._lock:
            self._handlers[topic] = handler
            if self._connected.is_set():
                self._client.subscribe(topic, qos=qos)
                logger.debug('Subscribed to topic %s with qos %s', topic, qos)

    def unregister_handler(self, topic: str) -> None:
        """取消主题处理器并退订。"""
        with self._lock:
            if topic in self._handlers:
                del self._handlers[topic]
                if self._connected.is_set():
                    self._client.unsubscribe(topic)
                    logger.debug('Unsubscribed from topic %s', topic)

    def publish(
        self,
        topic: str,
        payload: bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> bool:
        """发布消息到指定主题。

        Args:
            topic (str): 目标主题。
            payload (bytes): 消息内容。
            qos (int, optional): 发布 QoS。
            retain (bool, optional): 是否保留消息。

        Returns:
            bool: 发布是否成功。
        """
        if not self._enabled:
            logger.debug('MQTT publish skipped because MQTT is disabled')
            return False
        if not self._connected.wait(timeout=2):
            logger.warning('MQTT publish skipped, broker not connected')
            return False

        result = self._client.publish(topic, payload=payload, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error('MQTT publish failed on %s: rc=%s', topic, result.rc)
            return False
        logger.debug('MQTT publish succeed on %s', topic)
        return True

    @property
    def is_connected(self) -> bool:
        """返回当前连接状态。"""
        if not self._enabled:
            return True
        return self._connected.is_set()

    # -- Internal callbacks -------------------------------------------------
    def _handle_connect(
        self,
        client: mqtt.Client,
        userdata: Optional[object],
        flags: Dict[str, int],
        rc: int,
    ) -> None:
        if rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error('MQTT connection failed with code %s', rc)
            return

        self._connected.set()
        logger.info('MQTT connected: %s', rc)

        with self._lock:
            for topic in self._handlers:
                client.subscribe(topic)
                logger.debug('Subscribed to topic %s after connect', topic)

    def _handle_disconnect(
        self,
        client: mqtt.Client,
        userdata: Optional[object],
        rc: int,
    ) -> None:
        self._connected.clear()
        if rc != mqtt.MQTT_ERR_SUCCESS:
            log_required = True
            now = time.time()
            if self._last_disconnect and self._last_disconnect[0] == rc:
                if now - self._last_disconnect[1] < 30:
                    log_required = False
            if log_required:
                logger.warning('Unexpected MQTT disconnect rc=%s', rc)
                self._last_disconnect = (rc, now)
        else:
            logger.info('MQTT disconnected gracefully')

    def _handle_message(
        self,
        client: mqtt.Client,
        userdata: Optional[object],
        message: mqtt.MQTTMessage,
    ) -> None:
        context = MQTTMessageContext(
            topic=message.topic,
            payload=bytes(message.payload or b''),
            qos=message.qos,
            retain=bool(message.retain),
        )
        handler = self._handlers.get(message.topic)
        if handler:
            try:
                handler(context)
            except Exception:  # pragma: no cover - 调用方处理
                logger.exception('Failed to handle MQTT message on %s', message.topic)
                logger.exception('Failed to handle MQTT message on %s', message.topic)
        else:
            logger.debug('No handler registered for topic %s', message.topic)

    def _connect_async(self) -> None:
        broker = self._settings.mqtt_broker
        port = int(self._settings.mqtt_port)
        self._client.connect_async(broker, port, keepalive=60)
