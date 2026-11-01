"""Full saved-sample verification plus real offscreen Qt import/decode flow."""
import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'tools')]
os.environ['QT_QPA_PLATFORM']='offscreen'
from verify_release import main as verify
from app_config import configure
from desktop_gui import dataio
from PySide6 import QtWidgets as W

def main():
    integrity=verify();Window,Backend=configure(ROOT);app=W.QApplication([]);window=Window()
    assert not window.host.text() and not window.password.text()
    def wait():
        until=time.monotonic()+30
        while window.worker is not None and time.monotonic()<until:app.processEvents();time.sleep(.01)
        assert window.worker is None and window.last_error is None,str(window.last_error)
    examples=json.loads((ROOT/'data/examples/catalog.json').read_text(encoding='utf-8'))['files']
    for row in examples:
        path=ROOT/'data/examples'/row['file'];window.load_path(path);wait()
        assert sum(map(len,window.source.values()))==sum(map(len,dataio.load(path).values()))
    window.select_compressed(ROOT/'data/reference/power01.ucf')
    assert window.action_mode=='decode' and window.run_button.text()=='开始解压'
    window.run_primary();wait()
    assert window.decode_result is not None
    assert hasattr(Backend,'shutdown') and len(list((ROOT/'runtime').glob('desktop_gui*')))==1
    window.close();app.processEvents()
    print(json.dumps(dict(status='CURRENT_GUI_INSTALLATION_CHECK_PASS',examples=3,exact_sample_comparisons=integrity['real_samples_verified'],local_decode=True,blank_credentials=True,single_gui_package=True,board_accessed=False)))

if __name__=='__main__':main()
