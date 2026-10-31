"""Group one/two independent jobs; overlap next DMA with this group's encoding."""
import operator,time
from lz77_pipeline import Job,frame,PREFIX

def compress_batches(records,driver,codec,sink,group_size=2,observer=None,batch_observer=None):
    if type(group_size) is not int or group_size not in (1,2):raise ValueError('Group size must be1/2')
    sequences=[0]*4;iterator=iter(records);summary=dict(blocks=0,batches=0,samples=0,bytes=0,first_emit_ns=None,
        input_prepare_ns=0,input_cma_ns=0,token_entropy_ns=0,final_frame_ns=0,input_dma_bytes=0,output_dma_bytes=0)
    start=time.perf_counter_ns()
    def following():
        jobs=[]
        for _ in range(group_size):
            try:ch,begin,values=next(iterator)
            except StopIteration:break
            ch=operator.index(ch);begin=operator.index(begin)
            if not 0<=ch<4 or begin<0:raise ValueError('Invalid source channel/offset')
            sequences[ch]=(sequences[ch]+1)&0xffffffff;jobs.append(Job(ch,begin,sequences[ch],values))
        if not jobs:return None
        return jobs,driver.prepare_group(tuple(j.values for j in jobs))
    try:
        current=following()
        if current is not None:driver.submit(current[1])
        while current is not None:
            nxt=following();members,data,batch=driver.finish(current[1])
            if nxt is not None:driver.submit(nxt[1])
            next_submit=None if nxt is None else driver._submitted_ns
            for job,((packet,tokens,meta,profile),raw) in zip(current[0],members):
                before=time.perf_counter_ns();payload=codec.pack(tokens,len(raw),meta['residual_adler32']);packed=time.perf_counter_ns()
                output=frame(raw,payload,job.channel,job.sequence,len(raw)//2,meta['raw_crc32']);framed=time.perf_counter_ns()
                sink(output);emitted=time.perf_counter_ns()
                profile.update(token_entropy_ns=packed-before,final_frame_ns=framed-packed,candidate_bytes=len(output),candidate_mode=PREFIX.unpack_from(output)[3],
                    encode_started_ns=before,encode_finished_ns=packed,emit_ns=emitted,following_submitted_ns=next_submit)
                if summary['first_emit_ns'] is None:summary['first_emit_ns']=emitted-start
                summary['blocks']+=1;summary['samples']+=len(raw)//2;summary['bytes']+=len(output)
                summary['token_entropy_ns']+=packed-before;summary['final_frame_ns']+=framed-packed
                if observer is not None:observer(job,packet,meta,profile,output)
            for key in ('input_prepare_ns','input_cma_ns','input_dma_bytes','output_dma_bytes'):summary[key]+=batch[key]
            summary['batches']+=1
            if batch_observer is not None:batch_observer(data,batch)
            current=nxt
    except BaseException:
        if not driver.failed:driver.abort()
        raise
    summary.update(total_ns=time.perf_counter_ns()-start,group_size=group_size,timing='Actual compact iterator through sink and observers. Same source/owned snapshots/entropy/full independent frames. Batch metadata checked before next submit. Batch intervals overlap encoding; no double charging.')
    return summary
