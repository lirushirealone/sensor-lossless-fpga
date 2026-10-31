"""Full saved-file screen: block length versus residual coding.

An isolated copy of the frozen MDC2 reference handles up to 1024 points.
Source files and the board version are untouched. DEFLATE is a CPU reference,
not a proposed drop-in FPGA core. Every output carries a 24-byte double-CRC
header; raw fallback, short tails and per-channel sequence numbers retained.
"""
import array
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codec import raw_bytes
from real_data_common import digest, load_record, delta_bytes

OUTPUT = ROOT / 'results/block-coding-screen-20261013'
BLOCKS = (256, 512, 1024)
METHODS = ('fpga_algorithm', 'delta_deflate_cpu')
spec = importlib.util.spec_from_file_location('screen_frozen_mdc2', ROOT/'contest_codec.py')
frozen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = frozen
spec.loader.exec_module(frozen)
frozen.MAGIC = b'BSC1'
frozen.VERSION = 1
frozen.MAX_BLOCK = 1024


def sources():
    for domain, directory in (('BIDMC','bidmc-full-v1'), ('POWER','household-power-v1')):
        folder = ROOT/'data'/directory
        manifest = json.loads((folder/'manifest.json').read_text())
        for item in manifest['records']:
            if domain == 'BIDMC':
                values = load_record(item, folder)
            else:
                path = folder/item['file']
                if digest(path) != item['sha256']:
                    raise ValueError('Power source hash changed')
                packed = array.array('h', path.read_bytes())
                if sys.byteorder != 'little':
                    packed.byteswap()
                values = {ch:list(packed[ch::4]) for ch in range(4)}
                if any(len(v) != item['samples_per_channel'] for v in values.values()):
                    raise ValueError('Power source length changed')
            yield domain, item['record'], values


def encode_delta(values, ch, sequence):
    raw = raw_bytes(values)
    payload = zlib.compress(delta_bytes(values), 6)
    mode = int(len(payload) < len(raw))
    if not mode:
        payload = raw
    prefix = frozen.PREFIX.pack(b'ZSC1', 1, ch, mode, 6, sequence,
                               len(values), len(payload), zlib.crc32(raw))
    return prefix + struct.pack('<I', zlib.crc32(prefix)) + payload


def decode_delta(frame):
    magic, version, ch, mode, level, seq, count, size, crc, meta = frozen.HEADER.unpack_from(frame)
    if zlib.crc32(frame[:20]) != meta or (magic,version,level) != (b'ZSC1',1,6):
        raise ValueError('Invalid delta reference metadata')
    if ch > 3 or mode not in (0,1) or not 1 <= count <= 1024 or size > 2*count:
        raise ValueError('Invalid delta reference header')
    if len(frame) != 24+size:
        raise ValueError('Invalid delta reference length')
    payload = frame[24:]
    if mode:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload, 2*count+1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Invalid bounded DEFLATE stream')
    else:
        raw = payload
    if len(raw) != 2*count:
        raise ValueError('Delta sample count mismatch')
    values = list(struct.unpack('<'+'h'*count, raw))
    if mode:
        for i in range(1, count):
            values[i] = ((values[i-1]+values[i]+32768)&65535)-32768
    if zlib.crc32(raw_bytes(values)) != crc:
        raise ValueError('Delta sample CRC mismatch')
    return frozen.Record(ch,seq,mode,level,values,bytes(frame))


def decode_stream(data, method, expected_counts):
    restored = {ch:[] for ch in range(4)}
    seq = [0]*4
    frames = 0
    modes = Counter()
    offset = 0
    while offset < len(data):
        if len(data)-offset < 24:
            raise ValueError('Truncated frame header')
        fields = frozen.HEADER.unpack_from(data, offset)
        end = offset+24+fields[7]
        if end > len(data):
            raise ValueError('Truncated frame payload')
        r = (frozen.decode_block if method == 'fpga_algorithm' else decode_delta)(data[offset:end])
        if r.sequence != seq[r.channel]+1:
            raise ValueError('Missing/duplicate/reordered block')
        seq[r.channel] += 1
        restored[r.channel].extend(r.samples)
        modes[str(r.mode)] += 1
        frames += 1
        offset = end
    if [len(restored[ch]) for ch in range(4)] != expected_counts:
        raise ValueError('Truncated whole-frame stream')
    return restored, frames, modes


