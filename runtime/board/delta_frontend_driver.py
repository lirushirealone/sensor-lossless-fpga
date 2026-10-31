"""Standalone frontend proof driver. It does not replace DirectFrameSystem."""
import math,operator,time
import numpy as np
from pynq import Overlay,allocate,MMIO,Clocks
from delta_frontend_protocol import metadata


class DeltaFrontend:
    def __init__(self,bitfile,timeout=5.0):
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Use a finite positive timeout')
        self.timeout=timeout;self.failed=False;self.closed=False;self.active=False
        self.overlay=Overlay(str(bitfile));self.dma=self.overlay.axi_dma_0
        self.input=allocate(shape=(8193,),dtype=np.uint32)
        self.output=allocate(shape=(16448,),dtype=np.uint8)
        self.stats=[MMIO(self.overlay.ip_dict[f'stats_{i}']['phys_addr'],4096) for i in range(5)]
        self.sequence=self.stats[3].read(0);self.clock_mhz=float(Clocks.fclk0_mhz)

    def status(self):return dict(send=self.dma.mmio.read(4),receive=self.dma.mmio.read(0x34))

    def check(self):
        if self.failed or self.closed:raise RuntimeError('Reload overlay after failure or close')

    def _wait(self):
        deadline=time.monotonic()+self.timeout
        while True:
            status=self.status()
            if (status['send']|status['receive'])&0x70:raise RuntimeError('Frontend DMA error')
            if status['send']&2 and status['receive']&2:
                self.dma.sendchannel.wait();self.dma.recvchannel.wait();return
            if time.monotonic()>=deadline:raise TimeoutError('Frontend DMA timeout')

    def abort(self):
        self.failed=True;self.dma.mmio.write(0,4)
        deadline=time.monotonic()+1
        while self.dma.mmio.read(0)&4:
            if time.monotonic()>=deadline:raise RuntimeError('DMA reset failed: retain CMA buffers until reboot')
        # Drop PYNQ transfer views only after the DMA has acknowledged reset.
        for ch in (self.dma.sendchannel,self.dma.recvchannel):
            if hasattr(ch,'_active_buffer'):ch._active_buffer=None
        self.active=False

    def profile(self,count,size,sequence):
        names=('total_cycles','collect_cycles','process_cycles','output_stalls',
               'input_samples','output_bytes','sequence','input_gap_cycles',
               'packet_active_cycles','max_active_lanes')
        before=self.stats[3].read(0)
        row={name:self.stats[i//2].read(8*(i%2)) for i,name in enumerate(names)}
        if self.stats[3].read(0)!=before or row['sequence']!=sequence:
            raise RuntimeError('Changed/wrong frontend statistics snapshot')
        if row['total_cycles']!=row['collect_cycles']+row['process_cycles'] or row['collect_cycles']!=count+1+row['input_gap_cycles']:
            raise RuntimeError('Frontend statistics phases do not add up')
        if (row['input_samples'],row['output_bytes'],row['packet_active_cycles'],row['max_active_lanes'])!=(count,size,row['total_cycles'],1):
            raise RuntimeError('Wrong frontend packet statistics')
        return row

    def _exchange(self,words,count):
        self.check()
        size=2*count+24;capacity=((size+63)//64)*64
        self.input[:len(words)]=words
        before=time.perf_counter_ns();self.active=True
        try:
            self.dma.recvchannel.transfer(self.output[:capacity])
            self.dma.sendchannel.transfer(self.input[:len(words)])
            self._wait();done=time.perf_counter_ns();self.active=False
            if self.dma.recvchannel.transferred!=size:raise RuntimeError('Wrong frontend received length')
            data=self.output[:size].tobytes();sequence=(self.sequence+1)&0xffffffff
            residual,meta=metadata(data,count,sequence)
            profile=self.profile(count,size,sequence)
            self.sequence=sequence
            profile.update(dma_ns=done-before,input_dma_bytes=4*len(words),output_dma_bytes=size,
                           clock_mhz=self.clock_mhz,phase_definition='command to final trailer handshake; packet_active_cycles includes waits, not arithmetic busy utilisation')
            return data,residual,meta,profile
        except BaseException:
            self.abort();raise

    def transform(self,values):
        self.check();values=[operator.index(v) for v in values]
        if not 1<=len(values)<=8192 or any(v < -32768 or v > 32767 for v in values):
            raise ValueError('Use1..8192 int16-range integers')
        words=np.empty(len(values)+1,dtype=np.uint32);words[0]=0xdf010000|len(values)
        words[1:]=np.asarray(values,dtype=np.int16).view(np.uint16)
        return self._exchange(words,len(values))

    def close(self):
        if self.closed:return
        if self.active:raise RuntimeError('Abort DMA before closing')
        st=self.status()
        if self.dma.mmio.read(0)&4 or self.dma.mmio.read(0x30)&4 or not all(v&3 for v in st.values()):
            raise RuntimeError('DMA not safe to release; retain buffers')
        self.input.freebuffer();self.output.freebuffer();self.closed=True

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
