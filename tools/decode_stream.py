#!/usr/bin/env python3
"""Decode an elementary H.264/H.265 stream through the Jetson socket service."""
import argparse
import json
import socket
import struct
import subprocess


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError('Service closed the socket')
        data.extend(part)
    return bytes(data)


def send(sock, kind, payload=b''):
    sock.sendall(struct.pack('<IB', len(payload) + 1, kind) + payload)


def receive(sock):
    size = struct.unpack('<I', exact(sock, 4))[0]
    if not 1 <= size <= 32 * 1024 * 1024:
        raise ValueError('Invalid reply length')
    data = exact(sock, size)
    return data[0], data[1:]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket', required=True)
    p.add_argument('--input', required=True, help='Annex B elementary stream')
    p.add_argument('--output', required=True, help='Packed I420 frames')
    p.add_argument('--codec', choices=('h264', 'h265'), required=True)
    args = p.parse_args()
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_packets',
                            '-show_entries', 'packet=pos,size,pts', '-of', 'json',
                            args.input], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
    packets = json.loads(probe.stdout)['packets']
    count = 0
    dimensions = set()
    with socket.socket(socket.AF_UNIX) as sock, open(args.input, 'rb') as source, open(args.output, 'wb') as output:
        sock.connect(args.socket)
        send(sock, 10, struct.pack('<IIII', 0 if args.codec == 'h264' else 1, 0, 1, 90000))
        kind, data = receive(sock)
        if kind != 12:
            raise RuntimeError('Decoder init: ' + data.decode(errors='replace'))
        for entry in packets + [None]:
            if entry is None:
                payload = b''
                pts = -(1 << 63)
            else:
                source.seek(int(entry['pos']))
                payload = source.read(int(entry['size']))
                pts = int(entry.get('pts', 0))
            send(sock, 11, struct.pack('<qI', pts, len(payload)) + payload)
            while True:
                kind, data = receive(sock)
                if kind == 15:
                    break
                if kind != 14 or len(data) < 16:
                    raise RuntimeError('Decoder: ' + data.decode(errors='replace'))
                _, w, h = struct.unpack_from('<qII', data)
                if len(data) != 16 + w * h * 3 // 2:
                    raise ValueError('Invalid decoded frame size')
                output.write(data[16:])
                dimensions.add((w, h))
                count += 1
        send(sock, 4)
    print(json.dumps({'packets': len(packets), 'frames': count, 'dimensions': sorted(dimensions)}))


if __name__ == '__main__':
    main()