def self_check():
    for n in (1,2,255,256,257,511,512,513,1024):
        values = {ch:(([0]*n) if ch == 0 else ([32767,-32768]*((n+1)//2))[:n]) for ch in range(4)}
        for method in METHODS:
            emit = frozen.encode_block if method == 'fpga_algorithm' else encode_delta
            data = b''.join(emit(v,ch,1) for ch,v in values.items())
            assert decode_stream(data,method,[n]*4)[0] == values
            for broken in (data[:-1], data[:24], data+data):
                try:
                    decode_stream(broken,method,[n]*4)
                except (ValueError,zlib.error):
                    pass
                else:
                    raise AssertionError('Corruption guard failed')
            broken = bytearray(data)
            broken[20] ^= 1
            try:
                decode_stream(bytes(broken),method,[n]*4)
            except ValueError:
                pass
            else:
                raise AssertionError('Metadata CRC guard failed')


def run():
    started = time.perf_counter()
    latest = json.loads((ROOT/'results/galmc-v1/full/summary.json').read_text())
    source_hashes = {p:digest(ROOT/p) for p in ('contest_codec.py','research/real_data_common.py','research/screen_block_coding.py')}
    if source_hashes['contest_codec.py'] != latest['source_hashes']['contest_codec.py']:
        raise ValueError('Frozen MDC2 changed')
    manifests = {}
    for domain, folder in (('BIDMC','bidmc-full-v1'), ('POWER','household-power-v1')):
        manifests[domain] = digest(ROOT/'data'/folder/'manifest.json')
        if manifests[domain] != latest['dataset_manifest_sha256'][domain]:
            raise ValueError('Dataset manifest changed')
    self_check()
    totals = {}
    for domain, name, channels in sources():
        domain_total = totals.setdefault(domain,{})
        counts = [len(channels[ch]) for ch in range(4)]
        raw = 2*sum(counts)
        original = {str(ch):hashlib.sha256(raw_bytes(v)).hexdigest() for ch,v in channels.items()}
        for block in BLOCKS:
            for method in METHODS:
                label = f'{method}_{block}'
                total = domain_total.setdefault(label,dict(full_bytes=0,raw_bytes=0,header_bytes=0,frames=0,per_record=[]))
                emit = frozen.encode_block if method == 'fpga_algorithm' else encode_delta
                seq = [0]*4
                parts = []
                for begin in range(0,max(counts),block):
                    for ch, values in channels.items():
                        chunk = values[begin:begin+block]
                        if chunk:
                            seq[ch] += 1
                            parts.append(emit(chunk,ch,seq[ch]))
                path = OUTPUT/domain/label/(name+'.'+('bsc1' if method == 'fpga_algorithm' else 'zsc1'))
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b''.join(parts))
                data = path.read_bytes()
                restored, frames, modes = decode_stream(data,method,counts)
                recovered = {str(ch):hashlib.sha256(raw_bytes(v)).hexdigest() for ch,v in restored.items()}
                if restored != channels or original != recovered or frames != sum(seq):
                    raise AssertionError('Full saved-stream restoration differs')
                row = dict(record=name,file=path.relative_to(OUTPUT).as_posix(),full_bytes=len(data),raw_bytes=raw,
                           frames=frames,header_bytes=24*frames,mode_counts=dict(modes),stream_sha256=digest(path),
                           original_sample_sha256=original,restored_sample_sha256=recovered)
                for key in ('full_bytes','raw_bytes','header_bytes','frames'):
                    total[key] += row[key]
                total['per_record'].append(row)
        print('BLOCK_SCREEN_PASS',domain,name,flush=True)
    for domain, profiles in totals.items():
        for label, total in profiles.items():
            total['saving_percent'] = 100*(1-total['full_bytes']/total['raw_bytes'])
            total['payload_bytes'] = total['full_bytes']-total['header_bytes']
            total['gain_vs_own_256_pp'] = 100*(profiles[label.rsplit('_',1)[0]+'_256']['full_bytes']-total['full_bytes'])/total['raw_bytes']
            total['gain_vs_latest_software_pp'] = 100*(latest['domains'][domain]['forest_all']['full_bytes']-total['full_bytes'])/total['raw_bytes']
        expected = 16000267 if domain == 'BIDMC' else 181214
        if profiles['fpga_algorithm_256']['full_bytes'] != expected:
            raise AssertionError('Frozen board-reference size changed')
    summary = dict(status='SAVED_FULL_BLOCK_CODING_SCREEN_PASS',hardware_changed=False,software_only=True,
                   all_samples_crcs_sequences_and_counts_pass=True,source_hashes=source_hashes,
                   dataset_manifest_sha256=manifests,elapsed_seconds=time.perf_counter()-started,
                   config=dict(block_sizes=BLOCKS,header_bytes=24,fpga_algorithm='Unchanged MDC2 arithmetic and mode selection in isolated BSC1 software copy; only block limit and format identifier changed',
                               delta_reference='Reversible modulo-2^16 first differences; CPU zlib6, independent stream each block; raw fallback',
                               state='Independent blocks; no carry of predictor or dictionary between blocks',
                               caveat='Longer blocks change buffering, latency, random access and damage-recovery granularity; no resource/throughput claim'),domains=totals)
    (OUTPUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    write_report(summary)
    print('BLOCK_SCREEN_COMPLETE',json.dumps({d:{p:round(t['saving_percent'],5) for p,t in v.items()} for d,v in totals.items()}),flush=True)


def write_report(summary):
    report = '# 分块长度与编码方式：完整软件筛选\n\n2026-10-13。板卡关闭期间运行的软件实验，未改写冻结源码或现有硬件。54个真实记录，每个方案均保存、重读并逐点解压，样本CRC、头CRC、序号和总点数全部核对。使用与上一轮相同的原值、24字节头、原始回退与短尾块。\n\n'
    report += '| 数据 | 方法 | 块长 | 完整字节 | 完整空间节省 | 相对同方法256点增益（百分点） | 相对最新MDC5软件增益（百分点） |\n|---|---|---:|---:|---:|---:|---:|\n'
    for domain, profiles in summary['domains'].items():
        for label,t in profiles.items():
            method,block = label.rsplit('_',1)
            report += f'| {domain} | {method} | {block} | {t["full_bytes"]:,} | {t["saving_percent"]:.4f}% | {t["gain_vs_own_256_pp"]:+.4f} | {t["gain_vs_latest_software_pp"]:+.4f} |\n'
    report += '\nfpga_algorithm使用当前MDC2的同一四种预测/编码组合，独立加载并仅放宽软件块长，更换格式BSC1。256点大小与已验证硬件参考完全一致，但512/1024点尚未移植硬件。delta_deflate_cpu使用可逆模2^16差分、每块独立zlib6与原始回退，格式ZSC1，只用于评估编码方向，不代表FPGA上实现了DEFLATE。每块zlib包装开销也已包含。\n\n'
    report += '所有方法在每块重置，不依赖跨块历史。本实验能够分开观察同编码器的块长影响与相同块长的编码器差异，但不是“只去掉块头”的对照。更长块同时改变头/种子占比、模式参数选择范围和字典可用范围；也增加缓冲需求与等待时间、扩大独立校验及随机解压的粒度。损坏仍通过CRC检测，不能纠错。\n\n'
    report += '空间节省以发布int16样本为原始大小。电力沿用精确整数化和缺失位置，不比较CSV文本大小；每分钟采样不能等同于高频实采。两数据域曾参与优化，不能宣称独立泛化验证。最新MDC5森林对照为波形32.6571%、电力48.3701%。本筛选不是改用有损算法，也没有修改或替换真实数据。\n'
    (OUTPUT/'report.md').write_text(report,encoding='utf-8')


if __name__ == '__main__':
    run()
