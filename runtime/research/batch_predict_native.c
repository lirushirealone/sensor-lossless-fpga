/* Batch envelope walker plus the frozen, strict PPC1 validator. */
#include "predict_result_native.c"
#define BATCH_ROW_WORDS (32u*ROW_WORDS)

API int bpp1_parse(const uint8_t *data,size_t bytes,uint32_t blocks,uint32_t transaction,
        const uint32_t *counts,const uint32_t *jobs,const uint32_t *orders,const uint32_t *crcs,
        uint32_t *rows,uint32_t *info) {
    size_t words,off=3; unsigned block,j,plan;
    if(!data || !counts || !jobs || !orders || !crcs || !rows || !info ||
       blocks<1 || blocks>8 || bytes%4 || bytes<28 || bytes>4u*19855u) return 101;
    words=bytes/4;
    if(word(data,0)!=0x314f5042 || word(data,1)!=blocks || word(data,2)!=transaction) return 102;
    for(block=0;block<blocks;++block) {
        size_t start,scan,end; uint32_t *meta=info+12*block; uint32_t residual_count=0; int status;
        if(counts[block]<1 || counts[block]>1024 || jobs[block]<1 || jobs[block]>32) return 103;
        if(off>words || words-off<7 || word(data,off)!=0x31435042 || word(data,off+1)!=block) return 104;
        start=off+2; scan=start+5;
        for(j=0;j<jobs[block];++j) {
            if(scan>words || words-scan<6) return 105;
            scan+=6;
            for(plan=0;plan<2;++plan) {
                unsigned parts;
                if(scan>words || words-scan<3) return 106;
                parts=word(data,scan)>>16;
                if(parts<1 || parts>64 || words-scan-3<parts) return 107;
                scan+=3+parts;
            }
        }
        if(scan>words || words-scan<10) return 108;
        end=scan+4;
        status=ppc1_parse(data+4*start,4*(end-start),counts[block],jobs[block],orders+32*block,
            crcs[block],rows+BATCH_ROW_WORDS*block,BATCH_ROW_WORDS,meta+10);
        if(status) return 200+status;
        for(j=0;j<jobs[block];++j) residual_count+=counts[block]-orders[32*block+j];
        meta[0]=(uint32_t)start; meta[1]=(uint32_t)(end-start); meta[2]=counts[block]; meta[3]=jobs[block];
        for(j=0;j<6;++j) meta[4+j]=word(data,end+j);
        if(meta[9] || meta[7]!=jobs[block] || meta[6]!=residual_count) return 109;
        off=end+6;
    }
    if(off>words || words-off!=4 || word(data,off)!=0x31455042 ||
       word(data,off+1)!=transaction || word(data,off+2)!=blocks || word(data,off+3)) return 110;
    return 0;
}
