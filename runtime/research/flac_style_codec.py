"""FSR1: FLAC-style subframes inside the project's independent 24-byte frames.

Software reference, not a .flac container or an FPGA implementation. Implements
RFC 9639 constant, fixed 0..4, LPC 1..12 and partitioned Rice/escape subframes.
All int16 input values remain exact. Floating point is used only to design LPC
coefficients; the transmitted model and the decoder use integer arithmetic.
Requires numpy for encoding; decoding uses only standard-library arithmetic.
"""
from dataclasses import dataclass
import math
import struct
import zlib

import numpy as np

MAGIC = b'FSR1'
PREFIX = struct.Struct('<4sBBBBIHHI')
HEADER = struct.Struct('<4sBBBBIHHII')
MAX_BLOCK = 1024
MAX_LPC_ORDER = 12
MAX_PARTITION_ORDER = 6
PROFILES = ('fixed_global', 'fixed_partition', 'lpc_global', 'lpc_partition')
FIXED = ((), (1,), (2, -1), (3, -3, 1), (4, -6, 4, -1))


def raw_bytes(values):
    return struct.pack('<' + 'h'*len(values), *values)


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.pending = self.bits = self.length = 0

    def put(self, value, width):
        if width < 0 or value < 0 or value.bit_length() > width:
            raise ValueError('Bit field does not fit')
        self.bits = (self.bits << width) | value
        self.pending += width
        self.length += width
        while self.pending >= 8:
            self.pending -= 8
            self.data.append((self.bits >> self.pending) & 255)
        self.bits &= (1 << self.pending)-1

    def signed(self, value, width):
        if width == 0:
            if value:
                raise ValueError('Nonzero zero-width residual')
        elif not -(1 << (width-1)) <= value < (1 << (width-1)):
            raise ValueError('Signed field does not fit')
        self.put(value & ((1 << width)-1), width)

    def finish(self):
        if self.pending:
            self.data.append(self.bits << (8-self.pending))
        return bytes(self.data)


class Reader:
    def __init__(self, data):
        self.number = int.from_bytes(data, 'big')
        self.remaining = len(data)*8

    def get(self, width):
        if width < 0 or width > self.remaining:
            raise ValueError('Truncated subframe bit field')
        self.remaining -= width
        return (self.number >> self.remaining) & ((1 << width)-1)

    def signed(self, width):
        if width == 0:
            return 0
        value = self.get(width)
        return value-(1 << width) if value & (1 << (width-1)) else value

    def unary(self):
        # A bounded frame has at most 16384 payload bits; a missing stop bit
        # terminates in get(), rather than reading past the supplied payload.
        zeros = 0
        while self.get(1) == 0:
            zeros += 1
        return zeros

    def finish(self):
        if self.remaining > 7 or self.get(self.remaining):
            raise ValueError('Extra payload or nonzero subframe padding')


@dataclass
class Model:
    kind: str
    order: int
    seeds: tuple
    coefficients: tuple
    precision: int
    shift: int
    residual: object

    @property
    def overhead(self):
        return 8+16*self.order + (9+self.precision*self.order if self.kind == 'lpc' else 0)


@dataclass
class Plan:
    partition_order: int
    parameter_bits: int
    partitions: tuple  # (Rice k, escaped signed width, residual count); width=-1 for Rice
    bits: int


