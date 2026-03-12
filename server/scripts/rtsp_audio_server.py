from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import gi  # type: ignore
except Exception as exc:
    raise SystemExit(f"gi module unavailable: {exc}") from exc

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GstRtspServer, GLib  # type: ignore


def _build_pipeline(args: argparse.Namespace) -> str:
    caps_out = (
        f"audio/x-raw,format=S16BE,channels={int(args.channels)},rate={int(args.sample_rate)}"
    )

    if args.source == "sine":
        src = f"audiotestsrc is-live=true wave=sine freq={int(args.freq)}"
        return f"{src} ! audioconvert ! audioresample ! {caps_out} ! rtpL16pay name=pay0 pt=96"

    if args.source == "mic":
        src = "autoaudiosrc"
        return f"{src} ! audioconvert ! audioresample ! {caps_out} ! rtpL16pay name=pay0 pt=96"

    if args.source == "file":
        if not args.file:
            raise SystemExit("file source requires --file")
        file_path = Path(args.file).expanduser().resolve()
        if not file_path.exists():
            raise SystemExit(f"file not found: {file_path}")
        uri = file_path.as_uri().replace('"', '\\"')
        src = f'uridecodebin uri="{uri}"'
        return f"{src} ! audioconvert ! audioresample ! {caps_out} ! rtpL16pay name=pay0 pt=96"

    raise SystemExit(f"Unknown source: {args.source}")


def _attach_loop(factory: GstRtspServer.RTSPMediaFactory, args: argparse.Namespace) -> None:
    if args.source != "file" or not args.loop:
        return
    factory.set_eos_shutdown(False)

    def _on_media_configure(_factory, media):
        pipeline = media.get_element()
        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def _on_message(_bus, message):
            if message.type == Gst.MessageType.EOS:
                pipeline.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    0,
                )
                pipeline.set_state(Gst.State.PLAYING)

        bus.connect("message", _on_message)

    factory.connect("media-configure", _on_media_configure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local RTSP audio test server (GStreamer).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8554, type=int)
    parser.add_argument("--mount", default="/audio")
    parser.add_argument("--source", default="sine", choices=["sine", "mic", "file"])
    parser.add_argument("--file", default="")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--freq", default=1000, type=int)
    parser.add_argument("--sample-rate", default=16000, type=int)
    parser.add_argument("--channels", default=1, type=int)
    args = parser.parse_args()

    Gst.init(None)
    pipeline = _build_pipeline(args)

    server = GstRtspServer.RTSPServer()
    server.set_address(str(args.host))
    server.set_service(str(args.port))

    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(pipeline)
    factory.set_shared(True)
    _attach_loop(factory, args)

    mounts = server.get_mount_points()
    mounts.add_factory(str(args.mount), factory)
    server.attach(None)

    print(f"[rtsp-audio] Ready: rtsp://{args.host}:{args.port}{args.mount}")
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        print("[rtsp-audio] Stopped")


if __name__ == "__main__":
    main()
