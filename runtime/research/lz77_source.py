"""Verified int16 source arrays; no per-sample Python containers on the hot path.

File bytes remain immutable. Contiguous channels also use immutable bytes,
not a mutable ADC/CMA view. The frozen driver still makes its owned block
snapshot, performs source CRC, and builds the same uint32 DMA command.
"""
import hashlib
from pathlib import Path
import numpy as np
from real_data_common import parse_header

ROOT=Path(__file__).resolve().parents[1]


def _verified(path,expected):
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Source SHA256 mismatch')
    return data


def load_case_arrays(case,organization='contiguous',root=None):
    if organization not in ('strided','contiguous'):raise ValueError('Unknown organization')
    folder=Path(root or ROOT)/case['data_directory'];item=case['source_item']
    if case['layout']=='wfdb16':
        header=folder/item['header'];_verified(header,item['header_sha256'])
        info=parse_header(header)
        raw=_verified(folder/item['data'],item['data_sha256'])
        stride=info['signal_count'];length=info['samples_per_channel']
        indices=item['channel_indices']
        if len(indices)!=4:raise ValueError('Four-channel mapping mismatch')
    elif case['layout']=='interleaved':
        raw=_verified(folder/item['file'],item['sha256'])
        stride=case['stride'];length=item['samples_per_channel'];indices=range(stride)
    else:raise ValueError('Unsupported source layout')
    if type(stride) is not int or type(length) is not int or not 1<=stride<=64 or length<1:
        raise ValueError('Invalid source shape')
    if len(raw)!=2*stride*length:raise ValueError('Source sample length mismatch')
    matrix=np.frombuffer(raw,dtype='<i2').reshape(length,stride)
    if case['layout']=='wfdb16':
        # Validate ALL signals, including channels not selected by the mapping.
        for i,signal in enumerate(info['signals']):
            values=matrix[:,i]
            if int(values[0])!=signal['initial'] or (int(values.sum(dtype=np.int64))&65535)!=(signal['checksum']&65535):
                raise ValueError('WFDB initial value/checksum mismatch')
    channels={}
    for ch,index in enumerate(indices):
        if type(index) is not int or not 0<=index<stride:raise ValueError('Invalid channel mapping')
        values=matrix[:,index]
        if organization=='contiguous':values=np.frombuffer(values.tobytes(order='C'),dtype='<i2')
        if values.flags.writeable:raise AssertionError('Source ownership must be immutable')
        channels[ch]=values
    return channels


def blocks(channels,size=8192):
    if type(size) is not int or not 1<=size<=8192:raise ValueError('Invalid block size')
    for begin in range(0,max(map(len,channels.values()),default=0),size):
        for ch,values in sorted(channels.items()):
            part=values[begin:begin+size]
            if len(part):yield ch,begin,part
