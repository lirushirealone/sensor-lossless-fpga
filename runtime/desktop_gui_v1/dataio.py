"""Exact, bounded integer import and full local result verification."""
import csv,hashlib,json,re,struct,sys,zipfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
MAX_SAMPLES=8_000_000
sys.path.insert(0,str(ROOT/'research'))
from unified_codec import decode_file,parse_file_header

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def normalize(channels):
    if not channels or any(type(c) is not int or c not in range(4) for c in channels):raise ValueError('需要1至4个通道，命名为ch0至ch3。')
    if sum(np.asarray(a).size for a in channels.values())>MAX_SAMPLES:raise ValueError('本版每次最多800万个采样值，请分段导入。')
    out={}
    for ch,a in channels.items():
        a=np.asarray(a)
        if a.ndim!=1 or a.dtype.kind not in 'iu':raise ValueError('每个通道必须是一维整数数组；不自动量化浮点数据。')
        if len(a) and (int(a.min())<-32768 or int(a.max())>32767):raise ValueError('采样值需要在-32768至32767之间。')
        out[ch]=np.frombuffer(a.astype('<i2',copy=False).tobytes(),dtype='<i2')
    if not sum(map(len,out.values())):raise ValueError('数据中没有采样值。')
    return out

def load(path):
    p=Path(path)
    if p.stat().st_size>64*1024*1024:raise ValueError('本版导入文件上限64MiB。')
    if p.suffix.lower()=='.npz':
        with zipfile.ZipFile(p) as z:
            entries=z.infolist();names=[x.filename for x in entries]
            if not 1<=len(entries)<=4 or len(set(names))!=len(names) or any(not re.fullmatch(r'ch[0-3]\.npy',n) for n in names) or sum(x.file_size for x in entries)>2*MAX_SAMPLES+16384:raise ValueError('NPZ仅支持ch0至ch3，且总采样量需在上限内。')
        with np.load(p,allow_pickle=False,max_header_size=4096) as archive:channels={int(k[-1]):archive[k] for k in archive.files}
    elif p.suffix.lower()=='.csv':
        columns=None;data=None
        with p.open(encoding='utf-8-sig',newline='') as f:
            for line,row in enumerate(csv.reader(f),1):
                if not row or not any(v.strip() for v in row):continue
                row=[v.strip() for v in row]
                if columns is None:
                    if all(re.fullmatch(r'ch[0-3]',v.lower()) for v in row):
                        columns=[int(v[-1]) for v in row];data=[[] for _ in row]
                        if len(set(columns))!=len(columns):raise ValueError('CSV通道名不能重复。')
                        continue
                    columns=list(range(len(row)));data=[[] for _ in row]
                if len(columns)>4 or len(row)!=len(columns) or any(not re.fullmatch(r'[+-]?\d+',v,flags=re.ASCII) for v in row):raise ValueError(f'CSV第{line}行：需要1至4列整数，不能有空缺或小数。')
                if (len(data[0])+1)*len(columns)>MAX_SAMPLES:raise ValueError('CSV采样量超过800万个。')
                for col,v in zip(data,row):
                    x=int(v)
                    if not -32768<=x<=32767:raise ValueError(f'CSV第{line}行：整数超出int16范围。')
                    col.append(x)
        if data is None:raise ValueError('CSV为空。')
        channels={ch:np.array(v,dtype='<i2') for ch,v in zip(columns,data)}
    else:raise ValueError('请选择CSV或NPZ数据文件。')
    return normalize(channels)

def example(name):
    from lz77_source import load_case_arrays
    manifest=json.loads((ROOT/'results/hybrid-board-v1/benchmark-manifest.json').read_text())
    case=next(c for c in manifest['cases'] if c['record']==name)
    return normalize(load_case_arrays(case)),dict(record=name,domain=case['domain'],source=case['source_item'],kind='公开真实数据回放')

def save_npz(path,channels):
    with Path(path).open('xb') as f:np.savez(f,**{f'ch{c}':v for c,v in sorted(channels.items())})

def decode(path):
    p=Path(path)
    if p.stat().st_size>2*MAX_SAMPLES+2_000_000:raise ValueError('压缩文件超过本版安全上限。')
    data=p.read_bytes();counts,_=parse_file_header(data[:64])
    if sum(counts)>MAX_SAMPLES:raise ValueError('文件声明点数超过本版上限。')
    return decode_file(data)

def verify(path,source):
    channels,check=decode(path)
    if any(not np.array_equal(channels[ch],source.get(ch,np.array([],dtype='<i2'))) for ch in range(4)):raise ValueError('电脑全量逐点复核失败，结果不能标记成功。')
    return channels,dict(**check,compared_samples=sum(map(len,source.values())),max_absolute_error=0,source_sha256={str(c):hashlib.sha256(v.tobytes()).hexdigest() for c,v in source.items()})

def history(folder):
    folder=Path(folder);report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    if report.get('schema')!='sensor-workbench-result-v1' or report.get('status')!='VERIFIED':raise ValueError('这不是本工作台已完成的结果。')
    source=load(folder/'input.npz')
    if any(digest(folder/n)!=report['files'][n] for n in ('compressed.ucf','input.npz','restored.npz')):raise ValueError('历史文件校验值发生变化。')
    restored,check=verify(folder/'compressed.ucf',source)
    with np.load(folder/'restored.npz',allow_pickle=False) as archive:
        if set(archive.files)!={f'ch{i}' for i in range(4)} or any(not np.array_equal(archive[f'ch{i}'],restored[i]) for i in range(4)):raise ValueError('历史还原文件与压缩文件不一致。')
    raw=sum(v.nbytes for v in source.values());size=(folder/'compressed.ucf').stat().st_size
    if report['counts']!=[len(source.get(i,())) for i in range(4)] or report['raw_sample_bytes']!=raw or report['compressed_bytes']!=size or abs(report['saving_pct']-100*(1-size/raw))>1e-8:raise ValueError('历史统计与实际文件不一致。')
    return source,restored,report,check
