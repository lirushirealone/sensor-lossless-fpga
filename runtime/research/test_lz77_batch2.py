"""Bounded protocol, batch scheduler and inherited DMA lifecycle checks."""
import importlib.util,struct,sys,threading,time,types,unittest,zlib
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'research'),str(ROOT/'board')]
from lz77_batch2_protocol import parse,aggregate,capacity,INPUT_WORDS,OUTPUT_BYTES,CMA_BYTES,COUNTERS
from lz77_driver_support import NAMES
from lz77_batch2_pipeline import compress_batches
from lz77_pipeline import compress_cpu_records
from test_lz77_pipeline import FakeBuffer,FakeBase,FakeDMA,FakeCodec,driver_class

def make_group(values,batch=1,block=0):
    payload=bytearray();profiles=[];sizes=[]
    for i,v in enumerate(values):
        raw=np.asarray(v,dtype='<i2').tobytes();u=np.frombuffer(raw,dtype='<u2');delta=u.copy();np.subtract(u[1:],u[:-1],out=delta[1:]);residual=delta.tobytes()
        body=np.frombuffer(residual,dtype='u1').astype('<u4').tobytes();n=len(v);seq=block+i+1
        head=struct.pack('<4sBBHIIIII',b'LZT1',1,0,n,seq,2*n,zlib.crc32(raw),zlib.adler32(residual),zlib.crc32(body));p=body+head+struct.pack('<I',zlib.crc32(head));size=len(p);sizes.append(size)
        st=[3*n+4,n+1,2*n+3,0,n,size,seq,0,n+2,n];profiles.append(st);payload.extend(p+COUNTERS.pack(*st))
    if len(sizes)==1:sizes.append(0)
    head=struct.pack('<4s6I',b'LZB1',1|(len(values)<<8),batch,*sizes,1+sum(len(v)+1 for v in values),zlib.crc32(payload))
    packet=bytes(payload)+head+struct.pack('<I',zlib.crc32(head));total=sum(p[0] for p in profiles)+50;collect=sum(p[1] for p in profiles)+1
    st=[total,collect,total-collect,0,sum(map(len,values)),len(packet),batch,0,sum(p[8] for p in profiles),sum(p[9] for p in profiles)]
    return packet,st

class BatchDMA(FakeDMA):
    def execute(self,words):
        count=int(words[0])&65535;values=[];p=1
        for _ in range(count):
            n=int(words[p])&65535;values.append(np.asarray(words[p+1:p+n+1],dtype='<u2').view('<i2'));p+=n+1
        self.seq+=1;packet,st=make_group(values,self.seq,getattr(self,'blocks',0));self.blocks=getattr(self,'blocks',0)+count
        self.owner.fake_profile=st;self.recvchannel._active_buffer[:len(packet)]=np.frombuffer(packet,dtype='u1');self.recvchannel.transferred=len(packet)

def fake_driver_class():
    base=driver_class();name='batch2_fake_driver';spec=importlib.util.spec_from_file_location(name,ROOT/'board/lz77_batch2_driver.py');module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'pynq':types.SimpleNamespace(Overlay=None,allocate=None,MMIO=None,Clocks=None),'lz77_async_driver':types.SimpleNamespace(LZ77AsyncDriver=base),name:module}):spec.loader.exec_module(module)
    class Driver(module.LZ77Batch2Driver):
        def __init__(self,timeout=10.):
            self.timeout=timeout;self.failed=False;self.closed=False;self.active=False;self.sequence=0;self.block_sequence=0;self.clock_mhz=66.666667
            self._owner=threading.get_ident();self._pending=None;self._next_slot=0;self._submitted_ns=self._deadline=None;self.reset_fail=False
            self._slots=[dict(input=FakeBuffer((INPUT_WORDS,),np.uint32),output=FakeBuffer((OUTPUT_BYTES,),np.uint8),ticket=None,state='free') for _ in range(2)]
            self.input=self._slots[0]['input'];self.output=self._slots[0]['output'];self.dma=BatchDMA(self)
            self._stats_reader=types.SimpleNamespace(read_batch=lambda counts,size,seq,members:aggregate(seq,self.fake_profile,seq,counts,size,seq,members))
    return Driver

class BatchProtocolTests(unittest.TestCase):
    def test_max_capacity_and_directory(self):
        values=[np.arange(8192,dtype='<i2'),np.zeros(8192,dtype='<i2')];data,st=make_group(values);m,e=parse(data,[8192,8192],1,0)
        self.assertEqual(len(data),131248);self.assertEqual(capacity((8192,8192)),131264);self.assertEqual(4*INPUT_WORDS,65548);self.assertEqual(CMA_BYTES,2*(4*INPUT_WORDS+OUTPUT_BYTES));self.assertLess(max(len(data),4*INPUT_WORDS),262143)
        aggregate(1,st,1,[8192,8192],len(data),1,m);self.assertTrue(all(not r[1].flags.writeable and r[1].base is data for r in m))

    def test_reject_corruption_and_unowned(self):
        data,st=make_group(([1,2,3],[-32768,32767]));counts=[3,2]
        for pos in (0,4,24,36,48,72,len(data)-32,len(data)-28,len(data)-24,len(data)-20,len(data)-16,len(data)-12,len(data)-8,len(data)-4):
            bad=bytearray(data);bad[pos]^=1
            with self.assertRaises((ValueError,RuntimeError)):parse(bytes(bad),counts,1,0)
        for bad in (bytearray(data),memoryview(data),data[:-1],data+b'\0'*4):
            with self.assertRaises(ValueError):parse(bad,counts,1,0)
        for counts,batch,block in (([3],1,0),([3,2],2,0),([3,2],1,1),([0,2],1,0),([8193,2],1,0)):
            with self.assertRaises((ValueError,RuntimeError)):parse(data,counts,batch,block)

    def test_protected_counter_semantics(self):
        data,st=make_group(([1,2],[3]));m,_=parse(data,[2,1],1,0)
        for idx in range(10):
            bad=st.copy();bad[idx]+=st[0]+1 if idx==3 else 1
            with self.assertRaises(RuntimeError):aggregate(1,bad,1,[2,1],len(data),1,m)
        with self.assertRaises(RuntimeError):aggregate(0,st,1,[2,1],len(data),1,m)

