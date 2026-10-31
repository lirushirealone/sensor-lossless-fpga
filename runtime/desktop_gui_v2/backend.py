"""Reuse frozen compression, add a separate guarded Linux shutdown action."""
import hashlib,json,shlex
from pathlib import Path
from desktop_gui_v1.backend import Backend as QualifiedBackend,Connection,Cancelled,CONFIG,PY
APP=Path(__file__).resolve().parent

class Backend(QualifiedBackend):
    def shutdown(self,check_only=False):
        c=self.client()
        try:
            self.notify('phase',text='检查任务与板卡传输状态')
            body=(APP/'poweroff_board.py').read_bytes();digest=hashlib.sha256(body).hexdigest();remote='/home/xilinx/projects/fpga_compression/gui-poweroff-'+digest[:12]+'.py';s=c.open_sftp();s.get_channel().settimeout(20)
            try:
                with s.open(remote,'rb') as f:old=f.read()
                if old!=body:raise RuntimeError('关机检查程序内容不一致。')
            except FileNotFoundError:
                with s.open(remote,'wb') as f:f.write(body)
            s.close();mode='--check-only' if check_only else '--shutdown';cmd=PY+' -u '+shlex.quote(remote)+' '+mode
            inp,out,err=c.exec_command('sudo -S -p "" bash -lc '+shlex.quote(cmd),timeout=45);inp.write(self.credentials.password+'\n');inp.flush();inp.channel.shutdown_write();out.channel.settimeout(45)
            for line in out:
                try:row=json.loads(line)
                except ValueError:continue
                if row.get('status')=='FAILED':raise RuntimeError(row['error'])
                if row.get('status')==('SHUTDOWN_CHECK_PASS' if check_only else 'SHUTDOWN_SCHEDULED'):
                    row['helper_sha256']=digest;return row
            raise RuntimeError('板卡没有返回关机确认，请检查连接和任务状态。')
        finally:c.close()
