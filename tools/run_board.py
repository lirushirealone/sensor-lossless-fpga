"""NPZ -> real dual-engine PYNQ compression; UCF1 -> PC/board decompression."""
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'runtime/research'),str(ROOT/'runtime/board')]
from desktop_gui_v1 import dataio


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    for name in ('compress','decompress'):
        c=sub.add_parser(name);c.add_argument('--input',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
        if name=='compress':c.add_argument('--bitfile',type=Path,default=ROOT/'runtime/unified-overlay/compressor.bit');c.add_argument('--group-size',type=int,choices=(1,2),default=2)
    a=p.parse_args()
    if a.output.exists():p.error('Output exists; select a fresh path')
    a.output.parent.mkdir(parents=True,exist_ok=True);part=a.output.with_name(a.output.name+'.part')
    if part.exists():p.error('Partial output exists; inspect it before retrying')
    if a.mode=='decompress':
        arrays,check=dataio.decode(a.input);dataio.save_npz(part,arrays);part.rename(a.output);print(json.dumps(check));return
    from unified_system import UnifiedSystem
    from lz77_source import blocks
    arrays=dataio.load(a.input);counts=[len(arrays.get(i,())) for i in range(4)]
    with UnifiedSystem(a.bitfile,group_size=a.group_size) as system,part.open('xb') as out:profile=system.compress_stream(blocks(arrays),out.write,counts)
    restored,check=dataio.verify(part,arrays);part.rename(a.output)
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(dict(profile=profile,verification=check),indent=2)+'\n')
    print(json.dumps(check))


if __name__=='__main__':main()
