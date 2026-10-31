"""Single-thread, two-slot DMA staging; at most ONE transaction in flight.

finish() owns and checks the complete packet and latched counters before a
new submit can overwrite them. Returned bytes/tokens never borrow CMA.
"""
from dataclasses import dataclass
import threading,time
import numpy as np
from pynq import allocate
from lz77_snapshot_driver import LZ77SnapshotDriver
from lz77_driver_support import metadata_view
from lz77_input import snapshot,command_words


@dataclass(frozen=True)
class Ticket:
    slot:int
    owned:object
    words:object
    input_prepare_ns:int
    input_cma_ns:int


class LZ77AsyncDriver(LZ77SnapshotDriver):
    def __init__(self,bitfile,timeout=10.0):
        super().__init__(bitfile,timeout)
        self._owner=threading.get_ident();self._pending=None
        self._slots=[dict(input=self.input,output=self.output,ticket=None,state='free')]
        extra_input=None
        try:
            extra_input=allocate(shape=(8193,),dtype=np.uint32)
            extra_output=allocate(shape=(65600,),dtype=np.uint8)
        except BaseException:
            if extra_input is not None:extra_input.freebuffer()
            super().close();raise
        self._slots.append(dict(input=extra_input,output=extra_output,ticket=None,state='free'))
        self._next_slot=0;self._submitted_ns=None;self._deadline=None

    @property
    def cma_bytes(self):return sum(s['input'].nbytes+s['output'].nbytes for s in self._slots)

    def check(self):
        super().check()
        if hasattr(self,'_owner') and threading.get_ident()!=self._owner:raise RuntimeError('DMA requires one owning thread')

    def _slot(self,ticket,state):
        self.check()
        if type(ticket) is not Ticket or not 0<=ticket.slot<2:raise ValueError('Foreign ticket')
        slot=self._slots[ticket.slot]
        if slot['ticket'] is not ticket or slot['state']!=state:raise ValueError('Stale or wrong-state ticket')
        return slot

    def prepare(self,values):
        self.check()
        available=[i for i in (self._next_slot,1-self._next_slot) if self._slots[i]['state']=='free']
        if not available:raise RuntimeError('Both input slots are owned')
        index=available[0];slot=self._slots[index]
        start=time.perf_counter_ns();owned=snapshot(values);words=command_words(owned)
        prepared=time.perf_counter_ns();slot['input'][:len(words)]=words;copied=time.perf_counter_ns()
        ticket=Ticket(index,owned,words,prepared-start,copied-prepared)
        slot.update(ticket=ticket,state='prepared');self._next_slot=1-index
        return ticket

    def submit(self,ticket):
        slot=self._slot(ticket,'prepared')
        if self._pending is not None or self.active:raise RuntimeError('Only one DMA transaction may be active')
        count=len(ticket.owned.samples);capacity=((8*count+32+63)//64)*64
        self._pending=ticket;slot['state']='active';self.active=True
        self._submitted_ns=time.perf_counter_ns();self._deadline=time.monotonic()+self.timeout
        try:
            self.dma.recvchannel.transfer(slot['output'][:capacity])
            self.dma.sendchannel.transfer(slot['input'][:len(ticket.words)])
        except BaseException:self.abort();raise

    def _wait(self):
        while True:
            status=self.status()
            if (status['send']|status['receive'])&0x70:raise RuntimeError('LZ77 DMA error')
            # A delayed host may find an already completed transaction. Its
            # completion wins even if ARM encoding exceeded the deadline.
            if status['send']&2 and status['receive']&2:
                self.dma.sendchannel.wait();self.dma.recvchannel.wait();return
            if time.monotonic()>=self._deadline:raise TimeoutError('LZ77 submitted transaction timeout')

    def finish(self,ticket):
        slot=self._slot(ticket,'active')
        if self._pending is not ticket or not self.active:raise RuntimeError('Wrong pending transaction')
        count=len(ticket.owned.samples)
        try:
            self._wait();done=time.perf_counter_ns();self.active=False
            size=self.dma.recvchannel.transferred
            if not 36<=size<=8*count+32 or size%4:raise RuntimeError('Wrong LZ77 output length')
            data=memoryview(slot['output'])[:size].tobytes();sequence=(self.sequence+1)&0xffffffff
            tokens,meta=metadata_view(data,count,sequence);profile=self.profile(count,size,sequence)
            if meta['raw_crc32']!=ticket.owned.raw_crc32:raise RuntimeError('LZ77 input original CRC mismatch')
            self.sequence=sequence
            profile.update(dma_ns=done-self._submitted_ns,input_dma_bytes=4*len(ticket.words),output_dma_bytes=size,
                clock_mhz=self.clock_mhz,input_prepare_ns=ticket.input_prepare_ns,input_cma_ns=ticket.input_cma_ns,
                source_crc_checked=True,retained_input_snapshot_bytes=len(ticket.owned.raw)+ticket.words.nbytes,
                input_preparation='owned_bulk_int16_v1',slot=ticket.slot,submit_ns=self._submitted_ns,completion_observed_ns=done,
                dma_timing='submit to host completion observation; may include overlapping ARM encoding')
            slot.update(state='free',ticket=None);self._pending=None
            self._submitted_ns=self._deadline=None
            return (data,tokens,meta,profile),ticket.owned.raw
        except BaseException:self.abort();raise

    def transform(self,values):
        self.check()
        if any(s['state']!='free' for s in self._slots):raise RuntimeError('Finish or cancel staged work first')
        ticket=self.prepare(values);self.submit(ticket);return self.finish(ticket)

    def _exchange(self,*args):raise RuntimeError('Use prepare/submit/finish or transform')

    def abort(self):
        if hasattr(self,'_owner') and threading.get_ident()!=self._owner:raise RuntimeError('Abort from owning thread')
        # The inherited implementation retains all CMA on reset failure and
        # drops PYNQ transfer views only AFTER reset acknowledgement.
        super().abort()
        if hasattr(self,'_slots'):
            for slot in self._slots:slot.update(state='free',ticket=None)
        self._pending=None;self._submitted_ns=self._deadline=None

    def close(self):
        if self.closed:return
        if threading.get_ident()!=self._owner:raise RuntimeError('Close from owning thread')
        if self.active:raise RuntimeError('Abort DMA before closing; retain both CMA slots')
        status=self.status()
        if self.dma.mmio.read(0)&4 or self.dma.mmio.read(0x30)&4 or not all(v&3 for v in status.values()):
            raise RuntimeError('DMA not safe to release; retain both CMA slots')
        for slot in self._slots:
            slot['input'].freebuffer();slot['output'].freebuffer();slot.update(state='closed',ticket=None)
        self.closed=True

    def __exit__(self,*args):
        if self.active:self.abort()
        self.close()
