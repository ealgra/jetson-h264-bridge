#!/usr/bin/env python3
"""Encode tightly packed I420 frames through the Jetson socket service.

This client has no Plex or GStreamer dependency. Input and output are files.
It exercises the same wire protocol that the Plex adapter uses.
"""
import argparse
import ctypes
import socket
import struct
import sys


class Init(ctypes.LittleEndianStructure):
    _fields_ = [('codec_id', ctypes.c_uint32), ('width', ctypes.c_uint32),
                ('height', ctypes.c_uint32), ('pix_fmt', ctypes.c_int32),
                ('bit_rate', ctypes.c_int64), ('gop_size', ctypes.c_uint32),
                ('framerate_num', ctypes.c_uint32), ('framerate_den', ctypes.c_uint32),
                ('timebase_num', ctypes.c_uint32), ('timebase_den', ctypes.c_uint32),
                ('rc_max_rate', ctypes.c_int64), ('rc_buffer_size', ctypes.c_int64),
                ('crf', ctypes.c_float), ('preset_len', ctypes.c_uint32),
                ('x264opts_len', ctypes.c_uint32), ('global_header', ctypes.c_uint32)]


def exact(conn, size):
    out = bytearray(size)
    view = memoryview(out)
    done = 0
    while done < size:
        count = conn.recv_into(view[done:])
        if not count:
            raise EOFError('Encoder service disconnected')
        done += count
    return bytes(out)


def send(conn, kind, payload=b''):
    conn.sendall(struct.pack('<IB', len(payload) + 1, kind) + payload)


def receive(conn):
    size = struct.unpack('<I', exact(conn, 4))[0]
    if not 1 <= size <= 32 * 1024 * 1024:
        raise ValueError('Invalid service message size')
    message = exact(conn, size)
    return message[0], message[1:]


def drain(conn, output):
    packets = 0
    while True:
        kind, data = receive(conn)
        if kind == 8:  # FRAME_DONE
            return packets
        if kind == 9:
            raise RuntimeError(data.rstrip(b'\0').decode(errors='replace'))
        if kind != 7 or len(data) < 24:
            raise ValueError('Unexpected service response {}'.format(kind))
        pts, dts, size, flags = struct.unpack_from('<qqII', data)
        if len(data) != 24 + size:
            raise ValueError('Invalid H.264 packet size')
        output.write(data[24:])
        packets += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True, help='encoder service Unix socket')
    parser.add_argument('--input', required=True, help='packed I420 raw video file')
    parser.add_argument('--output', required=True, help='Annex B H.264 output file')
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--bitrate', type=int, default=6000000)
    parser.add_argument('--gop', type=int, default=30)
    args = parser.parse_args()
    if (args.width < 16 or args.height < 16 or args.width > 4096
            or args.height > 2160 or args.width % 2 or args.height % 2
            or not 1 <= args.fps <= 120 or args.bitrate < 10000
            or args.gop < 1):
        parser.error('Unsupported dimensions, frame rate, bitrate or GOP')
    request = Init(codec_id=0, width=args.width, height=args.height, pix_fmt=0,
                   bit_rate=args.bitrate, gop_size=args.gop,
                   framerate_num=args.fps, framerate_den=1,
                   timebase_num=1, timebase_den=args.fps,
                   rc_max_rate=args.bitrate, rc_buffer_size=args.bitrate * 2,
                   crf=-1.0, preset_len=0, x264opts_len=0, global_header=0)
    frame_size = args.width * args.height * 3 // 2
    frames = packets = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(30)
        conn.connect(args.socket)
        send(conn, 1, bytes(request))
        kind, data = receive(conn)
        if kind != 5:
            raise RuntimeError('Encoder init failed: ' + data.rstrip(b'\0').decode(errors='replace'))
        with open(args.input, 'rb') as source, open(args.output, 'wb') as output:
            while True:
                frame = source.read(frame_size)
                if not frame:
                    break
                if len(frame) != frame_size:
                    raise ValueError('Partial final I420 frame')
                send(conn, 2, struct.pack('<q', frames) + frame)
                packets += drain(conn, output)
                frames += 1
            send(conn, 3)
            packets += drain(conn, output)
        send(conn, 4)
    print('encoded {} frames into {} H.264 packets'.format(frames, packets), file=sys.stderr)


if __name__ == '__main__':
    main()
