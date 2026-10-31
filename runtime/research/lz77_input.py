"""Owned1..8192 int16 snapshots, adapted from qualified arm_input_codec.

Exact list/tuple bulk packing and numeric ndarray vector bounds checks avoid
per-sample Python loops. Unusual integer iterables retain __index__ semantics.
No prediction, matching, token encoding or frame selection happens here.
"""
import array
from dataclasses import dataclass
import operator,sys,zlib
import numpy as np


@dataclass(frozen=True)
class Snapshot:
    raw:bytes
    samples:object
    raw_crc32:int


def snapshot(values):
    if type(values) in (list,tuple):
        if not 1<=len(values)<=8192:raise ValueError('Use1..8192 samples')
        try:packed=array.array('h',values)
        except (TypeError,OverflowError) as exc:raise ValueError('Every sample must be an int16 integer') from exc
        if packed.itemsize!=2:raise RuntimeError('Platform short must be16 bits')
        if sys.byteorder!='little':packed.byteswap()
        raw=packed.tobytes()
    elif type(values) is np.ndarray and values.dtype.kind in 'iu':
        x=values
        if x.ndim!=1 or not 1<=len(x)<=8192:raise ValueError('Use a1D array of1..8192 samples')
        safe=(x.dtype.kind=='i' and x.dtype.itemsize<=2) or (x.dtype.kind=='u' and x.dtype.itemsize==1)
        if not safe and (int(x.min()) < -32768 or int(x.max()) > 32767):raise ValueError('Every sample must fit int16')
        raw=x.astype('<i2',copy=False).tobytes(order='C')
    else:
        # NumPy bool __index__ availability differs by installed version;
        # preserve the frozen driver's local semantics through this fallback.
        owned=[]
        for value in values:
            if len(owned)==8192:raise ValueError('Use1..8192 samples')
            value=operator.index(value)
            if not -32768<=value<=32767:raise ValueError('Every sample must fit int16')
            owned.append(value)
        if not owned:raise ValueError('Use1..8192 samples')
        raw=np.asarray(owned,dtype='<i2').tobytes()
    samples=np.frombuffer(raw,dtype='<i2')
    return Snapshot(raw,samples,zlib.crc32(raw))


def command_words(owned):
    # Called on a snapshot created inside transform(), never borrowed source.
    n=len(owned.samples);words=np.empty(n+1,dtype=np.uint32)
    words[0]=0x4c5a0000|n;words[1:]=owned.samples.view('<u2')
    words.flags.writeable=False
    return words
