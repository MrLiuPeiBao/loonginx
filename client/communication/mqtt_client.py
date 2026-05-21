import json
import logging
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple

import threading

import paho.mqtt.client as mqtt


class MQTTClient:
    """Thin wrapper around paho-mqtt with callback registration."""

    def __init__(self, config: dict):
        self.config = config
        self.client = mqtt.Client(client_id=config.get('client_id'))
        self.connected = False
        self.message_callbacks: Dict[str, Callable[[bytes], None]] = {}
        self._loop_started = False
        self._last_connect_attempt = 0.0
        self._reconnect_backoff = 5.0
        self._connecting = False
        self._connect_started_at = 0.0
        self._connect_attempt_timeout = float(config.get('connect_timeout', 12.0) or 12.0)
        self._connect_lock = threading.Lock()
        self._default_qos = int(config.get('publish_qos', 0) or 0)
        self._offline_enabled = bool(config.get('offline_queue_enabled'))
        self._offline_max_items = int(config.get('offline_queue_max_items', 0) or 0)
        self._offline_max_bytes = int(config.get('offline_queue_max_bytes', 0) or 0)
        self._topic_qos_map = dict(config.get('topic_qos_map') or {})
        self._offline_policy = str(config.get('offline_policy') or 'queue').lower()
        self._offline_queue: Deque[Tuple[str, bytes, int, bool]] = deque()
        self._offline_bytes = 0
        self._offline_latest: Dict[str, Tuple[bytes, int, bool]] = {}
        self._offline_lock = threading.Lock()

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        if config.get('username'):
            self.client.username_pw_set(config['username'], config.get('password'))

    # MQTT callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self._connecting = False
            logging.info("MQTT connected")
            for topic in self.message_callbacks:
                client.subscribe(topic)
            self._drain_offline_queue()
        else:
            self.connected = False
            self._connecting = False
            logging.error("MQTT connect failed rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        callback = self.message_callbacks.get(msg.topic)
        if callback:
            callback(msg.payload)
        else:
            logging.warning("Unhandled MQTT topic=%s", msg.topic)

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        logging.info("MQTT disconnected rc=%s", rc)

    # Public APIs --------------------------------------------------------
    def connect(self):
        """Connect to the broker and start the network loop."""
        with self._connect_lock:
            if self._connecting or self.connected:
                return
            self._connecting = True
            self._last_connect_attempt = time.monotonic()
            self._connect_started_at = self._last_connect_attempt
        try:
            self.client.connect_async(
                self.config['broker'],
                self.config['port'],
                self.config.get('keepalive', 60),
            )
            if not self._loop_started:
                self.client.loop_start()
                self._loop_started = True
        except Exception as exc:
            self._connecting = False
            logging.error("MQTT connect error: %s", exc)

    def _format_payload_for_log(self, payload: Any) -> str:
        if isinstance(payload, bytes):
            return payload.hex(' ')
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload)

    def publish(self, topic: str, payload: Any) -> bool:
        """Publish a message. Non-bytes payloads are JSON serialised."""
        qos = self._resolve_qos(topic)
        retain = False

        if not isinstance(payload, (bytes, str)):
            payload = json.dumps(payload, ensure_ascii=False)

        payload_bytes = payload if isinstance(payload, bytes) else payload.encode('utf-8')

        if not self.connected:
            self.ensure_connected()
        if not self.connected:
            if self._offline_enabled:
                if self._offline_policy == 'latest':
                    queued = self._enqueue_offline_latest(topic, payload_bytes, qos, retain)
                else:
                    queued = self._enqueue_offline(topic, payload_bytes, qos, retain)
                if queued:
                    logging.warning("MQTT offline queued topic=%s size=%s", topic, len(payload_bytes))
                    return True
            logging.warning("MQTT not connected, drop publish topic=%s", topic)
            return False

        logging.info(
            "MQTT publish topic=%s payload=%s",
            topic,
            self._format_payload_for_log(payload_bytes if isinstance(payload_bytes, bytes) else payload),
        )

        try:
            result = self.client.publish(topic, payload_bytes, qos=qos, retain=retain)
        except Exception as exc:
            logging.error("MQTT publish failed topic=%s error=%s", topic, exc)
            return False
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error("MQTT publish failed topic=%s rc=%s", topic, result.rc)
            return False
        return True

    def _resolve_qos(self, topic: str) -> int:
        if topic in self._topic_qos_map:
            try:
                return int(self._topic_qos_map[topic])
            except (TypeError, ValueError):
                return self._default_qos
        return self._default_qos

    def _cast_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        return default

    def _trim_offline_queue(self) -> None:
        if self._offline_policy == 'latest':
            return
        with self._offline_lock:
            while self._offline_max_items > 0 and len(self._offline_queue) > self._offline_max_items:
                dropped = self._offline_queue.popleft()
                self._offline_bytes -= len(dropped[1])
            if self._offline_max_bytes > 0:
                while self._offline_bytes > self._offline_max_bytes and self._offline_queue:
                    dropped = self._offline_queue.popleft()
                    self._offline_bytes -= len(dropped[1])

    def apply_runtime_config(self, overrides: Dict[str, Any]) -> None:
        if 'MQTT_PUBLISH_QOS' in overrides:
            try:
                self._default_qos = int(overrides['MQTT_PUBLISH_QOS'])
                self.config['publish_qos'] = self._default_qos
            except (TypeError, ValueError):
                pass
        if 'MQTT_OFFLINE_QUEUE_ENABLED' in overrides:
            self._offline_enabled = self._cast_bool(
                overrides.get('MQTT_OFFLINE_QUEUE_ENABLED'),
                self._offline_enabled,
            )
            self.config['offline_queue_enabled'] = self._offline_enabled
        if 'MQTT_OFFLINE_QUEUE_MAX_ITEMS' in overrides:
            try:
                self._offline_max_items = int(overrides['MQTT_OFFLINE_QUEUE_MAX_ITEMS'])
                self.config['offline_queue_max_items'] = self._offline_max_items
            except (TypeError, ValueError):
                pass
        if 'MQTT_OFFLINE_QUEUE_MAX_BYTES' in overrides:
            try:
                self._offline_max_bytes = int(overrides['MQTT_OFFLINE_QUEUE_MAX_BYTES'])
                self.config['offline_queue_max_bytes'] = self._offline_max_bytes
            except (TypeError, ValueError):
                pass
        if 'MQTT_OFFLINE_POLICY' in overrides:
            policy = str(overrides.get('MQTT_OFFLINE_POLICY') or '').lower()
            if policy in {'queue', 'latest'}:
                self._offline_policy = policy
                self.config['offline_policy'] = policy
        if 'MQTT_TOPIC_QOS_MAP' in overrides:
            raw = overrides.get('MQTT_TOPIC_QOS_MAP')
            if isinstance(raw, dict):
                self._topic_qos_map = dict(raw)
                self.config['topic_qos_map'] = dict(raw)
            else:
                try:
                    parsed = json.loads(str(raw))
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    self._topic_qos_map = dict(parsed)
                    self.config['topic_qos_map'] = dict(parsed)

        self._trim_offline_queue()

    def subscribe(self, topic: str, callback: Callable[[bytes], None]):
        """Subscribe to a topic with a payload handler."""
        self.message_callbacks[topic] = callback
        if self.connected:
            self.client.subscribe(topic)

    def disconnect(self):
        """Stop the loop and disconnect."""
        self._connecting = False
        try:
            if self._loop_started:
                self.client.loop_stop()
                self._loop_started = False
        finally:
            self.client.disconnect()

    def ensure_connected(self) -> None:
        """Attempt reconnect with backoff when disconnected."""
        if self.connected:
            return
        now = time.monotonic()
        if self._connecting:
            if now - self._connect_started_at < self._connect_attempt_timeout:
                return
            logging.warning(
                "MQTT connect attempt timed out broker=%s port=%s timeout=%.2fs",
                self.config.get('broker'),
                self.config.get('port'),
                self._connect_attempt_timeout,
            )
            self._connecting = False
        if now - self._last_connect_attempt < self._reconnect_backoff:
            return
        self._last_connect_attempt = now
        logging.info("MQTT attempting reconnect")
        self.connect()

    # Offline queue ----------------------------------------------------
    def _enqueue_offline(self, topic: str, payload: bytes, qos: int, retain: bool) -> bool:
        if not self._offline_enabled:
            return False
        if self._offline_max_items == 0:
            return False
        message_size = len(payload)
        with self._offline_lock:
            while self._offline_max_items > 0 and len(self._offline_queue) >= self._offline_max_items:
                dropped = self._offline_queue.popleft()
                self._offline_bytes -= len(dropped[1])
            if self._offline_max_bytes > 0:
                while self._offline_bytes + message_size > self._offline_max_bytes and self._offline_queue:
                    dropped = self._offline_queue.popleft()
                    self._offline_bytes -= len(dropped[1])
            if self._offline_max_bytes > 0 and self._offline_bytes + message_size > self._offline_max_bytes:
                return False
            self._offline_queue.append((topic, payload, qos, retain))
            self._offline_bytes += message_size
        return True

    def _enqueue_offline_latest(self, topic: str, payload: bytes, qos: int, retain: bool) -> bool:
        if not self._offline_enabled:
            return False
        with self._offline_lock:
            self._offline_latest[topic] = (payload, qos, retain)
        return True

    def _drain_offline_queue(self) -> None:
        if not self._offline_enabled:
            return
        if not self.connected:
            return
        while True:
            with self._offline_lock:
                if not self._offline_queue:
                    break
                topic, payload, qos, retain = self._offline_queue[0]
            try:
                result = self.client.publish(topic, payload, qos=qos, retain=retain)
            except Exception as exc:
                logging.error("MQTT offline drain failed topic=%s error=%s", topic, exc)
                return
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logging.error("MQTT offline drain failed topic=%s rc=%s", topic, result.rc)
                return
            with self._offline_lock:
                if self._offline_queue and self._offline_queue[0][0] == topic:
                    self._offline_queue.popleft()
                    self._offline_bytes -= len(payload)
        if self._offline_latest:
            for topic, (payload, qos, retain) in list(self._offline_latest.items()):
                try:
                    result = self.client.publish(topic, payload, qos=qos, retain=retain)
                except Exception as exc:
                    logging.error("MQTT offline latest drain failed topic=%s error=%s", topic, exc)
                    return
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    logging.error("MQTT offline latest drain failed topic=%s rc=%s", topic, result.rc)
                    return
            with self._offline_lock:
                self._offline_latest.clear()