def partition_plans(residual, count, order):
    """Exact bit costs, including parameters and zero-width escape partitions."""
    residual = np.asarray(residual, dtype=np.int64)
    if len(residual) != count-order or not len(residual):
        raise ValueError('Invalid residual count')
    if np.any(np.abs(residual) >= (1 << 31)):
        raise ValueError('Residual outside RFC 9639 signed limit')
    folded = np.where(residual < 0, -2*residual-1, 2*residual)
    maximum_k = min(30, int(folded.max()).bit_length())
    ks = np.arange(maximum_k+1, dtype=np.int64)
    quotient = folded[None, :] >> ks[:, None]
    cumulative = np.concatenate((np.zeros((len(ks), 1), dtype=np.int64),
                                 np.cumsum(quotient, axis=1)), axis=1)
    choices = []
    for p in range(MAX_PARTITION_ORDER+1):
        parts = 1 << p
        if count % parts or count//parts <= order:
            break
        step = count//parts
        starts = np.maximum(np.arange(parts)*step-order, 0)
        ends = np.arange(1, parts+1)*step-order
        lengths = ends-starts
        sums = cumulative[:, ends]-cumulative[:, starts]
        minima = np.minimum.reduceat(residual, starts)
        maxima = np.maximum.reduceat(residual, starts)
        signed_magnitude = np.maximum(maxima, ~minima)
        widths = np.frexp(np.maximum(signed_magnitude, 0).astype(np.float64))[1]+1
        widths[(minima == 0) & (maxima == 0)] = 0
        for parameter_bits in (4, 5):
            usable = min(len(ks), (1 << parameter_bits)-1)
            costs = sums[:usable]+lengths[None, :]*(ks[:usable, None]+1)+parameter_bits
            best_k = costs.argmin(axis=0)
            rice_cost = costs[best_k, np.arange(parts)]
            escaped_cost = parameter_bits+5+lengths*widths
            escaped_cost = np.where(widths <= 31, escaped_cost, np.iinfo(np.int64).max//4)
            escaped = escaped_cost < rice_cost
            parameters = tuple((int(k), int(w) if e else -1, int(n))
                               for k,w,e,n in zip(best_k, widths, escaped, lengths))
            choices.append(Plan(p, parameter_bits, parameters,
                                6+int(np.minimum(rice_cost, escaped_cost).sum())))
    global_plan = min((p for p in choices if p.partition_order == 0), key=lambda p:p.bits)
    partition_plan = min(choices, key=lambda p:(p.bits, p.partition_order, p.parameter_bits))
    return global_plan, partition_plan


def fixed_models(values):
    for order, coefficients in enumerate(FIXED):
        if len(values) <= order:
            break
        prediction = np.zeros(len(values)-order, dtype=np.int64)
        for lag, coefficient in enumerate(coefficients, 1):
            prediction += coefficient*values[order-lag:len(values)-lag]
        yield Model('fixed', order, tuple(map(int, values[:order])), coefficients, 0, 0,
                    values[order:]-prediction)


def lpc_models(values):
    """Hann-window autocorrelation and Levinson-Durbin; quantized integer FIR."""
    limit = min(MAX_LPC_ORDER, len(values)-1)
    if limit < 1:
        return
    windowed = values.astype(np.float64)*np.hanning(len(values))
    ac = [float(np.dot(windowed[:len(values)-lag], windowed[lag:]))
          for lag in range(limit+1)]
    if not math.isfinite(ac[0]) or ac[0] <= 0:
        return
    coefficients = []
    error = ac[0]
    for order in range(1, limit+1):
        if error <= ac[0]*1e-12:
            break
        reflection = (ac[order]-sum(c*ac[order-i] for i,c in enumerate(coefficients, 1)))/error
        if not math.isfinite(reflection) or abs(reflection) >= 1:
            break
        coefficients = [c-reflection*d for c,d in zip(coefficients, reversed(coefficients))]+[reflection]
        error *= 1-reflection*reflection
        for precision in (12, 15):
            bound = (1 << (precision-1))-1
            peak = max(map(abs, coefficients))
            shift = min(15, int(math.floor(math.log2(bound/peak)))) if peak else 15
            if shift < 0:
                continue
            # Error-feedback rounding controls accumulated coefficient error.
            quantized = []
            rounding_error = 0.0
            for c in coefficients:
                scaled = c*(1 << shift)+rounding_error
                q = int(round(scaled))
                rounding_error = scaled-q
                quantized.append(q)
            if any(q < -(1 << (precision-1)) or q > bound for q in quantized):
                continue
            accumulator = np.zeros(len(values)-order, dtype=np.int64)
            for lag, q in enumerate(quantized, 1):
                accumulator += q*values[order-lag:len(values)-lag]
            residual = values[order:]-(accumulator >> shift)
            if np.any(np.abs(residual) >= (1 << 31)):
                continue
            yield Model('lpc', order, tuple(map(int, values[:order])), tuple(quantized),
                        precision, shift, residual)


def pack_subframe(model, plan=None):
    writer = Writer()
    if model.kind == 'constant':
        writer.put(0, 8)
        writer.signed(model.seeds[0], 16)
        return writer.finish(), writer.length
    subtype = 8+model.order if model.kind == 'fixed' else 32+model.order-1
    writer.put(subtype << 1, 8)  # padding=0, wasted-bit flag=0
    for sample in model.seeds:
        writer.signed(sample, 16)
    if model.kind == 'lpc':
        writer.put(model.precision-1, 4)
        writer.put(model.shift, 5)
        for q in model.coefficients:
            writer.signed(q, model.precision)
    writer.put(plan.parameter_bits-4, 2)
    writer.put(plan.partition_order, 4)
    offset = 0
    for k,width,length in plan.partitions:
        chunk = model.residual[offset:offset+length]
        if width >= 0:
            writer.put((1 << plan.parameter_bits)-1, plan.parameter_bits)
            writer.put(width, 5)
            for r in chunk:
                writer.signed(int(r), width)
        else:
            writer.put(k, plan.parameter_bits)
            mask = (1 << k)-1
            for r in chunk:
                r = int(r)
                folded = -2*r-1 if r < 0 else 2*r
                writer.put(1, (folded >> k)+1)
                writer.put(folded & mask, k)
        offset += length
    if offset != len(model.residual) or writer.length != model.overhead+plan.bits:
        raise AssertionError('Cost model differs from emitted payload')
    return writer.finish(), writer.length


def unpack_subframe(payload, count):
    reader = Reader(payload)
    first = reader.get(8)
    if first & 0x81:
        raise ValueError('Unsupported subframe padding/wasted bits')
    subtype = first >> 1
    if subtype == 0:
        result = [reader.signed(16)]*count
        reader.finish()
        return result, dict(kind='constant', order=0, partition_order=0, parameter_bits=0, escaped_partitions=0)
    if 8 <= subtype <= 12:
        kind, order = 'fixed', subtype-8
    elif 32 <= subtype < 32+MAX_LPC_ORDER:
        kind, order = 'lpc', subtype-31
    else:
        raise ValueError('Unsupported predictor subtype')
    if count <= order:
        raise ValueError('Predictor order exceeds block')
    result = [reader.signed(16) for _ in range(order)]
    if kind == 'lpc':
        precision = reader.get(4)+1
        shift = reader.signed(5)
        if precision > 15 or shift < 0:
            raise ValueError('Invalid LPC coefficient precision/shift')
        coefficients = tuple(reader.signed(precision) for _ in range(order))
    else:
        coefficients, precision, shift = FIXED[order], 0, 0
    coding = reader.get(2)
    p = reader.get(4)
    if coding > 1 or p > MAX_PARTITION_ORDER or count % (1 << p) or count//(1 << p) <= order:
        raise ValueError('Invalid Rice method/partition order')
    parameter_bits = 4+coding
    step = count//(1 << p)
    escaped_partitions = 0
    for part in range(1 << p):
        length = step-order if part == 0 else step
        k = reader.get(parameter_bits)
        if k == (1 << parameter_bits)-1:
            width = reader.get(5)
            escaped_partitions += 1
        else:
            width = -1
        for _ in range(length):
            if width >= 0:
                residual = reader.signed(width)
            else:
                folded = (reader.unary() << k) | reader.get(k)
                residual = -(folded//2)-1 if folded & 1 else folded//2
            if abs(residual) >= (1 << 31):
                raise ValueError('Decoded residual out of range')
            accumulator = sum(q*result[-lag] for lag,q in enumerate(coefficients, 1))
            value = (accumulator >> shift)+residual
            if not -32768 <= value <= 32767:
                raise ValueError('Decoded sample outside int16 range')
            result.append(value)
    if len(result) != count:
        raise ValueError('Subframe sample count mismatch')
    reader.finish()
    return result, dict(kind=kind, order=order, precision=precision, shift=shift,
                        partition_order=p, parameter_bits=parameter_bits,
                        escaped_partitions=escaped_partitions)


def _frame(raw, channel, sequence, payload, coded):
    prefix = PREFIX.pack(MAGIC, 1, channel, int(coded), 0, sequence,
                         len(raw)//2, len(payload), zlib.crc32(raw))
    return prefix+struct.pack('<I', zlib.crc32(prefix))+payload


def encode_profiles(values, channel=0, sequence=1):
    if not 1 <= len(values) <= MAX_BLOCK or not 0 <= channel <= 3 or not 0 <= sequence <= 0xffffffff:
        raise ValueError('Invalid FSR1 block/channel/sequence')
    raw = raw_bytes(values)
    x = np.array(values, dtype=np.int64)
    selected = {p:None for p in PROFILES}
    best_bytes = {p:len(raw) for p in PROFILES}
    if np.all(x == x[0]):
        model = Model('constant', 0, (int(x[0]),), (), 0, 0, None)
        payload, _ = pack_subframe(model)
        return {p:_frame(raw, channel, sequence, payload if len(payload) < len(raw) else raw,
                         len(payload) < len(raw)) for p in PROFILES}
    for family, models in (('fixed', fixed_models(x)), ('lpc', lpc_models(x))):
        for model in models:
            global_plan, partition_plan = partition_plans(model.residual, len(x), model.order)
            for profile in PROFILES:
                if family == 'lpc' and not profile.startswith('lpc'):
                    continue
                plan = global_plan if profile.endswith('global') else partition_plan
                size = (model.overhead+plan.bits+7)//8
                if size < best_bytes[profile]:
                    best_bytes[profile] = size
                    selected[profile] = model, plan
    frames = {}
    cache = {}
    for profile, choice in selected.items():
        if choice is None:
            frames[profile] = _frame(raw, channel, sequence, raw, False)
            continue
        model, plan = choice
        key = id(model), id(plan)
        if key not in cache:
            cache[key] = pack_subframe(model, plan)[0]
        payload = cache[key]
        if len(payload) != best_bytes[profile] or len(payload) >= len(raw):
            raise AssertionError('Selected cost differs from emitted frame')
        frames[profile] = _frame(raw, channel, sequence, payload, True)
    return frames


def encode_block(values, channel=0, sequence=1, profile='lpc_partition'):
    if profile not in PROFILES:
        raise ValueError('Unknown FSR1 profile')
    return encode_profiles(values, channel, sequence)[profile]


def header(data, offset=0):
    if len(data)-offset < HEADER.size:
        raise ValueError('Truncated FSR1 header')
    fields = HEADER.unpack_from(data, offset)
    magic,version,ch,mode,param,seq,count,size,crc,meta = fields
    if zlib.crc32(data[offset:offset+20]) != meta or (magic,version,param) != (MAGIC,1,0):
        raise ValueError('Invalid FSR1 metadata/CRC')
    if ch > 3 or mode > 1 or not 1 <= count <= MAX_BLOCK:
        raise ValueError('Invalid FSR1 channel/mode/count')
    if mode == 0 and size != 2*count or mode == 1 and not 3 <= size < 2*count:
        raise ValueError('Invalid FSR1 payload size')
    return fields


@dataclass
class Record:
    channel: int
    sequence: int
    samples: list
    frame: bytes
    stats: dict


def decode_block(frame):
    fields = header(frame)
    ch,mode,seq,count,size,crc = fields[2],fields[3],fields[5],fields[6],fields[7],fields[8]
    if len(frame) != 24+size:
        raise ValueError('FSR1 frame length mismatch')
    if mode:
        samples, stats = unpack_subframe(frame[24:], count)
    else:
        samples = list(struct.unpack('<'+'h'*count, frame[24:]))
        stats = dict(kind='raw', order=0, partition_order=0, parameter_bits=0, escaped_partitions=0)
    if zlib.crc32(raw_bytes(samples)) != crc:
        raise ValueError('FSR1 original sample CRC mismatch')
    return Record(ch, seq, samples, bytes(frame), stats)


def split_records(data):
    sequences = [0]*4
    offset = 0
    while offset < len(data):
        fields = header(data, offset)
        if fields[5] != (sequences[fields[2]]+1) & 0xffffffff:
            raise ValueError('Missing/duplicate/reordered FSR1 frame')
        end = offset+24+fields[7]
        if end > len(data):
            raise ValueError('Truncated FSR1 payload')
        record = decode_block(data[offset:end])
        sequences[record.channel] = record.sequence
        yield record
        offset = end


def decode_channels(data, expected_counts):
    if len(expected_counts) != 4 or any(type(n) is not int or n < 0 for n in expected_counts):
        raise ValueError('Expected counts must contain four nonnegative integers')
    restored = {ch:[] for ch in range(4)}
    for record in split_records(data):
        restored[record.channel].extend(record.samples)
    if [len(restored[ch]) for ch in range(4)] != list(expected_counts):
        raise ValueError('Missing complete FSR1 frame(s)')
    return restored
