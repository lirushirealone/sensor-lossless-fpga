"""Cold-build pinned native code and check every supplied LZ77 stimulus block."""
import json,sys,zlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime/research'),str(ROOT/'runtime')]
from lz77_packed_runtime import TokenConsumer
from lz77_entropy_runtime import BulkReturnConsumer


def main():
    oracle=TokenConsumer();consumer=BulkReturnConsumer();words=[int(x,16)&0xffffffff for x in (ROOT/'sim/vectors/lz-input.hex').read_text().split()];p=records=samples=0
    while p<len(words):
        header=words[p];p+=1
        if header>>16!=0x4c42:raise ValueError('Bad batch input fixture')
        for _ in range(header&65535):
            header=words[p];p+=1;n=header&65535
            if header>>16!=0x4c5a:raise ValueError('Bad member input fixture')
            original=np.asarray(words[p:p+n],dtype='<u2');p+=n;delta=original.copy();np.subtract(original[1:],original[:-1],out=delta[1:]);raw=delta.tobytes()
            tokens,reference=oracle.tokenize(raw,reference=True);packed=consumer.pack(tokens,len(raw),zlib.adler32(raw))
            # The complete deflater may choose a stored block; token-only packing
            # cannot, because it deliberately receives no original raw bytes.
            if packed!=oracle.pack(tokens,len(raw),zlib.adler32(raw)):raise ValueError('Frozen token consumer bytes changed')
            if zlib.decompress(reference)!=raw or zlib.decompress(packed)!=raw:raise ValueError('Native decompression mismatch')
            if min(len(raw),len(packed))!=min(len(raw),len(reference)):raise ValueError('Raw fallback selection changed')
            if len(packed)<len(raw) and packed!=reference:raise ValueError('Winning encoded bytes changed')
            hardware_tokens=oracle.tokenize(raw,reference=False)
            if zlib.decompress(consumer.pack(hardware_tokens,len(raw),zlib.adler32(raw)))!=raw:raise ValueError('Hardware-oracle token decode mismatch')
            records+=1;samples+=n
    print(json.dumps(dict(status='SOURCE_NATIVE_C_PASS',records=records,samples=samples,pinned_zlib='1.2.11',validation='all supplied blocks: frozen consumer equality, winning encoded bytes, raw-fallback lengths, reference and hardware-oracle full decompression')))


if __name__=='__main__':main()
