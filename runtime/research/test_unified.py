"""Format, selection, dual-engine ownership and cancellation contract checks."""
import sys,types,unittest,zlib,struct
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'research'),str(ROOT/'board')]
from unified_codec import *
from lz77_input import snapshot
from test_lz77_batch2 import fake_driver_class
from test_lz77_pipeline import FakeCodec
from test_continuous_stream import FakeHardware,Buffer
from native_stream_driver import NativeStreamDriver
from lz77_source import blocks

def system_class():
    # Only constructors require PYNQ; production scheduling runs unchanged
    # against the existing tested DMA mocks on PC and ARM unit tests.
    with patch.dict(sys.modules,{'pynq':types.SimpleNamespace(Overlay=None,allocate=None,MMIO=None,Clocks=None)}):
        import unified_system
    return unified_system.UnifiedSystem
def fake_system(group=2):
    cls=fake_driver_class()
    class LZ(cls):
        def prepare_owned(self,owned):return self.prepare_group(tuple(o.samples for o in owned))
    lz=LZ();lz.dma.mmio.read=lambda a:2 if a in (4,0x34) else 0
    hw=FakeHardware();rice=NativeStreamDriver(hw,allocator=Buffer)
    return system_class()(hardware=(lz,rice),codec=FakeCodec(),group_size=group)

