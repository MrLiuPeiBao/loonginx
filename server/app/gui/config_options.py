"""Fallback runtime configuration options for the Tkinter GUI.

The API exposes the authoritative option list at /api/runtime-config/options.
This module only keeps the GUI importable and usable when the API is not yet
ready, so it must avoid importing API routes or starting service dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, List


Option = Dict[str, Any]
Section = Dict[str, Any]


def _option(
    key: str,
    opt_type: str = "text",
    default: object = "",
    *,
    choices: List[str] | None = None,
    hint: str = "",
    effect: str = "hot update",
) -> Option:
    item: Option = {
        "key": key,
        "label": key,
        "type": "select" if choices else opt_type,
        "default": default,
        "hint": hint,
        "effect": effect,
    }
    if choices:
        item["choices"] = choices
    return item


CONFIG_SECTIONS: Dict[str, Section] = {
    "general": {
        "title": "General",
        "description": "Basic API behavior.",
        "options": [
            _option("APP_NAME", default="sensor_server", effect="restart"),
            _option("PREFER_PAYLOAD_DEVICE_ID", "bool", False),
            _option("COMMAND_TIMEOUT_SECONDS", "int", 15),
        ],
    },
    "api": {
        "title": "API",
        "description": "Control-plane listener and CORS settings.",
        "options": [
            _option("API_HOST", default="0.0.0.0", effect="restart"),
            _option("API_PORT", "int", 8000, effect="restart"),
            _option("CORS_ALLOW_ORIGINS", "list", []),
        ],
    },
    "mqtt": {
        "title": "MQTT",
        "description": "MQTT broker connection.",
        "options": [
            _option("MQTT_ENABLED", "bool", True),
            _option("MQTT_BROKER", default="localhost"),
            _option("MQTT_PORT", "int", 1883),
            _option("MQTT_USERNAME"),
            _option("MQTT_PASSWORD"),
        ],
    },
    "mysql": {
        "title": "MySQL",
        "description": "Database connection.",
        "options": [
            _option("MYSQL_HOST", default="localhost"),
            _option("MYSQL_PORT", "int", 3306),
            _option("MYSQL_USER", default="root"),
            _option("MYSQL_PASSWORD", default="root"),
            _option("MYSQL_DATABASE", default="loognix"),
        ],
    },
    "media": {
        "title": "Media",
        "description": "Media gateway and non-blocking media storage.",
        "options": [
            _option("MEDIA_GATEWAY_ENABLED", "bool", False),
            _option("MEDIA_GATEWAY_TYPE", default="custom", choices=["custom", "mediamtx", "go2rtc", "none"]),
            _option("MEDIA_GATEWAY_RUN_MODE", default="process", choices=["process"]),
            _option("MEDIA_GATEWAY_EXEC"),
            _option("MEDIA_GATEWAY_ARGS"),
            _option("MEDIA_GATEWAY_WORKDIR"),
            _option("MEDIA_GATEWAY_RELAY_RTSP"),
            _option("MEDIA_GATEWAY_CONTROL_PANEL_RTSP"),
            _option("MEDIA_GATEWAY_HTTP_API", default="http://127.0.0.1:1984"),
            _option("MEDIA_GATEWAY_REWRITE_YOLO_INPUT", "bool", True),
            _option("MEDIA_GATEWAY_REWRITE_AUDIO_INPUT", "bool", True),
            _option("MEDIA_STORAGE_MODE", default="filesystem", choices=["database", "filesystem"]),
            _option("MEDIA_STORAGE_DIR", default="media"),
            _option("MEDIA_STORE_IMAGE", "bool", True),
            _option("MEDIA_STORE_AUDIO", "bool", True),
            _option("MEDIA_WRITE_QUEUE_CAPACITY", "int", 1000),
            _option("MEDIA_READ_QUERY_TIMEOUT_SECONDS", "float", 2.0),
        ],
    },
    "yolo": {
        "title": "YOLO",
        "description": "YOLO worker RTSP and inference load controls.",
        "options": [
            _option("YOLO_ENABLED", "bool", False),
            _option("YOLO_RUN_MODE", default="thread", choices=["thread", "process"]),
            _option("YOLO_RTSP_INPUT"),
            _option("YOLO_RTSP_OUTPUT", default="rtsp://127.0.0.1:8555/yolo"),
            _option("YOLO_RTSP_BACKEND", default="gstreamer", choices=["gstreamer", "ffmpeg"]),
            _option("YOLO_RTSP_CAPTURE_OPTIONS"),
            _option("YOLO_CAPTURE_BUFFER_SIZE", "int", 1),
            _option("YOLO_DROP_FRAMES", "bool", True),
            _option("YOLO_DROP_MAX_FRAMES", "int", 10),
            _option("YOLO_READ_FAILURE_RECONNECT_THRESHOLD", "int", 5),
            _option("YOLO_RTSP_LOW_LATENCY", "bool", True),
            _option("YOLO_MODEL_PATH", default="yolov8n.pt"),
            _option("YOLO_COMPUTE_DEVICE", default="auto"),
            _option("YOLO_FPS", "int", 10),
            _option("YOLO_INFERENCE_INTERVAL", "float", 0.5),
            _option("YOLO_INFERENCE_SIZE", "int", 512),
            _option("YOLO_SCREENSHOT_INTERVAL", "float", 5.0),
            _option("YOLO_CONFIDENCE_THRESHOLD", "float", 0.35),
            _option("YOLO_FRAME_WIDTH", "int", 640),
            _option("YOLO_FRAME_HEIGHT", "int", 360),
        ],
    },
    "audio": {
        "title": "Audio",
        "description": "Audio worker RTSP and storage controls.",
        "options": [
            _option("AUDIO_ENABLED", "bool", False),
            _option("AUDIO_RUN_MODE", default="thread", choices=["thread", "process"]),
            _option("AUDIO_RTSP_INPUT"),
            _option("AUDIO_DEVICE_ID", default="mic"),
            _option("AUDIO_LOCATION", default="rtsp"),
            _option("AUDIO_SAMPLE_RATE", "int", 16000),
            _option("AUDIO_WINDOW_SECONDS", "float", 1.0),
            _option("AUDIO_STORE_MIN_INTERVAL_SECONDS", "float", 30.0),
            _option("AUDIO_DEBUG_STORE_ALL", "bool", False),
            _option("AUDIO_THRESHOLD_CENTROID", "float", 0.0),
            _option("AUDIO_THRESHOLD_BANDWIDTH", "float", 0.0),
            _option("AUDIO_THRESHOLD_ROLLOFF", "float", 0.0),
            _option("AUDIO_THRESHOLD_FLATNESS", "float", 0.0),
            _option("AUDIO_THRESHOLD_FLUX", "float", 0.0),
            _option("AUDIO_THRESHOLD_RMS", "float", 0.0),
        ],
    },
    "data_retention": {
        "title": "Data Retention",
        "description": "Background cleanup limits.",
        "options": [
            _option("DATA_RETENTION_DAYS", "int", 0),
            _option("DATA_RETENTION_MAX_GB", "float", 0.0),
            _option("DATA_RETENTION_CHECK_INTERVAL_SECONDS", "int", 3600),
            _option("DATA_RETENTION_TABLES", "list", []),
        ],
    },
}

