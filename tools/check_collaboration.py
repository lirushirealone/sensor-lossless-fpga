"""Audit selected visible messages and unchanged historical evidence; no board access."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    records=json.loads((ROOT/'report/ai_records/records.json').read_text(encoding='utf-8'))
    index=json.loads((ROOT/'evidence/collaboration/index.json').read_text(encoding='utf-8'))
    assert len(records['cases'])==10 and records['source']['complete_chat_export'] is False
    messages=0
    for case in records['cases']:
        assert case['context'] and case['derived_lesson'] and case['evidence_refs']
        assert {m['role'] for m in case['original_messages']}=={'user','assistant'}
        for m in case['original_messages']:
            assert hashlib.sha256(m['text'].encode()).hexdigest()==m['published_text_sha256']
            if not m['redactions']:assert m['original_text_sha256']==m['published_text_sha256']
            assert not re.search(r'[A-Z]:[/\\]Users[/\\]|-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{30,}',m['text'],re.I)
            messages+=1
        for name in case['evidence_refs']:
            path=(ROOT/name).resolve();assert path.is_relative_to(ROOT) and path.is_file()
    for name,entry in index['sources'].items():assert sha(ROOT/name)==entry['sha256']
    assert messages==index['messages']==88 and index['new_board_run'] is False
    print(json.dumps(dict(status='COLLABORATION_RECORDS_AND_EVIDENCE_PASS',cases=10,messages=messages,historical_evidence_files=len(index['sources']),full_chat_export=False,real_board_accessed=False)))
if __name__=='__main__':main()
