"""Ownership, bounded prefetch, cancellation and DMA failures without a board."""
import sys
import threading
import time
import unittest
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'research'),str(ROOT/'board')]
from compression_system import CompressionSystem
from stream_codec import prepare_packet, native_frames
import hybrid_codec as hybrid
import flac_hybrid_codec as combined
from screen_block_coding import frozen


class Buffer(np.ndarray):
    def __new__(cls, shape, dtype):
        result = np.zeros(shape,dtype=dtype).view(cls)
        result.owner = dict(freed=False,dma_views=0)
        return result
    def __array_finalize__(self, obj):
        self.owner = getattr(obj,'owner',None)
    def freebuffer(self):
        if self.owner['dma_views']:
            raise AssertionError('A DMA buffer view still owns this CMA allocation')
        self.owner['freed'] = True


class Channel:
    def __init__(self, hardware, receive):
        self.hardware = hardware
        self.receive = receive
        self.transferred = 0
        self._buffer = None
    @property
    def _active_buffer(self):
        return self._buffer
    @_active_buffer.setter
    def _active_buffer(self,value):
        if self._buffer is not None:
            self._buffer.owner['dma_views'] -= 1
        self._buffer = value
        if value is not None:
            value.owner['dma_views'] += 1
    def transfer(self, buffer):
        self._active_buffer = buffer
        self.hardware.events.append(('recv' if self.receive else 'send',threading.get_ident()))
        if self.receive:
            self.hardware.destination = buffer
        else:
            self.hardware.launch(buffer)
    def wait(self):
        self.hardware.events.append(('wait',threading.get_ident()))
        self._active_buffer = None


class FakeHardware:
    def __init__(self, timeout=.1, delay=0):
        self.timeout = timeout
        self.delay = delay
        self.input = Buffer((8193,),np.uint32)
        self.output = Buffer((16640,),np.uint8)
        self.channel_sequence = [0]*4
        self.wire_sequence = [0]*4
        self.last_sequence = self.wire_global = 0
        self.clock_mhz = 50
        self.failed = self.active = self.halted = False
        self.never_complete = self.corrupt = False
        self.events = []
        self.destination = None
        self.sendchannel = Channel(self,False)
        self.recvchannel = Channel(self,True)
        self.dma = self.mmio = self
        self.last_profile = None
    def launch(self, words):
        if self.active:
            raise AssertionError('Two transfers active')
        records = []
        values = []
        for word in words[1:]:
            w = int(word)
            ch = (w>>16)&3
            value = w&65535
            values.append(value-65536 if value&32768 else value)
            if w&(1<<18):
                records.append((ch,values))
                values = []
        assert not values and len(records) == int(words[0])&65535
        parts = []
        for ch,values in records:
            self.wire_sequence[ch] = (self.wire_sequence[ch]+1)&0xffffffff
            parts.append(frozen.encode_block(values,ch,self.wire_sequence[ch]))
        self.pending = b''.join(parts)
        if self.corrupt:
            b = bytearray(self.pending)
            b[20] ^= 1
            self.pending = bytes(b)
        self.wire_global = (self.wire_global+len(records))&0xffffffff
        samples = sum(len(v) for ch,v in records)
        self.stats = dict(sequence=self.wire_global,input_samples=samples,output_bytes=len(self.pending),
            total_cycles=2*samples,collect_cycles=samples,process_cycles=samples,
            input_gap_cycles=0,output_stalls=0,busy_lane_cycles=samples,max_active_lanes=1)
        self.ready = time.monotonic()+self.delay
        self.active = True
        self.halted = False
    def read(self, offset):
        self.events.append(('read',threading.get_ident()))
        if offset in (0,0x30):
            return 0
        if self.active and not self.never_complete and time.monotonic() >= self.ready:
            self.destination[:len(self.pending)] = np.frombuffer(self.pending,dtype=np.uint8)
            self.recvchannel.transferred = len(self.pending)
            self.active = False
        return 1 if self.halted else 0x1000 if self.active else 0x1002
    def write(self, offset, value):
        assert offset == 0 and value == 4
        self.events.append(('reset',threading.get_ident()))
        self.active = False
        self.halted = True
    def read_profile(self):
        assert not self.active
        return self.stats.copy()
    def close(self):
        assert not self.active
        self.input.freebuffer()
        self.output.freebuffer()


