# Validation on the reference Xavier NX

Measurements on 2026-10-25, 1920×1080 25 fps 10-bit AV1 source decoded with
Plex's `libdav1d`; H.264 encoding used the Jetson video engine. These are
observations from one system, not capacity guarantees.

- A direct encoder test produced 300 synthetic 1080p frames in 5.45 seconds
  (about 55 fps), and all frames decoded.
- An isolated Plex Transcoder converted 30 seconds of AV1 to H.264 in
  17.31 seconds wall time. All 750 frames decoded without timestamp warnings.
- A 10-second comparison took about 16.15 seconds with software `libx264`
  and roughly 6–8 seconds with the hardware bridge, depending on muxing/audio.
- A test container running as Plex's UID/GID used the persistent socket
  service and produced video plus audio segments.
- Live Plex returned a DASH manifest and six consecutive video fragments;
  the service logged `NVMEDIA: NVENC`. Physical-client playback was not
  observed during that test.

Record a new matrix for each Plex, Jetson Linux, and container image version.
Useful checks include first playback, seeking, cancellation, subtitles,
multiple sessions, reboot, bitrate/quality, and rollback after an update.

## Isolated H.264/H.265 codec tests

On the same Xavier NX, each explicit Plex adapter path processed a 90-frame
1280×720 8-bit sample on a separate socket and scratch files:

| Path | Result |
| --- | --- |
| `jetson_h264` encode | 90 frames; FFprobe decoded all 90 |
| `jetson_h265` encode | 90 frames; FFprobe decoded all 90 |
| `jetson_h264_dec` decode | 90 packed I420 frames, 1280×720 |
| `jetson_h265_dec` decode | 90 packed I420 frames, 1280×720 from MP4/hvcC |

The standalone service clients also decoded 90 Annex B frames of each codec.
An isolated Plex command also decoded H.265 from MP4 through
`jetson_h265_dec` and encoded H.264 through `jetson_h264` in one pipeline;
FFprobe decoded all 90 output frames. H.264 MP4/avcC decoding separately
returned all 90 frames.
The HEVC elementary-stream probe reported `missing picture in access unit`
once while still decoding all 90 frames; this warning needs investigation before
claiming broad HEVC compatibility. These are functional checks, not throughput
benchmarks or proof of live Plex codec selection. The installed Plex setup was
not changed for these tests.
