"""Native token consumer: no residual regeneration or match scan in pack()."""
import ctypes as ct
import hashlib,json,os,platform,shutil,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
_lib=None;_build=None


def ensure_native():
    global _lib,_build
    if _lib is not None:return _build
    vendor=ROOT/'third_party/zlib-1.2.11';manifest=json.loads((vendor/'source-manifest.json').read_text())
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    for n,r in manifest['files'].items():
        if sha(vendor/n)!=r['sha256']:raise ValueError('Pinned source changed')
    source=Path(__file__).with_name('lz77_pack_native.c');original=source.with_name('lz77_native.c')
    key=hashlib.sha256((sha(source)+sha(original)+sha(Path(__file__))+sha(vendor/'source-manifest.json')).encode()).hexdigest()
    folder=ROOT/'results/lz77-hw-v1/packed-native'/platform.machine();folder.mkdir(parents=True,exist_ok=True)
    binary=folder/('lz77_'+key[:16]+('.dll' if os.name=='nt' else '.so'))
    text=(vendor/'deflate.c').read_text()
    hook='''#include "deflate.h"
extern int lz_capture_tally(deflate_state*,unsigned,unsigned);
#undef _tr_tally_lit
#undef _tr_tally_dist
#define _tr_tally_lit(s,c,flush) { flush=lz_capture_tally(s,0,c); }
#define _tr_tally_dist(s,d,l,flush) { flush=lz_capture_tally(s,d,l); }
'''
    adapted=folder/'deflate_capture.c';adapted.write_text(text.replace('#include "deflate.h"',hook))
    sources=[source,adapted]+[vendor/n for n in ('trees.c','adler32.c','crc32.c','zutil.c','compress.c')]
    if not binary.exists():
        if os.name=='nt':
            from windows_native_build import run_msvc
            result=run_msvc(folder,sources,binary,includes=[vendor],crt=True)
        else:
            compiler=shutil.which('gcc')
            if not compiler:raise RuntimeError('gcc unavailable')
            result=subprocess.run([compiler,'-O3','-std=c99','-fPIC','-shared','-Wl,-Bsymbolic','-I'+str(vendor),*map(str,sources),'-o',str(binary)],capture_output=True,text=True)
        (folder/'build.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('Token consumer build failed: '+result.stdout+result.stderr)
    lib=ct.CDLL(str(binary));u8=ct.POINTER(ct.c_uint8);u32=ct.POINTER(ct.c_uint32)
    lib.lz_oracle.argtypes=[u8,ct.c_uint32,u32,ct.c_uint32,u32,ct.POINTER(ct.c_uint64)]
    lib.lz_reference.argtypes=[u8,ct.c_uint32,u32,ct.c_uint32,u32,u8,ct.c_uint32,u32]
    lib.lz_pack.argtypes=[u32,ct.c_uint32,ct.c_uint32,ct.c_uint32,u8,ct.c_uint32,u32]
    for n in ('lz_oracle','lz_reference','lz_pack'):getattr(lib,n).restype=ct.c_int
    _lib=lib;_build=dict(key=key,binary_sha256=sha(binary),binary=str(binary),
        source_sha256={p.relative_to(ROOT).as_posix():sha(p) for p in (source,original,Path(__file__))},
        vendor_manifest_sha256=sha(vendor/'source-manifest.json'),symbol_binding='Windows internal or ELF -Bsymbolic')
    return _build


class TokenConsumer:
    def __init__(self):
        ensure_native();self.tokens=np.empty(16384,dtype='<u4');self.output=(ct.c_uint8*65536)();self.count=ct.c_uint32();self.size=ct.c_uint32();self.work=(ct.c_uint64*2)()

    def tokenize(self,raw,reference=False):
        if not isinstance(raw,bytes) or not 1<=len(raw)<=16384:raise ValueError('Use owned bytes')
        pointer=ct.cast(ct.c_char_p(raw),ct.POINTER(ct.c_uint8));target=self.tokens.ctypes.data_as(ct.POINTER(ct.c_uint32))
        code=(_lib.lz_reference(pointer,len(raw),target,16384,ct.byref(self.count),self.output,len(self.output),ct.byref(self.size)) if reference else
              _lib.lz_oracle(pointer,len(raw),target,16384,ct.byref(self.count),self.work))
        if code:raise ValueError('Token reference failed: '+str(code))
        tokens=self.tokens[:self.count.value].copy()
        return (tokens,bytes(self.output[:self.size.value])) if reference else tokens

    def pack(self,tokens,count,adler):
        if not 1<=count<=16384 or not 0<=adler<=0xffffffff:raise ValueError('Invalid token consumer bounds')
        array=np.asarray(tokens)
        if array.ndim!=1 or array.dtype.kind not in 'iu' or not 1<=len(array)<=count or int(array.min())<0 or int(array.max())>0xffffffff:
            raise ValueError('Invalid token array')
        array=np.ascontiguousarray(array,dtype='<u4')
        code=_lib.lz_pack(array.ctypes.data_as(ct.POINTER(ct.c_uint32)),len(array),count,adler,self.output,len(self.output),ct.byref(self.size))
        if code:raise ValueError('Token Huffman rejected: '+str(code))
        return bytes(self.output[:self.size.value])
