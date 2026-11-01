"""Installed entry point; paths are resolved on this computer, never hard-coded."""
import json,os,runpy,sys,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parent
try:
    receipt=json.loads((ROOT/'installation.json').read_text(encoding='utf-8-sig'))
    app=(ROOT/receipt['app_relative_path']).resolve()
    if not app.is_relative_to(ROOT/'app'):raise ValueError('Invalid installation path')
    os.environ['SENSOR_WORKBENCH_HOME']=str(ROOT)
    os.chdir(app)
    runpy.run_path(str(app/'tools/desktop.py'),run_name='__main__')
except SystemExit:
    raise
except BaseException:
    (ROOT/'运行日志').mkdir(exist_ok=True)
    log=ROOT/'运行日志/startup-error.txt';log.write_text(traceback.format_exc(),encoding='utf-8')
    import ctypes
    ctypes.windll.user32.MessageBoxW(None,'启动失败，诊断信息已保存：\n'+str(log),'多通道传感数据无损压缩系统',16)
    sys.exit(1)
