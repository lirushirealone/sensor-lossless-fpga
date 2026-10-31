"""Deterministic state/ownership tests; hardware timing is NOT simulated here."""
import importlib.util,struct,sys,threading,time,types,unittest,zlib
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
from lz77_pipeline import compress_records,compress_cpu_records
from lz77_protocol import expand,TRAILER
from lz77_source import blocks



class FakeBuffer(np.ndarray):
    serial=0
    def __new__(cls,shape,dtype):
        a=np.zeros(shape,dtype).view(cls);a.frees=0;cls.serial+=1;a.physical_address=cls.serial*0x100000;return a
    def __array_finalize__(self,other):
        if other is not None:self.frees=getattr(other,'frees',0);self.physical_address=getattr(other,'physical_address',0)
    def freebuffer(self):self.frees+=1


class FakeChannel:
    def __init__(self,dma,receive):self.dma=dma;self.receive=receive;self._active_buffer=None;self.transferred=0
    def transfer(self,buffer):
        self._active_buffer=buffer
        if not self.receive:self.dma.execute(buffer)
    def wait(self):self._active_buffer=None


class FakeDMA:
    def __init__(self,owner):
        self.owner=owner;self.sendchannel=FakeChannel(self,False);self.recvchannel=FakeChannel(self,True)
        self.mmio=types.SimpleNamespace(read=lambda offset:0);self.seq=0;self.stuck=False
    def execute(self,words):
        count=int(words[0])&65535;source=np.asarray(words[1:1+count],dtype='<u2');raw=source.tobytes()
        delta=source.copy();np.subtract(source[1:],source[:-1],out=delta[1:]);residual=delta.tobytes()
        tokens=np.frombuffer(residual,dtype='u1').astype('<u4').tobytes();self.seq+=1
        prefix=struct.pack('<4sBBHIIIII',b'LZT1',1,0,count,self.seq,len(residual),zlib.crc32(raw),zlib.adler32(residual),zlib.crc32(tokens))
        packet=tokens+prefix+struct.pack('<I',zlib.crc32(prefix))
        self.recvchannel._active_buffer[:len(packet)]=np.frombuffer(packet,dtype='u1');self.recvchannel.transferred=len(packet)
        self.owner.fake_profile=dict(total_cycles=2*count+4,collect_cycles=count+1,process_cycles=count+3,output_stalls=0,
            input_samples=count,output_bytes=len(packet),sequence=self.seq,input_gap_cycles=0,matcher_work_cycles=count+2,search_probes=count)


class FakeBase:
    def __init__(self,bitfile,timeout=10.):
        self.timeout=timeout;self.failed=False;self.closed=False;self.active=False;self.sequence=0;self.clock_mhz=50.
        self.input=FakeBuffer((8193,),np.uint32);self.output=FakeBuffer((65600,),np.uint8);self.dma=FakeDMA(self);self.reset_fail=False
    def check(self):
        if self.failed or self.closed:raise RuntimeError('Driver unavailable')
    def status(self):return dict(send=0 if self.dma.stuck else 2,receive=0 if self.dma.stuck else 2)
    def profile(self,*args):return dict(self.fake_profile)
    def abort(self):
        self.failed=True
        if self.reset_fail:raise RuntimeError('Reset failed; retain CMA')
        self.active=False
        for ch in (self.dma.sendchannel,self.dma.recvchannel):ch._active_buffer=None
    def close(self):self.input.freebuffer();self.output.freebuffer();self.closed=True
    def __enter__(self):return self


def driver_class():
    name='pipeline_fake_async_driver';path=ROOT/'board/lz77_async_driver.py'
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'pynq':types.SimpleNamespace(allocate=lambda shape,dtype:FakeBuffer(shape,dtype)),
        'lz77_snapshot_driver':types.SimpleNamespace(LZ77SnapshotDriver=FakeBase),name:module}):spec.loader.exec_module(module)
    return module.LZ77AsyncDriver


class FakeCodec:
    def pack(self,tokens,count,adler):
        residual=expand(tokens,count)
        if zlib.adler32(residual)!=adler:raise ValueError('Wrong checksum')
        return zlib.compress(residual,6)


