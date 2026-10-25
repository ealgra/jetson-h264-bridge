#!/usr/bin/env python3
"""Bounded Unix-socket codec service; run on the Jetson host.

Implements H.264/H.265 encode and decode over a local socket. No shell
execution, network listener, or software fallback.
"""
import argparse
import ctypes
import json
import os
import re
import socket
import struct
import sys
import threading
import time
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstApp, GstVideo
from bitstream import parameter_sets, packet_to_annexb


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
    data = bytearray(size)
    view = memoryview(data)
    pos = 0
    while pos < size:
        n = conn.recv_into(view[pos:])
        if not n:
            raise EOFError('Peer disconnected')
        pos += n
    return bytes(data)


def receive(conn):
    length = struct.unpack('<I', exact(conn, 4))[0]
    if not 1 <= length <= 32 * 1024 * 1024:
        raise ValueError('Invalid message size')
    data = exact(conn, length)
    return data[0], data[1:]


def send(conn, kind, data=b''):
    conn.sendall(struct.pack('<IB', len(data) + 1, kind) + data)


class Encoder:
    def __init__(self, req):
        self.req = req
        w, h = req.width, req.height
        if (req.codec_id not in (0, 1) or req.pix_fmt != 0 or req.preset_len or req.x264opts_len
                or w % 2 or h % 2
                or not 16 <= w <= 4096 or not 16 <= h <= 2160
                or not req.timebase_num or not req.timebase_den):
            raise ValueError('Only bounded, even-sized I420 H.264/H.265 input is supported')
        self.fps_num = req.framerate_num or req.timebase_den
        self.fps_den = req.framerate_den or req.timebase_num
        if not 1 <= self.fps_num / self.fps_den <= 120:
            raise ValueError('Invalid frame rate')
        # FFmpeg leaves AVCodecContext.bit_rate at its 200 kb/s default for
        # Plex's CRF+maxrate command. In that mode, use the explicit maxrate.
        bitrate = (req.rc_max_rate if req.crf >= 0 and req.rc_max_rate > 0
                   else req.bit_rate if req.bit_rate > 0 else req.rc_max_rate)
        if not 10000 <= bitrate <= 100000000:
            raise ValueError('Prototype requires explicit bitrate or maxrate (CRF-only unsupported)')
        gop = max(1, min(req.gop_size, 600))
        codec = 'h264' if req.codec_id == 0 else 'h265'
        self.pipeline = Gst.parse_launch(
            'appsrc name=src format=time block=true max-bytes=8388608 '
            'caps=video/x-raw,format=I420,width={},height={},framerate={}/{} '
            '! nvvidconv ! video/x-raw(memory:NVMM),format=NV12 '
            '! nvv4l2{}enc bitrate={} iframeinterval={} idrinterval={} '
            'num-B-Frames=0 insert-sps-pps=true '
            '! {}parse ! video/x-{},stream-format=byte-stream,alignment=au '
            '! appsink name=sink sync=false async=false max-buffers=8 drop=false'.format(
                w, h, self.fps_num, self.fps_den, codec, bitrate, gop, gop, codec, codec))
        self.source = self.pipeline.get_by_name('src')
        self.sink = self.pipeline.get_by_name('sink')
        self.bus = self.pipeline.get_bus()
        self.frame_size = w * h * 3 // 2
        self.index = 0
        self.flushed = False
        self.metrics = {'gst_fill': 0.0, 'gst_push_pull': 0.0, 'packet_copy': 0.0}
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError('Cannot start Jetson encoder')
        print('ENCODER_INIT ' + json.dumps({'codec': codec, 'width': w, 'height': h, 'bitrate': bitrate,
              'fps': [self.fps_num, self.fps_den], 'requested_crf': req.crf,
              'rate_control': 'CBR', 'gop': gop}), flush=True)

    def check_error(self):
        message = self.bus.pop_filtered(Gst.MessageType.ERROR)
        if message:
            err, debug = message.parse_error()
            raise RuntimeError('{} ({})'.format(err, debug))

    def push(self, data, wait_packet=True):
        if len(data) != self.frame_size or self.flushed:
            raise ValueError('Invalid frame size or frame after flush')
        t = time.monotonic()
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        self.metrics['gst_fill'] += time.monotonic() - t
        buf.pts = self.index * Gst.SECOND * self.fps_den // self.fps_num
        buf.duration = (self.index + 1) * Gst.SECOND * self.fps_den // self.fps_num - buf.pts
        self.index += 1
        t = time.monotonic()
        if self.source.emit('push-buffer', buf) != Gst.FlowReturn.OK:
            raise RuntimeError('Hardware pipeline rejected frame')
        sample = self.sink.emit('try-pull-sample', 10 * Gst.SECOND) if wait_packet else None
        self.metrics['gst_push_pull'] += time.monotonic() - t
        self.check_error()
        if wait_packet and sample is None:
            raise RuntimeError('Timed out waiting for hardware packet')
        if not wait_packet:
            return None
        return self.packet(sample)

    def packet(self, sample):
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError('Cannot map packet')
        try:
            t = time.monotonic()
            packet = bytes(mapped.data)
            self.metrics['packet_copy'] += time.monotonic() - t
            return packet, not bool(buffer.get_flags() & Gst.BufferFlags.DELTA_UNIT), int(buffer.pts)
        finally:
            buffer.unmap(mapped)

    def flush(self):
        if self.flushed:
            return
        self.flushed = True
        self.source.emit('end-of-stream')

    def ready_packets(self, wait=False):
        while True:
            sample = self.sink.emit('try-pull-sample', 200 * Gst.MSECOND if wait else 0)
            if sample is None:
                self.check_error()
                return
            yield self.packet(sample)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)


