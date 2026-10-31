"""LZB1 bounded envelope. Every member remains an independent LZT1 packet."""
import struct,zlib
import numpy as np
from lz77_protocol import TRAILER
from lz77_driver_support import NAMES,checked_stats
FOOTER=struct.Struct('<4s7I');COUNTERS=struct.Struct('<10I')
INPUT_WORDS=16387;OUTPUT_BYTES=131264;CMA_BYTES=393624

def capacity(counts):
    counts=tuple(counts)
    if not 1<=len(counts)<=2 or any(type(n) is not int or not 1<=n<=8192 for n in counts):raise ValueError('Use one/two bounded int16 blocks')
    return ((sum(8*n+72 for n in counts)+32+63)//64)*64

def parse(data,counts,batch_sequence,block_sequence):
    """Accept owned bytes ONLY. All returned views retain that immutable owner."""
    counts=tuple(counts);capacity(counts)
    if type(data) is not bytes or len(data)%4 or not 108<=len(data)<=sum(8*n+72 for n in counts)+32:raise ValueError('Invalid owned batch extent')
    f=FOOTER.unpack_from(data,len(data)-32);view=memoryview(data)
    if f[:3]!=(b'LZB1',1|(len(counts)<<8),batch_sequence) or f[5]!=1+sum(n+1 for n in counts):raise ValueError('Batch identity/input words')
    if zlib.crc32(view[-32:-4])!=f[7] or zlib.crc32(view[:-32])!=f[6]:raise ValueError('Batch integrity')
    lengths=f[3:5]
    if len(counts)==1 and lengths[1]!=0 or sum(lengths)+40*len(counts)+32!=len(data):raise ValueError('Batch directory extent')
    out=[];offset=0
    for i,n in enumerate(counts):
        size=lengths[i];seq=(block_sequence+i+1)&0xffffffff
        if not 36<=size<=8*n+32 or size%4:raise ValueError('Member extent')
        packet=view[offset:offset+size];t=TRAILER.unpack_from(packet,size-32);length=size-32
        if t[:5]!=(b'LZT1',1,0,n,seq) or t[5]*4!=length or not 1<=t[5]<=2*n:raise ValueError('Member identity')
        if zlib.crc32(packet[-32:-4])!=t[9] or zlib.crc32(packet[:length])!=t[8]:raise ValueError('Member integrity')
        stats=checked_stats(seq,COUNTERS.unpack_from(data,offset+size),seq,n,size,seq)
        if not 0<=stats['output_stalls']<stats['total_cycles']:raise ValueError('Member stall bounds')
        meta=dict(samples=n,sequence=seq,tokens=t[5],raw_crc32=t[6],residual_adler32=t[7],token_crc32=t[8])
        tokens=np.frombuffer(data,dtype='<u4',count=t[5],offset=offset)
        out.append((packet,tokens,meta,stats));offset+=size+40
    return out,dict(batch_sequence=batch_sequence,members=len(counts),input_words=f[5],output_bytes=len(data),payload_crc32=f[6])

def aggregate(before,values,after,counts,size,sequence,members):
    row=dict(zip(NAMES,values));count=sum(counts)
    if before!=after or row['sequence']!=sequence or row['total_cycles']!=row['collect_cycles']+row['process_cycles']:raise RuntimeError('Batch statistics identity')
    if row['collect_cycles']!=count+len(counts)+1+row['input_gap_cycles'] or (row['input_samples'],row['output_bytes'])!=(count,size):raise RuntimeError('Batch statistics extent/phases')
    for name in ('collect_cycles','input_samples','input_gap_cycles','matcher_work_cycles','search_probes'):
        if row[name]!=sum(m[3][name] for m in members)+(name=='collect_cycles'):raise RuntimeError('Batch/member statistics mismatch')
    if not 0<row['matcher_work_cycles']<=row['total_cycles'] or row['total_cycles']<sum(m[3]['total_cycles'] for m in members):raise RuntimeError('Batch work cycles')
    if not sum(m[3]['output_stalls'] for m in members)<=row['output_stalls']<row['total_cycles']:raise RuntimeError('Batch stall bounds')
    return row
