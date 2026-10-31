"""HSC1 software-only, independent blocks with explicit codec selection.

Modes0..4 are unchanged MDC2 arithmetic; mode5 is modulo16 difference+zlib6.
Uses the block screen's isolated extended reference, not the board driver.
Both candidate outputs are measured, then the shorter complete frame wins.
"""
from dataclasses import dataclass
import struct
import zlib

from screen_block_coding import frozen, encode_delta, decode_delta

MAGIC = b'HSC1'
VERSION = 1
PREFIX = frozen.PREFIX
HEADER = frozen.HEADER
MAX_BLOCK = 1024
MODE_NAMES = ('raw','previous_rice','trend_rice','previous_group4','trend_group4','delta_deflate')


@dataclass(frozen=True)
class Record:
    channel: int
    sequence: int
    mode: int
    parameter: int
    samples: list
    frame: bytes


def _pack(fields, payload):
    prefix = PREFIX.pack(*fields[:9])
    return prefix + struct.pack('<I',zlib.crc32(prefix)) + payload


def header(data, offset=0):
    if len(data)-offset < 24:
        raise ValueError('Truncated hybrid header')
    fields = HEADER.unpack_from(data,offset)
    magic,version,ch,mode,param,seq,count,size,crc,meta = fields
    if zlib.crc32(data[offset:offset+20]) != meta:
        raise ValueError('Hybrid metadata CRC mismatch')
    if magic != MAGIC or version != VERSION or ch > 3 or mode > 5:
        raise ValueError('Unsupported hybrid format')
    if not 1 <= count <= MAX_BLOCK or size > 2*count:
        raise ValueError('Hybrid count/payload out of range')
    if mode == 0:
        if size != 2*count or param != 0:
            raise ValueError('Invalid hybrid raw block')
    else:
        if not 2 <= size < 2*count:
            raise ValueError('Invalid hybrid coded length')
        if mode in (1,2) and param > 16 or mode in (3,4) and param != 0 or mode == 5 and param != 6:
            raise ValueError('Invalid hybrid codec parameter')
    return fields


def wrap_candidate(frame):
    if len(frame) < 24:
        raise ValueError('Missing candidate frame header')
    fields = list(HEADER.unpack_from(frame))
    if zlib.crc32(frame[:20]) != fields[9] or len(frame) != 24+fields[7]:
        raise ValueError('Invalid candidate frame')
    if fields[0] == b'BSC1' and fields[1] == 1:
        frozen._header(frame)
    elif fields[0] == b'ZSC1' and fields[1] == 1:
        if fields[3] not in (0,1) or fields[4] != 6:
            raise ValueError('Invalid DEFLATE reference mode')
        fields[3],fields[4] = (5,6) if fields[3] else (0,0)
    else:
        raise ValueError('Unsupported candidate format')
    fields[0],fields[1] = MAGIC,VERSION
    result = _pack(fields,frame[24:])
    header(result)
    return result


def choose_frames(native, strong):
    a = HEADER.unpack_from(native)
    b = HEADER.unpack_from(strong)
    # Both candidates must represent the same original channel block.
    if tuple(a[i] for i in (2,5,6,8)) != tuple(b[i] for i in (2,5,6,8)):
        raise ValueError('Candidates refer to different channel blocks')
    return wrap_candidate(strong if len(strong) < len(native) else native)


def encode_block(values, channel=0, sequence=1):
    values = list(values)
    if not 1 <= len(values) <= MAX_BLOCK:
        raise ValueError('Hybrid block must contain1..1024 samples')
    native = frozen.encode_block(values,channel,sequence)
    strong = encode_delta(values,channel,sequence)
    return choose_frames(native,strong)


def decode_block(frame):
    fields = list(header(frame))
    if len(frame) != 24+fields[7]:
        raise ValueError('Hybrid payload length mismatch')
    ch,mode,param,seq = fields[2:6]
    if mode == 5:
        fields[0],fields[1],fields[3],fields[4] = b'ZSC1',1,1,6
        decode = decode_delta
    else:
        fields[0],fields[1] = b'BSC1',1
        decode = frozen.decode_block
    try:
        reference = decode(_pack(fields,frame[24:]))
    except zlib.error as error:
        raise ValueError('Invalid hybrid DEFLATE payload') from error
    return Record(ch,seq,mode,param,reference.samples,bytes(frame))


def split_records(data, sequences=None):
    seq = frozen._sequences(sequences)
    offset = 0
    while offset < len(data):
        fields = header(data,offset)
        ch,number = fields[2],fields[5]
        if number != ((seq[ch]+1)&0xffffffff):
            raise ValueError('Missing/duplicate/reordered hybrid block')
        end = offset+24+fields[7]
        if end > len(data):
            raise ValueError('Truncated hybrid payload')
        r = decode_block(data[offset:end])
        seq[ch] = number
        yield r
        offset = end


def decode_channels(data, expected_counts, sequences=None):
    if len(expected_counts) != 4 or any(type(n) is not int or n < 0 for n in expected_counts):
        raise ValueError('Provide four expected sample counts')
    restored = {ch:[] for ch in range(4)}
    for r in split_records(data,sequences):
        restored[r.channel].extend(r.samples)
    if [len(restored[ch]) for ch in range(4)] != list(expected_counts):
        raise ValueError('Incomplete hybrid channel stream')
    return restored
