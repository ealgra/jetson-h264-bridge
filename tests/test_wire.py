"""Protocol checks that do not require Plex or a Jetson."""
import ctypes
import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import encode_raw


class WireTest(unittest.TestCase):
    def test_init_layout_matches_the_existing_adapter(self):
        self.assertEqual(ctypes.sizeof(encode_raw.Init), 80)
        req = encode_raw.Init(codec_id=0, width=1920, height=1080,
                              pix_fmt=0, bit_rate=8000000, crf=-1)
        self.assertEqual(struct.unpack_from('<q', bytes(req), 16)[0], 8000000)
        self.assertEqual(struct.unpack_from('<f', bytes(req), 64)[0], -1)

    def test_framing_round_trip(self):
        left, right = socket.socketpair()
        try:
            encode_raw.send(left, 2, b'frame')
            self.assertEqual(encode_raw.receive(right), (2, b'frame'))
            encode_raw.send(right, 8)
            self.assertEqual(encode_raw.receive(left), (8, b''))
        finally:
            left.close()
            right.close()


if __name__ == '__main__':
    unittest.main()
