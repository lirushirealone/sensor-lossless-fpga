"""GUI adapter on the board; imports identical private copies of frozen runtime."""
import fcntl,hashlib,json,signal,sys,time,traceback
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'board'),str(ROOT/'research')]
FORMAL=Path('/home/xilinx/projects/fpga_compression/unified-v1')
from unified_system import UnifiedSystem
from lz77_source import blocks
from unified_codec import parse_file_header,frame_header,decode_frame
cancelled=False
class Cancelled(Exception):pass
def signal_cancel(*args):
    global cancelled
    cancelled=True
for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,signal_cancel)
def emit(kind,**fields):
    global cancelled
    try:print(json.dumps(dict(kind=kind,**fields),ensure_ascii=False),flush=True)
    except (BrokenPipeError,OSError):cancelled=True
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic(path,row):
    part=path.with_suffix('.tmp');part.write_text(json.dumps(row,indent=2)+'\n');part.replace(path)
def main():
    job=Path(sys.argv[1]).resolve()
    if job.parent!=(ROOT/'jobs').resolve() or len(job.name)!=32 or any(c not in '0123456789abcdef' for c in job.name):raise ValueError('Invalid job directory')
    req=json.loads((job/'request.json').read_text());system=None;lock=None;start=time.perf_counter_ns()
    def check_cancel():
        if cancelled or (job/'cancel.flag').exists():raise Cancelled('任务已安全停止。')
    try:
        lock=open('/home/xilinx/projects/fpga_compression/desktop-gui-board.lock','a+')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('板卡正在执行另一项工作，请稍后再试。')
        manifest=json.loads((ROOT/'gui-runtime-manifest.json').read_text())
        for n,h in manifest['file_hashes'].items():
            if sha(ROOT/n)!=h:raise ValueError('Runtime differs: '+n)
        if sha(FORMAL/'unified-overlay/compressor.bit')!=req['bit_sha256'] or sha(job/'input.npz')!=req['input_sha256']:raise ValueError('Input/design digest mismatch')
        with np.load(job/'input.npz',allow_pickle=False) as z:
            if not z.files or any(k not in ('ch0','ch1','ch2','ch3') for k in z.files):raise ValueError('Invalid channel keys')
            channels={int(k[-1]):z[k].copy() for k in z.files}
        if sum(v.size for v in channels.values())>8_000_000 or any(v.ndim!=1 or v.dtype!=np.dtype('<i2') for v in channels.values()) or [len(channels.get(i,())) for i in range(4)]!=req['counts']:raise ValueError('Input contract differs')
        check_cancel();emit('phase',text='检查板卡并加载设计')
        scope={};exec((ROOT/'board/gui_preflight.py').read_text(),scope)
        check_cancel();system=UnifiedSystem(FORMAL/'unified-overlay/compressor.bit',group_size=req['group_size']);processed=frames=0;last=0
        def source():
            for rec in blocks(channels):check_cancel();yield rec
        def observe(job_obj,packet,native,meta,p,choice,output):
            nonlocal processed,frames,last
            check_cancel();processed+=choice['samples'];frames+=choice['frames'];now=time.monotonic()
            if now-last>=.15 or processed==sum(req['counts']):emit('progress',processed=processed,total=sum(req['counts']),output_bytes=out.tell(),frames=frames);last=now
        with (job/'compressed.ucf.part').open('xb') as out:profile=system.compress_stream(source(),out.write,req['counts'],observe)
        check_cancel();emit('phase',text='板卡全量还原校验');t=time.perf_counter_ns();data=(job/'compressed.ucf.part').read_bytes();counts,group=parse_file_header(data[:64]);seen=[0]*4;seq=[0]*4;offset=64;frames=0
        if list(counts)!=req['counts']:raise ValueError('Board file count mismatch')
        while offset<len(data):
            check_cancel();f=frame_header(data,offset);end=offset+24+f[7];ch=f[2]
            if end>len(data) or seen[ch]+f[6]>counts[ch] or f[5]!=((seq[ch]+1)&0xffffffff):raise ValueError('Board frame extent/sequence mismatch')
            raw,f=decode_frame(data[offset:end]);original=channels[ch][seen[ch]:seen[ch]+f[6]]
            if raw!=original.tobytes():raise ValueError('Board all-point verification failed')
            seen[ch]+=f[6];seq[ch]=f[5];offset=end;frames+=1
        if tuple(seen)!=counts:raise ValueError('Board missing samples')
        verification=dict(counts=counts,frames=frames,group_size=group,all_samples_and_crcs_pass=True)
        check_cancel();verify_ns=time.perf_counter_ns()-t;dma={k:{'send':d.dma.mmio.read(4),'receive':d.dma.mmio.read(0x34)} for k,d in (('lz',system.lz),('rice',system.rice))};loaded=system.overlay.bitfile_name;system.close();system=None
        check_cancel();(job/'compressed.ucf.part').rename(job/'compressed.ucf')
        row=dict(status='COMPLETE',schema='sensor-gui-board-job-v1',profile=profile,verification=verification,board_verification_ns=verify_ns,board_adapter_total_ns=time.perf_counter_ns()-start,dma=dma,loaded_bitfile=loaded,bit_sha256=sha(loaded),output_sha256=sha(job/'compressed.ucf'),input_sha256=req['input_sha256'],runtime_manifest_sha256=sha(ROOT/'gui-runtime-manifest.json'),helper_sha256=sha(Path(__file__)),group_size=req['group_size'])
    except BaseException as exc:
        reset_error=None;dma=None
        if system is not None:
            try:
                if not system.failed:system.abort()
                dma={k:{'send':d.dma.mmio.read(4),'receive':d.dma.mmio.read(0x34)} for k,d in (('lz',system.lz),('rice',system.rice))};system.close()
            except BaseException as reset:reset_error=str(reset)
        row=dict(status='CANCELLED' if isinstance(exc,Cancelled) and reset_error is None else 'FAILED',error=str(exc),error_type=type(exc).__name__,reset_error=reset_error,dma=dma,traceback=traceback.format_exc(),helper_sha256=sha(Path(__file__)))
    finally:
        if lock is not None:lock.close()
    atomic(job/'result.json',row);emit('result',**row)
    return 0 if row['status']=='COMPLETE' else 2 if row['status']=='CANCELLED' else 1
if __name__=='__main__':sys.exit(main())
