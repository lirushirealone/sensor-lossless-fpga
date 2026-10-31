"""Same DMA/input/lifecycle; owned packet view and ordered statistics gather."""
import time
from lz77_input_driver import LZ77InputDriver
from lz77_driver_support import StatsReader,metadata_view,ensure_native


class LZ77ResultDriver(LZ77InputDriver):
    def __init__(self,bitfile,timeout=10.0):
        ensure_native();super().__init__(bitfile,timeout)
        self._stats_reader=StatsReader([m.array for m in self.stats])

    def profile(self,count,size,sequence):
        return self._stats_reader.read(count,size,sequence)

    def _exchange(self,words,count):
        self.check();capacity=((8*count+32+63)//64)*64
        self.input[:len(words)]=words;before=time.perf_counter_ns();self.active=True
        try:
            self.dma.recvchannel.transfer(self.output[:capacity]);self.dma.sendchannel.transfer(self.input[:len(words)])
            self._wait();done=time.perf_counter_ns();self.active=False
            size=self.dma.recvchannel.transferred
            if not 36<=size<=8*count+32 or size%4:raise RuntimeError('Wrong LZ77 output length')
            data=self.output[:size].tobytes();sequence=(self.sequence+1)&0xffffffff
            tokens,meta=metadata_view(data,count,sequence);profile=self.profile(count,size,sequence)
            self.sequence=sequence;profile.update(dma_ns=done-before,input_dma_bytes=4*len(words),output_dma_bytes=size,clock_mhz=self.clock_mhz)
            return data,tokens,meta,profile
        except BaseException:self.abort();raise
