"""Strict integer acceptance, byte parity and ownership of input snapshots."""
import array,operator,random,struct,unittest,zlib
import numpy as np
from lz77_input import snapshot,command_words


class IndexValue:
    def __init__(self,value):self.value=value
    def __index__(self):return self.value


class IntOnly:
    def __int__(self):return 7


class InputTests(unittest.TestCase):
    def parity(self,values):
        expected=[operator.index(v) for v in values]
        raw=struct.pack('<'+'h'*len(expected),*expected);owned=snapshot(values);words=command_words(owned)
        self.assertEqual(owned.raw,raw);self.assertEqual(owned.samples.tobytes(),raw)
        self.assertEqual(owned.raw_crc32,zlib.crc32(raw))
        self.assertEqual(words.tolist(),[0x4c5a0000|len(expected)]+[v&65535 for v in expected])
        self.assertFalse(owned.samples.flags.writeable);self.assertFalse(words.flags.writeable)
        return owned,words

    def test_edges_and_random_lengths(self):
        rng=random.Random(81927020)
        for n in (1,2,3,7,8,9,31,32,33,255,256,257,258,259,1023,1024,1025,4095,4096,4097,8191,8192):
            values=[rng.randrange(-32768,32768) for _ in range(n)];values[0]=-32768;values[-1]=32767;self.parity(values)
        for _ in range(32):self.parity([rng.randrange(-32768,32768) for _ in range(rng.randrange(1,8193))])

    def test_numeric_arrays_endian_stride_and_ownership(self):
        for dtype in ('i1','i2','>i2','i4','>i4','i8','u1','u2','>u2','u4','u8'):
            for stride in (1,2,-1,-2):
                values=np.array([0,1,5,7,8,0],dtype=dtype)[::stride]
                owned,words=self.parity(values);raw=owned.raw;cmd=words.copy();values[:]=0
                self.assertEqual(owned.raw,raw);self.assertEqual(words.tobytes(),cmd.tobytes())
                with self.assertRaises(ValueError):owned.samples.flags.writeable=True
        boolean=np.array([True,False],dtype='?')
        try:operator.index(boolean[0])
        except TypeError:
            with self.assertRaises((TypeError,ValueError)):snapshot(boolean)
        else:self.parity(boolean)

    def test_tuples_objects_and_index_iterables(self):
        for values in ((-32768,0,32767),[True,False],array.array('h',[-32768,32767]),
                [IndexValue(-32768),IndexValue(32767)],np.array([IndexValue(7),IndexValue(-3)],dtype=object),
                range(-16,16),b'\x00\xff\x01'):
            self.parity(values)
        owned=snapshot(IndexValue(i) for i in (-32768,0,32767))
        self.assertEqual(owned.samples.tolist(),[-32768,0,32767])

    def test_rejected_values_never_truncate_or_wrap(self):
        bad=([],[32768],[-32769],[2**100],[1.0],[float('nan')],[float('inf')],[True,2.5],['1'],[[1]],
            [IntOnly()],[IndexValue(32768)],[IndexValue(1.5)],np.array([65535],dtype='u2'),
            np.array([2**63],dtype='u8'),np.array([-32769],dtype='i8'),np.array([1.0],dtype=object),
            np.array([2**100],dtype=object),np.zeros((2,2),dtype='i2'),np.array(7,dtype='i2'),[0]*8193)
        for values in bad:
            with self.subTest(value=str(values)[:60]):
                with self.assertRaises((TypeError,ValueError,OverflowError)):snapshot(values)

    def test_owned_list_snapshot_and_reused_source(self):
        values=[-32768,0,32767];owned,words=self.parity(values);raw=owned.raw;cmd=words.tobytes();values[:]=[1,1,1]
        self.assertEqual(owned.raw,raw);self.assertEqual(words.tobytes(),cmd)
        buf=np.empty(17,dtype='i2');saved=[]
        for value in range(9):buf[:]=value;saved.append(snapshot(buf))
        buf[:]=-1
        for i,s in enumerate(saved):self.assertEqual(s.samples.tolist(),[i]*17)

    def test_iterable_bound_and_index_called_once(self):
        consumed=[]
        def source():
            for i in range(10000):consumed.append(i);yield 0
        with self.assertRaises(ValueError):snapshot(source())
        self.assertEqual(len(consumed),8193)
        calls=[]
        class Once:
            def __index__(self):calls.append(1);return 3
        self.assertEqual(snapshot([Once(),Once()]).samples.tolist(),[3,3]);self.assertEqual(len(calls),2)

    def test_command_extent_and_retained_storage(self):
        for n in (1,8192):
            owned=snapshot([-1]*n);words=command_words(owned)
            self.assertEqual(words.nbytes,4*(n+1));self.assertEqual(words[0],0x4c5a0000|n)
            self.assertTrue(np.all(words[1:]==65535));self.assertIs(owned.samples.base,owned.raw)
            self.assertEqual(len(owned.raw)+words.nbytes,6*n+4)


if __name__=='__main__':unittest.main()
