"""Single DMA owner; two CMA slots, immutable completions, fail-closed abort."""
import time
import numpy as np
from stream_codec import PreparedPacket, native_frames


class NativeStreamDriver:
    def __init__(self, hardware, allocator=None):
        if allocator is None:
            from pynq import allocate
            allocator = allocate
        self.hardware = hardware
        self.dma = hardware.dma
        self.slots = [dict(input=hardware.input, output=hardware.output, state='free'),
                      dict(input=allocator(shape=(8193,), dtype=np.uint32),
                           output=allocator(shape=(16640,), dtype=np.uint8), state='free')]
        self.cma_bytes = sum(s['input'].nbytes+s['output'].nbytes for s in self.slots)
        self.reserved_channels = hardware.channel_sequence.copy()
        self.reserved_sequence = hardware.last_sequence
        self.active = None
        self.failed = False
        self.closed = False

    def check(self):
        if self.closed or self.failed or self.hardware.failed:
            raise RuntimeError('Reload overlay after a failed/closed stream')

    def prepare(self, prepared):
        self.check()
        if not isinstance(prepared, PreparedPacket):
            raise TypeError('Use an owned validated packet')
        index = next((i for i,s in enumerate(self.slots) if s['state']=='free'), None)
        if index is None:
            raise RuntimeError('Both CMA slots are owned')
        slot = self.slots[index]
        before = time.perf_counter_ns()
        slot['input'][:len(prepared.words)] = prepared.words
        sequences = self.reserved_channels.copy()
        expected = {}
        for ch,p in prepared.records:
            sequences[ch] = (sequences[ch]+1) & 0xffffffff
            expected[ch,sequences[ch]] = len(p.samples)
        end_sequence = (self.reserved_sequence+len(prepared.records)) & 0xffffffff
        token = dict(slot=index, prepared=prepared, expected=expected,
            initial=self.reserved_channels.copy(), final=sequences, end_sequence=end_sequence,
            copy_ns=time.perf_counter_ns()-before,
            capacity=((prepared.maximum+63)//64)*64)
        slot['state'] = 'prepared'
        slot['token'] = token
        self.reserved_channels = sequences
        self.reserved_sequence = end_sequence
        return token

    def submit(self, token):
        self.check()
        slot = self.slots[token['slot']]
        if self.active is not None or slot['state'] != 'prepared' or slot['token'] is not token:
            raise RuntimeError('Only one native DMA transaction may be active')
        slot['state'] = 'active'
        self.active = token
        token['launch_ns'] = time.perf_counter_ns()
        token['deadline'] = time.monotonic()+self.hardware.timeout
        try:
            self.dma.recvchannel.transfer(slot['output'][:token['capacity']])
            token['armed_ns'] = time.perf_counter_ns()
            self.dma.sendchannel.transfer(slot['input'][:len(token['prepared'].words)])
            token['launched_ns'] = time.perf_counter_ns()
        except BaseException:
            self.abort()
            raise

    def complete(self, token):
        self.check()
        if self.active is not token:
            raise RuntimeError('Wrong outstanding native transaction')
        slot = self.slots[token['slot']]
        before = time.perf_counter_ns()
        try:
            while True:
                send, recv = self.dma.mmio.read(4), self.dma.mmio.read(0x34)
                if (send|recv)&0x70:
                    raise RuntimeError('Native stream DMA hardware error')
                # Completion wins over a deadline elapsed in a slow consumer.
                if send&2 and recv&2:
                    self.dma.sendchannel.wait()
                    self.dma.recvchannel.wait()
                    break
                if time.monotonic() >= token['deadline']:
                    raise TimeoutError('Native stream DMA timeout')
            done = time.perf_counter_ns()
            n = self.dma.recvchannel.transferred
            if not 26 <= n <= token['prepared'].maximum:
                raise RuntimeError('Invalid native stream output length')
            data = slot['output'][:n].tobytes()
            observed = {}
            frames = {}
            sequences = token['initial'].copy()
            for ch,seq,count,frame in native_frames(data):
                if (ch,seq) in frames or seq != (sequences[ch]+1)&0xffffffff:
                    raise RuntimeError('Duplicate/reordered native stream frame')
                sequences[ch] = seq
                observed[ch,seq] = count
                frames[ch,seq] = frame
            if observed != token['expected'] or sequences != token['final']:
                raise RuntimeError('Native stream count/sequence mismatch')
            stats = self.hardware.read_profile()
            if stats['sequence'] != token['end_sequence'] or stats['input_samples'] != token['prepared'].samples or stats['output_bytes'] != n:
                raise RuntimeError('Wrong native stream statistics snapshot')
            self.hardware.channel_sequence = token['final'].copy()
            self.hardware.last_sequence = token['end_sequence']
            self.hardware.last_profile = stats.copy()
            finished = time.perf_counter_ns()
            profile = dict(hardware=stats, wait_ns=done-before, snapshot_and_check_ns=finished-done,
                copy_ns=token['copy_ns'], launch_ns=token['launch_ns'], observed_complete_ns=finished,
                receive_setup_ns=token['armed_ns']-token['launch_ns'],
                send_setup_ns=token['launched_ns']-token['armed_ns'],
                input_dma_bytes=token['prepared'].words.nbytes, output_dma_bytes=n)
            self.active = None
            slot['state'] = 'free'
            slot.pop('token')
            return data, frames, profile
        except BaseException:
            self.abort()
            raise

    def abort(self):
        self.failed = True
        self.hardware.failed = True
        self.dma.mmio.write(0,4)
        deadline = time.monotonic()+1
        while self.dma.mmio.read(0)&4:
            if time.monotonic() >= deadline:
                raise RuntimeError('DMA reset failed; retain CMA buffers until reboot')
        # PYNQ3.1.1 wait() normally clears these views. Reset skips wait();
        # dropping views after freeing CMA can defer a BO destructor until a
        # new mapping reuses its address. Clear only AFTER reset is acknowledged.
        for channel in (self.dma.sendchannel,self.dma.recvchannel):
            if hasattr(channel,'_active_buffer'):
                channel._active_buffer = None
        self.active = None
        for s in self.slots:
            s['state'] = 'free'
            s.pop('token',None)

    def close(self):
        if self.closed:
            return
        if self.active is not None or any(s['state']!='free' for s in self.slots):
            raise RuntimeError('Close stream session before releasing CMA slots')
        if self.dma.mmio.read(0)&4 or self.dma.mmio.read(0x30)&4 or not self.dma.mmio.read(4)&3 or not self.dma.mmio.read(0x34)&3:
            raise RuntimeError('Native DMA not safe to release')
        self.slots[1]['input'].freebuffer()
        self.slots[1]['output'].freebuffer()
        self.hardware.close()
        self.closed = True
