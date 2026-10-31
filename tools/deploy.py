"""Upload a checked private runtime; do not load a bitstream or run compression."""
import argparse,getpass,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'tools')]
from app_config import configure
from desktop_gui_v1.backend import Connection


def main():
    p=argparse.ArgumentParser();p.add_argument('--host',required=True);p.add_argument('--user',default='xilinx');a=p.parse_args()
    _,Backend=configure(ROOT);password=os.environ.get('PYNQ_PASSWORD') or getpass.getpass('Board password: ')
    result=Backend(Connection(a.host,a.user,password)).connect();print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
