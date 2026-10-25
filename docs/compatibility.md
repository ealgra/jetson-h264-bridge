# Compatibility and limits

| Item | Tested configuration |
| --- | --- |
| Jetson | Xavier NX |
| Jetson Linux | L4T 32.6.1 / Ubuntu 18.04 |
| Plex | 1.43.5.11029, aarch64 |
| Plex codec build | `a336ba9-16a9aaf440ee9a92b75f070b-linux-aarch64` |
| libavcodec ABI | 60.31.102 |
| Input to service | 8-bit, tightly packed I420; even dimensions up to 4096×2160 |
| Output | H.264 Annex B packets |

The adapter rejects other Plex build hashes or libavcodec versions. A new
version needs ABI inspection, a rebuild, isolated Transcoder testing, and
live playback validation. A passing preflight check only means the tested
prerequisites are present; it does not establish broader compatibility.

CRF quality control, HDR metadata preservation, dynamic resolution, multiple
simultaneous streams, and hardware decoding are outside this validation.
The service has no software fallback. If its socket is unavailable while the
`libx264` replacement is enabled, Plex transcodes requiring that encoder fail.
