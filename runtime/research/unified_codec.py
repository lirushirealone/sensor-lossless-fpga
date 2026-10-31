"""UCF1 file, UHC1 independent frames, full-cost legacy-small/large/raw selection."""
import struct,zlib
import numpy as np
from screen_block_coding import frozen
PREFIX=struct.Struct('<4sBBBBIHHI');HEADER=struct.Struct('<4sBBBBIHHII');FILE=struct.Struct('<4sBBH4Q6I')
MAGIC=b'UHC1';METHODS=('raw','previous_rice','trend_rice','previous_group4','trend_group4','delta_deflate')

def file_header(counts,group_size):
    if len(counts)!=4 or any(type(n) is not int or not 0<=n<2**64 for n in counts) or type(group_size) is not int or group_size not in (1,2):raise ValueError('Four nonnegative counts, group1/2 required')
    raw=FILE.pack(b'UCF1',1,0,64,*counts,1,8192,1024,group_size,0,0)
    return raw[:-4]+struct.pack('<I',zlib.crc32(raw[:-4]))

def parse_file_header(data):
    if len(data)!=64:raise ValueError('Truncated UCF1 header')
    f=FILE.unpack(data)
    if f[:4]!=(b'UCF1',1,0,64) or f[8:11]!=(1,8192,1024) or f[11] not in (1,2) or f[12]!=0 or zlib.crc32(data[:60])!=f[13]:raise ValueError('Invalid UCF1 metadata')
    return tuple(f[4:8]),f[11]

def residual(raw):
    u=np.frombuffer(raw,dtype='<u2');d=u.copy();np.subtract(u[1:],u[:-1],out=d[1:]);return d.tobytes()

def serialize(ch,seq,mode,param,count,crc,payload):
    p=PREFIX.pack(MAGIC,1,ch,mode,param,seq,count,len(payload),crc)
    return p+struct.pack('<I',zlib.crc32(p))+payload

def finish_job(owned,native_frames,large_payload,ch,sequences):
    """All candidates computed, even when raw/native wins. Serialize winners only.

    Legacy-small plan chooses native or delta/zlib6 per1024. Compare its SUM
    of complete24-byte frames against one full8192 LZ77 frame and one raw frame.
    Ties retain legacy-small, then raw; nonshrinking DEFLATE never beats raw.
    """
    raw=owned.raw;count=len(raw)//2
    if type(raw) is not bytes or not 1<=count<=8192 or len(raw)!=2*count or zlib.crc32(raw)!=owned.raw_crc32:raise ValueError('Invalid job ownership/CRC')
    if type(large_payload) is not bytes or not 1<=len(large_payload)<=65535:raise ValueError('Invalid large payload')
    chunks=(count+1023)//1024
    if len(native_frames)!=chunks:raise ValueError('Missing native candidate(s)')
    parts=[];trace=[];native_cost=small_cost=0
    for i,frame in enumerate(native_frames):
        original=raw[2048*i:2048*(i+1)];n=len(original)//2;crc=zlib.crc32(original)
        f=frozen._header(frame)
        if len(frame)!=24+f[7] or (f[2],f[6],f[8])!=(ch,n,crc):raise ValueError('Native candidate source mismatch')
        # Mandatory legacy comparison; matching for these small candidates is ARM.
        coded=zlib.compress(residual(original),6);small_z_bytes=24+len(coded)
        if small_z_bytes<len(frame):mode,param,payload=5,6,coded
        else:mode,param,payload=f[3],f[4],memoryview(frame)[24:]
        native_cost+=len(frame);small_cost+=24+len(payload)
        parts.append((mode,param,n,crc,payload));trace.append(dict(samples=n,native_bytes=len(frame),small_deflate_bytes=small_z_bytes,selected_mode=mode))
    costs=dict(legacy1024=small_cost,lz77=24+len(large_payload),raw=24+len(raw))
    selected=min(('legacy1024','raw','lz77'),key=lambda name:(costs[name],('legacy1024','raw','lz77').index(name)))
    if selected=='lz77':parts=[(5,6,count,owned.raw_crc32,large_payload)]
    elif selected=='raw':parts=[(0,0,count,owned.raw_crc32,raw)]
    output=[]
    for mode,param,n,crc,payload in parts:
        sequences[ch]=(sequences[ch]+1)&0xffffffff;output.append(serialize(ch,sequences[ch],mode,param,n,crc,payload))
    joined=b''.join(output)
    if len(joined)!=costs[selected] or len(joined)>24+len(raw):raise AssertionError('Final cost/raw bound')
    return joined,dict(method=selected,samples=count,frames=len(parts),bytes=len(joined),candidate_bytes=costs,native_only_bytes=native_cost,small_blocks=trace,source_crc32=owned.raw_crc32)

