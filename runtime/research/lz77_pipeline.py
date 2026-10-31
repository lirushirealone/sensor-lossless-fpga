"""Bounded, single-thread stream scheduling around a single hardware engine."""
from dataclasses import dataclass
import operator,struct,time,zlib
import numpy as np
from lz77_input import snapshot
PREFIX=struct.Struct('<4sBBBBIHHI')


def frame(raw,payload,ch,sequence,count,crc):
    mode=5 if len(payload)<len(raw) else 0
    body=payload if mode else raw
    prefix=PREFIX.pack(b'LBC1',1,ch,mode,6 if mode else 0,sequence,count,len(body),crc)
    return prefix+struct.pack('<I',zlib.crc32(prefix))+body


@dataclass
class Job:
    channel:int
    begin:int
    sequence:int
    values:object
    ticket:object=None


def compress_records(records,driver,codec,sink,mode='overlap',observer=None,diagnostic=False):
    if mode not in ('frozen_serial','split_serial','overlap'):raise ValueError('Unknown scheduling mode')
    sequences=[0]*4;iterator=iter(records);summary=dict(blocks=0,samples=0,bytes=0,first_emit_ns=None,
        input_prepare_ns=0,token_entropy_ns=0,final_frame_ns=0,pending_encode_ns=0,diagnostic_rows=[])
    start=time.perf_counter_ns();last_submit=None

    def next_job():
        try:ch,begin,values=next(iterator)
        except StopIteration:return None
        ch=operator.index(ch);begin=operator.index(begin)
        if not 0<=ch<4 or begin<0:raise ValueError('Invalid source channel/offset')
        sequences[ch]=(sequences[ch]+1)&0xffffffff
        return Job(ch,begin,sequences[ch],values)

    def emit(job,result,following=None):
        nonlocal last_submit
        (data,tokens,meta,profile),raw=result
        before=time.perf_counter_ns();status_before=driver.status() if diagnostic and following is not None else None
        payload=codec.pack(tokens,len(raw),meta['residual_adler32']);packed=time.perf_counter_ns()
        status_after=driver.status() if diagnostic and following is not None else None
        output=frame(raw,payload,job.channel,job.sequence,len(raw)//2,meta['raw_crc32']);framed=time.perf_counter_ns()
        sink(output);emitted=time.perf_counter_ns()
        profile.update(token_entropy_ns=packed-before,final_frame_ns=framed-packed,candidate_bytes=len(output),
            candidate_mode=PREFIX.unpack_from(output)[3],encode_started_ns=before,encode_finished_ns=packed,
            frame_finished_ns=framed,emit_ns=emitted,following_submitted_ns=following)
        if summary['first_emit_ns'] is None:summary['first_emit_ns']=emitted-start
        summary['blocks']+=1;summary['samples']+=len(raw)//2;summary['bytes']+=len(output)
        for key in ('input_prepare_ns','token_entropy_ns','final_frame_ns'):summary[key]+=profile[key]
        if following is not None:
            if following>before:raise AssertionError('Following DMA must precede ARM encoding')
            summary['pending_encode_ns']+=packed-before
            if diagnostic:summary['diagnostic_rows'].append(dict(before=status_before,after=status_after,encode_ns=packed-before))
        if observer is not None:observer(job,data,meta,profile,output)

    try:
        if mode!='overlap':
            while True:
                job=next_job()
                if job is None:break
                if mode=='frozen_serial':result=driver.transform(job.values)
                else:
                    job.ticket=driver.prepare(job.values);driver.submit(job.ticket);result=driver.finish(job.ticket)
                last_submit=result[0][3].get('submit_ns')
                emit(job,result)
        else:
            current=next_job()
            if current is not None:
                current.ticket=driver.prepare(current.values);driver.submit(current.ticket)
            while current is not None:
                following=next_job()
                if following is not None:following.ticket=driver.prepare(following.values)
                result=driver.finish(current.ticket);last_submit=result[0][3]['submit_ns']
                if following is not None:driver.submit(following.ticket)
                emit(current,result,None if following is None else driver._submitted_ns)
                current=following
    except BaseException:
        # Includes source iterator, C consumer, output callback and interrupt.
        # Invalidate the stream even when the failing operation is after DMA.
        if not driver.failed:driver.abort()
        raise
    elapsed=time.perf_counter_ns()-start
    summary.update(total_ns=elapsed,last_submit_to_finish_ns=None if last_submit is None else time.perf_counter_ns()-last_submit,
        mode=mode,timing='Actual record iterator to sink return, including slicing/preparation/submission/wait/encoding/frame/observer; no reference/IO/startup unless the supplied source/sink performs it. Overlap intervals are not additive.')
    return summary


def compress_cpu_records(records,sink):
    sequences=[0]*4;start=time.perf_counter_ns();count=0;samples=0;size=0;first=None
    for ch,begin,values in records:
        ch=operator.index(ch);begin=operator.index(begin)
        if not 0<=ch<4 or begin<0:raise ValueError('Invalid source channel/offset')
        owned=snapshot(values);words=owned.samples.view('<u2');delta=np.empty(len(words),dtype='<u2');delta[0]=words[0]
        np.subtract(words[1:],words[:-1],out=delta[1:]);payload=zlib.compress(delta,6);sequences[ch]=(sequences[ch]+1)&0xffffffff
        output=frame(owned.raw,payload,ch,sequences[ch],len(words),owned.raw_crc32);sink(output)
        if first is None:first=time.perf_counter_ns()-start
        count+=1;samples+=len(words);size+=len(output)
    return dict(total_ns=time.perf_counter_ns()-start,first_emit_ns=first,blocks=count,samples=samples,bytes=size,
        timing='Same compact source iterator, owned snapshot, modulo16 delta, zlib6 and complete frame to sink; no file loading/startup/verification.')
