"""Recreate source hashes after a deliberate, reviewed source change."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
files={}
for p in sorted(ROOT.rglob('*')):
    if not p.is_file() or p==ROOT/'manifest.json':continue
    parts=p.relative_to(ROOT).parts
    if any(x in parts for x in ('.git','.venv','__pycache__')) or parts[0]=='results' or parts[:2] in (('runtime','results'),('build','output')):continue
    files[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
(ROOT/'manifest.json').write_text(json.dumps(dict(schema='sensor-lossless-fpga-source-v4',files=files),indent=2)+'\n',encoding='utf-8')
print('SOURCE_MANIFEST_WRITTEN',len(files))
