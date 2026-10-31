"""Stage exact qualified RTL and rebuild with a user-selected Vivado."""
import argparse,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--vivado',default=os.environ.get('VIVADO',shutil.which('vivado') or shutil.which('vivado.bat')));p.add_argument('--work',type=Path,default=ROOT/'build/output');a=p.parse_args()
    if not a.vivado:p.error('Set VIVADO or --vivado to the installed executable')
    work=a.work.resolve();work.mkdir(parents=True,exist_ok=True)
    if (work/'project').exists():p.error('Choose a fresh work directory; no existing build is overwritten')
    for source in [*ROOT.glob('src/rtl/*.sv'),ROOT/'build/ps7_preset.tcl',ROOT/'build/build_overlay.tcl']:shutil.copy2(source,work/source.name)
    result=subprocess.run([a.vivado,'-mode','batch','-source','build_overlay.tcl','-log','build.log','-journal','build.jou'],cwd=work)
    if result.returncode:raise SystemExit(result.returncode)
    print('Built overlay:',work/'overlay')


if __name__=='__main__':main()
