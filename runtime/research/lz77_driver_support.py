"""Owned packet views and ordered GPIO snapshots for a serial LZ77 driver."""
import ctypes as ct
import hashlib,os,platform,shutil,subprocess,zlib
from pathlib import Path
import numpy as np
from lz77_protocol import TRAILER
ROOT=Path(__file__).resolve().parents[1]
NAMES=('total_cycles','collect_cycles','process_cycles','output_stalls',
       'input_samples','output_bytes','sequence','input_gap_cycles','matcher_work_cycles','search_probes')
_lib=None;_build=None


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ensure_native():
    global _lib,_build
    if _lib is not None:return _build
    source=ROOT/'research/lz77_driver_native.c';folder=ROOT/'results/lz77-driver-v1/native'/platform.machine();folder.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256((digest(source)+platform.platform()).encode()).hexdigest()
    binary=folder/('stats_'+key[:16]+('.dll' if os.name=='nt' else '.so'))
    if not binary.exists():
        if os.name=='nt':
            found=sorted(Path('C:/Program Files/Microsoft Visual Studio').glob('*/Community/VC/Auxiliary/Build/vcvars64.bat'))
            if not found:raise RuntimeError('MSVC unavailable')
            script=folder/'build.bat';script.write_text('@echo off\ncall "'+str(found[-1])+'" >nul\nif errorlevel 1 exit /b 1\ncl /nologo /O2 /LD /TC "'+str(source)+'" /link /OUT:"'+str(binary)+'"\n')
            result=subprocess.run(['cmd','/d','/c',str(script)],cwd=folder,capture_output=True,text=True,errors='replace')
        else:
            result=subprocess.run([shutil.which('gcc') or 'gcc','-O3','-std=c99','-fPIC','-shared',str(source),'-o',str(binary)],capture_output=True,text=True)
        (folder/'build.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('Statistics helper build failed: '+result.stdout+result.stderr)
    lib=ct.CDLL(str(binary));u32p=ct.POINTER(ct.c_uint32)
    lib.lz77_stats_snapshot.argtypes=[ct.POINTER(u32p),ct.c_uint32,u32p,ct.c_uint32];lib.lz77_stats_snapshot.restype=ct.c_int
    _lib=lib;_build=dict(binary=str(binary),sha256=digest(binary),source_sha256=digest(source),reads=12,barriers=True)
    return _build


def checked_stats(before,values,after,count,size,sequence):
    row=dict(zip(NAMES,values))
    if before!=after or row['sequence']!=sequence:raise RuntimeError('Wrong LZ77 snapshot')
    if row['total_cycles']!=row['collect_cycles']+row['process_cycles'] or row['collect_cycles']!=count+1+row['input_gap_cycles']:
        raise RuntimeError('LZ77 phases do not add up')
    if (row['input_samples'],row['output_bytes'])!=(count,size) or not 0<row['matcher_work_cycles']<=row['total_cycles']:
        raise RuntimeError('LZ77 counters invalid')
    return row


class StatsReader:
    def __init__(self,arrays):
        ensure_native();self.arrays=tuple(arrays)
        if len(self.arrays)!=5 or any(a.ndim!=1 or len(a)<3 or a.dtype!=np.dtype('uint32') or not a.flags.c_contiguous for a in self.arrays):
            raise ValueError('Use five mapped native uint32 register arrays')
        u32p=ct.POINTER(ct.c_uint32);self.pointers=(u32p*5)(*(a.ctypes.data_as(u32p) for a in self.arrays));self.output=(ct.c_uint32*12)()

    def read(self,count,size,sequence):
        code=_lib.lz77_stats_snapshot(self.pointers,5,self.output,12)
        if code:raise RuntimeError('Statistics snapshot rejected: '+str(code))
        return checked_stats(self.output[0],self.output[1:11],self.output[11],count,size,sequence)


def metadata_view(data,count,sequence):
    if type(data) is not bytes:raise ValueError('Use an owned immutable packet snapshot')
    if not 1<=count<=8192 or len(data)<36 or len(data)>8*count+32 or len(data)%4:
        raise ValueError('Invalid LZT1 packet size')
    f=TRAILER.unpack_from(data,len(data)-32);length=len(data)-32
    if f[:5]!=(b'LZT1',1,0,count,sequence) or not 1<=f[5]<=2*count or length!=4*f[5]:
        raise ValueError('LZT1 identity/count mismatch')
    view=memoryview(data)
    if zlib.crc32(view[-32:-4])!=f[9] or zlib.crc32(view[:length])!=f[8]:
        raise ValueError('LZT1 DMA integrity mismatch')
    return np.frombuffer(data,dtype='<u4',count=f[5]),dict(samples=count,sequence=sequence,tokens=f[5],
        raw_crc32=f[6],residual_adler32=f[7],token_crc32=f[8])
