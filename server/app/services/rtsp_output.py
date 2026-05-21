"""RTSP output helpers for YOLO annotated frames."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _prepend_env_path(name: str, value: str) -> None:
    if not value or not os.path.isdir(value):
        return
    existing = os.environ.get(name, '')
    entries = [item for item in existing.split(os.pathsep) if item]
    normalized = {os.path.normcase(os.path.normpath(item)) for item in entries}
    if os.path.normcase(os.path.normpath(value)) in normalized:
        return
    os.environ[name] = os.pathsep.join([value, *entries])


def _configure_gstreamer_env() -> None:
    prefix = os.environ.get('CONDA_PREFIX')
    if not prefix:
        return
    _prepend_env_path('GST_PLUGIN_PATH', os.path.join(prefix, 'Library', 'lib', 'gstreamer-1.0'))
    _prepend_env_path('GI_TYPELIB_PATH', os.path.join(prefix, 'Library', 'lib', 'girepository-1.0'))


_configure_gstreamer_env()

try:  # pragma: no cover - optional dependency
    import gi

    gi.require_version('Gst', '1.0')
    gi.require_version('GstRtspServer', '1.0')
    from gi.repository import GLib, Gst, GstRtspServer
except Exception as exc:  # pragma: no cover - optional dependency
    Gst = None  # type: ignore[assignment]
    GstRtspServer = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    logger.warning('GStreamer bindings unavailable: %s', exc)


@dataclass(frozen=True)
class RtspOutputConfig:
    """Configuration for RTSP output backends."""

    output_url: str
    width: int
    height: int
    fps: int
    backend: str
    low_latency: bool = True
    encoder: str = 'x264enc'
    encoder_props: str = ''


class RtspOutput:
    """Abstract RTSP output interface."""

    def start(self) -> bool:
        """Start the RTSP output backend."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the RTSP output backend."""
        raise NotImplementedError

    def write_frame(self, frame: Any) -> None:
        """Write a single BGR frame."""
        raise NotImplementedError


class NullRtspOutput(RtspOutput):
    """No-op RTSP output implementation."""

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        return None

    def write_frame(self, frame: Any) -> None:
        return None


