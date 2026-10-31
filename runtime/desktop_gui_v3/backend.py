"""Keep qualified board processing and guarded shutdown; organize completed files."""
import json,time
from desktop_gui_v2.backend import Backend as QualifiedBackend,Connection,Cancelled,CONFIG
from .history_store import STAGING_ROOT,HISTORY_ROOT,publish


class Backend(QualifiedBackend):
    def compress(self,channels,output_parent=HISTORY_ROOT,group_size=2,source_info=None):
        start=time.perf_counter_ns()
        result=super().compress(channels,STAGING_ROOT/'jobs',group_size,source_info)
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