def frame_header(data,offset=0):
    if len(data)-offset<24:raise ValueError('Truncated UHC1 header')
    f=HEADER.unpack_from(data,offset);magic,v,ch,mode,param,seq,n,size,crc,meta=f
    if (magic,v)!=(MAGIC,1) or ch>3 or mode>5 or not 1<=n<=8192 or zlib.crc32(data[offset:offset+20])!=meta:raise ValueError('Invalid UHC1 identity/CRC')
    if mode==0:
        if param!=0 or size!=2*n:raise ValueError('Invalid raw UHC1')
    else:
        if not 2<=size<2*n or (mode in (1,2) and param>16) or (mode in (3,4) and param!=0) or (mode==5 and param!=6) or (mode<5 and n>1024):raise ValueError('Invalid UHC1 coding bounds')
    return f

def decode_frame(data):
    f=frame_header(data);n=f[6]
    if len(data)!=24+f[7]:raise ValueError('UHC1 extent')
    if f[3]==0:raw=data[24:]
    elif f[3]==5:
        try:
            dec=zlib.decompressobj();d=dec.decompress(data[24:],2*n+1)
        except zlib.error as exc:raise ValueError('Invalid DEFLATE payload') from exc
        if not dec.eof or dec.unused_data or dec.unconsumed_tail or len(d)!=2*n:raise ValueError('Bounded DEFLATE extent')
        raw=(np.cumsum(np.frombuffer(d,dtype='<u2'),dtype=np.uint64)&65535).astype('<u2').tobytes()
    else:
        p=PREFIX.pack(b'BSC1',1,*f[2:9]);b=p+struct.pack('<I',zlib.crc32(p))+data[24:]
        raw=np.asarray(frozen.decode_block(b).samples,dtype='<i2').tobytes()
    if len(raw)!=2*n or zlib.crc32(raw)!=f[8]:raise ValueError('Original sample CRC mismatch')
    return raw,f

def decode_file(data):
    if type(data) is not bytes or len(data)<64:raise ValueError('Owned UCF1 file required')
    counts,group=parse_file_header(data[:64]);sequence=[0]*4;seen=[0]*4;parts=[[] for _ in range(4)];offset=64;frames=0
    while offset<len(data):
        f=frame_header(data,offset);end=offset+24+f[7]
        if end>len(data) or seen[f[2]]+f[6]>counts[f[2]]:raise ValueError('File truncated/excess samples')
        raw,f=decode_frame(data[offset:end]);ch=f[2]
        if f[5]!=(sequence[ch]+1)&0xffffffff:raise ValueError('Missing/duplicate/reordered frame')
        sequence[ch]=f[5];seen[ch]+=f[6];parts[ch].append(raw);offset=end;frames+=1
    if tuple(seen)!=counts:raise ValueError('Missing whole frame(s)')
    return {ch:np.frombuffer(b''.join(parts[ch]),dtype='<i2') for ch in range(4)},dict(counts=counts,frames=frames,group_size=group,all_samples_and_crcs_pass=True)
