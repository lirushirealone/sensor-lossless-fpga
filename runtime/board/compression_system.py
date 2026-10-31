"""Production default: native PL + delta-DEFLATE, bounded continuous scheduling."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'research'), str(ROOT/'board')]
from stream_codec import prepare_packet, finish_packet, batch_blocks
from stream_source import PreparedSource
from native_stream_driver import NativeStreamDriver


@dataclass(frozen=True)
class CompressedPacket:
    data: bytes
    native: bytes
    profile: dict


class CompressionSystem:
    MODES = ('serial', 'lookahead', 'pipeline')

    def __init__(self, bitfile=None, timeout=5, mode='lookahead', prefetch=3, hardware=None, allocator=None):
        if mode not in self.MODES or type(prefetch) is not int or not 3 <= prefetch <= 8:
            raise ValueError('Use serial/lookahead/pipeline and prefetch3..8')
        if hardware is None:
            from hybrid_native_driver import HybridNativeCompressor
            hardware = HybridNativeCompressor(str(bitfile or ROOT/'overlay/compressor.bit'), timeout)
        self.hardware = hardware
        self.driver = NativeStreamDriver(hardware, allocator)
        self.mode = mode
        self.prefetch = prefetch
        self.lock = threading.Lock()
        self.closed = False
        self.last_profile = None
        self.last_stream_profile = None

    def _enter(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('A compression session already owns this system')
        try:
            if self.closed:
                raise RuntimeError('Compression system is closed')
            self.driver.check()
        except BaseException:
            self.lock.release()
            raise

    def _finish(self, token, completed, mode):
        native, frames, transfer = completed
        data, blocks, finish_ns = finish_packet(token['prepared'], frames, token['initial'])
        ready_ns = time.perf_counter_ns()
        profile = dict(backend='native_delta', scheduling=mode,
            blocks=blocks, hardware=transfer['hardware'], transfer=transfer,
            prepare_ns=token['prepared'].validation_ns, input_pack_ns=token['prepared'].packing_ns,
            finish_ns=finish_ns, ready_ns=ready_ns,
            prepared_to_output_ns=ready_ns-token['prepared'].started_ns,
            raw_bytes=2*token['prepared'].samples, frames=len(blocks), output_bytes=len(data),
            native_output_bytes=len(native), flac_jobs=0,
            boundary='owned input snapshot through native DMA/checks and delta-DEFLATE selection; no acquisition/I/O/decode')
        self.last_profile = profile
        return CompressedPacket(data, native, profile)

    def encode_records(self, records):
        self._enter()
        launched = False
        try:
            start = time.perf_counter_ns()
            p = prepare_packet(records)
            token = self.driver.prepare(p)
            self.driver.submit(token)
            launched = True
            result = self._finish(token, self.driver.complete(token), 'serial')
            result.profile['encode_application_ns'] = time.perf_counter_ns()-start
            return result.data, result.native, result.profile
        except BaseException:
            if launched and not self.driver.failed:
                self.driver.abort()
            raise
        finally:
            self.lock.release()

    def _iterate(self, source, mode, totals):
        started = False
        complete = False
        held = set()
        try:
            try:
                current_ticket, prepared = source.get()
            except StopIteration:
                complete = True
                return
            held.add(current_ticket)
            token = self.driver.prepare(prepared)
            self.driver.submit(token)
            started = True
            prepared = None
            while True:
                following = None
                source_pending = False
                if mode != 'serial':
                    try:
                        ticket, prepared = source.get(block=mode!='pipeline')
                    except queue.Empty:
                        source_pending = True
                    except StopIteration:
                        pass
                    else:
                        held.add(ticket)
                        following = ticket, self.driver.prepare(prepared)
                        prepared = None
                completed = self.driver.complete(token)
                if following is not None:
                    self.driver.submit(following[1])
                    totals['overlapped_next_submissions'] += 1
                result = self._finish(token, completed, mode)
                totals['packets'] += 1
                for key in ('frames', 'raw_bytes', 'output_bytes', 'native_output_bytes'):
                    totals[key] += result.profile[key]
                totals['wait_ns'] += result.profile['transfer']['wait_ns']
                totals['finish_ns'] += result.profile['finish_ns']
                totals['latency_max_ns'] = max(totals['latency_max_ns'], result.profile['prepared_to_output_ns'])
                token = None
                completed = None
                yield result
                result = None
                source.release(current_ticket)
                held.remove(current_ticket)
                if mode == 'serial' or source_pending:
                    try:
                        ticket, prepared = source.get()
                    except StopIteration:
                        complete = True
                        return
                    held.add(ticket)
                    following = ticket, self.driver.prepare(prepared)
                    prepared = None
                    self.driver.submit(following[1])
                elif following is None:
                    complete = True
                    return
                current_ticket, token = following
                following = None
        finally:
            totals['status'] = 'COMPLETE' if complete else 'INTERRUPTED'
            if started and not complete and not self.driver.failed:
                self.driver.abort()
            for ticket in held:
                source.release(ticket)

    @contextmanager
    def open_stream(self, packets, mode=None):
        mode = self.mode if mode is None else mode
        if mode not in self.MODES:
            raise ValueError('Unknown stream scheduling mode')
        self._enter()
        source = None
        iterator = None
        start = time.perf_counter_ns()
        totals = dict(status='RUNNING', mode=mode, codec='native_delta', packets=0, frames=0,
            raw_bytes=0, output_bytes=0, native_output_bytes=0, wait_ns=0, finish_ns=0,
            latency_max_ns=0, overlapped_next_submissions=0, cma_bytes=self.driver.cma_bytes,
            flac_jobs=0, sink_ns=0,
            boundary='source iteration to output consumer; includes producer startup/join, all input preparation, DMA, selection and sink; source/sink costs separately recorded and may overlap DMA')
        self.last_stream_profile = totals
        try:
            source = PreparedSource(packets, depth=self.prefetch if mode=='pipeline' else 2 if mode=='lookahead' else 1,
                                    threaded=mode=='pipeline')
            iterator = self._iterate(source, mode, totals)
            yield iterator
        finally:
            try:
                try:
                    if iterator is not None:
                        iterator.close()
                finally:
                    if source is not None:
                        joined = source.close()
                        totals.update(source.snapshot(), producer_joined=joined)
                        if not joined:
                            self.driver.failed = self.hardware.failed = True
                            raise RuntimeError('Input source did not stop; give the source its own I/O timeout and reload before reuse')
            except BaseException:
                totals['status'] = 'INTERRUPTED'
                raise
            finally:
                if totals['status'] == 'RUNNING':
                    totals['status'] = 'INTERRUPTED'
                totals['elapsed_ns'] = time.perf_counter_ns()-start
                self.lock.release()

    def compress_stream(self, packets, sink, mode=None):
        if not callable(sink):
            raise TypeError('Provide a streaming sink; results are not accumulated internally')
        with self.open_stream(packets, mode) as stream:
            for result in stream:
                before = time.perf_counter_ns()
                try:
                    sink(result)
                finally:
                    self.last_stream_profile['sink_ns'] += time.perf_counter_ns()-before
        return self.last_stream_profile

    def compress_blocks(self, blocks, sink, batch_size=8, mode=None):
        return self.compress_stream(batch_blocks(blocks,batch_size),sink,mode)

    def close(self):
        if self.closed:
            return
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Close the active stream context before closing the system')
        try:
            self.driver.close()
            self.closed = True
        finally:
            self.lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
