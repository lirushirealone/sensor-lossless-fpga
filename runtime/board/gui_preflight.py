from pathlib import Path
import time
from pynq import PL,MMIO
state=Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()
checked=0
if state=='operating':
 for name,p in PL.ip_dict.items():
  kind=p.get('type','')
  if ':axi_dma:' not in kind and ':axi_vdma:' not in kind:continue
  params=p.get('parameters',{});mmio=MMIO(p['phys_addr'],4096)
  for channel,offset in (('MM2S',0),('S2MM',48)):
   enabled=params.get('C_INCLUDE_'+channel,params.get('c_include_'+channel.lower()))
   if enabled is None:raise RuntimeError('Unknown DMA enabled-channel metadata')
   if not int(str(enabled),0):continue
   control=mmio.read(offset);status=mmio.read(offset+4);checked+=1
   safe=(not(control&1) and bool(status&1)) if ':axi_vdma:' in kind else bool(status&3)
   if not safe and name in ('trace_analyzer_pmodb/axi_dma_0','trace_analyzer_pi/axi_dma_0') and channel=='S2MM' and not int(str(params.get('C_INCLUDE_MM2S','1')),0):
    print('RESET_DEFAULT_TRACE_DMA',name,control,status,flush=True)
    mmio.write(offset,4);deadline=time.monotonic()+1
    while mmio.read(offset)&4:
     if time.monotonic()>=deadline:raise RuntimeError('Default trace DMA reset not acknowledged')
    control=mmio.read(offset);status=mmio.read(offset+4)
    safe=not(control&1) and bool(status&1)
    print('DEFAULT_TRACE_DMA_HALTED',name,control,status,flush=True)
   if not safe or status&0x70:raise RuntimeError('DMA active/error: '+name+'/'+channel)
 if not checked:raise RuntimeError('Configured design has no known DMA state')
print('DMA_SAFE_FOR_LZ77_EXPERIMENT',state,checked,flush=True)
