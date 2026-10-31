"""Input preparation only; inherit frozen LZ77 DMA/protocol/reset lifecycle."""
import time
from lz77_driver import LZ77Driver
from lz77_input import snapshot,command_words


class LZ77InputDriver(LZ77Driver):
    def transform(self,values):
        self.check();start=time.perf_counter_ns();owned=snapshot(values);words=command_words(owned)
        prepared=time.perf_counter_ns();result=self._exchange(words,len(owned.samples))
        if result[2]['raw_crc32']!=owned.raw_crc32:self.abort();raise RuntimeError('LZ77 input original CRC mismatch')
        result[3].update(input_prepare_ns=prepared-start,source_crc_checked=True,
            retained_input_snapshot_bytes=len(owned.raw)+words.nbytes,input_preparation='owned_bulk_int16_v1')
        return result,owned.raw
