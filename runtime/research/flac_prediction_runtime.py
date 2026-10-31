"""Exact ARM frontend acceleration; all frozen predictor choices are retained.

LPC fitting uses the same NumPy dots and the same Python recurrence/rounding.
Only integer FIR residuals are replaced by a single bounded compiled call.
This module neither edits nor monkey-patches the frozen reference.
"""
import ctypes as ct
from dataclasses import dataclass
import hashlib
import math
import operator
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import time
import zlib

import numpy as np
import flac_style_codec as reference
import flac_runtime_codec as runtime
from flac_cost_batch import BatchFlacEncoder

ROOT=Path(__file__).resolve().parents[1]
REFERENCE_SHA='648797af1ac7018f2037d795a573bc54971f0d11fbb5e05eba97fe9f844e8c45'
_library=None
_build_info=None


def ensure_native():
    global _library,_build_info
    if _library is not None: return _build_info
    if hashlib.sha256(Path(reference.__file__).read_bytes()).hexdigest()!=REFERENCE_SHA:
        raise ValueError('Frozen predictor reference changed')
    source=Path(__file__).with_name('flac_prediction_native.c')
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    directory=ROOT/'results/arm-frontend-v3/native'
    directory.mkdir(parents=True,exist_ok=True)
    binary=directory/('predict_'+platform.machine()+'_'+sha[:16]+('.dll' if os.name=='nt' else '.so'))
    compiler='cached'
    if not binary.exists():
        if os.name=='nt':
            found=sorted(Path('C:/Program Files/Microsoft Visual Studio').glob('*/Community/VC/Auxiliary/Build/vcvars64.bat'))
            if not found: raise RuntimeError('MSVC x64 compiler is unavailable')
            compiler=str(found[-1])
            build=directory/'build-prediction.bat'
            build.write_text('@echo off\ncall "'+compiler+'" >nul\nif errorlevel 1 exit /b 1\n'
                'cl /nologo /O2 /LD /TC "'+str(source)+'" /Fo:"'+str(directory/'prediction.obj')+'" '
                '/link /OUT:"'+str(binary)+'"\n',encoding='utf-8')
            completed=subprocess.run(['cmd','/d','/c',str(build)],cwd=directory,capture_output=True,text=True)
        else:
            compiler=shutil.which('gcc')
            if not compiler: raise RuntimeError('gcc is unavailable')
            completed=subprocess.run([compiler,'-O3','-std=c99','-fPIC','-shared',str(source),'-o',str(binary)],capture_output=True,text=True)
        (directory/'build.log').write_text(completed.stdout+'\n'+completed.stderr)
        if completed.returncode: raise RuntimeError('Integer prediction compilation failed: '+completed.stderr)
    lib=ct.CDLL(str(binary))
    lib.flac_predict_many.argtypes=[ct.POINTER(ct.c_int16),ct.c_int,ct.c_int,
        ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32),
        ct.POINTER(ct.c_int64),ct.POINTER(ct.c_uint8)]
    lib.flac_predict_many.restype=ct.c_int
    _library=lib
    _build_info=dict(source_sha256=sha,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        binary=str(binary),compiler=compiler,architecture=platform.machine(),reference_sha256=REFERENCE_SHA)
    return _build_info


@dataclass(frozen=True)
class PreparedBlock:
    samples:object
    x64:object
    raw:bytes


def prepare_values(values):
    # Range/type validation always precedes narrowing. Make an owned snapshot
    # so reusing it cannot observe a caller modifying an array during encoding.
    x=np.asarray(values)
    if x.ndim!=1 or not 1<=len(x)<=1024: raise ValueError('Use1..1024 samples')
    if x.dtype.kind not in 'iub':
        if x.dtype.kind!='O': raise ValueError('Every sample must be an integer')
        try: values=[operator.index(v) for v in values]
        except TypeError as error: raise ValueError('Every sample must be an integer') from error
        if min(values)<-32768 or max(values)>32767: raise ValueError('Every sample must fit int16')
        x=np.asarray(values,dtype=np.int64)
    if int(x.min())<-32768 or int(x.max())>32767: raise ValueError('Every sample must fit int16')
    samples=np.array(x,dtype='<i2',order='C',copy=True)
    x64=samples.astype(np.int64)
    samples.flags.writeable=False; x64.flags.writeable=False
    return PreparedBlock(samples,x64,samples.tobytes())


def lpc_parameters(values):
    """Exact copy of reference fitting order, excluding integer FIR work."""
    limit=min(reference.MAX_LPC_ORDER,len(values)-1)
    if limit<1: return
    windowed=values.astype(np.float64)*np.hanning(len(values))
    ac=[float(np.dot(windowed[:len(values)-lag],windowed[lag:])) for lag in range(limit+1)]
    if not math.isfinite(ac[0]) or ac[0]<=0: return
    coefficients=[]; error=ac[0]
    for order in range(1,limit+1):
        if error<=ac[0]*1e-12: break
        reflection=(ac[order]-sum(c*ac[order-i] for i,c in enumerate(coefficients,1)))/error
        if not math.isfinite(reflection) or abs(reflection)>=1: break
        coefficients=[c-reflection*d for c,d in zip(coefficients,reversed(coefficients))]+[reflection]
        error*=1-reflection*reflection
        for precision in (12,15):
            bound=(1<<(precision-1))-1
            peak=max(map(abs,coefficients))
            shift=min(15,int(math.floor(math.log2(bound/peak)))) if peak else 15
            if shift<0: continue
            quantized=[]; rounding_error=0.0
            for c in coefficients:
                scaled=c*(1<<shift)+rounding_error
                q=int(round(scaled)); rounding_error=scaled-q
                quantized.append(q)
            if any(q<-(1<<(precision-1)) or q>bound for q in quantized): continue
            yield 'lpc',order,tuple(quantized),precision,shift


