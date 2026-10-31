"""Bind existing checked DMA drivers to ONE already-loaded dual-engine overlay."""
import math,threading,time,zlib
import numpy as np
from pynq import allocate,MMIO,Clocks
from lz77_batch2_driver import LZ77Batch2Driver,BatchStatsReader,BatchTicket
from lz77_batch2_protocol import INPUT_WORDS,OUTPUT_BYTES
from lz77_input import Snapshot
from hybrid_native_driver import HybridNativeCompressor
from stream_codec import PreparedPacket
from native_stream_driver import NativeStreamDriver

class UnifiedLZDriver(LZ77Batch2Driver):
    def __init__(self,overlay,timeout=10.):
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Finite positive timeout required')
        self.timeout=timeout;self.failed=False;self.closed=False;self.active=False;self._owner=threading.get_ident()
        self._pending=None;self._next_slot=0;self._submitted_ns=self._deadline=None;self.overlay=overlay;self.dma=overlay.axi_dma_0
        self.stats=[MMIO(overlay.ip_dict[f'stats_{i}']['phys_addr'],4096) for i in range(5)];self._stats_reader=BatchStatsReader([m.array for m in self.stats])
        self.sequence=self.stats[3].read(0);self.block_sequence=0;self.clock_mhz=float(Clocks.fclk0_mhz)
        if self.sequence:raise RuntimeError('Fresh unified overlay required')
        self._slots=[]
        try:
            for i in range(2):
                self._slots.append(dict(input=allocate(shape=(INPUT_WORDS,),dtype=np.uint32),output=None,ticket=None,state='free'))
                self._slots[-1]['output']=allocate(shape=(OUTPUT_BYTES,),dtype=np.uint8)
        except BaseException:
            for s in self._slots:
                for k in ('input','output'):
                    if s[k] is not None:s[k].freebuffer()
            self.closed=True;raise
        self.input=self._slots[0]['input'];self.output=self._slots[0]['output']

    def prepare_owned(self,owned):
        self.check();owned=tuple(owned)
        if not 1<=len(owned)<=2:raise ValueError('One/two owned blocks required')
        for o in owned:
            if type(o) is not Snapshot or type(o.raw) is not bytes or type(o.samples) is not np.ndarray or o.samples.ndim!=1 or not 1<=len(o.samples)<=8192 or len(o.raw)!=2*len(o.samples) or o.samples.dtype!=np.dtype('<i2') or not o.samples.flags.c_contiguous or o.samples.flags.writeable or o.samples.base is not o.raw or zlib.crc32(o.raw)!=o.raw_crc32:raise ValueError('Untrusted snapshot')
        available=[i for i in (self._next_slot,1-self._next_slot) if self._slots[i]['state']=='free']
        if not available:raise RuntimeError('Both LZ batch slots owned')
        index=available[0];slot=self._slots[index];start=time.perf_counter_ns();words=np.empty(1+sum(len(o.samples)+1 for o in owned),dtype='<u4');words[0]=0x4c420000|len(owned);p=1
        for o in owned:
            n=len(o.samples);words[p]=0x4c5a0000|n;words[p+1:p+n+1]=o.samples.view('<u2');p+=n+1
        words.flags.writeable=False;prepared=time.perf_counter_ns();slot['input'][:len(words)]=words;copied=time.perf_counter_ns()
        ticket=BatchTicket(index,owned,words,prepared-start,copied-prepared);slot.update(ticket=ticket,state='prepared');self._next_slot=1-index;return ticket

class UnifiedRiceHardware(HybridNativeCompressor):
    def __init__(self,overlay,timeout=10.):
        self.overlay=overlay;self.dma=overlay.axi_dma_rice;self.timeout=timeout;self.failed=False;self.closed=False
        self.clock_mhz=float(Clocks.fclk1_mhz);self.stats_banks=[MMIO(overlay.ip_dict[f'rice_stats_{i}']['phys_addr'],4096) for i in range(4)]
        self.parallel_stats=MMIO(overlay.ip_dict['rice_stats_4']['phys_addr'],4096);self.last_sequence=self.stats_banks[3].read(0);self.channel_sequence=[0]*4;self.last_profile=None
        self.input=allocate(shape=(8193,),dtype=np.uint32)
        try:self.output=allocate(shape=(16640,),dtype=np.uint8)
        except BaseException:self.input.freebuffer();self.closed=True;raise
    def close(self):
        if self.closed:return
        if self.dma.mmio.read(0)&4 or self.dma.mmio.read(0x30)&4 or not all(self.dma.mmio.read(a)&3 for a in (4,0x34)):raise RuntimeError('Rice DMA not safe to free')
        self.input.freebuffer();self.output.freebuffer();self.closed=True

class UnifiedRiceDriver(NativeStreamDriver):
    def __init__(self,hardware):
        self.hardware=hardware;self.dma=hardware.dma;self.slots=[dict(input=hardware.input,output=hardware.output,state='free')]
        extra_input=None
        try:
            extra_input=allocate(shape=(8193,),dtype=np.uint32);extra_output=allocate(shape=(16640,),dtype=np.uint8)
        except BaseException:
            if extra_input is not None:extra_input.freebuffer()
            raise
        self.slots.append(dict(input=extra_input,output=extra_output,state='free'));self.cma_bytes=sum(s['input'].nbytes+s['output'].nbytes for s in self.slots)
        self.reserved_channels=hardware.channel_sequence.copy();self.reserved_sequence=hardware.last_sequence;self.active=None;self.failed=False;self.closed=False

def prepare_native(records):
    """Build existing PreparedPacket from strict owned <=1024 subblocks, no int64/list."""
    start=time.perf_counter_ns();records=tuple(records)
    if not 1<=len(records)<=8:raise ValueError('Native packet1..8 subblocks')
    for ch,o in records:
        if type(ch) is not int or not 0<=ch<4 or type(o) is not Snapshot or not 1<=len(o.samples)<=1024 or o.samples.flags.writeable or o.samples.base is not o.raw or len(o.raw)!=2*len(o.samples) or zlib.crc32(o.raw)!=o.raw_crc32:raise ValueError('Invalid native snapshot')
    samples=sum(len(o.samples) for _,o in records);validated=time.perf_counter_ns();words=np.empty(1+samples,dtype='<u4');words[0]=0x4d430000|len(records);p=1
    for ch,o in records:
        n=len(o.samples);words[p:p+n]=o.samples.view('<u2');words[p:p+n]|=ch<<16;words[p+n-1]|=1<<18;p+=n
    words.flags.writeable=False;ready=time.perf_counter_ns()
    return PreparedPacket(records,words,samples,2*samples+24*len(records),words.nbytes+sum(len(o.raw) for _,o in records),start,ready,validated-start,ready-validated)
