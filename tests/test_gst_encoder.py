#!/usr/bin/env python3
"""Run on Jetson: python3 tests/test_gst_encoder.py WORK_DIRECTORY."""
import json
import os
import struct
import subprocess
import sys
import time

d = os.path.abspath(sys.argv[1])
repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
base = ['sudo', '-n', 'env', 'GST_REGISTRY=' + d + '/registry.bin',
        'python3', repo + '/tools/gst_encode_raw.py', '--width', '1280', '--height', '720',
        '--fps', '30', '--gop', '30', '--bitrate', '4000000']

with open(d + '/helper.packets', 'wb') as output, open(d + '/helper.log', 'wb') as log:
    producer = subprocess.Popen(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                                 'testsrc2=size=1280x720:rate=30', '-frames:v', '90',
                                 '-pix_fmt', 'yuv420p', '-f', 'rawvideo', '-'], stdout=subprocess.PIPE)
    start = time.monotonic()
    helper = subprocess.Popen(base, stdin=producer.stdout, stdout=output, stderr=log)
    producer.stdout.close()
    try:
        result = helper.wait(timeout=45)
        producer_result = producer.wait(timeout=5)
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait()
        if producer.poll() is None:
            producer.kill()
            producer.wait()
    elapsed = time.monotonic() - start
assert result == 0, open(d + '/helper.log').read()
assert producer_result == 0

headers = []
with open(d + '/helper.packets', 'rb') as source, open(d + '/helper.h264', 'wb') as video:
    while True:
        length = source.read(4)
        if not length:
            break
        assert len(length) == 4
        n = struct.unpack('!I', length)[0]
        assert n < 65536, 'Protocol contaminated by native diagnostic output'
        header = json.loads(source.read(n))
        payload = source.read(header['size'])
        assert len(payload) == header['size']
        headers.append(header)
        if header['type'] == 'packet':
            video.write(payload)

packets = [h for h in headers if h['type'] == 'packet']
assert len(packets) == 90
assert headers[-1]['type'] == 'eos' and headers[-1]['frames'] == 90
pts = [p['pts_ns'] for p in packets]
assert all(b > a for a, b in zip(pts, pts[1:]))
assert all(abs((t - pts[0]) - i * 1000000000 // 30) <= 1 for i, t in enumerate(pts))
assert packets[0]['keyframe']
probe = subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                                '-show_entries', 'stream=codec_name,width,height,nb_read_frames',
                                '-of', 'json', d + '/helper.h264']).decode()
stream = json.loads(probe)['streams'][0]
assert stream['codec_name'] == 'h264' and stream['nb_read_frames'] == '90'
assert stream['width'] == 1280 and stream['height'] == 720
decode = subprocess.run(['ffmpeg', '-v', 'error', '-i', d + '/helper.h264', '-f', 'null', '-'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
assert decode.returncode == 0 and not decode.stderr, decode.stderr

# Negative input must fail explicitly; EOF with no frames must close cleanly.
bad = subprocess.run(base, input=b'bad-frame', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
assert bad.returncode != 0 and b'Truncated I420 frame' in bad.stderr, bad.stderr
empty = subprocess.run(base, input=b'', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
assert empty.returncode == 0, empty.stderr

report = {'frames': len(packets), 'keyframes': sum(p['keyframe'] for p in packets),
          'elapsed_seconds': elapsed, 'pts_first_ns': pts[0], 'pts_last_ns': pts[-1],
          'stream': stream, 'full_decode': 'passed', 'truncated_input': 'rejected',
          'empty_input': 'clean shutdown', 'encoder': 'nvv4l2h264enc'}
open(d + '/helper-test-report.json', 'w').write(json.dumps(report, indent=2))
open(d + '/packet-headers.json', 'w').write(json.dumps(headers, indent=2))
print(json.dumps(report, indent=2))
