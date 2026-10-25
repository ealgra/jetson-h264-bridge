# Architecture

```text
Decode: Plex demuxer → adapter → Unix socket → parser → nvv4l2decoder
        → nvvidconv → packed I420 → adapter → Plex filters

Encode: Plex I420 frames → adapter → Unix socket → nvvidconv
        → nvv4l2h264enc / nvv4l2h265enc → parser → adapter → Plex muxer
```

The service does not depend on Plex. `tools/encode_raw.py` and
`tools/decode_stream.py` exercise the same protocol independently. Each codec
context opens its own socket session. The service accepts at most two active
sessions and rejects additional ones; this limit has not been load-tested.

The decoder converts AVC/HEVC configuration records and length-prefixed NAL
packets to Annex B before handing them to GStreamer. Decoded surfaces are
converted to tightly packed 8-bit I420. The encoder accepts that I420 layout
and returns byte-stream access units with parameter sets. Decoder packets and
frames retain the demuxer's timestamp timebase. The wire format is local-only
and unversioned (v0), not a remote network API.

The Jetson video engine handles H.264/H.265 decoding and encoding. AV1
decoding, Plex filters, raw-frame packing, socket copies, and muxing still
use the CPU. The decoder currently downconverts to 8-bit I420; it does not
preserve 10-bit precision or HDR metadata.

Plex's aarch64 Transcoder uses a musl runtime while Jetson's multimedia
libraries are provided for the host environment. The socket boundary keeps
the small Plex adapter separate from those libraries.