class UnifiedCodecTests(unittest.TestCase):
    def native(self,o,ch=0):return [frozen.encode_block(o.samples[i:i+1024].tolist(),ch,i//1024+1) for i in range(0,len(o.samples),1024)]
    def test_complete_cost_and_roundtrip(self):
        rng=np.random.RandomState(7020)
        for n in (1,2,17,1024,1025,8191,8192):
            for kind in ('constant','ramp','random'):
                values=np.full(n,17,dtype='<i2') if kind=='constant' else np.arange(n,dtype='<i2') if kind=='ramp' else rng.randint(-32768,32768,n).astype('<i2');o=snapshot(values);seq=[0]*4
                output,t=finish_job(o,self.native(o),zlib.compress(residual(o.raw),6),0,seq);file=file_header([n,0,0,0],2)+output;restored,p=decode_file(file)
                self.assertEqual(restored[0].tobytes(),o.raw);self.assertLessEqual(len(output),min(t['candidate_bytes'].values()));self.assertEqual(p['frames'],seq[0])
    def test_deflate_raw_length_tie_retains_raw(self):
        o=snapshot([123]);output,t=finish_job(o,self.native(o),b'xx',0,[0]*4)
        self.assertEqual(t['method'],'legacy1024');self.assertEqual(decode_frame(output)[0],o.raw)
        rng=np.random.RandomState(2);o=snapshot(rng.randint(-32768,32768,2048).astype('<i2'));out,t=finish_job(o,self.native(o),b'x'*len(o.raw),0,[0]*4)
        self.assertEqual(t['method'],'raw');self.assertEqual(frame_header(out)[3],0);decode_frame(out)
    def test_both_native_identity_and_crc_are_mandatory(self):
        o=snapshot(np.zeros(2048,dtype='<i2'));native=self.native(o);bad=bytearray(native[1]);bad[16]^=1;p=bad[:20];bad[20:24]=struct.pack('<I',zlib.crc32(p));native[1]=bytes(bad)
        with self.assertRaises(ValueError):finish_job(o,native,zlib.compress(residual(o.raw),6),0,[0]*4)
        with self.assertRaises(ValueError):finish_job(o,native[:1],b'12',0,[0]*4)
    def test_file_integrity_truncation_and_duplicates(self):
        o=snapshot(np.arange(2050,dtype='<i2'));out,_=finish_job(o,self.native(o),zlib.compress(residual(o.raw),6),0,[0]*4);data=file_header([2050,0,0,0],1)+out
        for pos in (0,8,40,60,64,80,len(data)-1):
            b=bytearray(data);b[pos]^=1
            with self.assertRaises((ValueError,zlib.error)):decode_file(bytes(b))
        for bad in (data[:63],data[:64],data[:-1],data+out,data+b'\0'):
            with self.assertRaises(ValueError):decode_file(bad)
    def test_reserved_modes_and_bounded_decompression(self):
        raw=np.zeros(8192,dtype='<i2').tobytes();crc=zlib.crc32(raw)
        for mode,param,count,payload in ((6,0,1,b'12'),(5,6,8192,zlib.compress(b'\0'*20000,6)),(1,17,1,b'1'),(0,1,1,b'12')):
            frame=serialize(0,1,mode,param,count,crc,payload)
            with self.assertRaises(ValueError):decode_frame(frame)
    def test_empty_file_and_strict_metadata(self):
        restored,p=decode_file(file_header([0]*4,1));self.assertEqual(sum(map(len,restored.values())),0)
        for counts,group in (([True,0,0,0],1),([-1,0,0,0],1),([0]*4,True),([0]*4,3)):
            with self.assertRaises(ValueError):file_header(counts,group)

class UnifiedSystemTests(unittest.TestCase):
    def channels(self):return {0:np.arange(9001,dtype='<i2'),1:np.array([32767,-32768]*4101,dtype='<i2'),2:np.zeros(5,dtype='<i2'),3:np.array([7],dtype='<i2')}
    def run_one(self,group):
        channels=self.channels();out=[];proof=[]
        with fake_system(group) as s:
            result=s.compress_stream(blocks(channels),out.append,[len(channels[i]) for i in range(4)],batch_observer=proof.append)
            self.assertFalse(s.lz.active);self.assertIsNone(s.rice.active)
        restored,p=decode_file(b''.join(out))
        for ch in channels:self.assertEqual(restored[ch].tobytes(),channels[ch].tobytes())
        self.assertEqual(result['jobs'],6);return b''.join(out)
    def test_group1_and2_same_body_and_odd_tail(self):self.assertEqual(self.run_one(1)[64:],self.run_one(2)[64:])
    def test_source_reused_buffer_is_snapshotted_before_advance(self):
        shared=np.zeros(33,dtype='<i2')
        def source():
            for i in range(5):shared[:]=i;yield i%4,0 if i<4 else 33,shared
        with fake_system() as s:
            out=[];s.compress_stream(source(),out.append,[66,33,33,33])
        restored,p=decode_file(b''.join(out));self.assertEqual(restored[0].tolist(),[0]*33+[4]*33)
    def test_both_resets_on_pipeline_failures(self):
        for kind in ('source','sink','observer','batch','consumer','interrupt'):
            s=fake_system();seen=[];original=s.codec.pack
            def fail(*args):
                if kind in ('observer','batch','consumer','interrupt'):
                    self.assertTrue(s.lz.active);self.assertIsNotNone(s.rice.active)
                seen.append(True)
                if kind=='interrupt':raise KeyboardInterrupt()
                raise ValueError('Injected')
            def source():
                for i in range(6):
                    if kind=='source' and i==2:fail()
                    yield i%4,0 if i<4 else 17,[i]*17
            if kind in ('consumer','interrupt'):s.codec.pack=fail
            def sink(data):
                if kind=='sink' and data[:4]!=b'UCF1':fail()
            with self.assertRaises((ValueError,KeyboardInterrupt)):s.compress_stream(source(),sink,[34,34,17,17],fail if kind=='observer' else None,fail if kind=='batch' else None)
            self.assertTrue(seen and s.failed and s.lz.failed and s.rice.failed);self.assertFalse(s.lz.active);self.assertIsNone(s.rice.active);s.close()
    def test_reset_failure_still_resets_other_and_retains_all(self):
        s=fake_system();s.lz.reset_fail=True;t=s.lz.prepare_group(([1],));s.lz.submit(t)
        with self.assertRaises(RuntimeError):s.abort()
        self.assertTrue(s.rice.failed)
        with self.assertRaises(RuntimeError):s.close()
        self.assertFalse(s.rice.closed);s.lz.reset_fail=False;s.abort();s.close()
    def test_counts_offsets_and_invalid_following_abort(self):
        for records,counts in (([(0,1,[1])],[1,0,0,0]),([(0,0,[1])],[2,0,0,0]),([(0,0,[1,2])],[1,0,0,0]),([(0,0,[1]),(1,0,[32768])],[1,1,0,0])):
            s=fake_system()
            with self.assertRaises(ValueError):s.compress_stream(records,lambda d:None,counts)
            self.assertTrue(s.failed);s.close()
    def test_empty_and_multiple_files_reset_file_sequences(self):
        with fake_system() as s:
            empty=[];s.compress_stream([],empty.append,[0]*4);decode_file(b''.join(empty))
            a=[];b=[];s.compress_stream([(0,0,[1,2,3])],a.append,[3,0,0,0]);s.compress_stream([(0,0,[1,2,3])],b.append,[3,0,0,0]);self.assertEqual(a,b)
if __name__=='__main__':unittest.main(verbosity=2)
