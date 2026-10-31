"""One overlay, two independent engines, complete candidates, bounded lookahead."""
from dataclasses import dataclass
import operator,threading,time,zlib
from pathlib import Path
import numpy as np
from pynq import Overlay,Clocks
from lz77_input import snapshot,Snapshot
from lz77_entropy_runtime import BulkReturnConsumer
from native_stream_driver import NativeStreamDriver
from unified_drivers import UnifiedLZDriver,UnifiedRiceHardware,UnifiedRiceDriver,prepare_native
from unified_codec import file_header,finish_job
from screen_block_coding import frozen
ROOT=Path(__file__).resolve().parents[1]

@dataclass
class UnifiedJob:
    channel:int
    begin:int
    owned:object

class UnifiedSystem:
    def __init__(self,bitfile=None,timeout=10.,group_size=2,hardware=None,codec=None):
        if type(group_size) is not int or group_size not in (1,2):raise ValueError('Group1/2 required')
        self.group_size=group_size;self._owner=threading.get_ident();self._running=False;self.closed=False;self.failed=False
        self.lz=self.rice=self.rice_hw=None;self.codec=codec or BulkReturnConsumer()
        if hardware is not None:self.lz,self.rice=hardware;self.overlay=None;return
        self.overlay=Overlay(str(bitfile or ROOT/'unified-overlay/compressor.bit'))
        try:
            self.lz=UnifiedLZDriver(self.overlay,timeout);self.rice_hw=UnifiedRiceHardware(self.overlay,timeout);self.rice=UnifiedRiceDriver(self.rice_hw)
            if abs(float(Clocks.fclk0_mhz)-66.666667)>1e-4 or abs(float(Clocks.fclk1_mhz)-50)>1e-4:raise RuntimeError('Physical clock differs from routed clocks')
            # Complete real first transfers before idle-close. Also initialize
            # both host/hardware sequences together; outside stream timing.
            self.lz.transform([0]);o=snapshot([0]);t=self.rice.prepare(prepare_native(((0,o),)));self.rice.submit(t);self.rice.complete(t)
        except BaseException:
            try:self.abort()
            finally:
                if self.lz is not None and not self.lz.active:self.lz.close()
                if self.rice is not None and self.rice.active is None:self.rice.close()
                elif self.rice_hw is not None:self.rice_hw.close()
            raise
    @property
    def cma_bytes(self):return self.lz.cma_bytes+self.rice.cma_bytes
    def check(self):
        if threading.get_ident()!=self._owner:raise RuntimeError('Unified system requires one owning thread')
        if self.closed or self.failed:raise RuntimeError('Reload unified system after failure/close')
        self.lz.check();self.rice.check()
    def abort(self):
        if threading.get_ident()!=self._owner:raise RuntimeError('Abort from owning thread')
        self.failed=True;errors=[]
        # Attempt BOTH resets even if one fails. No release on reset failure.
        for d in (self.lz,self.rice):
            if d is not None:
                try:d.abort()
                except BaseException as exc:errors.append(exc)
        if self.rice is None and self.rice_hw is not None:
            try:
                hw=self.rice_hw;hw.failed=True;hw.dma.mmio.write(0,4);deadline=time.monotonic()+1
                while hw.dma.mmio.read(0)&4:
                    if time.monotonic()>=deadline:raise RuntimeError('Partial Rice constructor reset failed; retain CMA')
                for channel in (hw.dma.sendchannel,hw.dma.recvchannel):
                    if hasattr(channel,'_active_buffer'):channel._active_buffer=None
            except BaseException as exc:errors.append(exc)
        if errors:raise errors[0]
    def _group(self,jobs):
        lz=self.lz.prepare_owned(tuple(j.owned for j in jobs));parts=[]
        for begin in range(0,max(len(j.owned.samples) for j in jobs),1024):
            for i,j in enumerate(jobs):
                raw=j.owned.raw[2*begin:2*(begin+1024)]
                if raw:
                    o=Snapshot(raw,np.frombuffer(raw,dtype='<i2'),zlib.crc32(raw));parts.append((i,begin,j.channel,o))
        packets=[]
        for begin in range(0,len(parts),8):
            p=parts[begin:begin+8];packets.append((p,prepare_native(tuple((ch,o) for _,_,ch,o in p))))
        return dict(jobs=jobs,lz=lz,packets=packets,first=None)
    def _start(self,g):
        self.lz.submit(g['lz']);token=self.rice.prepare(g['packets'][0][1]);self.rice.submit(token);g['first']=token
    def _complete(self,g):
        natives=[[] for _ in g['jobs']];proof=[]
        for index,(parts,prepared) in enumerate(g['packets']):
            if index==0:token=g['first']
            else:token=self.rice.prepare(prepared);self.rice.submit(token)
            data,frames,profile=self.rice.complete(token);seq=token['initial'].copy()
            for i,begin,ch,o in parts:
                seq[ch]=(seq[ch]+1)&0xffffffff;frame=frames[ch,seq[ch]];f=frozen._header(frame)
                if len(frame)!=24+f[7] or (f[2],f[5],f[6],f[8])!=(ch,seq[ch],len(o.samples),o.raw_crc32):raise ValueError('Rice candidate source metadata/CRC mismatch')
                natives[i].append(frame)
            proof.append(dict(data=data,profile=profile,parts=[dict(job=i,begin=b,channel=ch,samples=len(o.samples),crc32=o.raw_crc32) for i,b,ch,o in parts]))
        members,wire,profile=self.lz.finish(g['lz'])
        if len(members)!=len(g['jobs']):raise RuntimeError('LZ group member mismatch')
        return natives,members,dict(lz_wire=wire,lz_profile=profile,rice=proof)
    def compress_stream(self,records,sink,expected_counts,observer=None,batch_observer=None):
        self.check()
        if self._running:raise RuntimeError('Compression session already active')
        if not callable(sink):raise TypeError('Provide streaming sink')
        header=file_header(expected_counts,self.group_size);self._running=True;start=time.perf_counter_ns();seen=[0]*4;seq=[0]*4;it=iter(records)
        totals=dict(status='RUNNING',jobs=0,batches=0,samples=0,frames=0,bytes=64,first_emit_ns=None,cma_bytes=self.cma_bytes,group_size=self.group_size,methods={},candidate_selection_ns=0,source_snapshot_ns=0)
        def following():
            jobs=[]
            for _ in range(self.group_size):
                try:ch,begin,values=next(it)
                except StopIteration:break
                ch=operator.index(ch);begin=operator.index(begin)
                if not 0<=ch<4 or begin!=seen[ch]:raise ValueError('Source channel/contiguous offset mismatch')
                t=time.perf_counter_ns();o=snapshot(values);totals['source_snapshot_ns']+=time.perf_counter_ns()-t
                if seen[ch]+len(o.samples)>expected_counts[ch]:raise ValueError('Source exceeds declared count')
                seen[ch]+=len(o.samples);jobs.append(UnifiedJob(ch,begin,o))
                # Snapshot before advancing a producer that reuses its buffer.
            return self._group(jobs) if jobs else None
        try:
            sink(header);current=following()
            if current is not None:self._start(current)
            while current is not None:
                natives,members,proof=self._complete(current);nxt=following()
                if nxt is not None:self._start(nxt)
                for job,native,((packet,tokens,meta,profile),raw) in zip(current['jobs'],natives,members):
                    if raw!=job.owned.raw or meta['raw_crc32']!=job.owned.raw_crc32:raise ValueError('LZ result source mismatch')
                    t=time.perf_counter_ns();payload=self.codec.pack(tokens,len(raw),meta['residual_adler32']);output,choice=finish_job(job.owned,native,payload,job.channel,seq);finish=time.perf_counter_ns()
                    sink(output);emit=time.perf_counter_ns();totals['candidate_selection_ns']+=finish-t
                    totals['jobs']+=1;totals['samples']+=choice['samples'];totals['frames']+=choice['frames'];totals['bytes']+=len(output);totals['methods'][choice['method']]=totals['methods'].get(choice['method'],0)+1
                    if totals['first_emit_ns'] is None:totals['first_emit_ns']=emit-start
                    if observer is not None:observer(job,packet,native,meta,profile,choice,output)
                if batch_observer is not None:batch_observer(proof)
                totals['batches']+=1;current=nxt
            if list(expected_counts)!=seen:raise ValueError('Source ended before declared sample counts')
            totals.update(status='COMPLETE',counts=seen,total_ns=time.perf_counter_ns()-start,timing='Actual iterator through owned snapshots, both real DMA engines, all legacy1024 and large8192 candidates, CRC/sequence checks, final file framing and sink/callbacks. Setup/load/fileIO/decode outside unless source/sink performs them. Overlapping device/host intervals are not additive.')
            self.last_profile=totals;return totals
        except BaseException:
            if not self.failed:self.abort()
            raise
        finally:self._running=False
    def close(self):
        if self.closed:return
        if threading.get_ident()!=self._owner:raise RuntimeError('Close from owning thread')
        if self._running:raise RuntimeError('Session still owns unified system')
        if self.lz.active or self.rice.active is not None:self.abort()
        # Both DMA safe checks precede either free, including reset failure.
        for dma in (self.lz.dma,self.rice.dma):
            if dma.mmio.read(0)&4 or dma.mmio.read(0x30)&4 or not all(dma.mmio.read(a)&3 for a in (4,0x34)):raise RuntimeError('Unsafe DMA; retain all CMA')
        self.rice.close();self.lz.close();self.closed=True
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
