"""Four-lane 1..1024-point BSC1 hardware; no strong coding is hidden in PL."""
import operator
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'research'))

import numpy as np
from pynq import allocate, MMIO
from dma_compressor import DMACompressor
from screen_block_coding import frozen


def checked_records(records):
    result = []
    for ch,values in records:
        ch = operator.index(ch)
        values = [operator.index(v) for v in values]
        if not 0 <= ch <= 3 or not 1 <= len(values) <= 1024:
            raise ValueError('Use channel0..3, block1..1024')
        if any(not -32768 <= v <= 32767 for v in values):
            raise ValueError('Every sample must fit int16 exactly')
        result.append((ch,values))
    if not 1 <= len(result) <= 8:
        raise ValueError('Each DMA packet contains1..8 blocks')
    return result


def pack_input(records):
    words = [0x4d430000 | len(records)]
    for ch,values in records:
        words.extend((value & 65535) | (ch << 16) | (int(i == len(values)-1) << 18)
                     for i,value in enumerate(values))
    return words


def channel_packets(channels):
    if any(type(ch) is not int or not 0 <= ch <= 3 for ch in channels):
        raise ValueError('Channels must be0..3')
    block_records = []
    for begin in range(0,max(map(len,channels.values()),default=0),1024):
        for ch in sorted(channels):
            chunk = channels[ch][begin:begin+1024]
            if chunk:
                block_records.append((ch,chunk))
                if len(block_records) == 8:
                    yield block_records
                    block_records = []
    if block_records:
        yield block_records


def frame_records(data):
    offset = 0
    while offset < len(data):
        fields = frozen._header(data,offset)
        end = offset+24+fields[7]
        if end > len(data):
            raise ValueError('Truncated hardware BSC1 payload')
        yield fields[2],fields[5],fields[6],data[offset:end]
        offset = end


class HybridNativeCompressor(DMACompressor):
    def __init__(self,bitfile,timeout=5.0):
        super().__init__(bitfile,timeout)
        self.input.freebuffer(); self.output.freebuffer()
        self.input = allocate(shape=(8193,),dtype=np.uint32)
        self.output = allocate(shape=(16640,),dtype=np.uint8)
        self.channel_sequence = [0]*4
        self.parallel_stats = MMIO(self.overlay.ip_dict['stats_4']['phys_addr'],4096)

    def read_profile(self):
        result = super().read_profile()
        busy,maximum = self.parallel_stats.read(0),self.parallel_stats.read(8)
        if self.stats_banks[3].read(0) != result['sequence'] or busy > 4*result['total_cycles'] or maximum > 4:
            raise RuntimeError('Invalid/changed parallel snapshot')
        result.update(busy_lane_cycles=busy,max_active_lanes=maximum,
            average_active_lanes=busy/result['total_cycles'] if result['total_cycles'] else 0,
            phase_definition='first external sample to final output handshake; busy includes waits')
        return result

    def encode_records(self,records):
        if self.failed:
            raise RuntimeError('Reload overlay after failed transfer')
        records = checked_records(records)
        words = pack_input(records)
        samples = sum(len(v) for _,v in records)
        next_sequences = self.channel_sequence.copy()
        expected = {}
        for ch,values in records:
            next_sequences[ch] = (next_sequences[ch]+1) & 0xffffffff
            expected[ch,next_sequences[ch]] = len(values)
        maximum = 2*samples+24*len(records)
        capacity = ((maximum+63)//64)*64
        self.input[:len(words)] = words
        source,destination = self.input[:len(words)],self.output[:capacity]
        start = time.perf_counter_ns()
        try:
            self.dma.recvchannel.transfer(destination)
            armed = time.perf_counter_ns()
            self.dma.sendchannel.transfer(source)
            launched = time.perf_counter_ns()
            self._wait()
        except Exception:
            self.failed = True
            self.dma.mmio.write(0x00,4)
            deadline = time.monotonic()+1
            while self.dma.mmio.read(0x00) & 4:
                if time.monotonic() > deadline:
                    raise RuntimeError('DMA reset failed; reboot before freeing buffers')
            raise
        finished = time.perf_counter_ns()
        size = self.dma.recvchannel.transferred
        self.failed = True
        if not 26 <= size <= maximum:
            raise RuntimeError('Invalid DMA output length')
        data = bytes(self.output[:size])
        observed = {}
        current = self.channel_sequence.copy()
        for ch,seq,count,frame in frame_records(data):
            if (ch,seq) in observed or seq != (current[ch]+1) & 0xffffffff:
                raise RuntimeError('Duplicate/reordered hardware frame')
            current[ch] = seq
            observed[ch,seq] = count
        if observed != expected or current != next_sequences:
            raise RuntimeError('Hardware counts/sequences mismatch')
        profile = self.read_profile()
        if profile['sequence'] != (self.last_sequence+len(records)) & 0xffffffff or profile['input_samples'] != samples or profile['output_bytes'] != len(data):
            raise RuntimeError('Wrong packet performance snapshot')
        self.last_sequence = profile['sequence']
        self.channel_sequence = next_sequences
        profile.update(frames=len(records),core_interval_us=profile['total_cycles']/self.clock_mhz,
            dma_us=(finished-start)/1000,receive_setup_us=(armed-start)/1000,
            send_setup_us=(launched-armed)/1000,wait_us=(finished-launched)/1000,
            input_dma_bytes=4*len(words),raw_sample_bytes=2*samples)
        self.last_profile = profile
        self.failed = False
        return data,finished-start
