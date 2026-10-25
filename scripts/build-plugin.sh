#!/bin/sh
set -eu
# Run on an aarch64 Jetson with Docker installed. Docker is a build tool here;
# neither Plex nor the encoder service needs Docker Compose.
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$repo/dist"
docker run --rm --platform linux/arm64 \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$repo:/work" -w /work alpine:3.19 \
  sh packaging/build-in-alpine.sh
