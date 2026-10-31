/* PPC1 result validation only. No prediction, cost search or selection here.
 * Input is an immutable little-endian byte snapshot, so no alignment assumption
 * and no reference to a reusable DMA output buffer escapes this function.
 */
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
#define ROW_WORDS 15u
#define MAX_JOBS 32u
#define MAX_WORDS 2473u

static uint32_t word(const uint8_t *data, size_t i) {
    const uint8_t *p=data+4u*i;
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}

/* rows per job: flag, residual CRC, cycles; then two plans of
 * partition order, parameter bits, parts, cost low/high, parameter word offset.
 * Every bounds check precedes reading the corresponding source word.
 */
API int ppc1_parse(const uint8_t *data, size_t bytes, uint32_t count,
                  uint32_t jobs, const uint32_t *orders, uint32_t packet_crc,
                  uint32_t *rows, size_t capacity, uint32_t *wrapper) {
    size_t words, end, offset, j;
    if (!data || !orders || !rows || !wrapper || bytes%4u || bytes<36u ||
        bytes>4u*MAX_WORDS || count<1u || count>1024u || jobs<1u || jobs>MAX_JOBS ||
        capacity<jobs*ROW_WORDS) return 1;
    words=bytes/4u; end=words-4u;
    if (word(data,0)!=UINT32_C(0x31435050) || word(data,1)!=count ||
        word(data,2)!=jobs || word(data,3)!=packet_crc || word(data,4)!=1u) return 2;
    if (word(data,end)!=UINT32_C(0x31455050) || word(data,end+3u)!=0u) return 3;
    offset=5u;
    for (j=0;j<jobs;++j) {
        uint32_t *row=rows+j*ROW_WORDS;
        uint64_t costs[2]; unsigned plan;
        if (orders[j]>12u || orders[j]>=count) return 4;
        if (offset>end || end-offset<6u) return 5;
        row[0]=word(data,offset++);
        if (row[0]>1u || word(data,offset)!=UINT32_C(0x31504352) ||
            word(data,offset+1u)!=j || word(data,offset+2u)!=(count | orders[j]<<16)) return 6;
        row[1]=word(data,offset+3u); row[2]=word(data,offset+4u); offset+=5u;
        for (plan=0;plan<2u;++plan) {
            uint32_t descriptor,p,b,parts,lo,hi,k;
            uint32_t *out=row+3u+6u*plan;
            if (offset>end || end-offset<3u) return 7;
            descriptor=word(data,offset); lo=word(data,offset+1u); hi=word(data,offset+2u);
            offset+=3u; p=descriptor&255u; b=(descriptor>>8)&255u; parts=descriptor>>16;
            if (p>6u || (b!=4u && b!=5u) || parts!=(1u<<p) || hi>4095u ||
                offset>end || end-offset<parts) return 8;
            if (count%parts || count/parts<=orders[j]) return 9;
            out[0]=p; out[1]=b; out[2]=parts; out[3]=lo; out[4]=hi; out[5]=(uint32_t)offset;
            costs[plan]=(uint64_t)lo | (uint64_t)hi<<32;
            /* Validate every packed parameter, even for unselected/invalid jobs.
             * Valid hardware/golden packets always have reserved bits zero,
             * the exact per-partition residual count and a representable Rice k.
             */
            for (k=0;k<parts;++k) {
                uint32_t v=word(data,offset+k), width=(v>>5)&63u;
                uint32_t expected=count/parts-(k==0u ? orders[j] : 0u);
                if ((width!=63u && width>31u) || (v>>22)!=0u || ((v>>11)&2047u)!=expected ||
                    (width==63u && (v&31u)>=(1u<<b)-1u)) return 10;
            }
            offset+=parts;
        }
        if (row[3]!=0u || costs[1]>costs[0]) return 11;
    }
    if (offset!=end) return 12;
    wrapper[0]=word(data,end+1u); wrapper[1]=word(data,end+2u);
    return 0;
}
