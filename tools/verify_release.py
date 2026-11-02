"""Verify repository integrity, naming, full example decoding and safe publication."""
import hashlib,json,os,re,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'runtime/research')]
from desktop_gui import dataio


def files():
    for p in ROOT.rglob('*'):
        if not p.is_file():continue
        parts=p.relative_to(ROOT).parts
        if any(x in parts for x in ('.git','.venv','__pycache__')) or parts[0]=='results' or parts[:2] in (('runtime','results'),('build','output')):continue
        yield p


def main():
    manifest=json.loads((ROOT/'manifest.json').read_text());names={p.relative_to(ROOT).as_posix():p for p in files() if p.name!='manifest.json'}
    if set(names)!=set(manifest['files']):raise ValueError('Missing or extra source files: '+str(set(names)^set(manifest['files'])))
    for name,path in names.items():
        if any(not re.fullmatch(r'[a-z0-9_.-]+',part) for part in Path(name).parts):raise ValueError('Nonportable filename: '+name)
        if dataio.digest(path)!=manifest['files'][name]:raise ValueError('Changed file: '+name)
        if path.suffix.lower() in ('.py','.json','.md','.txt','.tcl','.sv','.yml','.yaml','.c','.h','.ps1','.cmd'):
            text=path.read_text(encoding='utf-8-sig',errors='replace')
            if re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',text):raise ValueError('Private key: '+name)
            if re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',text):raise ValueError('Token-like content: '+name)
            if re.search(r'[A-Z]:[/\\]Users[/\\](?!Public[/\\])',text,re.I):raise ValueError('Personal local path: '+name)
            for word in json.loads(os.environ.get('RELEASE_FORBIDDEN_TEXT','[]')):
                if word and word in text:raise ValueError('Private publication marker: '+name)
    catalog=json.loads((ROOT/'data/examples/catalog.json').read_text(encoding='utf-8'));rows=[]
    for item in catalog['files']:
        source=ROOT/'data/examples'/item['file'];arrays=dataio.load(source);reference=ROOT/'data/reference'/(source.stem+'.ucf')
        if dataio.digest(source)!=item['sha256']:raise ValueError('Example source changed')
        restored,check=dataio.verify(reference,arrays);rows.append(dict(record=source.stem,samples=check['compared_samples'],sha256=dataio.digest(reference)))
    remote=json.loads((ROOT/'board/runtime_manifest.json').read_text())
    for name,sha in remote['file_hashes'].items():
        if dataio.digest(ROOT/'runtime'/name)!=sha:raise ValueError('Remote payload differs: '+name)
    provenance=json.loads((ROOT/'evidence/source_provenance.json').read_text())
    for item in provenance['files']:
        if dataio.digest(ROOT/item['file'])!=item['submitted_sha256']:raise ValueError('Provenance mismatch: '+item['file'])
        if item['file'].startswith('src/rtl/') and item['original_sha256']!=item['submitted_sha256']:raise ValueError('RTL was altered')
    result=dict(status='SOURCE_RELEASE_INTEGRITY_PASS',files=len(names),examples=rows,real_samples_verified=sum(r['samples'] for r in rows),private_tokens_found=0,personal_paths_found=0,team_institution_found=0,bit_sha256=dataio.digest(ROOT/'runtime/unified-overlay/compressor.bit'))
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


if __name__=='__main__':main()
