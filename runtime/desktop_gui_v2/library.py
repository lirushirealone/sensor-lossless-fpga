"""Read known demo provenance only after exact-file hash verification."""
import json
from pathlib import Path
from desktop_gui_v1.dataio import digest
ROOT=Path(__file__).resolve().parents[1];DEMO=ROOT/'演示数据'
SAMPLES={name:DEMO/(name+'（示例文件）.npz') for name in ('power01','appliances_energy','bidmc01')}
def provenance(path):
    p=Path(path);base=dict(name=p.name,path=str(p.resolve()),kind='用户导入')
    catalog=DEMO/'数据来源与说明.json'
    if p.resolve().parent!=DEMO.resolve() or not catalog.exists():return base
    rows=json.loads(catalog.read_text(encoding='utf-8'))['files'];row=next((r for r in rows if r['file']==p.name),None)
    if row is not None and digest(p)==row['sha256']:base.update(row,record=p.stem)
    return base
