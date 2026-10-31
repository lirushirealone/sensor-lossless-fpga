"""Version 1 reference codec: signed int16, independent blocks, Rice or raw.

This is a software correctness reference, not an FPGA implementation.
Frame: little-endian <4sBBBBHII (18 bytes), then payload.
Header fields: magic, version, mode, k, reserved, count, payload bytes, CRC32.
Rice payload: int16 first sample, then MSB-first Rice codes of mapped deltas.
Rice code: q zero bits, one bit, k remainder bits. Final padding is zero.
"""
import struct
import zlib

HEADER = struct.Struct('<4sBBBBHII')
MAGIC = b'SDC1'
MAX_BLOCK = 256


def raw_bytes(samples):
    values = list(samples)
    if any(type(x) is not int or not -32768 <= x <= 32767 for x in values):
        raise ValueError('Samples must be Python integers in signed int16 range')
    return struct.pack('<' + 'h' * len(values), *values)


def map_delta(delta):
    return 2 * delta if delta >= 0 else -2 * delta - 1


def unmap_delta(value):
    return value // 2 if value % 2 == 0 else -(value // 2) - 1


def encode_block(samples):
    values = list(samples)
    if not 1 <= len(values) <= MAX_BLOCK:
        raise ValueError('Block must contain 1..256 samples')
    raw = raw_bytes(values)
    mapped = [map_delta(b - a) for a, b in zip(values, values[1:])]
    costs = [sum((u >> k) + 1 + k for u in mapped) for k in range(17)]
    k = min(range(17), key=lambda i: costs[i])
    compressed_length = 2 + (costs[k] + 7) // 8
    if compressed_length >= len(raw):
        mode, k, payload = 0, 0, raw
    else:
        mode = 1
        # Chosen k and fallback bound the bit string to less than raw size.
        bits = ''.join('0' * (u >> k) + '1' +
                       (format(u & ((1 << k) - 1), f'0{k}b') if k else '')
                       for u in mapped)
        bits += '0' * (-len(bits) % 8)
        payload = struct.pack('<h', values[0]) + bytes(
            int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    return HEADER.pack(MAGIC, 1, mode, k, 0, len(values), len(payload),
                       zlib.crc32(raw)) + payload


def decode_block(frame):
    if len(frame) < HEADER.size:
        raise ValueError('Truncated header')
    magic, version, mode, k, reserved, count, size, crc = HEADER.unpack_from(frame)
    if magic != MAGIC or version != 1 or reserved != 0:
        raise ValueError('Unsupported or invalid frame header')
    if mode not in (0, 1) or not 1 <= count <= MAX_BLOCK or k > 16:
        raise ValueError('Invalid mode, sample count, or Rice parameter')
    if len(frame) != HEADER.size + size:
        raise ValueError('Payload length mismatch')
    payload = frame[HEADER.size:]
    if mode == 0:
        if k != 0 or size != count * 2:
            raise ValueError('Invalid raw block')
        values = list(struct.unpack('<' + 'h' * count, payload))
    else:
        if size < 2 or size >= count * 2:
            raise ValueError('Invalid compressed block size')
        values = [struct.unpack_from('<h', payload)[0]]
        bit_count, position = (size - 2) * 8, 0

        def read_bit():
            nonlocal position
            if position >= bit_count:
                raise ValueError('Truncated Rice code')
            result = (payload[2 + position // 8] >> (7 - position % 8)) & 1
            position += 1
            return result

        for _ in range(count - 1):
            q = 0
            while read_bit() == 0:
                q += 1
                if q > (131070 >> k):
                    raise ValueError('Rice quotient out of range')
            remainder = 0
            for _ in range(k):
                remainder = (remainder << 1) | read_bit()
            mapped = (q << k) | remainder
            if mapped > 131070:
                raise ValueError('Delta out of range')
            value = values[-1] + unmap_delta(mapped)
            if not -32768 <= value <= 32767:
                raise ValueError('Decoded sample out of range')
            values.append(value)
        if bit_count - position >= 8:
            raise ValueError('Extra bytes in Rice payload')
        while position < bit_count:
            if read_bit() != 0:
                raise ValueError('Nonzero padding')
    if zlib.crc32(raw_bytes(values)) != crc:
        raise ValueError('CRC mismatch')
    return values


def compress(samples, block_size=MAX_BLOCK):
    if not 1 <= block_size <= MAX_BLOCK:
        raise ValueError('Block size must be 1..256')
    values = list(samples)
    return b''.join(encode_block(values[i:i + block_size])
                    for i in range(0, len(values), block_size))


def split_frames(stream):
    offset = 0
    while offset < len(stream):
        if len(stream) - offset < HEADER.size:
            raise ValueError('Truncated stream header')
        size = HEADER.unpack_from(stream, offset)[6]
        if size > MAX_BLOCK * 2:
            raise ValueError('Frame payload exceeds block limit')
        end = offset + HEADER.size + size
        if end > len(stream):
            raise ValueError('Truncated stream payload')
        yield stream[offset:end]
        offset = end


def decompress(stream):
    return [x for frame in split_frames(stream) for x in decode_block(frame)]
