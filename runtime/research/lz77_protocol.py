"""LZT1 checked token transport, bounded independent expansion."""
import struct,zlib
import numpy as np
TRAILER=struct.Struct('<4sBBHIIIIII')


def metadata(data,count,sequence):
    if not 1<=count<=8192 or len(data)<36 or len(data)>8*count+32 or len(data)%4:
        raise ValueError('Invalid LZT1 packet size')
    f=TRAILER.unpack_from(data,len(data)-32);payload=data[:-32]
    if f[:5]!=(b'LZT1',1,0,count,sequence) or not 1<=f[5]<=2*count or len(payload)!=4*f[5]:
        raise ValueError('LZT1 identity/count mismatch')
    if zlib.crc32(data[-32:-4])!=f[9] or zlib.crc32(payload)!=f[8]:
        raise ValueError('LZT1 DMA integrity mismatch')
    return np.frombuffer(payload,dtype='<u4'),dict(samples=count,sequence=sequence,tokens=f[5],
        raw_crc32=f[6],residual_adler32=f[7],token_crc32=f[8])


def expand(tokens,expected):
    if not 1<=expected<=16384:raise ValueError('Invalid expansion bound')
    output=bytearray()
    for value in tokens:
        token=int(value)
        if token<0 or token>0xffffffff:raise ValueError('Token outside uint32')
        if token&0x80000000:
            length=(token>>16)&511;distance=token&32767
            if token&~0x81ff7fff or not 3<=length<=258 or not 1<=distance<=len(output) or len(output)+length>expected:
                raise ValueError('Invalid length/distance token')
            for _ in range(length):output.append(output[-distance])
        else:
            if token>255 or len(output)>=expected:raise ValueError('Invalid literal token')
            output.append(token)
    if len(output)!=expected:raise ValueError('Expansion count mismatch')
    return bytes(output)
