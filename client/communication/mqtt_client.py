import json
import logging
import time
from typing import Any, Callable, Dict

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

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        if config.get('username'):
            self.client.username_pw_set(config['username'], config.get('password'))

    # MQTT callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logging.info("MQTT connected")
            for topic in self.message_callbacks:
                client.subscribe(topic)
        else:
            self.connected = False
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
        try:
            self.client.connect(
                self.config['broker'],
                self.config['port'],
                self.config.get('keepalive', 60),
            )
            if not self._loop_started:
                self.client.loop_start()
                self._loop_started = True
            self._last_connect_attempt = time.monotonic()
        except Exception as exc:
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

    def publish(self, topic: str, payload: Any):
        """Publish a message. Non-bytes payloads are JSON serialised."""
        if not self.connected:
            logging.warning("MQTT not connected, drop publish topic=%s", topic)
            return

        if not isinstance(payload, (bytes, str)):
            payload = json.dumps(payload, ensure_ascii=False)

        logging.info(
            "MQTT publish topic=%s payload=%s",
            topic,
            self._format_payload_for_log(payload),
        )

        result = self.client.publish(topic, payload)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error("MQTT publish failed topic=%s rc=%s", topic, result.rc)

    def subscribe(self, topic: str, callback: Callable[[bytes], None]):
        """Subscribe to a topic with a payload handler."""
        self.message_callbacks[topic] = callback
        if self.connected:
            self.client.subscribe(topic)

    def disconnect(self):
        """Stop the loop and disconnect."""
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
        if now - self._last_connect_attempt < self._reconnect_backoff:
            return
        self._last_connect_attempt = now
        logging.info("MQTT attempting reconnect")
        try:
            if not self._loop_started:
                self.client.loop_start()
                self._loop_started = True
            self.client.reconnect()
        except Exception as exc:
            logging.error("MQTT reconnect failed: %s", exc)
