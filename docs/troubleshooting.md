# Troubleshooting

- Run `tools/preflight.py` with `--gst-registry` set to the same dedicated
  registry used by the service. A stale default registry can blacklist the
  NVIDIA plugins.
- Check the service log for `NVMEDIA: NVENC` and `ENCODER_INIT`. A valid socket
  alone does not prove the hardware pipeline started.
- Confirm the Plex process can traverse the socket directory and connect to
  the socket's owner/group. The service defaults to mode 0600; `--socket-gid`
  changes it to mode 0660 for that group.
- Verify the Plex codec directory hash and libavcodec version after every
  Plex update. The adapter intentionally refuses untested builds.
- On an encoder failure, restore the original `liblibx264_encoder.so` and
  remove `JETSON_REPLACE_X264=1` before stopping the service. Installation
  and rollback commands depend on how Plex is launched.
- If output size differs from software `libx264`, inspect Plex's explicit
  maxrate. This prototype uses that value as the hardware bitrate when CRF
  is requested; it does not reproduce x264 constant-quality rate control.
