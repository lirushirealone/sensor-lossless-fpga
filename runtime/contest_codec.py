"""MDC2 software codec: four channels, independent int16 blocks, two CRCs.

This version has NOT been implemented in the FPGA. The MDC1/SDC1 codec,
driver and bitstream remain separate. See CONTEST_V2.md for the wire format.
Only standard-library modules are needed; usable from Spyder or a terminal.
"""
from dataclasses import dataclass
import struct
import zlib

from codec import raw_bytes

MAGIC = b'MDC2'
VERSION = 2
MAX_BLOCK = 256
PREFIX = struct.Struct('<4sBBBBIHHI')
HEADER = struct.Struct('<4sBBBBIHHII')
RAW, PREVIOUS_RICE, TREND_RICE, PREVIOUS_GROUP, TREND_GROUP = range(5)
MODE_NAMES = ('raw', 'previous_rice', 'trend_rice', 'previous_group4', 'trend_group4')
PROFILES = {
    'previous_rice': (PREVIOUS_RICE,),
    'trend_rice': (TREND_RICE,),
    'previous_group': (PREVIOUS_GROUP,),
    'adaptive_predictor': (PREVIOUS_RICE, TREND_RICE),
    'trend_group': (TREND_GROUP,),
    'adaptive': (PREVIOUS_RICE, TREND_RICE, PREVIOUS_GROUP, TREND_GROUP),
}


class _Writer:
    def __init__(self):
        self.buffer = bytearray()
        self.bits = 0
        self.pending = 0

    def put(self, value, width):
        if width < 0 or value < 0 or value.bit_length() > width:
            raise ValueError('Value does not fit bit field')
        self.bits = (self.bits << width) | value
        self.pending += width
        while self.pending >= 8:
            self.pending -= 8
            self.buffer.append((self.bits >> self.pending) & 255)
        self.bits &= (1 << self.pending) - 1

    def finish(self):
        if self.pending:
            self.buffer.append(self.bits << (8 - self.pending))
            self.pending = 0
            self.bits = 0
        return bytes(self.buffer)


class _Reader:
    def __init__(self, data):
        self.value = int.from_bytes(data, 'big')
        self.remaining = len(data) * 8

    def get(self, width):
        if width < 0 or width > self.remaining:
            raise ValueError('Truncated code')
        self.remaining -= width
        return (self.value >> self.remaining) & ((1 << width) - 1)

    def finish(self):
        if self.remaining >= 8:
            raise ValueError('Extra bytes after coded samples')
        if self.get(self.remaining):
            raise ValueError('Nonzero padding')


def _errors(values, trend):
    errors = []
    for i in range(1, len(values)):
        prediction = 2 * values[i - 1] - values[i - 2] if trend and i >= 2 else values[i - 1]
        prediction = max(-32768, min(32767, prediction))
        errors.append(values[i] - prediction)
    return errors


def _width(group):
    if not any(group):
        return 0
    return max((e if e >= 0 else ~e).bit_length() + 1 for e in group)


def _rice_cost(errors):
    mapped = [2 * e if e >= 0 else -2 * e - 1 for e in errors]
    costs = [sum(u >> k for u in mapped) + len(mapped) * (k + 1) for k in range(17)]
    k = min(range(17), key=costs.__getitem__)
    return costs[k], k


def _group_cost(errors):
    cost, previous = 0, None
    for begin in range(0, len(errors), 4):
        group = errors[begin:begin + 4]
        width = _width(group)
        cost += (5 if previous is None else 2 if abs(width - previous) <= 1 else 7) + width * len(group)
        previous = width
    return cost