class Decoder:
    def __init__(self, payload):
        if len(payload) < 16:
            raise ValueError('Short decoder init')
        codec_id, extra_size, self.tb_num, self.tb_den = struct.unpack_from('<IIII', payload)
        if codec_id not in (0, 1) or extra_size != len(payload) - 16 or extra_size > 65536:
            raise ValueError('Invalid decoder init')
        if not self.tb_num or not self.tb_den:
            raise ValueError('Invalid decoder timebase')
        codec = 'h264' if codec_id == 0 else 'h265'
        self.prefix, self.length_size = parameter_sets(codec, payload[16:])
        self.first = True
        self.flushed = False
        self.pipeline = Gst.parse_launch(
            'appsrc name=src format=time block=true max-bytes=8388608 '
            'caps=video/x-{0},stream-format=byte-stream,alignment=au '
            '! {0}parse ! nvv4l2decoder ! nvvidconv '
            '! video/x-raw,format=I420 '
            '! appsink name=sink sync=false async=false max-buffers=8 drop=false'.format(codec))
        self.source = self.pipeline.get_by_name('src')
        self.sink = self.pipeline.get_by_name('sink')
        self.bus = self.pipeline.get_bus()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError('Cannot start Jetson decoder')
        print('DECODER_INIT ' + json.dumps({'codec': codec, 'extradata': extra_size}), flush=True)

    def check_error(self):
        message = self.bus.pop_filtered(Gst.MessageType.ERROR)
        if message:
            err, debug = message.parse_error()
            raise RuntimeError('{} ({})'.format(err, debug))

    def push(self, pts, data):
        if self.flushed:
            raise ValueError('Packet after drain')
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('Oversized compressed packet')
        data = packet_to_annexb(data, self.length_size)
        if self.first:
            data = self.prefix + data
            self.first = False
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        if pts != -(1 << 63):
            buf.pts = pts * self.tb_num * Gst.SECOND // self.tb_den
        if self.source.emit('push-buffer', buf) != Gst.FlowReturn.OK:
            raise RuntimeError('Hardware decoder rejected packet')
        self.check_error()

    def flush(self):
        if not self.flushed:
            self.flushed = True
            self.source.emit('end-of-stream')

    def frames(self, wait=False):
        while True:
            sample = self.sink.emit('try-pull-sample', 200 * Gst.MSECOND if wait else 0)
            if sample is None:
                self.check_error()
                return
            info = GstVideo.VideoInfo()
            if not info.from_caps(sample.get_caps()):
                raise RuntimeError('Cannot inspect decoded frame layout')
            w, h = info.width, info.height
            if w % 2 or h % 2 or not 16 <= w <= 4096 or not 16 <= h <= 2160:
                raise ValueError('Unsupported decoded frame size')
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                raise RuntimeError('Cannot map decoded frame')
            try:
                raw = mapped.data
                packed = bytearray(w * h * 3 // 2)
                pos = 0
                for plane, row_width, rows in ((0, w, h), (1, w // 2, h // 2), (2, w // 2, h // 2)):
                    for row in range(rows):
                        start = info.offset[plane] + row * info.stride[plane]
                        packed[pos:pos + row_width] = raw[start:start + row_width]
                        pos += row_width
                pts = -(1 << 63) if buffer.pts == Gst.CLOCK_TIME_NONE else (buffer.pts * self.tb_den // (self.tb_num * Gst.SECOND))
                yield struct.pack('<qII', pts, w, h) + packed
            finally:
                buffer.unmap(mapped)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)


def decode_session(conn, payload):
    decoder = None
    initialized = False
    packets = 0
    frames = 0
    try:
        decoder = Decoder(payload)
        send(conn, 12)
        initialized = True
        while True:
            kind, payload = receive(conn)
            if kind == 11:
                if len(payload) < 12:
                    raise ValueError('Short decoder packet')
                pts, size = struct.unpack_from('<qI', payload)
                if size != len(payload) - 12:
                    raise ValueError('Invalid decoder packet length')
                if size:
                    decoder.push(pts, payload[12:])
                    packets += 1
                    for frame in decoder.frames():
                        send(conn, 14, frame)
                        frames += 1
                else:
                    decoder.flush()
                    deadline = time.monotonic() + 10
                    while True:
                        for frame in decoder.frames(wait=True):
                            send(conn, 14, frame)
                            frames += 1
                        if decoder.sink.is_eos():
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Hardware decoder drain timed out')
                send(conn, 15)
            elif kind == 4:
                break
            else:
                raise ValueError('Unsupported decoder message')
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        print('DECODER_ERROR ' + str(exc), flush=True)
        try:
            send(conn, 9 if initialized else 13, str(exc).encode()[:180] + b'\0')
        except OSError:
            pass
    finally:
        if decoder:
            decoder.close()
        print('DECODER_END ' + json.dumps({'packets': packets, 'frames': frames}), flush=True)


def session(conn):
    encoder = None
    initialized = False
    frames = 0
    metrics = {'ipc_read': 0.0, 'ipc_write': 0.0}
    start = time.monotonic()
    try:
        kind, payload = receive(conn)
        if kind == 10:
            decode_session(conn, payload)
            return
        if kind != 1 or len(payload) != ctypes.sizeof(Init):
            raise ValueError('Invalid INIT message')
        req = Init.from_buffer_copy(payload)
        # Obtain real SPS/PPS before Plex writes its muxer header. Use an
        # independent encoder so no dummy frame enters the real video stream.
        warm = Encoder(req)
        try:
            data = bytes([16]) * (req.width * req.height) + bytes([128]) * (req.width * req.height // 2)
            warm.push(data, wait_packet=False)
            warm.flush()
            packet = None
            deadline = time.monotonic() + 10
            while packet is None and not warm.sink.is_eos() and time.monotonic() < deadline:
                for ready, key, _ in warm.ready_packets(wait=True):
                    packet = ready
                    break
            if packet is None:
                raise RuntimeError('Warmup encoder returned no packet')
            units = re.split(b'\x00\x00\x00?\x01', packet)
            if req.codec_id == 0:
                header_units = [u for u in units if u and (u[0] & 31) in (7, 8)]
            else:
                header_units = [u for u in units if u and ((u[0] >> 1) & 63) in (32, 33, 34)]
            extra = b''.join(b'\x00\x00\x00\x01' + u for u in header_units)
            if not extra:
                raise RuntimeError('Encoder returned no parameter sets')
        finally:
            warm.close()
        encoder = Encoder(req)
        send(conn, 5, struct.pack('<I', len(extra)) + extra)
        initialized = True
        while True:
            t = time.monotonic()
            kind, payload = receive(conn)
            metrics['ipc_read'] += time.monotonic() - t
            if kind == 2:
                if len(payload) != 8 + encoder.frame_size:
                    raise ValueError('Invalid FRAME message')
                pts = struct.unpack_from('<q', payload)[0]
                encoder.push(payload[8:], wait_packet=False)
                t = time.monotonic()
                for packet, key, pts_ns in encoder.ready_packets():
                    denom = Gst.SECOND * req.timebase_num
                    packet_pts = (pts_ns * req.timebase_den + denom // 2) // denom
                    send(conn, 7, struct.pack('<qqII', packet_pts, packet_pts, len(packet), int(key)) + packet)
                frames += 1
                send(conn, 8)
                metrics['ipc_write'] += time.monotonic() - t
            elif kind == 3:
                encoder.flush()
                deadline = time.monotonic() + 10
                while True:
                    for packet, key, pts_ns in encoder.ready_packets(wait=True):
                        denom = Gst.SECOND * req.timebase_num
                        packet_pts = (pts_ns * req.timebase_den + denom // 2) // denom
                        send(conn, 7, struct.pack('<qqII', packet_pts, packet_pts, len(packet), int(key)) + packet)
                    if encoder.sink.is_eos():
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Hardware drain timed out')
                send(conn, 8)
            elif kind == 4:
                break
            else:
                raise ValueError('Unsupported message type')
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        print('SESSION_ERROR ' + str(exc), flush=True)
        try:
            send(conn, 9 if initialized else 6, str(exc).encode()[:180] + b'\0')
        except OSError:
            pass
    finally:
        if encoder:
            metrics.update(encoder.metrics)
            encoder.close()
        print('SESSION_END ' + json.dumps({'frames': frames, 'elapsed': time.monotonic() - start,
              'metrics': metrics}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--sessions', type=int, default=0, help='0 keeps serving')
    parser.add_argument('--socket-gid', type=int, help='group allowed to connect')
    args = parser.parse_args()
    Gst.init(None)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Refuse to overwrite another service's socket.
    listener.bind(args.socket)
    if args.socket_gid is not None:
        os.chown(args.socket, -1, args.socket_gid)
        os.chmod(args.socket, 0o660)
    else:
        os.chmod(args.socket, 0o600)
    listener.listen(8)
    limiter = threading.BoundedSemaphore(2)
    workers = []
    def serve(conn):
        try:
            with conn:
                conn.settimeout(30)
                if not limiter.acquire(False):
                    try:
                        receive(conn)
                        send(conn, 6, b'Jetson encoder is busy\0')
                    except (EOFError, OSError, ValueError):
                        pass
                    return
                try:
                    session(conn)
                finally:
                    limiter.release()
        finally:
            pass
    try:
        i = 0
        while args.sessions == 0 or i < args.sessions:
            conn, _ = listener.accept()
            worker = threading.Thread(target=serve, args=(conn,))
            worker.start()
            workers.append(worker)
            workers = [x for x in workers if x.is_alive()]
            i += 1
        for worker in workers:
            worker.join()
    finally:
        listener.close()
        os.unlink(args.socket)


if __name__ == '__main__':
    main()
