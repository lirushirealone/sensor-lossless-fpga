"""Official unified command entry. No Jupyter required; actual board DMA."""
import argparse,json,sys,os
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'research'),str(ROOT/'board')]
from unified_codec import decode_file
from lz77_source import load_case_arrays,blocks

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('compress');src=c.add_mutually_exclusive_group(required=True);src.add_argument('--record');src.add_argument('--npz',type=Path);c.add_argument('--output',type=Path,required=True);c.add_argument('--group-size',type=int,choices=(1,2),default=2)
    d=sub.add_parser('decompress');d.add_argument('--input',type=Path,required=True);d.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Output already exists; choose a new filename')
    args.output.parent.mkdir(parents=True,exist_ok=True);temporary=args.output.with_name(args.output.name+'.part')
    if temporary.exists():p.error('Partial output already exists; inspect it or choose a new filename')
    if args.command=='decompress':
        channels,verification=decode_file(args.input.read_bytes())
        with temporary.open('xb') as out:np.savez(out,**{f'ch{ch}':v for ch,v in channels.items()})
        temporary.rename(args.output);print(json.dumps(dict(output=str(args.output),verification=verification),ensure_ascii=False));return
    if args.record:
        manifest=json.loads((ROOT/'benchmark-manifest.json').read_text());cases=[v for v in manifest['cases'] if v['record']==args.record]
        if len(cases)!=1:p.error('Record must uniquely identify a benchmark case')
        channels=load_case_arrays(cases[0])
    else:
        with np.load(args.npz,allow_pickle=False) as source:
            if not source.files or any(k not in ('ch0','ch1','ch2','ch3') for k in source.files):p.error('NPZ must contain only ch0..ch3 integer arrays')
            channels={int(k[2:]):source[k].copy() for k in source.files}
            if any(v.ndim!=1 or v.dtype.kind not in 'iu' for v in channels.values()):p.error('Each channel must be a1D integer array')
    from unified_system import UnifiedSystem
    counts=[len(channels.get(ch,())) for ch in range(4)]
    with UnifiedSystem(group_size=args.group_size) as system,temporary.open('xb') as out:profile=system.compress_stream(blocks(channels),out.write,counts)
    restored,verification=decode_file(temporary.read_bytes())
    if any(not np.array_equal(restored[ch],channels.get(ch,np.array([],dtype='<i2'))) for ch in range(4)):raise ValueError('Saved all-point verification failed; partial file retained')
    temporary.rename(args.output);record=dict(output=str(args.output),profile=profile,verification=verification)
    args.output.with_name(args.output.name+'.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,ensure_ascii=False))
if __name__=='__main__':main()
