"""PYNQ driver for ping-pong core: 1..8 independent blocks per DMA."""
import time
from pathlib import Path
import numpy as np
from pynq import Overlay, allocate, MMIO, Clocks
from codec import HEADER, MAX_BLOCK, raw_bytes, split_frames

MAX_BATCH_BLOCKS=8
MAX_PACKET_SAMPLES=MAX_BATCH_BLOCKS*MAX_BLOCK

class DMACompressor:
    def __init__(self, bitfile=None, timeout=5.0):
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        self.timeout = timeout
        self.overlay = Overlay(str(bitfile or Path(__file__).with_name('compressor.bit')))
        self.dma = self.overlay.axi_dma_0
        self.input = allocate(shape=(MAX_PACKET_SAMPLES,),dtype=np.int16)
        capacity=((MAX_BATCH_BLOCKS*(HEADER.size+2*MAX_BLOCK)+63)//64)*64
        self.output = allocate(shape=(capacity,),dtype=np.uint8)
        self.failed = False
        self.clock_mhz=float(Clocks.fclk0_mhz)
        self.stats_banks=[MMIO(self.overlay.ip_dict[f'stats_{i}']['phys_addr'],4096)
                          for i in range(4)]
        self.last_sequence=self.stats_banks[3].read(0)
        self.last_profile=None

    def read_profile(self):
        names=['total_cycles','collect_cycles','process_cycles','output_stalls',
               'input_samples','output_bytes','sequence','input_gap_cycles']
        before=self.stats_banks[3].read(0)
        values={name:self.stats_banks[i//2].read(8*(i%2)) for i,name in enumerate(names)}
        after=self.stats_banks[3].read(0)
        if before!=after or values['sequence']!=after:
            raise RuntimeError('Performance snapshot changed while reading')
        if values['total_cycles']!=values['collect_cycles']+values['process_cycles']:
            raise RuntimeError('Performance phase counts do not add up')
        if values['collect_cycles']!=values['input_samples']+values['input_gap_cycles']:
            raise RuntimeError('Performance input counts do not add up')
        return values

    def status(self):
        return {'mm2s_status':hex(self.dma.mmio.read(0x04)),
                's2mm_status':hex(self.dma.mmio.read(0x34)),
                'mm2s_bytes':self.dma.mmio.read(0x28),
                's2mm_bytes':self.dma.mmio.read(0x58)}

    def _wait(self):
        deadline=time.monotonic()+self.timeout
        while True:
            send=self.dma.mmio.read(0x04)
            recv=self.dma.mmio.read(0x34)
            if (send|recv)&0x70:
                raise RuntimeError(f'DMA hardware error: {self.status()}')
            if send&2 and recv&2:
                self.dma.sendchannel.wait()
                self.dma.recvchannel.wait()  # invalidates cache and records actual BTT
                return
            if time.monotonic()>=deadline:
                raise TimeoutError(f'DMA timeout: {self.status()}')

    def encode_block(self,samples):
        samples=list(samples)
        if not 1<=len(samples)<=MAX_BLOCK:
            raise ValueError('Block must contain 1..256 samples')
        return self.encode_batch(samples)

    def encode_batch(self,samples):
        if self.failed:
            raise RuntimeError('Reload the overlay after a failed transfer')
        samples=list(samples)
        if not 1<=len(samples)<=MAX_PACKET_SAMPLES:
            raise ValueError('Each DMA input packet must contain 1..2048 samples')
        raw_bytes(samples)  # reject out-of-range/non-integer values before casting
        blocks=(len(samples)+MAX_BLOCK-1)//MAX_BLOCK
        max_bytes=2*len(samples)+blocks*HEADER.size
        capacity=((max_bytes+63)//64)*64
        self.input[:len(samples)]=samples
        # Views limit cache maintenance to bytes involved in this transfer.
        source=self.input[:len(samples)]
        destination=self.output[:capacity]
        start=time.perf_counter_ns()
        try:
            # Receive is armed before input starts; variable length ends on TLAST.
            self.dma.recvchannel.transfer(destination)
            receive_armed=time.perf_counter_ns()
            self.dma.sendchannel.transfer(source)
            send_launched=time.perf_counter_ns()
            self._wait()
        except Exception:
            self.failed=True
            # Reset DMA before any allocated buffer can be released.
            self.dma.mmio.write(0x00,4)
            limit=time.monotonic()+1
            while self.dma.mmio.read(0x00)&4:
                if time.monotonic()>limit:
                    raise RuntimeError('DMA reset failed; keep buffers allocated and reboot board')
            raise
        finished=time.perf_counter_ns()
        elapsed_ns=finished-start
        transferred=self.dma.recvchannel.transferred
        if not HEADER.size<=transferred<=max_bytes:
            raise RuntimeError(f'Unexpected DMA byte count: {transferred}')
        frame=bytes(self.output[:transferred])
        frames=list(split_frames(frame))
        expected_counts=[min(MAX_BLOCK,len(samples)-i*MAX_BLOCK) for i in range(blocks)]
        if len(frames)!=blocks or [HEADER.unpack_from(f)[5] for f in frames]!=expected_counts:
            raise RuntimeError('DMA packet does not contain the expected SDC1 frames')
        profile=self.read_profile()  # register reads excluded from measured DMA time
        if profile['sequence']!=((self.last_sequence+blocks)&0xffffffff) or \
                profile['input_samples']!=len(samples) or profile['output_bytes']!=transferred:
            raise RuntimeError('Performance snapshot is not for the completed packet')
        self.last_sequence=profile['sequence']
        # Input collection overlaps encoding: collect is the packet input
        # prefix, process is the remaining output drain tail, not CPU work.
        profile.update(phase_definition='packet_input_prefix_and_drain_tail',
            frames=blocks,core_interval_us=profile['total_cycles']/self.clock_mhz,
            collect_us=profile['collect_cycles']/self.clock_mhz,
            process_us=profile['process_cycles']/self.clock_mhz,
            output_stall_us=profile['output_stalls']/self.clock_mhz,
            host_receive_setup_us=(receive_armed-start)/1000,
            host_send_setup_us=(send_launched-receive_armed)/1000,
            host_wait_finish_us=(finished-send_launched)/1000,
            host_transaction_us=elapsed_ns/1000)
        self.last_profile=profile
        return frame,elapsed_ns

    def compress(self,samples,batch_blocks=MAX_BATCH_BLOCKS):
        if type(batch_blocks) is not int or not 1<=batch_blocks<=MAX_BATCH_BLOCKS:
            raise ValueError('batch_blocks must be 1..8')
        samples=list(samples)
        frames=[]
        times=[]
        packet_size=batch_blocks*MAX_BLOCK
        for i in range(0,len(samples),packet_size):
            frame,elapsed=self.encode_batch(samples[i:i+packet_size])
            frames.append(frame)
            times.append(elapsed)
        return b''.join(frames),times

    def close(self):
        self.input.freebuffer()
        self.output.freebuffer()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
