/* Frozen batch/PPC1 checks, then exact ordered model selection and compact
 * metadata. No model, predictor, partition or bitstream rule changes. */
#include "batch_predict_native.c"

API int bpp1_parse_select(const uint8_t *data,size_t bytes,uint32_t blocks,uint32_t transaction,
        const uint32_t *counts,const uint32_t *jobs,const uint32_t *tags,const uint32_t *crcs,
        uint32_t *rows,uint32_t *info,uint32_t *compact,uint32_t *winners) {
    uint32_t orders[256]; unsigned block,j,which; int status;
    if(!tags || !compact || !winners || !counts || !jobs || blocks<1 || blocks>8) return 301;
    for(block=0;block<blocks;++block) {
        if(jobs[block]<1 || jobs[block]>32) return 302;
        for(j=0;j<jobs[block];++j) {
            uint32_t tag=tags[32*block+j],order=tag&255u,precision=(tag>>8)&255u,lpc=(tag>>16)&1u;
            if(tag>>17 || order>12 || order>=counts[block] ||
               (!lpc && (order>4 || precision!=0)) || (lpc && (order<1 || precision<1 || precision>15))) return 303;
            orders[32*block+j]=order;
        }
    }
    status=bpp1_parse(data,bytes,blocks,transaction,counts,jobs,orders,crcs,rows,info);
    if(status) return status;
    for(block=0;block<blocks;++block) {
        for(which=0;which<2;++which) {winners[4*block+2*which]=UINT32_MAX; winners[4*block+2*which+1]=2*counts[block];}
        for(j=0;j<jobs[block];++j) {
            uint32_t *r=rows+BATCH_ROW_WORDS*block+ROW_WORDS*j;
            uint32_t tag=tags[32*block+j],order=orders[32*block+j],precision=(tag>>8)&255u,lpc=(tag>>16)&1u;
            uint64_t overhead=8+16*(uint64_t)order+(lpc ? 9+(uint64_t)precision*order : 0);
            compact[96*block+j]=r[0]; compact[96*block+jobs[block]+j]=r[1]; compact[96*block+2*jobs[block]+j]=r[2];
            if(!r[0]) continue;
            for(which=0;which<2;++which) {
                uint32_t offset=6+6*which;
                uint64_t bits=(uint64_t)r[offset]|((uint64_t)r[offset+1]<<32);
                uint64_t size=(overhead+bits+7)/8;
                if(size<winners[4*block+2*which+1]) {
                    winners[4*block+2*which]=j; winners[4*block+2*which+1]=(uint32_t)size;
                }
            }
        }
    }
    return 0;
}
