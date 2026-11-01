"""Exercise the standalone manifest skill on independent positive and negative cases."""
import hashlib,json,subprocess,sys,tempfile
from pathlib import Path
SCRIPT=Path(__file__).with_name('checksum_audit.py')
def main():
    cases=[]
    with tempfile.TemporaryDirectory(prefix='manifest-skill-') as temporary:
        base=Path(temporary)
        def fixture(name,content):
            root=base/name;(root/'nested').mkdir(parents=True)
            (root/'nested/data.bin').write_bytes(content)
            mapping={'nested/data.bin':hashlib.sha256(content).hexdigest()}
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'files':mapping}),encoding='utf-8')
            return root,manifest
        def check(name,root,manifest,expected):
            result=subprocess.run([sys.executable,str(SCRIPT),str(root),str(manifest)],capture_output=True,text=True)
            passed=result.returncode==0
            if passed!=expected:raise AssertionError(name+': '+result.stdout+result.stderr)
            cases.append(dict(case=name,accepted=passed,expected=expected,passed=True))
        a,am=fixture('first_project',b'\x00\x01first independent data');check('valid_nested_first_project',a,am,True)
        b,bm=fixture('another_project',b'another independently generated input');check('valid_different_project',b,bm,True)
        (a/'nested/data.bin').write_bytes(b'changed');check('tampered_file_rejected',a,am,False)
        (b/'nested/data.bin').unlink();check('missing_file_rejected',b,bm,False)
        outside=base/'outside.bin';outside.write_bytes(b'outside');digest=hashlib.sha256(b'outside').hexdigest()
        for name,path in [('relative_escape_rejected','../outside.bin'),('absolute_escape_rejected',str(outside.resolve()))]:
            am.write_text(json.dumps({'files':{path:digest}}),encoding='utf-8');check(name,a,am,False)
    result=dict(status='GENERIC_MANIFEST_SKILL_EXAMPLES_PASS',cases=cases,passed=len(cases),real_board_accessed=False)
    print(json.dumps(result,ensure_ascii=False,indent=2));return result
if __name__=='__main__':main()
