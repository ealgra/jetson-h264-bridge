#!/bin/sh
set -eu
apk add --no-cache build-base git
if [ ! -d /work/.build/ffmpeg-headers ]; then
  mkdir -p /work/.build
  git clone --depth 1 --branch n6.1 https://github.com/FFmpeg/FFmpeg.git /work/.build/ffmpeg-headers
fi
cd /work/.build/ffmpeg-headers
test "$(git rev-parse HEAD)" = d4ff0020b40b524a490cf62eccbd3a318f4c0e58 || {
  echo 'FFmpeg n6.1 checkout differs from the tested headers' >&2
  exit 1
}
./configure --disable-doc --disable-programs > /work/dist/ffmpeg-configure.log 2>&1
gcc -shared -fPIC -O2 -I. -I/work/src/protocol \
  -o /work/dist/libjetson_h264_plex.so /work/src/plex/plugin.c
chown "${HOST_UID:-0}:${HOST_GID:-0}" /work/dist/libjetson_h264_plex.so /work/dist/ffmpeg-configure.log
