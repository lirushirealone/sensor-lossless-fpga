"""Bounded packet snapshots and unchanged native + delta-DEFLATE selection."""
from dataclasses import dataclass
import operator
import time
import numpy as np
from flac_prediction_runtime import prepare_values, encode_delta_prepared
import hybrid_codec as hybrid
import flac_hybrid_codec as combined
from screen_block_coding import frozen


@dataclass(frozen=True)
class PreparedPacket:
    records: tuple
    words: object
    samples: int
    maximum: int
    snapshot_bytes: int
    started_ns: int
    ready_ns: int
    validation_ns: int
    packing_ns: int


def batch_blocks(blocks, batch_size=8):
    """Copy each arriving block before advancing a source that may reuse it.

    This is throughput batching: source end flushes a short packet. A live
    source must select a suitable batch size or implement its own timed flush.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise ValueError('Use batch_size1..8')
    pending = []
    for ch, values in blocks:
        ch = operator.index(ch)
        if not 0 <= ch <= 3:
            raise ValueError('Use channel0..3')
        snapshot = prepare_values(values)
        pending.append((ch,snapshot.samples))
        snapshot = None
        if len(pending) == batch_size:
            yield pending
            pending = []
    if pending:
        yield pending


def prepare_packet(records):
    start = time.perf_counter_ns()
    prepared = []
    for ch, values in records:
        if len(prepared) == 8:
            raise ValueError('Use1..8 blocks per packet')
        ch = operator.index(ch)
        if not 0 <= ch <= 3:
            raise ValueError('Use channel0..3')
        prepared.append((ch, prepare_values(values)))
    if not prepared:
        raise ValueError('Use1..8 blocks per packet')
    validated = time.perf_counter_ns()
    samples = sum(len(p.samples) for ch, p in prepared)
    words = np.empty(1 + samples, dtype=np.uint32)
    words[0] = 0x4d430000 | len(prepared)
    offset = 1
    for ch, p in prepared:
        end = offset + len(p.samples)
        view = words[offset:end]
        view[:] = p.samples.view(np.uint16)
        np.bitwise_or(view, ch << 16, out=view)
        view[-1] |= 1 << 18
        offset = end
    words.flags.writeable = False
    ready = time.perf_counter_ns()
    snapshot = words.nbytes + sum(p.samples.nbytes + p.x64.nbytes + len(p.raw) for ch,p in prepared)
    return PreparedPacket(tuple(prepared), words, samples, 2*samples+24*len(prepared),
                          snapshot, start, ready, validated-start, ready-validated)


def native_frames(data):
    offset = 0
    while offset < len(data):
        f = frozen._header(data, offset)
        end = offset + 24 + f[7]
        if end > len(data):
            raise ValueError('Truncated native frame')
        yield f[2], f[5], f[6], data[offset:end]
        offset = end


def finish_packet(prepared, frames, sequences):
    start = time.perf_counter_ns()
    current = list(sequences)
    parts, traces = [], []
    for ch, p in prepared.records:
        current[ch] = (current[ch]+1) & 0xffffffff
        seq = current[ch]
        frame = frames[ch, seq]
        before = time.perf_counter_ns()
        delta = encode_delta_prepared(p, ch, seq)
        delta_done = time.perf_counter_ns()
        candidate = hybrid.choose_frames(frame, delta)
        fields = list(hybrid.header(candidate))
        fields[0] = combined.MAGIC
        result = combined.repack(fields, candidate[24:])
        parts.append(result)
        traces.append(dict(channel=ch, sequence=seq, samples=len(p.samples),
            native_bytes=len(frame), deflate_bytes=len(delta), selected_bytes=len(result),
            selected_mode=fields[3], deflate_ns=delta_done-before))
    data = b''.join(parts)
    return data, traces, time.perf_counter_ns()-start