class FFmpegRtspOutput(RtspOutput):
    """Push raw frames into FFmpeg for RTSP publishing."""

    def __init__(self, config: RtspOutputConfig) -> None:
        self._config = config
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self._process and self._process.poll() is None:
            return True
        if not self._config.output_url:
            return False

        cmd = [
            'ffmpeg',
            '-loglevel',
            'error',
            '-f',
            'rawvideo',
            '-pixel_format',
            'bgr24',
            '-video_size',
            f'{self._config.width}x{self._config.height}',
            '-framerate',
            str(self._config.fps),
            '-i',
            '-',
            '-c:v',
            'libx264',
            '-preset',
            'ultrafast',
            '-tune',
            'zerolatency',
            '-pix_fmt',
            'yuv420p',
            '-f',
            'rtsp',
            '-rtsp_transport',
            'tcp',
            self._config.output_url,
        ]

        try:
            logger.info(
                'Starting FFmpeg RTSP push to %s (%sx%s@%sfps)',
                self._config.output_url,
                self._config.width,
                self._config.height,
                self._config.fps,
            )
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return True
        except FileNotFoundError:
            logger.warning('FFmpeg executable not found, disable RTSP restreaming')
        except Exception as exc:  # pragma: no cover - ffmpeg specific
            logger.exception('Failed to start FFmpeg RTSP output: %s', exc)
        self._process = None
        return False

    def stop(self) -> None:
        self._terminate()

    def write_frame(self, frame: Any) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        data = frame.tobytes()
        try:
            with self._lock:
                stdin = self._process.stdin
                if stdin:
                    stdin.write(data)
        except Exception as exc:  # pragma: no cover - ffmpeg specific
            logger.warning('Failed to write frame to FFmpeg: %s', exc)
            self._terminate()

    def _terminate(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        logger.info('Stopping FFmpeg RTSP process')
        try:
            stdin = proc.stdin
            if stdin:
                stdin.close()
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:  # pragma: no cover - ffmpeg specific
            proc.kill()
        except Exception as exc:  # pragma: no cover - ffmpeg specific
            logger.debug('FFmpeg termination raised %s', exc)


class GStreamerRtspOutput(RtspOutput):
    """Publish frames through an embedded GStreamer RTSP server."""

    def __init__(self, config: RtspOutputConfig) -> None:
        self._config = config
        self._server: Optional[Any] = None
        self._loop: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._appsrc: Optional[Any] = None
        self._lock = threading.Lock()
        self._frame_count = 0
        self._last_push_warning_at = 0.0
        self._ready = threading.Event()
        self._mount_path = '/yolo'
        self._bind_host = '0.0.0.0'
        self._bind_port = 8554

    def start(self) -> bool:
        if Gst is None or GstRtspServer is None or GLib is None:
            logger.warning('GStreamer RTSP unavailable: missing gi/GstRtspServer')
            return False
        if not self._config.output_url:
            return False
        if self._thread and self._thread.is_alive():
            return True

        url_host, self._bind_port, self._mount_path = _parse_rtsp_url(self._config.output_url)
        self._bind_host = _resolve_server_bind_host(url_host)
        _configure_gstreamer_env()
        Gst.init(None)

        self._server = GstRtspServer.RTSPServer()
        self._server.props.service = str(self._bind_port)
        self._server.props.address = self._bind_host or '0.0.0.0'

        factory = GstRtspServer.RTSPMediaFactory()
        factory.set_shared(True)
        factory.set_launch(_build_gst_launch(self._config))
        factory.connect('media-configure', self._on_media_configure)

        mounts = self._server.get_mount_points()
        mounts.add_factory(self._mount_path, factory)
        self._server.attach(None)

        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._run_loop, name='rtsp-server', daemon=True)
        self._thread.start()

        logger.info(
            'GStreamer RTSP server ready on rtsp://%s:%s%s (bind=%s)',
            url_host,
            self._bind_port,
            self._mount_path,
            self._bind_host or '0.0.0.0',
        )
        return True

    def stop(self) -> None:
        with self._lock:
            appsrc = self._appsrc
            self._appsrc = None
            self._frame_count = 0
            self._ready.clear()

        if appsrc is not None and Gst is not None:
            try:
                appsrc.emit('end-of-stream')
            except Exception:  # pragma: no cover - best effort cleanup
                pass

        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._loop = None
        self._server = None

    def write_frame(self, frame: Any) -> None:
        if Gst is None:
            return
        if not self._ready.is_set():
            return

        with self._lock:
            appsrc = self._appsrc
            if appsrc is None:
                return

        data = frame.tobytes()
        buffer = Gst.Buffer.new_allocate(None, len(data), None)
        buffer.fill(0, data)

        duration = Gst.util_uint64_scale_int(1, Gst.SECOND, max(1, self._config.fps))
        pts = self._frame_count * duration
        buffer.pts = pts
        buffer.dts = pts
        buffer.duration = duration
        self._frame_count += 1

        ret = appsrc.emit('push-buffer', buffer)
        if ret != Gst.FlowReturn.OK:
            if ret == Gst.FlowReturn.FLUSHING:
                self._ready.clear()
            self._warn_push_failed(ret)

    def _warn_push_failed(self, ret: Any) -> None:
        now = time.time()
        if now - self._last_push_warning_at < 5.0:
            return
        self._last_push_warning_at = now
        logger.warning('GStreamer push-buffer failed: %s', ret)

    def _run_loop(self) -> None:
        if self._loop is None:
            return
        try:
            self._loop.run()
        except Exception as exc:  # pragma: no cover - GLib loop failure
            logger.error('GStreamer RTSP loop stopped: %s', exc)

    def _on_media_configure(self, _factory: Any, media: Any) -> None:
        element = media.get_element()
        appsrc = element.get_child_by_name('source')
        if appsrc is None:
            logger.warning('GStreamer appsrc not found')
            return

        caps = Gst.Caps.from_string(_build_caps(self._config))
        appsrc.set_property('caps', caps)
        appsrc.set_property('is-live', True)
        appsrc.set_property('block', not self._config.low_latency)
        appsrc.set_property('format', Gst.Format.TIME)
        if self._config.low_latency:
            try:
                appsrc.set_property('max-buffers', 1)
            except Exception:  # pragma: no cover - optional property
                logger.debug('Failed to set appsrc max-buffers', exc_info=True)

        with self._lock:
            self._appsrc = appsrc
            self._frame_count = 0
            self._last_push_warning_at = 0.0
            self._ready.set()


