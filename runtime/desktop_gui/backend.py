"""One worker owns each SSH session; real board jobs and verified local exports."""
import hashlib,json,shlex,threading,time,uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import paramiko
from . import dataio
APP=Path(__file__).resolve().parent;CONFIG=json.loads((APP/'runtime.json').read_text());PY='/usr/local/share/pynq-venv/bin/python';FORMAL='/home/xilinx/projects/fpga_compression/unified-v1'
class Cancelled(Exception):pass
@dataclass(frozen=True)
class Connection:
    host:str='192.168.2.99'
    username:str='xilinx'
    password:str=''

class Backend:
    def __init__(self,credentials,notify=lambda *a,**k:None,cancel=None):self.credentials=credentials;self.notify=notify;self.cancel=cancel or threading.Event();self.remote_job=None;self.result_folder=None
    def check(self):
        if self.cancel.is_set():raise Cancelled('操作已停止，未发布未验证结果。')
    def client(self):
        self.check();c=paramiko.SSHClient();c.load_system_host_keys();c.connect(self.credentials.host,username=self.credentials.username,password=self.credentials.password,timeout=10,banner_timeout=10,auth_timeout=10,allow_agent=False,look_for_keys=False);c.get_transport().set_keepalive(15);return c
    def code(self,c,source):
        _,o,e=c.exec_command('/usr/bin/python3 -c '+shlex.quote(source));out=o.read().decode();err=e.read().decode();status=o.channel.recv_exit_status()
        if status:raise RuntimeError('板卡准备失败：'+err[-1500:])
        return out
    def prepare(self, client):
        raise RuntimeError("Launch through tools/desktop.py to configure this release")
    def connect(self):
        c=self.client()
        try:remote,manifest=self.prepare(c);return dict(status='CONNECTED',host=self.credentials.host,version='unified-v1',bit_sha256=CONFIG['bit_sha256'],runtime=remote,hardware=CONFIG['hardware'])
        finally:c.close()
    def _compress(self,channels,output_parent,group_size=2,source_info=None):
        channels=dataio.normalize(channels);start=time.perf_counter_ns();identity=uuid.uuid4().hex;folder=Path(output_parent)/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+identity[:8]);folder.mkdir(parents=True,exist_ok=False);self.result_folder=folder;dataio.save_npz(folder/'input.npz',channels);c=None;request_sent=False
        try:
            c=self.client();remote,manifest=self.prepare(c);s=c.open_sftp();job=remote+'/jobs/'+identity;self.remote_job=job;s.mkdir(job)
            self.notify('phase',text='上传采样数据');s.put(str(folder/'input.npz'),job+'/input.npz',callback=lambda done,total:self.notify('transfer',text='上传采样数据',done=done,total=total));self.check()
            req=dict(counts=[len(channels.get(i,())) for i in range(4)],group_size=group_size,bit_sha256=CONFIG['bit_sha256'],input_sha256=dataio.digest(folder/'input.npz'))
            with s.open(job+'/request.json','w') as f:f.write(json.dumps(req))
            command='cd '+shlex.quote(remote)+' && '+PY+' -X faulthandler -u board/gui_job.py '+shlex.quote(job)
            inp,out,err=c.exec_command('sudo -S -p "" bash -lc '+shlex.quote(command));inp.write(self.credentials.password+'\n');inp.flush();inp.channel.shutdown_write();request_sent=True;channel=out.channel;pending=b'';error=b'';sent_cancel=False;result=None
            while True:
                if self.cancel.is_set() and not sent_cancel:
                    with s.open(job+'/cancel.flag','w') as f:f.write('cancel\n')
                    sent_cancel=True;self.notify('phase',text='正在安全停止，请稍候')
                if channel.recv_ready():
                    pending+=channel.recv(65536)
                    while b'\n' in pending:
                        line,pending=pending.split(b'\n',1)
                        try:event=json.loads(line.decode('utf-8'))
                        except (ValueError,UnicodeDecodeError):continue
                        kind=event.pop('kind',None)
                        if kind=='result':result=event
                        elif kind:self.notify(kind,**event)
                if channel.recv_stderr_ready():error+=channel.recv_stderr(8192)
                if channel.exit_status_ready() and not channel.recv_ready():break
                time.sleep(.04)
            status=channel.recv_exit_status()
            # Durable board status remains authoritative even if the last line was fragmented.
            try:
                with s.open(job+'/result.json') as f:result=json.loads(f.read())
                (folder/'board-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            except FileNotFoundError:raise RuntimeError('板卡未返回完整结果：'+error.decode(errors='replace')[-1000:])
            if result['status']=='CANCELLED':raise Cancelled('板卡已安全停止；未完成的结果未发布。')
            if status or result['status']!='COMPLETE':raise RuntimeError('板卡处理失败：'+result.get('error','未知错误'))
            if result['bit_sha256']!=CONFIG['bit_sha256'] or result['runtime_manifest_sha256']!=hashlib.sha256((json.dumps(manifest,indent=2)+'\n').encode()).hexdigest() or result['input_sha256']!=req['input_sha256']:raise RuntimeError('板卡结果版本或源数据不一致。')
            self.check();self.notify('phase',text='下载压缩文件');part=folder/'compressed.ucf.part';s.get(job+'/compressed.ucf',str(part),callback=lambda done,total:self.notify('transfer',text='下载压缩文件',done=done,total=total))
            if dataio.digest(part)!=result['output_sha256']:raise ValueError('下载文件SHA256不一致。')
            self.check();self.notify('phase',text='电脑全量逐点复核');restored,verification=dataio.verify(part,channels);self.check();dataio.save_npz(folder/'restored.npz',restored);part.rename(folder/'compressed.ucf')
            raw=sum(v.nbytes for v in channels.values());size=(folder/'compressed.ucf').stat().st_size;report=dict(schema='sensor-workbench-result-v1',status='VERIFIED',origin='板卡实跑 · 文件回放',completed_at=datetime.now().astimezone().isoformat(),version='unified-v1',host=self.credentials.host,source=source_info or {},counts=req['counts'],raw_sample_bytes=raw,compressed_bytes=size,saving_pct=100*(1-size/raw),group_size=group_size,board_process_ns=result['profile']['total_ns'],end_to_end_ns=time.perf_counter_ns()-start,verification=verification,board_result=result,hardware=CONFIG['hardware'],hardware_statistics_kind='当前设计实现报告，非实时利用率',files={n:dataio.digest(folder/n) for n in ('input.npz','compressed.ucf','restored.npz')},scope='原始体积按选中通道int16采样字节计算。压缩体积含完整文件及帧头。板端处理含写入；端到端含SSH/传输/板卡加载/完整校验。文件回放不等于实体ADC实时采集。',timing_note='不得相加重叠的设备、DMA和主机区间；本界面不声称FPGA端到端加速。')
            (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            (folder/'摘要.txt').write_text(f"多通道传感数据无损压缩系统\n来源：板卡实跑 / 文件回放\n原始采样：{raw}字节\n完整压缩文件：{size}字节\n空间节省：{report['saving_pct']:.5f}%\n全量逐点：{verification['compared_samples']}点一致\n最大绝对误差：0\nCRC、序号、总数：通过\n板端处理：{report['board_process_ns']/1e6:.3f}ms\n端到端：{report['end_to_end_ns']/1e6:.3f}ms\n时间边界不同，不代表端到端加速。\n",encoding='utf-8')
            return dict(folder=str(folder),report=report,source=channels,restored=restored)
        except BaseException as exc:
            if c is not None and request_sent and not isinstance(exc,Cancelled):
                try:
                    with c.open_sftp().open(self.remote_job+'/cancel.flag','w') as f:f.write('cancel\n')
                except Exception:pass
            if not (folder/'report.json').exists():(folder/'未完成.txt').write_text(str(exc)+'\n未标记为成功。远端任务：'+str(self.remote_job),encoding='utf-8')
            raise
        finally:
            if c is not None:c.close()

    def compress(self,channels,output_parent=None,group_size=2,source_info=None):
        from .history_store import STAGING_ROOT,HISTORY_ROOT,publish
        output_parent=HISTORY_ROOT if output_parent is None else output_parent
        start=time.perf_counter_ns()
        result=self._compress(channels,STAGING_ROOT/'jobs',group_size,source_info)
        self.notify('phase',text='按数据名与完成时间整理历史结果')
        # The qualified backend already performed the full comparison. Recheck
        # the stored files before publishing them into the browsable history.
        archive_start=time.perf_counter_ns();folder=publish(result['folder'],output_parent,move=True)
        report=result['report'];old_time=report['end_to_end_ns'];report['history_archive_ns']=time.perf_counter_ns()-archive_start;report['end_to_end_ns']=time.perf_counter_ns()-start
        temporary=folder/'report.json.pending';temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(folder/'report.json')
        summary=folder/'摘要.txt'
        if summary.exists():summary.write_text(summary.read_text(encoding='utf-8').replace(f'端到端：{old_time/1e6:.3f}ms',f'端到端：{report["end_to_end_ns"]/1e6:.3f}ms（含本地历史归档复核）'),encoding='utf-8')
        result['folder']=str(folder);self.result_folder=folder
        return result

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
