"""Publish only verified runs, grouped by data name; keep earlier records intact."""
import json,re,shutil,uuid
from datetime import datetime
from pathlib import Path
from . import dataio

ROOT=Path(__file__).resolve().parents[1]
HISTORY_ROOT=ROOT/'历史结果汇总'
STAGING_ROOT=ROOT/'desktop_gui_v3/work'


def inside(path,parent):
    path=Path(path).resolve();parent=Path(parent).resolve()
    if path==parent or not path.is_relative_to(parent):
        raise ValueError('结果路径超出指定文件夹。')
    return path


def dataset_name(info):
    # Catalog names are stable across the old menu and imported example files.
    raw=str(info.get('name') or info.get('record') or Path(info.get('path') or '采样数据').name)
    if raw.lower().endswith(('.npz','.csv','.ucf')):raw=raw.rsplit('.',1)[0]
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',raw).strip(' .') or '采样数据'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:name='数据_'+name
    if len(name)>44:
        import hashlib
        name=name[:35]+'_'+hashlib.sha256(name.encode()).hexdigest()[:8]
    return name


def run_name(info,completed_at):
    stamp=datetime.fromisoformat(completed_at).strftime('%Y-%m-%d_%H-%M-%S')
    return dataset_name(info)+'_'+stamp


def unique_target(parent,base):
    target=Path(parent)/base
    if target.exists():
        # Keep the same data/date/time convention even for simultaneous runs.
        target=Path(parent)/(base+'_'+datetime.now().strftime('%f')+'_'+uuid.uuid4().hex[:4])
    return target


def publish(source,root=HISTORY_ROOT,move=False):
    """Validate before creating a dataset folder. Moving is limited to owned work."""
    source=Path(source).resolve();root=Path(root).resolve()
    if move:inside(source,STAGING_ROOT)
    arrays,restored,report,check=dataio.history(source)
    name=dataset_name(report['source']);base=run_name(report['source'],report['completed_at']);parent=inside(root/name,root)
    parent_existed=parent.exists();parent.mkdir(parents=True,exist_ok=True)
    report_sha=dataio.digest(source/'report.json')
    if not move:
        for old in parent.glob('*/report.json'):
            if dataio.digest(old)==report_sha:
                dataio.history(old.parent)
                return old.parent
    target=inside(unique_target(parent,base),parent)
    try:
        if move:
            source.rename(target)
        else:
            # Only publish the immutable completed-result files, never aliases,
            # nested exports or incomplete/decompressed files from an old run.
            staging=STAGING_ROOT/'migration'/uuid.uuid4().hex
            staging.mkdir(parents=True,exist_ok=False)
            for filename in ('input.npz','compressed.ucf','restored.npz','report.json','board-result.json','摘要.txt'):
                if (source/filename).exists():shutil.copy2(source/filename,staging/filename)
            dataio.history(staging)
            inside(staging,STAGING_ROOT).rename(target)
        return target
    except BaseException:
        if not parent_existed and parent.exists() and not any(parent.iterdir()):parent.rmdir()
        raise


def migrate_legacy(parent,root=HISTORY_ROOT):
    """Read old user history, copy verified runs, and never modify the originals."""
    rows=[]
    for report in sorted(Path(parent).glob('*/report.json')):
        try:
            target=publish(report.parent,root)
            rows.append(dict(source=str(report.parent),target=str(target),status='VERIFIED',report_sha256=dataio.digest(report)))
        except Exception as exc:
            rows.append(dict(source=str(report.parent),status='SKIPPED',reason=str(exc)))
    return rows


def decode_to_result(path,history_root=HISTORY_ROOT):
    path=Path(path).resolve();history_root=Path(history_root).resolve()
    if path.stat().st_size>2*dataio.MAX_SAMPLES+2_000_000:raise ValueError('压缩文件超过本版安全上限。')
    before=dataio.digest(path);arrays,check=dataio.decode(path)
    if before!=dataio.digest(path):raise ValueError('选择的压缩文件在读取期间发生了变化，请重新选择。')
    name=dataset_name(dict(name=path.stem))
    if path.is_relative_to(history_root):
        # Save alongside the selected historical compressed file.
        parent=path.parent;base='解压结果_'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    else:
        parent=ROOT/'解压结果汇总'/name;base=name+'_'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    staging=STAGING_ROOT/'decode'/uuid.uuid4().hex;staging.mkdir(parents=True,exist_ok=False)
    dataio.save_npz(staging/'restored.npz',arrays)
    receipt=dict(source_file=str(path),source_sha256=before,verification=check,
        restored_sha256=dataio.digest(staging/'restored.npz'),
        note='完整校验内置CRC、序号和计数；未提供外部原始文件，不等同于与外部原值逐点比较。')
    (staging/'解压说明.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    parent.mkdir(parents=True,exist_ok=True);target=inside(unique_target(parent,base),parent)
    inside(staging,STAGING_ROOT).rename(target)
    return dict(folder=str(target),arrays=arrays,verification=check,path=str(path),compressed_bytes=path.stat().st_size)
