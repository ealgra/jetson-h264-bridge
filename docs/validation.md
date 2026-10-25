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
