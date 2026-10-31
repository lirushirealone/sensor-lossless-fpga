"""Concurrent unchanged Rice and LZ77 endpoint regression, with saved fixtures."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime/research'),str(ROOT/'runtime')]
from lz77_batch2_protocol import parse,aggregate


def lz_members(packets,words,stats=None):
    """Validate complete envelopes, then compare stable member bytes.

    Backpressure changes performance counters and their envelope CRCs. It must
    not change token bytes. The qualified parser checks those counters too.
    """
    p=sequence=0;out=[]
    for batch,data in enumerate(packets,1):
        header=words[p];p+=1;counts=[]
        if header>>16!=0x4c42:raise ValueError('Bad batch fixture')
        for _ in range(header&65535):
            header=words[p];p+=1;n=header&65535
            if header>>16!=0x4c5a:raise ValueError('Bad block fixture')
            counts.append(n);p+=n
        members,_=parse(data,counts,batch,sequence)
        if stats is not None:aggregate(batch,stats[batch-1],batch,counts,len(data),batch,members)
        out.extend(hashlib.sha256(m[0]).hexdigest() for m in members);sequence+=len(counts)
    if p!=len(words):raise ValueError('Missing fixture coverage')
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--tools',type=Path,default=os.environ.get('VIVADO_BIN'));p.add_argument('--seed',type=int,action='append');a=p.parse_args()
    tools=a.tools
    def exe(name):
        candidates=[tools/(name+('.bat' if os.name=='nt' else ''))] if tools else []
        found=next((str(x) for x in candidates if x.exists()),None) or shutil.which(name) or shutil.which(name+'.bat')
        if not found:p.error('Missing '+name+'; set --tools or VIVADO_BIN')
        return found
    work=ROOT/'results/simulation';work.mkdir(parents=True,exist_ok=True)
    for source in [*ROOT.glob('src/rtl/*.sv'),*ROOT.glob('sim/*.sv'),*ROOT.glob('sim/vectors/*'),ROOT/'sim/run.tcl']:
        if source.is_file():shutil.copy2(source,work/source.name)
    def run(command,log):
        r=subprocess.run(command,cwd=work,capture_output=True,text=True,errors='replace');(work/log).write_text(r.stdout+'\n'+r.stderr,encoding='utf-8')
        if r.returncode:raise RuntimeError(log+': '+r.stdout[-1500:]+r.stderr[-1500:])
        return r.stdout+r.stderr
    sources=[x.name for x in work.glob('*.sv')]
    run([exe('xvlog'),'--sv',*sources],'compile.txt');run([exe('xelab'),'work.tb_unified','-s','unified'],'elaborate.txt');trials=[]
    for seed in a.seed or (0,1,17):
        # Windows vendor batch wrappers split '=' on command lines. The same
        # options-file form as the original qualified runner preserves plusargs.
        (work/'options.args').write_text('-tclbatch run.tcl\n-testplusarg SEED='+str(seed)+'\n-log sim-'+str(seed)+'.log\n',encoding='ascii')
        text=run([exe('xsim'),'unified','-f','options.args'],'run-'+str(seed)+'.txt')
        if not all(s in text for s in ('UNIFIED_DUAL_CLOCK_PASS','PARALLEL_PASS','LZ77_BATCH2_SIM_PASS')) or 'Fatal:' in text:raise RuntimeError('RTL assertions failed')
        # A software independent parser compares every received packet with the
        # frozen endpoint reference bytes, beyond the bench's internal checks.
        def packets(path,width):
            out=[];b=bytearray()
            for line in path.read_text().split():
                value=int(line,16);b.extend((value&((1<<width)-1)).to_bytes(width//8,'little'))
                if value>>width:out.append(bytes(b));b.clear()
            if b:raise ValueError('Partial AXI packet')
            return out
        lz=packets(work/'lz-actual.hex',32);rice=packets(work/'rice-actual.hex',8)
        reference=json.loads((ROOT/'sim/vectors/expected_packets.json').read_text())
        words=[int(x,16)&0xffffffff for x in (work/'lz-input.hex').read_text().split()]
        stats=[list(map(int,x.split())) for x in (work/'lz-stats.txt').read_text().splitlines()]
        # Independent lanes may finish in a different order under stalls. The
        # bench checks frame identity, duplicates, packet membership and bytes;
        # compare the complete multiset as well, without imposing completion order.
        if lz_members(lz,words,stats)!=reference['lz_member_sha256'] or sorted(hashlib.sha256(x).hexdigest() for x in rice)!=sorted(reference['rice_sha256']):raise ValueError('Frozen endpoint bytes changed')
        trials.append(dict(seed=seed,lz_batches=len(lz),lz_blocks=len(reference['lz_member_sha256']),rice_frames=len(rice),passed=True))
    (work/'summary.json').write_text(json.dumps(dict(status='SOURCE_RELEASE_RTL_PASS',trials=trials),indent=2)+'\n');print('SOURCE_RELEASE_RTL_PASS',len(trials),'seeds')


if __name__=='__main__':main()
