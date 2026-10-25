"""AVC/HEVC packet conversion checks, independent of Jetson hardware."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'service'))
from bitstream import START, parameter_sets, packet_to_annexb


class BitstreamTest(unittest.TestCase):
    def test_avcc(self):
        avcc = bytes([1, 66, 0, 30, 255, 225, 0, 3]) + b'\x67ab' + b'\x01\x00\x02\x68z'
        sets, length_size = parameter_sets('h264', avcc)
        self.assertEqual(length_size, 4)
        self.assertEqual(sets, START + b'\x67ab' + START + b'\x68z')
        self.assertEqual(packet_to_annexb(b'\x00\x00\x00\x02\x65x', length_size), START + b'\x65x')
        self.assertEqual(packet_to_annexb(b'\x00\x00\x00\x01\x09', length_size), START + b'\x09')

    def test_hvcc(self):
        hvcc = bytearray(23)
        hvcc[0] = 1; hvcc[21] = 3; hvcc[22] = 1
        hvcc += b'\xa0\x00\x01\x00\x03\x40ab'
        sets, length_size = parameter_sets('h265', bytes(hvcc))
        self.assertEqual((sets, length_size), (START + b'\x40ab', 4))

    def test_rejects_truncation(self):
        with self.assertRaises(ValueError):
            parameter_sets('h264', b'\x01\x02')
        with self.assertRaises(ValueError):
            packet_to_annexb(b'\x00\x00\x00\x05abc', 4)


if __name__ == '__main__':
    unittest.main()
