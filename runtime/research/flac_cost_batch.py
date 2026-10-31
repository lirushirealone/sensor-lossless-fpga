"""Common exact encoder front end for fair compiled-CPU vs FPGA cost engines."""
from dataclasses import dataclass
import ctypes as ct
import time

import numpy as np
import flac_style_codec as reference
import flac_runtime_codec as runtime


@dataclass
class CPlan:
    bits:int
    packed:object
    offset:int

    def materialize(self):
        a=self.packed; o=self.offset
        p,b,parts,bits=map(int,a[o:o+4])
        return reference.Plan(p,b,tuple(tuple(map(int,a[o+4+3*i:o+7+3*i])) for i in range(parts)),bits)


class CompiledCostEngine:
    __name__='compiled_cost_cpu'
    def __init__(self):
        runtime.ensure_native()
        self.last_profile=None

    def calculate(self,jobs):
        start=time.perf_counter_ns()
        results=[]
        for residual,count,order in jobs:
            x=np.ascontiguousarray(residual,dtype=np.int64)
            if len(x)!=count-order: raise ValueError('Invalid residual length')
            packed=(ct.c_int64*(2*runtime.PLAN_SIZE))()
            code=runtime._library.flac_partition_plans(x.ctypes.data_as(runtime.I64P),count,order,packed)
            if code: raise ValueError('Compiled cost input rejected')
            results.append((CPlan(int(packed[3]),packed,0),
                            CPlan(int(packed[runtime.PLAN_SIZE+3]),packed,runtime.PLAN_SIZE)))
        self.last_profile=dict(backend=self.__name__,jobs=len(jobs),cost_batch_ns=time.perf_counter_ns()-start,
                               residual_samples=sum(len(r) for r,n,o in jobs))
        return results


class BatchFlacEncoder:
    def __init__(self,engine):
        self.engine=engine
        self.__name__='flac_batch_'+engine.__name__
        runtime.ensure_native()
        self.last_profile=None

    def encode_block(self,values,channel=0,sequence=1,profile='lpc_partition'):
        if profile not in reference.PROFILES: raise ValueError('Unknown FSR1 profile')
        if not 1<=len(values)<=1024 or not 0<=channel<=3 or not 0<=sequence<=0xffffffff:
            raise ValueError('Invalid FSR1 block metadata')
        start=time.perf_counter_ns()
        raw=reference.raw_bytes(values)
        x=np.array(values,dtype=np.int64)
        if np.all(x==x[0]):
            model=reference.Model('constant',0,(int(x[0]),),(),0,0,None)
            payload,_=reference.pack_subframe(model)
            coded=len(payload)<len(raw)
            frame=reference._frame(raw,channel,sequence,payload if coded else raw,coded)
            self.last_profile=dict(backend=self.engine.__name__,jobs=0,constant=True,
                encode_ns=time.perf_counter_ns()-start)
            return frame
        models=list(reference.fixed_models(x))
        if profile.startswith('lpc'): models.extend(reference.lpc_models(x))
        predicted=time.perf_counter_ns()
        jobs=[(m.residual,len(x),m.order) for m in models]
        plans=self.engine.calculate(jobs)
        searched=time.perf_counter_ns()
        selected=None; best_bytes=len(raw)
        for model,pair in zip(models,plans):
            plan=pair[0] if profile.endswith('global') else pair[1]
            size=(model.overhead+plan.bits+7)//8
            if size<best_bytes:
                best_bytes=size; selected=model,plan
        if selected is None:
            frame=reference._frame(raw,channel,sequence,raw,False)
        else:
            model,plan=selected
            payload,_=runtime.pack_subframe(model,plan.materialize())
            if len(payload)!=best_bytes: raise AssertionError('Selected hardware cost differs from encoded bytes')
            frame=reference._frame(raw,channel,sequence,payload,True)
        finished=time.perf_counter_ns()
        self.last_profile=dict(backend=self.engine.__name__,jobs=len(jobs),prediction_ns=predicted-start,
            search_call_ns=searched-predicted,selection_and_pack_ns=finished-searched,
            encode_ns=finished-start,cost_engine=self.engine.last_profile.copy())
        return frame