class PipelineTests(unittest.TestCase):
    def setUp(self):self.Driver=driver_class()
    def channels(self):return {0:np.arange(-5000,4000,dtype='<i2'),1:np.array([-32768,32767,0]*2790,dtype='<i2')}

    def test_two_slots_and_one_transaction(self):
        with self.Driver('fake') as d:
            a=d.prepare([1,2,3]);d.submit(a);b=d.prepare([7,8,9]);self.assertNotEqual(a.slot,b.slot)
            with self.assertRaises(RuntimeError):d.submit(b)
            with self.assertRaises(RuntimeError):d.prepare([1])
            result,raw=d.finish(a);d.submit(b);d.finish(b)
            self.assertEqual(raw,np.array([1,2,3],dtype='<i2').tobytes());self.assertEqual(d.cma_bytes,196744)
            with self.assertRaises(ValueError):d.finish(a)

    def test_input_snapshot_and_packet_survive_reuse_and_free(self):
        d=self.Driver('fake');source=np.array([-32768,32767,1,0],dtype='<i2');t=d.prepare(source);expected=source.tobytes();source[:]=3
        d.submit(t);(data,tokens,meta,profile),raw=d.finish(t);saved=tokens.tobytes()
        for i in range(4):d.transform([i]*100)
        d.close();self.assertEqual(raw,expected);self.assertEqual(tokens.tobytes(),saved)
        self.assertIs(tokens.base,data);self.assertFalse(tokens.flags.writeable)
        self.assertTrue(all(s['input'].frees==s['output'].frees==1 for s in d._slots))

    def test_stream_modes_match_cpu_and_order(self):
        expected=[];cpu=compress_cpu_records(blocks(self.channels()),expected.append)
        for mode in ('split_serial','overlap'):
            output=[];captured=[]
            with self.Driver('fake') as d:
                result=compress_records(blocks(self.channels()),d,FakeCodec(),output.append,mode,lambda *a:captured.append(a))
                self.assertFalse(d.active);self.assertTrue(all(s['state']=='free' for s in d._slots))
            self.assertEqual(output,expected);self.assertEqual(result['samples'],cpu['samples']);self.assertEqual(result['blocks'],4)
            self.assertEqual([j.channel for j,*_ in captured],[0,1,0,1])
            self.assertEqual(sum(p['following_submitted_ns'] is not None for _,_,_,p,_ in captured),3 if mode=='overlap' else 0)

    def test_invalid_prepare_does_not_touch_running_slot(self):
        with self.Driver('fake') as d:
            t=d.prepare([1]*10);d.submit(t);before=d._slots[t.slot]['input'].tobytes()
            for values in ([],[32768],[1.5],np.zeros((2,2),dtype='i2')):
                with self.assertRaises((ValueError,TypeError)):d.prepare(values)
                self.assertTrue(d.active);self.assertFalse(d.failed);self.assertEqual(d._slots[t.slot]['input'].tobytes(),before)
            d.finish(t)

    def test_foreign_stale_and_wrong_state_tickets(self):
        with self.Driver('fake') as d:
            t=d.prepare([1]);fake=types.SimpleNamespace(slot=t.slot)
            with self.assertRaises(ValueError):d.submit(fake)
            with self.assertRaises(ValueError):d.finish(t)
            d.submit(t);d.finish(t)
            with self.assertRaises(ValueError):d.submit(t)

    def test_context_aborts_before_free_and_reset_failure_retains(self):
        d=self.Driver('fake');arrays=[s[k] for s in d._slots for k in ('input','output')]
        with d:t=d.prepare([1]);d.submit(t)
        self.assertTrue(d.failed);self.assertTrue(d.closed);self.assertTrue(all(a.frees==1 for a in arrays))
        d=self.Driver('fake');t=d.prepare([1]);d.submit(t);d.reset_fail=True
        with self.assertRaises(RuntimeError):d.__exit__(None,None,None)
        self.assertTrue(d.active);self.assertFalse(d.closed);self.assertTrue(all(s[k].frees==0 for s in d._slots for k in ('input','output')))
        d.reset_fail=False;d.abort();d.close()

    def test_consumer_sink_source_errors_cancel_following(self):
        for kind in ('consumer','sink','source','interrupt'):
            d=self.Driver('fake');codec=FakeCodec();sink=lambda data:None
            def failure(*args):raise KeyboardInterrupt() if kind=='interrupt' else ValueError('injected')
            if kind in ('consumer','interrupt'):codec.pack=failure
            elif kind=='sink':sink=failure
            def source():
                yield 0,0,[1,2,3]
                if kind=='source':raise ValueError('source failure')
                yield 1,0,[7,8,9]
            with self.assertRaises((KeyboardInterrupt,ValueError)):
                with d:compress_records(source(),d,codec,sink)
            self.assertTrue(d.failed);self.assertTrue(d.closed);self.assertFalse(d.active)
            self.assertTrue(all(c._active_buffer is None for c in (d.dma.sendchannel,d.dma.recvchannel)))

    def test_deadline_and_completion_precedence(self):
        with self.Driver('fake',timeout=.00001) as d:
            t=d.prepare([1]);d.submit(t);d._deadline=time.monotonic()-1;d.finish(t)
        with self.Driver('fake') as d:
            t=d.prepare([1]);d.submit(t);d.dma.stuck=True;d._deadline=time.monotonic()-1
            with self.assertRaises(TimeoutError):d.finish(t)
            self.assertTrue(d.failed);self.assertFalse(d.active);d.dma.stuck=False

    def test_empty_single_and_output_backpressure(self):
        for channels in ({},{0:np.array([32767],dtype='<i2')}):
            with self.Driver('fake') as d:
                out=[];r=compress_records(blocks(channels),d,FakeCodec(),out.append)
                self.assertEqual(r['blocks'],len(channels));self.assertFalse(d.active)
        with self.Driver('fake',timeout=.00001) as d:
            out=[]
            def slow(data):time.sleep(.001);out.append(data)
            compress_records(blocks(self.channels()),d,FakeCodec(),slow)
            self.assertFalse(d.failed);self.assertEqual(len(out),4)

    def test_foreign_thread_rejected(self):
        with self.Driver('fake') as d:
            errors=[]
            def worker():
                for fn in (lambda:d.prepare([1]),d.abort,d.close):
                    try:fn()
                    except RuntimeError:errors.append(True)
            thread=threading.Thread(target=worker);thread.start();thread.join();self.assertEqual(len(errors),3)


if __name__=='__main__':unittest.main()