def system(mode='lookahead',**kwargs):
    hardware = FakeHardware(**kwargs)
    return CompressionSystem(hardware=hardware,allocator=Buffer,mode=mode), hardware


def expected_packet(records, sequences):
    output = []
    for ch,values in records:
        sequences[ch] = (sequences[ch]+1)&0xffffffff
        frame = hybrid.encode_block(values,ch,sequences[ch])
        f = list(hybrid.header(frame)); f[0] = combined.MAGIC
        output.append(combined.repack(f,frame[24:]))
    return b''.join(output)


class ContinuousTests(unittest.TestCase):
    def test_modes_bytes_tail_repeat_channel_and_order(self):
        packets = [[(0,list(range(64))),(1,[-32768,32767]*31)],
                   [(0,[4]),(0,[7]*279),(3,list(range(17)))],
                   [(2,[32767,-32768]),(1,[0]*513)]]
        sequence = [0]*4
        expected = [expected_packet(p,sequence) for p in packets]
        for mode in CompressionSystem.MODES:
            s,h = system()
            actual = []
            profile = s.compress_stream(iter(packets),lambda r:actual.append(r.data),mode)
            self.assertEqual(actual,expected)
            self.assertEqual(h.channel_sequence,sequence)
            self.assertEqual(profile['status'],'COMPLETE')
            self.assertEqual(profile['owned_packets'],0)
            self.assertEqual(profile['flac_jobs'],0)
            self.assertTrue(profile['producer_joined'])
            self.assertLessEqual(profile['peak_owned_packets'],3 if mode=='pipeline' else 2 if mode=='lookahead' else 1)
            if mode=='lookahead': self.assertEqual(profile['overlapped_next_submissions'],2)
            if mode=='pipeline': self.assertLessEqual(profile['overlapped_next_submissions'],2)
            self.assertTrue(all(t==threading.get_ident() for _,t in h.events))
            s.close()

    def test_bounds_and_slow_sink(self):
        s,h = system(mode='pipeline')
        count = 0
        def sink(packet):
            nonlocal count
            count += 1
            time.sleep(.001)
        p = s.compress_stream(([(i%4,list(range(16)))] for i in range(80)),sink)
        self.assertEqual(count,80)
        self.assertEqual(p['pulled_packets'],80)
        self.assertLessEqual(p['peak_owned_packets'],3)
        self.assertLessEqual(p['peak_snapshot_bytes'],3*(4+16*16))
        self.assertTrue(p['producer_joined'])
        s.close()

    def test_snapshot_and_input_narrowing_guards(self):
        values = np.arange(64,dtype=np.int64)
        p = prepare_packet([(0,values)])
        values[:] = -100
        np.testing.assert_array_equal(p.records[0][1].samples,np.arange(64))
        self.assertFalse(p.words.flags.writeable)
        s,h = system()
        for records in ([],[(0,[32768])],[(0,[-32769])],[(0,[1.5])],[(4,[1])],[(0,[1]*1025)],[(0,[1])]*9):
            before = h.channel_sequence.copy()
            with self.assertRaises((ValueError,TypeError)):
                s.encode_records(records)
            self.assertEqual(h.channel_sequence,before)
            self.assertFalse(any(e=='send' for e,t in h.events))
            self.assertFalse(s.driver.failed)
        s.encode_records([(0,[1,2,3])])
        s.close()

    def test_cancel_aborts_owned_dma_and_rejects_reuse(self):
        s,h = system()
        with s.open_stream(([(0,list(range(16)))] for _ in range(30))) as stream:
            next(stream)
        self.assertTrue(s.driver.failed)
        self.assertFalse(h.active)
        self.assertTrue(s.last_stream_profile['producer_joined'])
        with self.assertRaisesRegex(RuntimeError,'Reload'):
            s.encode_records([(0,[1])])
        extra = s.driver.slots[1]['input']
        s.close()
        self.assertTrue(extra.owner['freed'])

    def test_sink_exception_source_exception_and_invalid_next(self):
        for error in ('sink','source','invalid'):
            s,h = system()
            def source():
                yield [(0,list(range(16)))]
                if error=='source': raise OSError('source failure')
                if error=='invalid': yield [(0,[32768])]
                else:
                    for _ in range(12): yield [(0,list(range(16)))]
            def sink(packet):
                if error=='sink': raise OSError('sink failure')
            with self.assertRaises((OSError,ValueError)):
                s.compress_stream(source(),sink)
            self.assertTrue(s.driver.failed)
            self.assertFalse(h.active)
            self.assertTrue(s.last_stream_profile['producer_joined'])
            s.close()

    def test_invalid_first_stream_can_reuse_and_empty_stream(self):
        s,h = system()
        with self.assertRaises(ValueError):
            s.compress_stream(iter([[(0,[32768])]]),lambda p:None)
        self.assertFalse(s.driver.failed)
        self.assertFalse(any(e=='send' for e,t in h.events))
        p = s.compress_stream([],lambda p:None)
        self.assertEqual(p['status'],'COMPLETE')
        self.assertEqual(p['packets'],0)
        s.encode_records([(0,[1,2,3])])
        self.assertEqual(h.channel_sequence[0],1)
        s.close()

    def test_dma_fault_and_timeout_reset_before_free(self):
        for kind in ('corrupt','timeout'):
            s,h = system(timeout=.001)
            if kind=='corrupt': h.corrupt = True
            else: h.never_complete = True
            with self.assertRaises((ValueError,RuntimeError,TimeoutError)):
                s.encode_records([(0,list(range(32)))])
            self.assertTrue(s.driver.failed)
            self.assertFalse(h.active)
            self.assertTrue(any(e=='reset' for e,t in h.events))
            s.close()

    def test_completed_dma_does_not_timeout_during_slow_consumer(self):
        s,h = system(timeout=.005)
        out = []
        def sink(p):
            out.append(p.data)
            time.sleep(.02)
        p = s.compress_stream([[(0,list(range(16)))]]*3,sink)
        self.assertEqual(p['status'],'COMPLETE')
        self.assertFalse(s.driver.failed)
        s.close()

    def test_single_owner_and_completed_session_reuse(self):
        s,h = system()
        def sink(p):
            with self.assertRaisesRegex(RuntimeError,'owns'):
                s.encode_records([(0,[1])])
            with self.assertRaises(RuntimeError): s.close()
        for mode in ('pipeline','serial'):
            s.compress_stream([[(0,[1,2,3])]]*2,sink,mode)
        self.assertEqual(h.channel_sequence[0],4)
        s.close()
        s.close()

    def test_block_coalescing_copies_reused_source_and_flushes_tail(self):
        buffer = np.empty(16,dtype=np.int16)
        expected = []
        seq = [0]*4
        def blocks():
            for i in range(19):
                buffer[:] = i
                expected.append(expected_packet([(i%4,buffer.tolist())],seq))
                yield i%4,buffer
        s,h = system()
        output = []
        profile = s.compress_blocks(blocks(),lambda p:output.append(p.data))
        self.assertEqual(b''.join(output),b''.join(expected))
        self.assertEqual(profile['packets'],3)
        self.assertEqual(profile['frames'],19)
        self.assertEqual(profile['overlapped_next_submissions'],2)
        s.close()

    def test_background_source_does_not_delay_ready_output_for_future_input(self):
        gate = threading.Event()
        def source():
            yield [(0,list(range(32)))]
            gate.wait(timeout=.4)
            yield [(1,[7]*16)]
        s,h = system(mode='pipeline')
        with s.open_stream(source()) as stream:
            before = time.perf_counter()
            first = next(stream)
            self.assertLess(time.perf_counter()-before,.2)
            self.assertTrue(first.data)
            gate.set()
            list(stream)
        self.assertEqual(s.last_stream_profile['status'],'COMPLETE')
        s.close()


if __name__ == '__main__':
    unittest.main()
