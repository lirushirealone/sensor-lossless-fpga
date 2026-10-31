"""Byte-compatible single-profile FSR1 encoder with compiled integer hotspots.

All predictor orders, coefficient precision, partition choices, cost overheads,
raw fallback and tie order are retained. Floating LPC fitting is imported from
the frozen reference; this module never changes it or the bitstream format.
"""
import ctypes as ct
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess

import numpy as np
import flac_style_codec as reference

ROOT = Path(__file__).resolve().parents[1]
PLAN_SIZE = 196
_library = None
_build_info = None
I64P = ct.POINTER(ct.c_int64)


def ensure_native():
    global _library, _build_info
    if _library is not None:
        return _build_info
    source = Path(__file__).with_name('flac_runtime_native.c')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = ROOT/'results/flac-runtime-v2/native'
    directory.mkdir(parents=True,exist_ok=True)
    suffix = '.dll' if os.name=='nt' else '.so'
    binary = directory/('flac_'+platform.machine()+'_'+sha[:16]+suffix)
    compiler = 'cached'
    if not binary.exists():
        if os.name=='nt':
            from windows_native_build import run_msvc
            compiler='MSVC Community';completed=run_msvc(directory,[source],binary,object_name='flac.obj')
        else:
            compiler = shutil.which('gcc')
            if not compiler:
                raise RuntimeError('gcc is needed to build integer runtime')
            completed = subprocess.run([compiler,'-O3','-std=c99','-fPIC','-shared',str(source),'-o',str(binary)],
                capture_output=True,text=True)
        (directory/'build.log').write_text(completed.stdout+'\n'+completed.stderr)
        if completed.returncode:
            raise RuntimeError('Native build failed: '+completed.stdout+completed.stderr)
    lib = ct.CDLL(str(binary))
    lib.flac_partition_plans.argtypes = [I64P,ct.c_int,ct.c_int,I64P]
    lib.flac_partition_plans.restype = ct.c_int
    lib.flac_pack_subframe.argtypes = [ct.c_int,ct.c_int,ct.c_int,ct.c_int,
        ct.POINTER(ct.c_int16),ct.POINTER(ct.c_int32),I64P,I64P,
        ct.POINTER(ct.c_uint8),ct.c_size_t,ct.POINTER(ct.c_uint64)]
    lib.flac_pack_subframe.restype = ct.c_int
    _library = lib
    _build_info = dict(source_sha256=sha,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                       compiler=compiler,binary=str(binary),architecture=platform.machine())
    return _build_info


def partition_plans(residual,count,order):
    ensure_native()
    x = np.ascontiguousarray(residual,dtype=np.int64)
    if len(x)!=count-order:
        raise ValueError('Invalid residual count')
    packed = (ct.c_int64*(2*PLAN_SIZE))()
    code = _library.flac_partition_plans(x.ctypes.data_as(I64P),count,order,packed)
    if code:
        raise ValueError('Native partition search rejected input: '+str(code))
    result = []
    for offset in (0,PLAN_SIZE):
        p,b,parts,bits = map(int,packed[offset:offset+4])
        parameters = tuple(tuple(map(int,packed[offset+4+3*i:offset+7+3*i])) for i in range(parts))
        result.append(reference.Plan(p,b,parameters,bits))
    return tuple(result)


def pack_subframe(model,plan=None):
    if model.kind=='constant':
        return reference.pack_subframe(model,plan)
    ensure_native()
    expected_bits = model.overhead+plan.bits
    capacity = (expected_bits+7)//8
    if capacity>65536 or capacity<1:
        raise ValueError('Unbounded native packing request')
    if sum(p[2] for p in plan.partitions)!=len(model.residual):
        raise ValueError('Partition sample counts differ')
    if model.kind not in ('fixed','lpc') or not 0<=model.order<=12:
        raise ValueError('Invalid predictor')
    seeds = (ct.c_int16*model.order)(*model.seeds)
    coefficients = (ct.c_int32*len(model.coefficients))(*model.coefficients)
    residual = np.ascontiguousarray(model.residual,dtype=np.int64)
    if np.any(np.abs(residual)>=1<<31):
        raise ValueError('Residual outside signed limit')
    packed = (ct.c_int64*PLAN_SIZE)()
    packed[:4] = (plan.partition_order,plan.parameter_bits,len(plan.partitions),plan.bits)
    for i,part in enumerate(plan.partitions):
        packed[4+3*i:7+3*i] = part
    output = (ct.c_uint8*capacity)()
    result = (ct.c_uint64*3)()
    code = _library.flac_pack_subframe(int(model.kind=='lpc'),model.order,model.precision,model.shift,
        seeds,coefficients,residual.ctypes.data_as(I64P),packed,output,capacity,result)
    if code or tuple(result)!=(capacity,expected_bits,len(residual)):
        raise AssertionError('Native bit packing differs from cost: '+str(code)+' '+str(tuple(result)))
    return bytes(output),expected_bits


def encode_block(values,channel=0,sequence=1,profile='lpc_partition'):
    if profile not in reference.PROFILES:
        raise ValueError('Unknown FSR1 profile')
    if not 1<=len(values)<=reference.MAX_BLOCK or not 0<=channel<=3 or not 0<=sequence<=0xffffffff:
        raise ValueError('Invalid FSR1 block/channel/sequence')
    raw = reference.raw_bytes(values)
    x = np.array(values,dtype=np.int64)
    if np.all(x==x[0]):
        model = reference.Model('constant',0,(int(x[0]),),(),0,0,None)
        payload,_ = reference.pack_subframe(model)
        coded = len(payload)<len(raw)
        return reference._frame(raw,channel,sequence,payload if coded else raw,coded)
    selected = None
    best_bytes = len(raw)
    families = [reference.fixed_models(x)]
    if profile.startswith('lpc'):
        families.append(reference.lpc_models(x))
    for models in families:
        for model in models:
            global_plan,partition_plan = partition_plans(model.residual,len(x),model.order)
            plan = global_plan if profile.endswith('global') else partition_plan
            size = (model.overhead+plan.bits+7)//8
            if size<best_bytes:
                best_bytes=size; selected=model,plan
    if selected is None:
        return reference._frame(raw,channel,sequence,raw,False)
    payload,_ = pack_subframe(*selected)
    if len(payload)!=best_bytes or len(payload)>=len(raw):
        raise AssertionError('Native result differs from selected cost')
    return reference._frame(raw,channel,sequence,payload,True)