def create_rtsp_output(config: RtspOutputConfig) -> RtspOutput:
    """Create RTSP output backend based on config and availability."""
    backend = (config.backend or 'gstreamer').strip().lower()
    if backend not in {'gstreamer', 'ffmpeg'}:
        logger.warning('Unknown YOLO_RTSP_BACKEND=%s, fallback to gstreamer', backend)
        backend = 'gstreamer'

    if backend == 'gstreamer':
        if Gst is None or GstRtspServer is None:
            logger.warning('GStreamer RTSP unavailable, fallback to FFmpeg')
            return FFmpegRtspOutput(config)
        return GStreamerRtspOutput(config)

    return FFmpegRtspOutput(config)


def _parse_rtsp_url(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or '0.0.0.0'
    port = parsed.port or 8555
    path = parsed.path or '/yolo'
    if not path.startswith('/'):
        path = '/' + path
    return host, port, path


def _resolve_server_bind_host(host: str) -> str:
    """Resolve RTSP server bind address from the advertised output host."""
    normalized = (host or '').strip().lower()
    if normalized in {'127.0.0.1', 'localhost', '::1'}:
        return host
    return '0.0.0.0'


def _build_caps(config: RtspOutputConfig) -> str:
    return (
        f'video/x-raw,format=BGR,width={config.width},height={config.height},'
        f'framerate={max(1, config.fps)}/1'
    )


def _build_gst_launch(config: RtspOutputConfig) -> str:
    caps = _build_caps(config)
    keyint = max(1, config.fps)
    encoder = _resolve_gst_encoder(config)
    encoder_props = _build_encoder_props(config, encoder, keyint)
    encoder_launch = encoder if not encoder_props else f'{encoder} {encoder_props}'
    encoder_format = _resolve_encoder_input_format(encoder)

    parts = [
        f'appsrc name=source is-live=true format=time caps={caps}',
    ]
    if config.low_latency:
        parts.append('queue max-size-buffers=1 leaky=downstream')
    parts.extend(
        [
            'videoconvert',
            f'video/x-raw,format={encoder_format}',
            encoder_launch,
            'rtph264pay name=pay0 pt=96 config-interval=1',
        ]
    )
    return ' ! '.join(parts)


def _resolve_gst_encoder(config: RtspOutputConfig) -> str:
    encoder = (config.encoder or 'x264enc').strip() or 'x264enc'
    if Gst is None:
        return encoder
    try:
        if Gst.ElementFactory.find(encoder) is None:
            logger.warning('GStreamer encoder %s not found, fallback to x264enc', encoder)
            return 'x264enc'
    except Exception:  # pragma: no cover - best effort
        logger.debug('Failed to query GStreamer encoder', exc_info=True)
    return encoder


def _build_encoder_props(config: RtspOutputConfig, encoder: str, keyint: int) -> str:
    props = (config.encoder_props or '').strip()
    requested_encoder = (config.encoder or 'x264enc').strip() or 'x264enc'
    if props and requested_encoder == encoder:
        return props
    if props and requested_encoder != encoder:
        logger.warning(
            'Discarding YOLO_GST_ENCODER_PROPS for %s after fallback to %s',
            requested_encoder,
            encoder,
        )
    if encoder != 'x264enc':
        return ''
    extra = 'rc-lookahead=0 sync-lookahead=0' if config.low_latency else ''
    pieces = [
        'speed-preset=ultrafast',
        'tune=zerolatency',
        f'key-int-max={keyint}',
        'bframes=0',
    ]
    if extra:
        pieces.append(extra)
    return ' '.join(pieces)


def _resolve_encoder_input_format(encoder: str) -> str:
    if (encoder or '').strip().lower() == 'nvh264enc':
        return 'NV12'
    return 'I420'
