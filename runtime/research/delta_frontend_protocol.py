"""Experimental DCF1 residual packet validation; not compressed output."""
import struct,zlib
TRAILER=struct.Struct('<4sBBHIIII')


def metadata(data,count,sequence):
    if not 1<=count<=8192 or len(data)!=2*count+24:
        raise ValueError('Wrong frontend packet byte count')
    f=TRAILER.unpack_from(data,2*count)
    if f[:5]!=(b'DCF1',1,0,count,sequence):
        raise ValueError('Frontend packet identity mismatch')
    if zlib.crc32(data[-24:-4])!=f[7]:
        raise ValueError('Frontend metadata CRC mismatch')
    residual=data[:-24]
    if zlib.adler32(residual)!=f[6]:
        raise ValueError('Residual DMA integrity mismatch')
    return residual,dict(samples=count,sequence=sequence,raw_crc32=f[5],residual_adler32=f[6])


def restore(residual):
    if not residual or len(residual)%2 or len(residual)>16384:
        raise ValueError('Invalid residual byte count')
    result=[];value=0
    for (d,) in struct.iter_unpack('<H',residual):
        value=(value+d)&65535
        result.append(value if value<32768 else value-65536)
    return result