def encode_block(samples, channel=0, sequence=1, profile='adaptive'):
    values = list(samples)
    if not 1 <= len(values) <= MAX_BLOCK:
        raise ValueError('Block must contain 1..256 samples')
    if type(channel) is not int or not 0 <= channel < 4:
        raise ValueError('Channel must be 0..3')
    if type(sequence) is not int or not 0 <= sequence <= 0xffffffff:
        raise ValueError('Sequence must be uint32')
    if profile not in PROFILES:
        raise ValueError('Unknown coding profile')
    raw = raw_bytes(values)
    # Raw wins ties; only the winning bounded payload is actually emitted.
    best_size, mode, parameter, chosen = len(raw), RAW, 0, None
    for trend in (False, True):
        modes = (TREND_RICE, TREND_GROUP) if trend else (PREVIOUS_RICE, PREVIOUS_GROUP)
        active = [m for m in modes if m in PROFILES[profile]]
        if not active:
            continue
        errors = _errors(values, trend)
        for candidate in active:
            bits, k = _rice_cost(errors) if candidate in (PREVIOUS_RICE, TREND_RICE) else (_group_cost(errors), 0)
            size = 2 + (bits + 7) // 8
            if size < best_size:
                best_size, mode, parameter, chosen = size, candidate, k, errors
    payload = raw
    if mode != RAW:
        writer = _Writer()
        if mode in (PREVIOUS_RICE, TREND_RICE):
            for error in chosen:
                u = 2 * error if error >= 0 else -2 * error - 1
                writer.put(1, (u >> parameter) + 1)  # q zeroes then one
                writer.put(u & ((1 << parameter) - 1), parameter)
        else:
            previous = None
            for begin in range(0, len(chosen), 4):
                group = chosen[begin:begin + 4]
                width = _width(group)
                if previous is None:
                    writer.put(width, 5)
                elif abs(width - previous) <= 1:
                    writer.put(width - previous + 1, 2)
                else:
                    writer.put(3, 2)
                    writer.put(width, 5)
                for error in group:
                    writer.put(error & ((1 << width) - 1), width)
                previous = width
        payload = struct.pack('<h', values[0]) + writer.finish()
        if len(payload) != best_size:
            raise AssertionError('Cost model and emitted payload differ')
    prefix = PREFIX.pack(MAGIC, VERSION, channel, mode, parameter, sequence,
                         len(values), len(payload), zlib.crc32(raw))
    return prefix + struct.pack('<I', zlib.crc32(prefix)) + payload


@dataclass(frozen=True)
class Record:
    channel: int
    sequence: int
    mode: int
    parameter: int
    samples: list
    frame: bytes


def _header(data, offset=0):
    if len(data) - offset < HEADER.size:
        raise ValueError('Truncated header')
    fields = HEADER.unpack_from(data, offset)
    magic, version, channel, mode, k, sequence, count, size, crc, metadata_crc = fields
    if zlib.crc32(data[offset:offset + PREFIX.size]) != metadata_crc:
        raise ValueError('Metadata CRC mismatch')
    if magic != MAGIC or version != VERSION or channel >= 4 or mode > TREND_GROUP:
        raise ValueError('Unsupported header')
    if not 1 <= count <= MAX_BLOCK or size > 2 * count:
        raise ValueError('Sample count or payload size out of range')
    if mode == RAW:
        if k != 0 or size != 2 * count:
            raise ValueError('Invalid raw mode')
    elif size < 2 or size >= 2 * count:
        raise ValueError('Invalid compressed size')
    elif (mode in (PREVIOUS_RICE, TREND_RICE) and k > 16) or (mode in (PREVIOUS_GROUP, TREND_GROUP) and k != 0):
        raise ValueError('Invalid coding parameter')
    return fields


