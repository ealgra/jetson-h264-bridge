# Compatibility and limits

| Item | Tested configuration |
| --- | --- |
| Jetson | Xavier NX |
| Jetson Linux | L4T 32.6.1 / Ubuntu 18.04 |
| Plex | 1.43.5.11029, aarch64 |
| Plex codec build | `a336ba9-16a9aaf440ee9a92b75f070b-linux-aarch64` |
| libavcodec ABI | 60.31.102 |
| Encode input | 8-bit, tightly packed I420; even dimensions up to 4096×2160 |
| Decode input | H.264/H.265 Annex B or AVC/HEVC length-prefixed access units and configuration records |
| Encode output | H.264/H.265 Annex B access units |
| Decode output | 8-bit, tightly packed I420; even dimensions up to 4096×2160 |

The explicit codec names are validated in isolated Plex Transcoder invocations.
Automatic Plex decoder selection and live deployment have not been validated.
The decoder deliberately outputs 8-bit I420; 10-bit precision and HDR metadata
are lost. Frame size, bitrate, concurrent-session, and long-playback limits
need further testing.

The adapter rejects other Plex build hashes or libavcodec versions. A new
version needs ABI inspection, a rebuild, isolated Transcoder testing, and
live playback validation. A passing preflight check only means the tested
prerequisites are present; it does not establish broader compatibility.

CRF quality control, HDR metadata preservation, dynamic resolution, multiple
simultaneous streams, and hardware decoding are outside this validation.
The service has no software fallback. If its socket is unavailable while the
`libx264` replacement is enabled, Plex transcodes requiring that encoder fail.
