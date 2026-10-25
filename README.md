# Jetson H.264 encoder bridge for Plex (experimental)

An encoding-only bridge between Plex's aarch64 Transcoder and the H.264 video
encoder on NVIDIA Jetson. Plex decodes and filters video as usual. A small
FFmpeg codec adapter sends packed I420 frames over a local Unix socket to a
Jetson-native Python/GStreamer service, which returns H.264 packets using
`nvvidconv` and `nvv4l2h264enc`. CUDA and desktop `libnvidia-encode` are not
required. This is independent of Docker Compose and of how Plex is launched.

**Status:** validated on one Xavier NX with L4T 32.6.1 and Plex
1.43.5.11029, transcoder build `a336ba9-16a9aaf440ee9a92b75f070b` and
libavcodec 60.31.102. Other Plex builds are refused by the adapter. AV1
decoding remains on the CPU. Client playback, HDR behavior, simultaneous
sessions and reboot ordering have not been fully validated. When the
`libx264` replacement is active there is no automatic software fallback.
See [compatibility](docs/compatibility.md) and [validation](docs/validation.md).

## Components

| Component | Purpose |
| --- | --- |
| `src/service/encoder_service.py` | Persistent local socket service using Jetson GStreamer plugins. |
| `src/plex/plugin.c` | Plex FFmpeg adapter; registers `jetson_h264` and optionally replaces `libx264`. |
| `src/protocol/` | Encoding-only wire format and C framing helpers. |
| `tools/encode_raw.py` | Plex-free client for testing the service with I420 input. |
| `tools/gst_encode_raw.py` | Direct GStreamer encoder check, independent of the socket service. |

The boundary between the adapter and service is documented in
[architecture.md](docs/architecture.md). The optional
[systemd example](packaging/systemd/jetson-h264.service) supervises the
service; any other supervisor can launch the same command.

## Start with the encoder, without Plex

On a Jetson with its multimedia GStreamer plugins and Python GI installed:

```sh
mkdir -p /tmp/jetson-h264
sudo python3 tools/preflight.py \
  --gst-registry /tmp/jetson-h264/gst-registry.bin
sudo env GST_REGISTRY=/tmp/jetson-h264/gst-registry.bin \
  python3 src/service/encoder_service.py \
  --socket /tmp/jetson-h264/encoder.sock --sessions 0
```

In another terminal, generate two seconds of raw I420 and encode it through
the socket. The socket is owner-only unless `--socket-gid` is supplied:

```sh
ffmpeg -v error -f lavfi -i testsrc2=size=1280x720:rate=30 \
  -frames:v 60 -pix_fmt yuv420p -f rawvideo /tmp/jetson-h264/input.i420
sudo python3 tools/encode_raw.py --socket /tmp/jetson-h264/encoder.sock \
  --input /tmp/jetson-h264/input.i420 --output /tmp/jetson-h264/output.h264 \
  --width 1280 --height 720 --fps 30 --bitrate 6000000
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=codec_name,nb_read_frames /tmp/jetson-h264/output.h264
```

This tests the Jetson service and wire protocol before involving Plex. The
GStreamer registry can be set with `GST_REGISTRY` if the normal registry has
stale blacklisted NVIDIA plugins.

## Plex adapter

`scripts/build-plugin.sh` builds the aarch64 musl plugin in a disposable
Alpine container; Docker is used only for the build. The resulting source
artifact is `dist/libjetson_h264_plex.so`. Plex's bundled Transcoder uses musl,
so a normal glibc build is not interchangeable. The script needs an aarch64
Docker host and an internet connection to fetch pinned FFmpeg 6.1 headers.
Run `sudo sh scripts/build-plugin.sh` if your account cannot access Docker.

Run `tools/preflight.py --plex-codec-dir PATH` against the exact Plex codec
directory before installing. A Plex administrator must then make the plugin
available in place of Plex's `liblibx264_encoder.so` (after backing it up),
expose the local Unix
socket to the Plex process, and set:

```text
JETSON_ENCODER_SOCKET=/path/visible/to/plex/encoder.sock
JETSON_REPLACE_X264=1
```

How those files and variables reach Plex depends on the installation. No
Compose file or Plex container image is provided or modified by this project.
Record a rollback path before replacing the codec file. The adapter only
registers on the tested Plex ABI; do not carry its binary
forward across Plex updates without rebuilding and validating it. See
[compatibility.md](docs/compatibility.md).

Plex's `-crf` option is accepted but this prototype uses the explicit
`-maxrate` value as the hardware bitrate. `-preset` and `-x264opts` are
accepted but not translated. Output quality and size can therefore differ
from software `libx264` at the same Plex settings.

## Tests and licensing

`python3 -m unittest discover -s tests -p 'test_wire.py'` checks the wire
layout and framing without Jetson hardware. On a Jetson,
`python3 tests/test_gst_encoder.py OUTPUT_DIRECTORY` tests the direct
GStreamer encoder, including decoding the complete result. Hardware tests
need FFmpeg/ffprobe and working Jetson multimedia plugins.

The protocol and Plex adapter are adapted from
[tpmullan/plex-nvenc-rpc](https://github.com/tpmullan/plex-nvenc-rpc).
The entire repository is licensed under GPL-2.0. See
[NOTICE.md](NOTICE.md) and [LICENSE](LICENSE).