def decode_block(frame):
    fields = _header(frame)
    _, _, channel, mode, k, sequence, count, size, crc, _ = fields
    if len(frame) != HEADER.size + size:
        raise ValueError('Payload length mismatch')
    payload = frame[HEADER.size:]
    if mode == RAW:
        values = list(struct.unpack('<' + 'h' * count, payload))
    else:
        values = [struct.unpack_from('<h', payload)[0]]
        reader = _Reader(payload[2:])
        errors = []
        if mode in (PREVIOUS_RICE, TREND_RICE):
            for _ in range(count - 1):
                quotient = 0
                while reader.get(1) == 0:
                    quotient += 1
                    if quotient > (131070 >> k):
                        raise ValueError('Rice quotient out of range')
                u = (quotient << k) | reader.get(k)
                if u > 131070:
                    raise ValueError('Mapped residual out of range')
                errors.append(u // 2 if not u & 1 else -(u + 1) // 2)
        else:
            width = None
            while len(errors) < count - 1:
                if width is None:
                    width = reader.get(5)
                else:
                    code = reader.get(2)
                    width = reader.get(5) if code == 3 else width + code - 1
                if not 0 <= width <= 17:
                    raise ValueError('Group width out of range')
                for _ in range(min(4, count - 1 - len(errors))):
                    u = reader.get(width)
                    error = u - (1 << width) if width and u & (1 << (width - 1)) else u
                    if not -65535 <= error <= 65535:
                        raise ValueError('Signed residual out of range')
                    errors.append(error)
        reader.finish()
        for error in errors:
            # Deliberately independent of the encoder's residual construction.
            if mode in (TREND_RICE, TREND_GROUP) and len(values) >= 2:
                estimate = 2 * values[-1] - values[-2]
                estimate = -32768 if estimate < -32768 else 32767 if estimate > 32767 else estimate
            else:
                estimate = values[-1]
            value = estimate + error
            if not -32768 <= value <= 32767:
                raise ValueError('Restored sample out of range')
            values.append(value)
    if zlib.crc32(raw_bytes(values)) != crc:
        raise ValueError('Sample CRC mismatch')
    return Record(channel, sequence, mode, k, values, bytes(frame))


def split_records(stream):
    offset = 0
    while offset < len(stream):
        fields = _header(stream, offset)
        end = offset + HEADER.size + fields[7]
        if end > len(stream):
            raise ValueError('Truncated payload')
        yield decode_block(stream[offset:end])
        offset = end


def _sequences(sequences):
    values = [0] * 4 if sequences is None else list(sequences)
    if len(values) != 4 or any(type(x) is not int or not 0 <= x <= 0xffffffff for x in values):
        raise ValueError('Provide four uint32 sequence values')
    return values


def decode_channels(stream, sequences=None, expected_counts=None):
    seq = _sequences(sequences)
    channels = {ch: [] for ch in range(4)}
    for record in split_records(stream):
        if record.sequence != ((seq[record.channel] + 1) & 0xffffffff):
            raise ValueError('Missing, duplicate or reordered channel block')
        seq[record.channel] = record.sequence
        channels[record.channel].extend(record.samples)
    if expected_counts is not None:
        counts = list(expected_counts)
        if len(counts) != 4 or any(type(n) is not int or n < 0 for n in counts):
            raise ValueError('Provide four nonnegative expected sample counts')
        if any(len(channels[ch]) != counts[ch] for ch in range(4)):
            raise ValueError('Incomplete stream: expected sample counts do not match')
    return channels, seq


def encode_channels(channels, block_size=MAX_BLOCK, profile='adaptive', sequences=None):
    if type(block_size) is not int or not 1 <= block_size <= MAX_BLOCK:
        raise ValueError('Block size must be 1..256')
    if profile not in PROFILES:
        raise ValueError('Unknown coding profile')
    if not isinstance(channels, dict) or any(type(ch) is not int or not 0 <= ch < 4 for ch in channels):
        raise ValueError('Use a dictionary of channel IDs 0..3')
    values = {ch: list(channels.get(ch, [])) for ch in range(4)}
    for samples in values.values():
        raw_bytes(samples)
    seq = _sequences(sequences)
    frames = []
    for begin in range(0, max(map(len, values.values()), default=0), block_size):
        for ch in range(4):
            block = values[ch][begin:begin + block_size]
            if block:
                seq[ch] = (seq[ch] + 1) & 0xffffffff
                frames.append(encode_block(block, ch, seq[ch], profile))
    return b''.join(frames), seq
