"""Convert AVC/HEVC configuration records and packets to Annex B."""

START = b'\x00\x00\x00\x01'


def _u16(data, pos):
    if pos + 2 > len(data):
        raise ValueError('Truncated codec configuration')
    return (data[pos] << 8) | data[pos + 1]


def parameter_sets(codec, data):
    """Return (Annex B parameter sets, NAL length width; 0 for Annex B)."""
    if not data:
        return b'', 0
    if data.startswith(START) or data.startswith(b'\x00\x00\x01'):
        return data, 0
    if data[0] != 1:
        raise ValueError('Unsupported codec configuration record')
    output = bytearray()
    if codec == 'h264':
        if len(data) < 7:
            raise ValueError('Truncated AVC configuration')
        length_size = (data[4] & 3) + 1
        pos = 6
        groups = (data[5] & 31,)
        for count in groups:
            for _ in range(count):
                size = _u16(data, pos); pos += 2
                if pos + size > len(data):
                    raise ValueError('Truncated AVC parameter set')
                output.extend(START + data[pos:pos + size]); pos += size
        if pos >= len(data):
            raise ValueError('Missing AVC picture parameter set count')
        count = data[pos]; pos += 1
        for _ in range(count):
            size = _u16(data, pos); pos += 2
            if pos + size > len(data):
                raise ValueError('Truncated AVC picture parameter set')
            output.extend(START + data[pos:pos + size]); pos += size
    elif codec == 'h265':
        if len(data) < 23:
            raise ValueError('Truncated HEVC configuration')
        length_size = (data[21] & 3) + 1
        arrays = data[22]
        pos = 23
        for _ in range(arrays):
            if pos + 3 > len(data):
                raise ValueError('Truncated HEVC NAL array')
            pos += 1  # array completeness and NAL unit type
            count = _u16(data, pos); pos += 2
            for _ in range(count):
                size = _u16(data, pos); pos += 2
                if pos + size > len(data):
                    raise ValueError('Truncated HEVC parameter set')
                output.extend(START + data[pos:pos + size]); pos += size
    else:
        raise ValueError('Unsupported codec')
    if not output:
        raise ValueError('No codec parameter sets')
    return bytes(output), length_size


def packet_to_annexb(data, length_size):
    """Convert one length-prefixed access unit; pass Annex B through."""
    if not length_size:
        return data
    output = bytearray()
    pos = 0
    while pos < len(data):
        if pos + length_size > len(data):
            raise ValueError('Truncated NAL length')
        size = int.from_bytes(data[pos:pos + length_size], 'big'); pos += length_size
        if not size or pos + size > len(data):
            raise ValueError('Invalid length-prefixed NAL')
        output.extend(START + data[pos:pos + size]); pos += size
    return bytes(output)
