#!/usr/bin/env python3
"""Verify the Zero2 bootloader and run defaults checks in the actual rootfs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    candidates = list(args.tree.glob('staging_dir/target-*/image/nanopi-zero2-rk3528-u-boot-rockchip.bin'))
    if len(candidates) != 1:
        parser.error('Expected exactly one staged NanoPi Zero2 bootloader')
    bootloader = candidates[0].read_bytes()
    if b'FriendlyElec NanoPi Zero2\0' not in bootloader or b'Radxa E20C\0' in bootloader:
        raise ValueError('Bootloader model does not match NanoPi Zero2')

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / 'image.raw'
        # fwtool appends metadata after the first gzip stream.
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        with args.image.open('rb') as src, raw.open('wb') as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(decoder.decompress(chunk))
                if decoder.eof:
                    break
            if not decoder.eof:
                raise ValueError('Incomplete gzip stream')
        with raw.open('rb') as src:
            mbr = src.read(512)
            if mbr[510:512] != b'\x55\xaa':
                raise ValueError('Missing MBR')
            root_offset = struct.unpack_from('<I', mbr, 446 + 16 + 8)[0] * 512
            if root_offset == 0:
                raise ValueError('Missing rootfs partition')
            src.seek(64 * 512)
            if src.read(len(bootloader)) != bootloader:
                raise ValueError('Image bootloader differs from the Zero2 build')
        rootfs = Path(tmp) / 'root'
        # /dev is supplied by proot; extracting device nodes needs privileges.
        subprocess.run(['unsquashfs', '-no-xattrs', '-no-progress', '-excludes',
                        '-d', str(rootfs), '-o', str(root_offset), str(raw), 'dev'], check=True)
        (rootfs / 'dev').mkdir(exist_ok=True)
        project = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(project / 'tests'), '-v'],
                       env=dict(os.environ, ZERO2_TEST_ROOTFS=str(rootfs)), check=True)
    print(json.dumps({
        'bootloader_sha256': hashlib.sha256(bootloader).hexdigest(),
        'bootloader_bytes': len(bootloader),
        'image_offset': 64 * 512,
        'bootloader_matches': True,
        'defaults_tests_passed': True,
    }, indent=2))


if __name__ == '__main__':
    main()
