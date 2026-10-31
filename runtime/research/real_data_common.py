"""Exact WFDB format-16 loading and block-local, equally framed baselines."""
import array
import hashlib
import struct
import sys
import zlib
from pathlib import Path

from codec import HEADER, raw_bytes, encode_block, decode_block, map_delta

METHODS=('raw','fixed_rice_8','adaptive_rice','zlib_6','delta_zlib_6')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def parse_header(path):
    lines=[line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines()
           if line.strip() and not line.startswith('#')]
    first=lines[0].split(); count=int(first[1]); rate=float(first[2]); length=int(first[3])
    signals=[]
    for line in lines[1:1+count]:
        fields=line.split()
        if fields[1]!='16': raise ValueError('Only unmultiplexed WFDB format 16 is supported')
        signals.append(dict(file=fields[0],name=fields[-1].rstrip(','),
                            initial=int(fields[5]),checksum=int(fields[6]),gain=fields[2]))
    if len(signals)!=count or len({s['file'] for s in signals})!=1:
        raise ValueError('Expected one interleaved waveform file')
    return dict(record=first[0],signal_count=count,sample_rate_Hz=rate,
                samples_per_channel=length,signals=signals)

def load_record(item,directory):
    directory=Path(directory)
    header=directory/item['header']; binary=directory/item['data']
    if digest(header)!=item['header_sha256'] or digest(binary)!=item['data_sha256']:
        raise ValueError('Dataset SHA-256 mismatch')
    info=parse_header(header); values=array.array('h',binary.read_bytes())
    if sys.byteorder!='little': values.byteswap()
    stride=info['signal_count']; length=info['samples_per_channel']
    if len(values)!=stride*length: raise ValueError('WFDB sample length mismatch')
    for i,s in enumerate(info['signals']):
        data=values[i::stride]
        if data[0]!=s['initial'] or (sum(data)&0xffff)!=(s['checksum']&0xffff):
            raise ValueError('WFDB initial value/checksum mismatch')
    channels={ch:list(values[index::stride]) for ch,index in enumerate(item['channel_indices'])}
    if len(channels)!=4 or any(len(v)!=length for v in channels.values()):
        raise ValueError('Four-channel mapping mismatch')
    return channels

def delta_bytes(values):
    residual=[values[0]]+[((b-a+32768)&0xffff)-32768 for a,b in zip(values,values[1:])]
    return raw_bytes(residual)

def encode_method(values,method):
    if method not in METHODS: raise ValueError('Unknown baseline')
    raw=raw_bytes(values); count=len(values)
    if not 1<=count<=256: raise ValueError('Block size out of range')
    if method=='adaptive_rice': return encode_block(values)
    magic=b'SDC1'; mode=0; k=0; payload=raw
    if method=='fixed_rice_8':
        mapped=[map_delta(b-a) for a,b in zip(values,values[1:])]; fixed=8
        size=2+(sum((u>>fixed)+1+fixed for u in mapped)+7)//8
        if size<len(raw):
            bits=''.join('0'*(u>>fixed)+'1'+format(u&255,'08b') for u in mapped)
            bits+='0'*(-len(bits)%8)
            payload=struct.pack('<h',values[0])+bytes(int(bits[i:i+8],2) for i in range(0,len(bits),8))
            mode=1; k=fixed
    elif method in ('zlib_6','delta_zlib_6'):
        magic=b'ZDC1'; k=1 if method=='zlib_6' else 2
        compressed=zlib.compress(raw if k==1 else delta_bytes(values),level=6)
        if len(compressed)<len(raw): payload=compressed; mode=1
    return HEADER.pack(magic,1,mode,k,0,count,len(payload),zlib.crc32(raw))+payload

def decode_method(frame,method):
    if method in ('raw','fixed_rice_8','adaptive_rice'): return decode_block(frame)
    magic,version,mode,k,reserved,count,size,crc=HEADER.unpack_from(frame)
    expected_k=1 if method=='zlib_6' else 2
    if (magic,version,k,reserved)!=(b'ZDC1',1,expected_k,0) or mode not in (0,1) or not 1<=count<=256:
        raise ValueError('Baseline header mismatch')
    payload=frame[HEADER.size:]
    if len(payload)!=size: raise ValueError('Baseline size mismatch')
    raw=payload if mode==0 else zlib.decompress(payload)
    if len(raw)!=2*count: raise ValueError('Baseline sample count mismatch')
    values=list(struct.unpack('<'+'h'*count,raw))
    if mode==1 and k==2:
        restored=[values[0]]
        for d in values[1:]: restored.append(((restored[-1]+d+32768)&0xffff)-32768)
        values=restored
    if zlib.crc32(raw_bytes(values))!=crc: raise ValueError('Baseline CRC mismatch')
    return values
