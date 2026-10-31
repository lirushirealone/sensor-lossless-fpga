"""A single CPU producer; credits bound all pulled-but-undelivered packets."""
import queue
import threading
import time
from stream_codec import prepare_packet

_END = object()


class SourceFailure:
    def __init__(self, error):
        self.error = error


class PreparedSource:
    def __init__(self, packets, depth=3, threaded=True):
        if type(depth) is not int or not 1 <= depth <= 8:
            raise ValueError('Prefetch must be1..8 packets')
        self.iterator = iter(packets)
        self.depth = depth
        self.threaded = threaded
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.credits = threading.BoundedSemaphore(depth)
        self.queue = queue.Queue(maxsize=depth)
        self.leases = {}
        self.number = 0
        self.stats = dict(pulled_packets=0, peak_owned_packets=0, peak_snapshot_bytes=0,
                          source_ns=0, prepare_ns=0, validation_ns=0, packing_ns=0)
        self.closed = False
        self.cleanup_error = None
        self.thread = None
        if threaded:
            self.thread = threading.Thread(target=self._producer, name='compression-input', daemon=True)
            self.thread.start()

    def _pull(self):
        ticket = None
        try:
            before = time.perf_counter_ns()
            try:
                records = next(self.iterator)
            finally:
                with self.lock:
                    self.stats['source_ns'] += time.perf_counter_ns()-before
            with self.lock:
                ticket = self.number
                self.number += 1
                self.leases[ticket] = 0
                self.stats['pulled_packets'] += 1
                self.stats['peak_owned_packets'] = max(self.stats['peak_owned_packets'], len(self.leases))
            prepared = prepare_packet(records)
            records = None
            with self.lock:
                self.leases[ticket] = prepared.snapshot_bytes
                self.stats['prepare_ns'] += prepared.ready_ns-prepared.started_ns
                self.stats['validation_ns'] += prepared.validation_ns
                self.stats['packing_ns'] += prepared.packing_ns
                self.stats['peak_snapshot_bytes'] = max(self.stats['peak_snapshot_bytes'], sum(self.leases.values()))
            return ticket, prepared
        except BaseException:
            if ticket is not None:
                self.release(ticket)
            elif self.threaded:
                self.credits.release()
            raise

    def _put(self, item):
        while not self.stop.is_set():
            try:
                self.queue.put(item, timeout=.05)
                return True
            except queue.Full:
                pass
        return False

    def _producer(self):
        try:
            while not self.stop.is_set():
                if not self.credits.acquire(timeout=.05):
                    continue
                try:
                    item = self._pull()
                except StopIteration:
                    self._put(_END)
                    return
                except BaseException as error:
                    self._put(SourceFailure(error))
                    return
                if not self._put(item):
                    self.release(item[0])
                    return
                item = None
        finally:
            close = getattr(self.iterator, 'close', None)
            if close:
                try:
                    close()
                except BaseException as error:
                    self.cleanup_error = error

    def get(self, block=True):
        if self.closed:
            raise RuntimeError('Prepared source is closed')
        if not self.threaded:
            if not block:
                raise ValueError('Nonblocking fetch requires a background source')
            return self._pull()
        while True:
            try:
                item = self.queue.get(timeout=.05) if block else self.queue.get_nowait()
            except queue.Empty:
                if not self.thread.is_alive():
                    raise RuntimeError('Input producer ended without a terminal message')
                if not block:
                    raise
                continue
            if item is _END:
                raise StopIteration
            if isinstance(item, SourceFailure):
                raise item.error
            return item

    def release(self, ticket):
        with self.lock:
            found = ticket in self.leases
            if found:
                self.leases.pop(ticket)
        if found and self.threaded:
            self.credits.release()

    def snapshot(self):
        with self.lock:
            return dict(self.stats, owned_packets=len(self.leases),
                        snapshot_bytes=sum(self.leases.values()), prefetch_limit=self.depth)

    def close(self):
        if self.closed:
            return self.thread is None or not self.thread.is_alive()
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        else:
            close = getattr(self.iterator, 'close', None)
            if close:
                close()
        joined = self.thread is None or not self.thread.is_alive()
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self.release(item[0])
        if joined:
            for ticket in list(self.leases):
                self.release(ticket)
        self.closed = True
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return joined
