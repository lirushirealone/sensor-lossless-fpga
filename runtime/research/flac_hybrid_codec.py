"""FHC1: frozen HSC1 modes 0..5 plus mode6 FLAC-style subframe.

Software only; complete frame size is compared, ties retain existing HSC1.
"""
import struct
import zlib

import flac_style_codec as flac
import hybrid_codec as existing

MAGIC = b'FHC1'
PREFIX,HEADER = flac.PREFIX,flac.HEADER


def repack(fields,payload):
    prefix = PREFIX.pack(*fields[:9])
    return prefix+struct.pack('<I',zlib.crc32(prefix))+payload


def choose_frames(old,new):
    a = existing.header(old)
    b = flac.header(new)
    if len(old) != 24+a[7] or len(new) != 24+b[7]:
        raise ValueError('Incomplete hybrid candidate')
    if tuple(a[i] for i in (2,5,6,8)) != tuple(b[i] for i in (2,5,6,8)):
        raise ValueError('Candidates differ in original channel/sequence/count/CRC')
    if len(new) < len(old):
        fields,payload = list(b),new[24:]
        fields[3] = 6 if b[3] else 0
    else:
        fields,payload = list(a),old[24:]
    fields[0] = MAGIC
    return repack(fields,payload)


def encode_block(values,channel=0,sequence=1):
    return choose_frames(existing.encode_block(values,channel,sequence),
                         flac.encode_block(values,channel,sequence))


def decode_block(frame):
    if len(frame) < 24:
        raise ValueError('Truncated FHC1 header')
    fields = list(HEADER.unpack_from(frame))
    if fields[0] != MAGIC or zlib.crc32(frame[:20]) != fields[9]:
        raise ValueError('Invalid FHC1 metadata/CRC')
    if fields[3] == 6:
        fields[0],fields[3] = flac.MAGIC,1
        reference = flac.decode_block(repack(fields,frame[24:]))
        return reference.channel,reference.sequence,reference.samples,'flac_'+reference.stats['kind']
    fields[0] = existing.MAGIC
    reference = existing.decode_block(repack(fields,frame[24:]))
    return reference.channel,reference.sequence,reference.samples,existing.MODE_NAMES[reference.mode]


def split_records(data):
    sequences = [0]*4
    offset = 0
    while offset < len(data):
        if len(data)-offset < 24:
            raise ValueError('Truncated FHC1 stream header')
        fields = HEADER.unpack_from(data,offset)
        end = offset+24+fields[7]
        if end > len(data):
            raise ValueError('Truncated FHC1 payload')
        channel,number,values,method = decode_block(data[offset:end])
        if number != (sequences[channel]+1) & 0xffffffff:
            raise ValueError('Missing/duplicate/reordered FHC1 block')
        sequences[channel] = number
        yield channel,number,values,method,data[offset:end]
        offset = end


def decode_channels(data,expected_counts):
    if len(expected_counts) != 4 or any(type(n) is not int or n < 0 for n in expected_counts):
        raise ValueError('Expected four nonnegative sample counts')
    restored = {ch:[] for ch in range(4)}
    for channel,_,values,_,_ in split_records(data):
        restored[channel].extend(values)
    if [len(restored[ch]) for ch in range(4)] != list(expected_counts):
        raise ValueError('Missing whole FHC1 block(s)')
    return restored
