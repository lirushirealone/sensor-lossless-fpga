"""One owning thread, two batch CMA slots, one active DMA, reset-before-free."""
from dataclasses import dataclass
import math,threading,time
import numpy as np
from pynq import Overlay,allocate,MMIO,Clocks
from lz77_async_driver import LZ77AsyncDriver
import lz77_driver_support as support
from lz77_input import snapshot
from lz77_batch2_protocol import INPUT_WORDS,OUTPUT_BYTES,capacity,parse,aggregate

@dataclass(frozen=True)
class BatchTicket:
    slot:int
    owned:tuple
    words:object
    input_prepare_ns:int
    input_cma_ns:int

class BatchStatsReader(support.StatsReader):
    def read_batch(self,counts,size,sequence,members):
        if support._lib.lz77_stats_snapshot(self.pointers,5,self.output,12):raise RuntimeError('Batch GPIO snapshot rejected')
        return aggregate(self.output[0],self.output[1:11],self.output[11],counts,size,sequence,members)

class LZ77Batch2Driver(LZ77AsyncDriver):
    def __init__(self,bitfile,timeout=10.):
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Use finite positive timeout')
        self.timeout=timeout;self.failed=False;self.closed=False;self.active=False
        self._owner=threading.get_ident();self._pending=None;self._next_slot=0;self._submitted_ns=self._deadline=None
        self.overlay=Overlay(str(bitfile));self.dma=self.overlay.axi_dma_0
        self.stats=[MMIO(self.overlay.ip_dict[f'stats_{i}']['phys_addr'],4096) for i in range(5)]
        self._stats_reader=BatchStatsReader([m.array for m in self.stats])
        self.sequence=self.stats[3].read(0);self.block_sequence=0;self.clock_mhz=float(Clocks.fclk0_mhz)
        if self.sequence:raise RuntimeError('Batch driver requires a freshly reset overlay')
        self._slots=[]
        try:
            for i in range(2):
                inp=allocate(shape=(INPUT_WORDS,),dtype=np.uint32)
                self._slots.append(dict(input=inp,output=None,ticket=None,state='free'))
                self._slots[-1]['output']=allocate(shape=(OUTPUT_BYTES,),dtype=np.uint8)
        except BaseException:
            # No transfer has been submitted by this fresh driver.
            for slot in self._slots:
                for name in ('input','output'):
                    if slot[name] is not None:slot[name].freebuffer()
            self.closed=True;raise
        self.input=self._slots[0]['input'];self.output=self._slots[0]['output']

    def _slot(self,ticket,state):
        self.check()
        if type(ticket) is not BatchTicket or not 0<=ticket.slot<2:raise ValueError('Foreign batch ticket')
        slot=self._slots[ticket.slot]
        if slot['ticket'] is not ticket or slot['state']!=state:raise ValueError('Stale/wrong-state batch ticket')
        return slot

    def prepare(self,values):return self.prepare_group((values,))

    def prepare_group(self,values):
        self.check();values=tuple(values)
        if not 1<=len(values)<=2:raise ValueError('Use one/two members')
        available=[i for i in (self._next_slot,1-self._next_slot) if self._slots[i]['state']=='free']
        if not available:raise RuntimeError('Both batch slots owned')
        index=available[0];slot=self._slots[index];start=time.perf_counter_ns();owned=tuple(snapshot(v) for v in values)
        words=np.empty(1+sum(1+len(o.samples) for o in owned),dtype='<u4');words[0]=0x4c420000|len(owned);p=1
        for o in owned:
            n=len(o.samples);words[p]=0x4c5a0000|n;words[p+1:p+n+1]=o.samples.view('<u2');p+=n+1
        words.flags.writeable=False;prepared=time.perf_counter_ns();slot['input'][:len(words)]=words;copied=time.perf_counter_ns()
        ticket=BatchTicket(index,owned,words,prepared-start,copied-prepared)
        slot.update(ticket=ticket,state='prepared');self._next_slot=1-index;return ticket

    def submit(self,ticket):
        slot=self._slot(ticket,'prepared')
        if self._pending is not None or self.active:raise RuntimeError('Only one active DMA')
        size=capacity(tuple(len(o.samples) for o in ticket.owned))
        self._pending=ticket;slot['state']='active';self.active=True
        self._submitted_ns=time.perf_counter_ns();self._deadline=time.monotonic()+self.timeout
        try:
            self.dma.recvchannel.transfer(slot['output'][:size]);self.dma.sendchannel.transfer(slot['input'][:len(ticket.words)])
        except BaseException:self.abort();raise

    def finish(self,ticket):
        slot=self._slot(ticket,'active')
        if self._pending is not ticket or not self.active:raise RuntimeError('Wrong pending batch')
        counts=tuple(len(o.samples) for o in ticket.owned)
        try:
            self._wait();done=time.perf_counter_ns();self.active=False;size=self.dma.recvchannel.transferred
            if not 108<=size<=sum(8*n+72 for n in counts)+32 or size%4:raise RuntimeError('Wrong batch receive length')
            data=memoryview(slot['output'])[:size].tobytes();seq=(self.sequence+1)&0xffffffff
            members,envelope=parse(data,counts,seq,self.block_sequence)
            profile=self._stats_reader.read_batch(counts,size,seq,members)
            for member,o in zip(members,ticket.owned):
                if member[2]['raw_crc32']!=o.raw_crc32:raise RuntimeError('Batch source CRC mismatch')
            profile.update(envelope,dma_ns=done-self._submitted_ns,input_dma_bytes=ticket.words.nbytes,output_dma_bytes=size,
                clock_mhz=self.clock_mhz,input_prepare_ns=ticket.input_prepare_ns,input_cma_ns=ticket.input_cma_ns,
                submit_ns=self._submitted_ns,completion_observed_ns=done,slot=ticket.slot,
                retained_input_snapshot_bytes=sum(len(o.raw) for o in ticket.owned)+ticket.words.nbytes,
                dma_timing='Shared batch submit to host completion; may overlap previous batch ARM encoding. Do not charge once per member.')
            results=[]
            for i,(m,o) in enumerate(zip(members,ticket.owned)):
                packet,tokens,meta,p=m
                p.update(clock_mhz=self.clock_mhz,source_crc_checked=True,retained_input_snapshot_bytes=6*len(o.samples)+4,
                    batch_sequence=seq,batch_index=i,batch_members=len(members),input_preparation='owned_bulk_int16_batch2',
                    input_prepare_ns=0,shared_costs='Preparation/DMA/CMA belong to enclosing batch, not this member.')
                results.append(((packet,tokens,meta,p),o.raw))
            self.sequence=seq;self.block_sequence=(self.block_sequence+len(counts))&0xffffffff
            slot.update(state='free',ticket=None);self._pending=None;self._submitted_ns=self._deadline=None
            return results,data,profile
        except BaseException:self.abort();raise

    def transform_group(self,values):
        self.check()
        if any(s['state']!='free' for s in self._slots):raise RuntimeError('Finish staged work first')
        t=self.prepare_group(values);self.submit(t);return self.finish(t)

    def transform(self,values):return self.transform_group((values,))[0][0]
