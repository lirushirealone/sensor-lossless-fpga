"""Privileged board shutdown guard. Check-only never schedules or resets anything."""
import argparse,json,os,signal,subprocess,time,uuid
from pathlib import Path
LOCK=Path('/home/xilinx/projects/fpga_compression/desktop-gui-board.lock')

def inspect_dma(state,ips,mmio_factory):
    rows=[]
    if state!='operating':
        if state not in ('power off','reset','unconfigured'):raise RuntimeError('无法确认FPGA状态：'+state)
        return rows
    for name,ip in ips.items():
        kind=ip.get('type','')
        if ':axi_dma:' not in kind and ':axi_vdma:' not in kind:continue
        params=ip.get('parameters',{});mmio=mmio_factory(ip['phys_addr'],4096)
        for channel,offset in (('MM2S',0),('S2MM',48)):
            enabled=params.get('C_INCLUDE_'+channel,params.get('c_include_'+channel.lower()))
            if enabled is None:raise RuntimeError('无法确认DMA通道配置：'+name)
            if not int(str(enabled),0):continue
            control,status=mmio.read(offset),mmio.read(offset+4)
            safe=not(control&1) and bool(status&1) if ':axi_vdma:' in kind else bool(status&3)
            if not safe or status&0x70:raise RuntimeError('板卡传输尚未结束或状态异常，请先停止任务：'+name+'/'+channel)
            rows.append(dict(ip=name,channel=channel,control=control,status=status))
    if not rows:raise RuntimeError('FPGA已加载，但没有可核对的DMA状态，请先检查板卡。')
    return rows

def command(unit):
    return ['/usr/bin/systemd-run','--unit='+unit,'--on-active=5s','--timer-property=AccuracySec=1s','/usr/bin/systemctl','poweroff']

def run(check_only,root=Path('/')):
    import fcntl
    from pynq import PL,MMIO
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    with LOCK.open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('板卡正在执行另一项压缩任务，请先停止后再关机。')
        state=(root/'sys/class/fpga_manager/fpga0/state').read_text().strip();rows=inspect_dma(state,PL.ip_dict if state=='operating' else {},MMIO)
        for executable in ('usr/bin/systemd-run','usr/bin/systemctl'):
            if not os.access(root/executable,os.X_OK):raise RuntimeError('板卡缺少正常关机命令：'+executable)
        if check_only:return dict(status='SHUTDOWN_CHECK_PASS',check_only=True,fpga_state=state,dma=rows,command_not_executed=True)
        unit='fpga-workbench-poweroff-'+uuid.uuid4().hex[:12]
        result=subprocess.run(command(unit),capture_output=True,text=True,timeout=15)
        if result.returncode:raise RuntimeError('关机指令未提交：'+(result.stderr or result.stdout).strip())
        ack=dict(status='SHUTDOWN_SCHEDULED',check_only=False,unit=unit,delay_seconds=5,fpga_state=state,dma=rows,power_led_may_remain_on=True)
        print(json.dumps(ack,ensure_ascii=False),flush=True)
        # Retain the same hardware lock until Linux shuts down. The timer is
        # managed by systemd and is independent of the desktop SSH connection.
        time.sleep(60)
        return dict(status='SHUTDOWN_NOT_COMPLETED',error='指令已提交，但60秒后系统仍在运行，需要检查板卡。')

def main():
    p=argparse.ArgumentParser();mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--check-only',action='store_true');mode.add_argument('--shutdown',action='store_true');args=p.parse_args()
    try:row=run(args.check_only)
    except Exception as e:row=dict(status='FAILED',error=str(e))
    print(json.dumps(row,ensure_ascii=False),flush=True);return 0 if row['status']=='SHUTDOWN_CHECK_PASS' else 1
if __name__=='__main__':raise SystemExit(main())