class BatchDriverTests(unittest.TestCase):
    def setUp(self):self.Driver=fake_driver_class()
    def test_owned_slots_and_stale_tickets(self):
        d=self.Driver();src=np.array([1,2,3],dtype='<i2');t=d.prepare_group((src,[-32768,32767]));src[:]=42;d.submit(t);next_t=d.prepare_group(([5]*10,))
        with self.assertRaises(RuntimeError):d.submit(next_t)
        with self.assertRaises(RuntimeError):d.prepare([1])
        members,data,p=d.finish(t);saved=members[0][0][1].tobytes();self.assertEqual(members[0][1],np.array([1,2,3],dtype='<i2').tobytes())
        d.submit(next_t);d.finish(next_t)
        with self.assertRaises(ValueError):d.submit(t)
        for i in range(4):d.transform_group(([i]*17,[i]*8))
        d.close();self.assertEqual(members[0][0][1].tobytes(),saved);self.assertEqual(d.cma_bytes,CMA_BYTES)
        self.assertTrue(all(s['input'].frees==s['output'].frees==1 for s in d._slots))

    def test_batch1_batch2_odd_tail_and_cpu(self):
        records=[(i%4,i,np.arange(1+i*3,18+i*3,dtype='<i2')) for i in range(7)];expected=[];compress_cpu_records(iter(records),expected.append)
        for size in (1,2):
            with self.Driver() as d:
                output=[];groups=[];s=compress_batches(iter(records),d,FakeCodec(),output.append,size,batch_observer=lambda data,p:groups.append(p))
                self.assertEqual(output,expected);self.assertEqual(s['blocks'],7);self.assertEqual(s['batches'],7 if size==1 else 4);self.assertEqual(groups[-1]['members'],1)

    def test_bad_prepare_does_not_touch_active(self):
        with self.Driver() as d:
            t=d.prepare_group(([1]*9,[2]*7));d.submit(t);before=d._slots[t.slot]['input'].tobytes()
            for values in ((),([0],[0],[0]),([],),([32768],),([1.5],),([1],[])):
                with self.assertRaises((ValueError,TypeError,OverflowError)):d.prepare_group(values)
                self.assertFalse(d.failed);self.assertTrue(d.active);self.assertEqual(before,d._slots[t.slot]['input'].tobytes())
            d.finish(t)

    def test_active_close_and_delayed_completion(self):
        with self.Driver(timeout=.000001) as d:
            t=d.prepare_group(([1],[2]));d.submit(t)
            with self.assertRaises(RuntimeError):d.close()
            time.sleep(.001);d.finish(t);self.assertFalse(d.failed)

    def test_reset_failure_retains_views_and_buffers(self):
        d=self.Driver();t=d.prepare_group(([1],[2]));d.submit(t);d.reset_fail=True
        with self.assertRaises(RuntimeError):d.abort()
        self.assertTrue(d.active);self.assertIsNotNone(d.dma.recvchannel._active_buffer)
        with self.assertRaises(RuntimeError):d.close()
        self.assertTrue(all(s['input'].frees==s['output'].frees==0 for s in d._slots));d.reset_fail=False;d.abort();d.close()

    def test_pipeline_errors_abort_next_dma(self):
        for kind in ('consumer','sink','observer','batch_observer','source','interrupt'):
            d=self.Driver();codec=FakeCodec();seen=[]
            def fail(*a):
                self.assertTrue(d.active);seen.append(True)
                if kind=='interrupt':raise KeyboardInterrupt()
                raise ValueError('Injected')
            def source():
                for i in range(6):
                    if kind=='source' and i==2:fail()
                    yield i%4,i,[i]*10
            if kind in ('consumer','interrupt'):codec.pack=fail
            with self.assertRaises((ValueError,KeyboardInterrupt)):compress_batches(source(),d,codec,fail if kind=='sink' else lambda x:None,2,fail if kind=='observer' else None,fail if kind=='batch_observer' else None)
            self.assertTrue(seen and d.failed and not d.active);d.close()

    def test_owner_thread(self):
        with self.Driver() as d:
            errors=[]
            def run():
                try:d.prepare_group(([1],))
                except RuntimeError:errors.append(True)
            t=threading.Thread(target=run);t.start();t.join();self.assertEqual(errors,[True]);d.transform([1])

if __name__=='__main__':unittest.main(verbosity=2)