def model_candidates(prepared,include_lpc=True):
    ensure_native()
    x=prepared.x64
    params=[('fixed',o,c,0,0) for o,c in enumerate(reference.FIXED) if len(x)>o]
    if include_lpc: params.extend(lpc_parameters(x))
    orders=np.array([p[1] for p in params],dtype=np.int32)
    shifts=np.array([p[4] for p in params],dtype=np.int32)
    coefficients=np.zeros((len(params),12),dtype=np.int32)
    for i,p in enumerate(params): coefficients[i,:len(p[2])]=p[2]
    residuals=np.empty((len(params),1024),dtype=np.int64)
    valid=np.empty(len(params),dtype=np.uint8)
    code=_library.flac_predict_many(prepared.samples.ctypes.data_as(ct.POINTER(ct.c_int16)),len(x),len(params),
        orders.ctypes.data_as(ct.POINTER(ct.c_int32)),shifts.ctypes.data_as(ct.POINTER(ct.c_int32)),
        coefficients.ctypes.data_as(ct.POINTER(ct.c_int32)),residuals.ctypes.data_as(ct.POINTER(ct.c_int64)),
        valid.ctypes.data_as(ct.POINTER(ct.c_uint8)))
    if code: raise ValueError('Compiled prediction rejected parameters: '+str(code))
    return [reference.Model(kind,o,tuple(map(int,x[:o])),c,precision,shift,residuals[i,:len(x)-o])
        for i,(kind,o,c,precision,shift) in enumerate(params) if valid[i]]


def encode_delta_prepared(prepared,ch,sequence):
    residual=np.empty(len(prepared.x64),dtype='<i2')
    residual[0]=prepared.samples[0]
    # Subtraction in int64 then explicit modulo16, matching the reference.
    difference=prepared.x64[1:]-prepared.x64[:-1]
    residual[1:]=((difference+32768)&65535)-32768
    payload=zlib.compress(residual.tobytes(),6)
    mode=int(len(payload)<len(prepared.raw))
    if not mode: payload=prepared.raw
    prefix=reference.PREFIX.pack(b'ZSC1',1,ch,mode,6,sequence,len(prepared.samples),len(payload),zlib.crc32(prepared.raw))
    return prefix+struct.pack('<I',zlib.crc32(prefix))+payload


class FastBatchFlacEncoder(BatchFlacEncoder):
    def __init__(self,engine):
        super().__init__(engine)
        ensure_native()
        self.__name__='native_prediction_'+engine.__name__

    def encode_block(self,values,channel=0,sequence=1,profile='lpc_partition'):
        start=time.perf_counter_ns()
        prepared=prepare_values(values)
        return self.encode_prepared(prepared,channel,sequence,profile,start)

    def encode_prepared(self,prepared,channel=0,sequence=1,profile='lpc_partition',start=None):
        if profile not in reference.PROFILES or not 0<=channel<=3 or not 0<=sequence<=0xffffffff:
            raise ValueError('Invalid FSR1 metadata/profile')
        if not isinstance(prepared,PreparedBlock): raise TypeError('Use validated PreparedBlock')
        if start is None: start=time.perf_counter_ns()
        raw=prepared.raw; x=prepared.x64
        if np.all(x==x[0]):
            model=reference.Model('constant',0,(int(x[0]),),(),0,0,None)
            payload,_=reference.pack_subframe(model)
            coded=len(payload)<len(raw)
            frame=reference._frame(raw,channel,sequence,payload if coded else raw,coded)
            self.last_profile=dict(backend=self.engine.__name__,jobs=0,constant=True,
                encode_ns=time.perf_counter_ns()-start)
            return frame
        models=model_candidates(prepared,profile.startswith('lpc'))
        predicted=time.perf_counter_ns()
        plans=self.engine.calculate([(m.residual,len(x),m.order) for m in models])
        searched=time.perf_counter_ns()
        selected=None; best_bytes=len(raw)
        for model,pair in zip(models,plans):
            plan=pair[0] if profile.endswith('global') else pair[1]
            size=(model.overhead+plan.bits+7)//8
            if size<best_bytes: best_bytes=size; selected=model,plan
        if selected is None: frame=reference._frame(raw,channel,sequence,raw,False)
        else:
            model,plan=selected
            payload,_=runtime.pack_subframe(model,plan.materialize())
            if len(payload)!=best_bytes: raise AssertionError('Native prediction cost/packing differ')
            frame=reference._frame(raw,channel,sequence,payload,True)
        finished=time.perf_counter_ns()
        self.last_profile=dict(backend=self.engine.__name__,jobs=len(models),prediction_ns=predicted-start,
            search_call_ns=searched-predicted,selection_and_pack_ns=finished-searched,
            encode_ns=finished-start,cost_engine=self.engine.last_profile.copy())
        return frame
