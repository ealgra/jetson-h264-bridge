#!/usr/bin/env python3
"""Read-only Jetson encoder and optional Plex codec compatibility checks."""
import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

TESTED_CODEC_DIR = 'a336ba9-16a9aaf440ee9a92b75f070b-linux-aarch64'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plex-codec-dir', type=Path,
                        help='optional Plex Codecs/<hash>-linux-aarch64 directory')
    parser.add_argument('--socket', type=Path, help='optional running service socket')
    parser.add_argument('--gst-registry', help='dedicated GStreamer registry path')
    args = parser.parse_args()
    checks = []

    def check(label, okay, detail):
        checks.append(okay)
        print('{} {}: {}'.format('PASS' if okay else 'FAIL', label, detail))

    machine = platform.machine()
    check('architecture', machine == 'aarch64', machine)
    inspector = shutil.which('gst-inspect-1.0')
    check('GStreamer inspector', bool(inspector), inspector or 'not installed')
    if inspector:
        env = os.environ.copy()
        if args.gst_registry:
            env['GST_REGISTRY'] = args.gst_registry
        for element in ('nvvidconv', 'nvv4l2h264enc', 'h264parse'):
            result = subprocess.run([inspector, element], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, env=env)
            check(element, result.returncode == 0,
                  'available' if result.returncode == 0 else 'not available in this registry')
    try:
        import gi  # noqa: F401 - tested for host Python/PyGObject availability
        gi_ok = True
    except ImportError:
        gi_ok = False
    check('Python GI', gi_ok, 'available' if gi_ok else 'not installed for this Python')
    if args.plex_codec_dir:
        p = args.plex_codec_dir
        okay = p.is_dir() and p.name == TESTED_CODEC_DIR and (p / 'liblibx264_encoder.so').is_file()
        check('Plex codec ABI', okay,
              'tested build' if okay else 'unsupported or missing codec directory: {}'.format(p))
    if args.socket:
        try:
            mode = args.socket.stat().st_mode
            okay = stat.S_ISSOCK(mode)
        except OSError:
            okay = False
        check('service socket', okay, str(args.socket))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    sys.exit(main())
