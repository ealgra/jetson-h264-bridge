# Provenance

`src/protocol/ipc_protocol.h` and `src/protocol/ipc_io.h` are adapted from
[tpmullan/plex-nvenc-rpc](https://github.com/tpmullan/plex-nvenc-rpc).
`src/plex/plugin.c` adapts that project's external-codec registration
approach and is limited here to Jetson H.264 encoding. The upstream source
and license are available in that repository. This repository is distributed
under GPL-2.0; its license text is in the root `LICENSE`. Keep the upstream
attribution when redistributing adapted files.

This is an independent experimental project. It is not affiliated with or
endorsed by Plex, NVIDIA, or the upstream project. Plex binaries, NVIDIA
libraries, FFmpeg source, and media files are not distributed here.
