# Architecture

```text
input video → Plex software decoder/filter → I420 frames
                                           ↓
                              Plex FFmpeg adapter (musl)
                                           ↓ local Unix socket
                              encoder service (Jetson host)
                                           ↓
                    appsrc → nvvidconv → nvv4l2h264enc → h264parse
                                           ↓ H.264 access units
                              Plex muxer → client
```

The service does not depend on Plex. `tools/encode_raw.py` can send packed
I420 frames through the same socket and save Annex B H.264. The adapter is a
thin FFmpeg boundary: it copies strided frames into I420 messages and returns
packets and global-header data to Plex. Each encoder context opens its own
socket session. The service accepts at most two concurrent sessions and
rejects additional ones; this limit has not been load-tested.

The service uses GStreamer and Jetson's integrated video engine through
`nvv4l2h264enc`. AV1 decoding, Plex filters, raw-frame packing, socket copies,
and muxing still use the CPU. The current wire format is local-only, encoding
only, and unversioned (v0). It is not a remote network API.

Plex's aarch64 Transcoder uses a musl runtime while Jetson's multimedia
libraries are provided for the host environment. The socket boundary keeps
the small Plex adapter separate from those libraries.
