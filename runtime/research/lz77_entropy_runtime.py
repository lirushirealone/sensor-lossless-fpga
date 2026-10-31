"""Private entropy experiment builds; frozen consumers remain untouched."""
import ctypes as ct
import hashlib,json,os,platform,shutil,subprocess,threading
from pathlib import Path
import numpy as np
import lz77_packed_runtime as frozen

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_profile():
    # The frozen build runs only in an isolated workspace/root in this stage.
    old=frozen.ensure_native();vendor=ROOT/'third_party/zlib-1.2.11'
    source=ROOT/'research/lz77_entropy_profile.c'
    folder=ROOT/'results/lz77-entropy-v1/native'/platform.machine();folder.mkdir(parents=True,exist_ok=True)
    adapted=Path(old['binary']).parent/'deflate_capture.c'
    sources=[source,adapted]+[vendor/n for n in ('trees.c','adler32.c','crc32.c','zutil.c','compress.c')]
    inputs=sources+[ROOT/'research/lz77_pack_native.c',ROOT/'research/lz77_native.c']
    key=hashlib.sha256(''.join(digest(p) for p in inputs).encode()).hexdigest()
    binary=folder/('profile_'+key[:16]+('.dll' if os.name=='nt' else '.so'))
    if not binary.exists():
        if os.name=='nt':
            from windows_native_build import run_msvc
            result=run_msvc(folder,sources,binary,includes=[vendor,ROOT/'research'],crt=True)
        else:
            result=subprocess.run([shutil.which('gcc') or 'gcc','-O3','-std=c99','-fPIC','-shared','-Wl,-Bsymbolic',
                '-I'+str(vendor),'-I'+str(ROOT/'research'),*map(str,sources),'-o',str(binary)],capture_output=True,text=True)
        (folder/'build.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('Diagnostic C build failed: '+result.stdout+result.stderr)
    lib=ct.CDLL(str(binary));u8=ct.POINTER(ct.c_uint8);u32=ct.POINTER(ct.c_uint32)
    lib.entropy_profile_pack.argtypes=[u32,ct.c_uint32,ct.c_uint32,ct.c_uint32,u8,ct.c_uint32,u32,ct.POINTER(ct.c_uint64)]
    lib.entropy_profile_pack.restype=ct.c_int
    return lib,dict(binary=str(binary),sha256=digest(binary),source_sha256={str(p.relative_to(ROOT)):digest(p) for p in inputs},frozen_build=old)


class BulkReturnConsumer(frozen.TokenConsumer):
    """Only change final bytes copying; same validation and frozen C call."""
    def pack(self,tokens,count,adler):
        if not 1<=count<=16384 or not 0<=adler<=0xffffffff:raise ValueError('Invalid token consumer bounds')
        array=np.asarray(tokens)
        if array.ndim!=1 or array.dtype.kind not in 'iu' or not 1<=len(array)<=count or int(array.min())<0 or int(array.max())>0xffffffff:
            raise ValueError('Invalid token array')
        array=np.ascontiguousarray(array,dtype='<u4')
        code=frozen._lib.lz_pack(array.ctypes.data_as(ct.POINTER(ct.c_uint32)),len(array),count,adler,self.output,len(self.output),ct.byref(self.size))
        if code:raise ValueError('Token Huffman rejected: '+str(code))
        return ct.string_at(self.output,self.size.value)
