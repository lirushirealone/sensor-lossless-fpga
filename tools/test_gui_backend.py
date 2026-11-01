"""Exercise the consolidated board facade without sending commands to hardware."""
import io,json,os,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
TEST_TMP=ROOT/'results/gui-test-temp';TEST_TMP.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'tools')]
from app_config import configure
from desktop_gui import dataio
from desktop_gui.backend import Connection

class ConsolidatedBackendTests(unittest.TestCase):
    def test_completed_run_is_verified_and_published_once(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp,patch.dict(os.environ,{'SENSOR_WORKBENCH_HOME':tmp}):
            _,Backend=configure(ROOT)
            from desktop_gui import history_store
            arrays=dataio.load(ROOT/'data/examples/power01.npz')
            restored,check=dataio.verify(ROOT/'data/reference/power01.ucf',arrays)
            def encoded(self,channels,output_parent,group_size,source_info):
                folder=Path(output_parent)/'job';folder.mkdir(parents=True)
                dataio.save_npz(folder/'input.npz',arrays);dataio.save_npz(folder/'restored.npz',restored)
                shutil.copyfile(ROOT/'data/reference/power01.ucf',folder/'compressed.ucf')
                raw=sum(v.nbytes for v in arrays.values());size=(folder/'compressed.ucf').stat().st_size
                report=dict(schema='sensor-workbench-result-v1',status='VERIFIED',source=source_info,completed_at='2026-11-01T12:34:56+08:00',end_to_end_ns=1,counts=[len(arrays.get(i,())) for i in range(4)],raw_sample_bytes=raw,compressed_bytes=size,saving_pct=100*(1-size/raw),files={n:dataio.digest(folder/n) for n in ['input.npz','restored.npz','compressed.ucf']})
                (folder/'report.json').write_text(json.dumps(report),encoding='utf-8')
                return dict(folder=str(folder),report=report,source=arrays,restored=restored)
            backend=Backend(Connection())
            with patch.object(Backend,'_compress',encoded):result=backend.compress(arrays,source_info=dict(name='power01'))
            folder=Path(result['folder'])
            self.assertEqual(folder.parent,Path(tmp)/'历史结果汇总/power01')
            self.assertTrue(folder.name.startswith('power01_2026-11-01_12-34-56'))
            self.assertEqual(dataio.history(folder)[3]['compared_samples'],34560)
            self.assertFalse((history_store.STAGING_ROOT/'jobs/job').exists())

    def test_preflight_shutdown_never_requests_actual_shutdown(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp,patch.dict(os.environ,{'SENSOR_WORKBENCH_HOME':tmp}):
            _,Backend=configure(ROOT);calls=[]
            class SFTP:
                def get_channel(self):return self
                def settimeout(self,n):pass
                def open(self,path,mode):
                    if mode=='rb':raise FileNotFoundError(path)
                    return io.BytesIO()
                def close(self):pass
            class Channel(io.StringIO):
                def __init__(self,*args):super().__init__(*args);self.channel=self
                def shutdown_write(self):pass
                def settimeout(self,n):pass
            class Client:
                def open_sftp(self):return SFTP()
                def exec_command(self,command,timeout):
                    calls.append(command)
                    return Channel(),Channel('{"status":"SHUTDOWN_CHECK_PASS"}\n'),Channel()
                def close(self):pass
            backend=Backend(Connection())
            with patch.object(backend,'client',return_value=Client()):result=backend.shutdown(check_only=True)
            self.assertEqual(result['status'],'SHUTDOWN_CHECK_PASS')
            self.assertEqual(len(calls),1);self.assertIn('--check-only',calls[0]);self.assertNotIn('--shutdown',calls[0])

if __name__=='__main__':unittest.main(verbosity=2)
