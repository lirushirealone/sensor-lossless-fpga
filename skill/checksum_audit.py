"""Verify a files->SHA256 manifest without accepting path traversal."""
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();mapping=json.loads(Path(sys.argv[2]).read_text())["files"]
for name,expected in mapping.items():
    path=(root/name).resolve()
    if path==root or not path.is_relative_to(root):raise ValueError("Unsafe manifest path")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError("Changed: "+name)
print("ALL_HASHES_PASS",len(mapping))
