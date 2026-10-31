"""Single-owner LZ77 DMA driver with frozen frontend reset/view lifecycle."""
import operator,time,zlib
import numpy as np
from pynq import allocate
from delta_frontend_driver import DeltaFrontend
from lz77_protocol import metadata


class LZ77Driver(DeltaFrontend):
    def __init__(self,bitfile,timeout=10.0):
        super().__init__(bitfile,timeout)
        self.output.freebuffer();self.output=allocate(shape=(65600,),dtype=np.uint8)

    def profile(self,count,size,sequence):
        names=('total_cycles','collect_cycles','process_cycles','output_stalls',
               'input_samples','output_bytes','sequence','input_gap_cycles','matcher_work_cycles','search_probes')
        before=self.stats[3].read(0)
        row={name:self.stats[i//2].read(8*(i%2)) for i,name in enumerate(names)}
        if before!=self.stats[3].read(0) or row['sequence']!=sequence:raise RuntimeError('Wrong LZ77 snapshot')
        if row['total_cycles']!=row['collect_cycles']+row['process_cycles'] or row['collect_cycles']!=count+1+row['input_gap_cycles']:
            raise RuntimeError('LZ77 phases do not add up')
        if (row['input_samples'],row['output_bytes'])!=(count,size) or not 0<row['matcher_work_cycles']<=row['total_cycles']:
            raise RuntimeError('LZ77 counters invalid')
        return row

    def _exchange(self,words,count):
        self.check();capacity=((8*count+32+63)//64)*64
        self.input[:len(words)]=words;before=time.perf_counter_ns();self.active=True
        try:
            self.dma.recvchannel.transfer(self.output[:capacity]);self.dma.sendchannel.transfer(self.input[:len(words)])
            self._wait();done=time.perf_counter_ns();self.active=False
            size=self.dma.recvchannel.transferred
            if not 36<=size<=8*count+32 or size%4:raise RuntimeError('Wrong LZ77 output length')
            data=self.output[:size].tobytes();sequence=(self.sequence+1)&0xffffffff
            tokens,meta=metadata(data,count,sequence);profile=self.profile(count,size,sequence)
            self.sequence=sequence;profile.update(dma_ns=done-before,input_dma_bytes=4*len(words),output_dma_bytes=size,clock_mhz=self.clock_mhz)
            return data,tokens,meta,profile
        except BaseException:self.abort();raise

    def transform(self,values):
        self.check();start=time.perf_counter_ns();values=[operator.index(v) for v in values]
        if not 1<=len(values)<=8192 or any(v < -32768 or v > 32767 for v in values):raise ValueError('Use1..8192 int16 integers')
        source=np.asarray(values,dtype='<i2');raw=source.tobytes();expected_crc=zlib.crc32(raw)
        words=np.empty(len(values)+1,dtype=np.uint32);words[0]=0x4c5a0000|len(values);words[1:]=source.view('<u2')
        prepared=time.perf_counter_ns();result=self._exchange(words,len(values))
        if result[2]['raw_crc32']!=expected_crc:self.abort();raise RuntimeError('LZ77 input original CRC mismatch')
        result[3].update(input_prepare_ns=prepared-start,source_crc_checked=True)
        return result,raw
