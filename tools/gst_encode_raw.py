#!/usr/bin/env python3
"""I420 stdin -> framed H.264 stdout, using Jetson hardware only.

Output records: 4-byte big-endian JSON-header length, UTF-8 JSON header,
then exactly header['size'] payload bytes. All diagnostics go to stderr.
"""
import argparse
import json
import struct
import sys
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--bitrate', type=int, default=6000000)
    parser.add_argument('--gop', type=int, default=30)
    args = parser.parse_args()
    if (args.width <= 0 or args.height <= 0 or args.width % 2 or args.height % 2
            or args.width > 4096 or args.height > 2160
            or not 1 <= args.fps <= 120 or args.bitrate <= 0 or args.gop <= 0):
        parser.error('Require even dimensions <=4096x2160, fps 1..120, positive bitrate/GOP')

    # NVIDIA plugins write native diagnostics to fd 1. Preserve the protocol
    # channel, then redirect fd 1 itself so those messages cannot corrupt it.
    import os
    output = os.fdopen(os.dup(1), 'wb')
    os.dup2(2, 1)

    def emit(header, data=b''):
        header['size'] = len(data)
        encoded = json.dumps(header, separators=(',', ':')).encode()
        output.write(struct.pack('!I', len(encoded)) + encoded + data)
        output.flush()

    Gst.init(None)
    pipeline = Gst.parse_launch(
        'appsrc name=source format=time is-live=false block=true max-bytes=8388608 '
        'caps=video/x-raw,format=I420,width={0.width},height={0.height},framerate={0.fps}/1 '
        '! nvvidconv ! video/x-raw(memory:NVMM),format=NV12 '
        '! nvv4l2h264enc bitrate={0.bitrate} iframeinterval={0.gop} '
        'idrinterval={0.gop} num-B-Frames=0 insert-sps-pps=true '
        '! h264parse ! video/x-h264,stream-format=byte-stream,alignment=au '
        '! appsink name=sink emit-signals=true sync=false max-buffers=8 drop=false'.format(args))
    source = pipeline.get_by_name('source')
    sink = pipeline.get_by_name('sink')
    failure = []
    counts = {'input': 0, 'output': 0}

    def sample_ready(sink):
        sample = sink.emit('pull-sample')
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            failure.append('Cannot map encoded buffer')
            return Gst.FlowReturn.ERROR
        try:
            emit({'type': 'packet', 'pts_ns': int(buffer.pts),
                  'dts_ns': None if buffer.dts == Gst.CLOCK_TIME_NONE else int(buffer.dts),
                  'duration_ns': int(buffer.duration),
                  'keyframe': not bool(buffer.get_flags() & Gst.BufferFlags.DELTA_UNIT)},
                 bytes(mapped.data))
            counts['output'] += 1
        except Exception as exc:
            failure.append(str(exc))
            return Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(mapped)
        return Gst.FlowReturn.OK

    sink.connect('new-sample', sample_ready)
    frame_size = args.width * args.height * 3 // 2

    def feed():
        try:
            while True:
                data = bytearray()
                while len(data) < frame_size:
                    chunk = sys.stdin.buffer.read(frame_size - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if not data:
                    break
                if len(data) != frame_size:
                    raise ValueError('Truncated I420 frame: {} of {} bytes'.format(len(data), frame_size))
                frame = counts['input']
                buffer = Gst.Buffer.new_allocate(None, frame_size, None)
                # PyGObject marshals bytearray element-by-element on this
                # JetPack version. Immutable bytes take its fast bulk path.
                buffer.fill(0, bytes(data))
                buffer.pts = frame * Gst.SECOND // args.fps
                buffer.duration = (frame + 1) * Gst.SECOND // args.fps - buffer.pts
                if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
                    raise RuntimeError('Hardware pipeline rejected input')
                counts['input'] += 1
        except Exception as exc:
            failure.append(str(exc))
        finally:
            source.emit('end-of-stream')

    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Cannot start hardware pipeline')
        thread = threading.Thread(target=feed, daemon=True)
        thread.start()
        bus = pipeline.get_bus()
        while True:
            message = bus.timed_pop_filtered(Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message is None:
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                raise RuntimeError('{} ({})'.format(error, debug))
            break
        if failure:
            raise RuntimeError('; '.join(failure))
        if counts['input'] != counts['output']:
            raise RuntimeError('Frame count mismatch: {}'.format(counts))
        emit({'type': 'eos', 'frames': counts['output'], 'encoder': 'nvv4l2h264enc'})
        print('Hardware encode complete: {}'.format(counts), file=sys.stderr)
    finally:
        pipeline.set_state(Gst.State.NULL)
        output.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ENCODER ERROR: {}'.format(exc), file=sys.stderr)
        sys.exit(1)
