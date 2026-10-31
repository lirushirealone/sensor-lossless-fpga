/* One bounded call: unchanged integer prediction, residual CRC, optional pack.
 * The two frozen integer kernels are included verbatim; build identity includes
 * both included files. No fitting, search, DMA or frame format changes here.
 */
#include "flac_runtime_native.c"
#include "flac_prediction_native.c"

static uint32_t load_parameter(const uint8_t *data, size_t word_index) {
    const uint8_t *p=data+4*word_index;
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}

/* Immutable nibble table: identical reflected IEEE CRC32 to zlib, independent
 * of host byte order; each signed residual is serialized as little-endian i32.
 */
static uint32_t residual_crc(const int64_t *residual,int count) {
    static const uint32_t t[16]={
        0x00000000,0x1db71064,0x3b6e20c8,0x26d930ac,
        0x76dc4190,0x6b6b51f4,0x4db26158,0x5005713c,
        0xedb88320,0xf00f9344,0xd6d6a3e8,0xcb61b38c,
        0x9b64c2b0,0x86d3d2d4,0xa00ae278,0xbdbdf21c};
    uint32_t crc=UINT32_MAX; int i,j;
    for(i=0;i<count;++i) {
        uint32_t value=(uint32_t)residual[i];
        for(j=0;j<8;++j) {crc=(crc>>4)^t[(crc^value)&15u]; value>>=4;}
    }
    return crc^UINT32_MAX;
}

API int flac_finish_selected(const int16_t *samples,int count,int lpc,int order,
        int precision,int shift,const int32_t *coefficients,uint32_t expected_crc,
        int emit,const uint8_t *parameters,size_t parameter_bytes,size_t offset,
        int p,int b,int parts,uint64_t plan_bits,uint8_t *output,size_t capacity,
        uint64_t *result) {
    int32_t orders[1],shifts[1]; uint8_t valid[1];
    int64_t residual[1024],plan[PLAN_SIZE];
    uint64_t packed[3],expected_bits,expected_bytes; uint32_t crc; int i,code;
    if(!samples || !coefficients || !result || count<1 || count>1024 ||
       order<0 || order>12 || count<=order || (lpc!=0 && lpc!=1) ||
       (emit!=0 && emit!=1) || shift<0 || shift>15) return -1;
    if((!lpc && (order>4 || precision!=0 || shift!=0)) ||
       (lpc && (order<1 || precision<1 || precision>15))) return -2;
    if(lpc) for(i=0;i<order;++i)
        if(coefficients[i]<-(INT32_C(1)<<(precision-1)) ||
           coefficients[i]>=(INT32_C(1)<<(precision-1))) return -2;
    orders[0]=order; shifts[0]=shift;
    code=flac_predict_many(samples,count,1,orders,shifts,coefficients,residual,valid);
    if(code || !valid[0]) return -3;
    crc=residual_crc(residual,count-order);
    result[0]=0; result[1]=0; result[2]=(uint64_t)(count-order); result[3]=crc; result[4]=0;
    if(crc!=expected_crc) return -10;
    if(!emit) return 0;
    if(!parameters || !output || parameter_bytes%4 || parameter_bytes>9892 ||
       p<0 || p>6 || (b!=4 && b!=5) || parts!=(1<<p) || count%parts ||
       count/parts<=order || offset>parameter_bytes/4 ||
       (size_t)parts>parameter_bytes/4-offset || plan_bits>=(UINT64_C(1)<<44)) return -4;
    memset(plan,0,sizeof(plan)); plan[0]=p; plan[1]=b; plan[2]=parts; plan[3]=(int64_t)plan_bits;
    for(i=0;i<parts;++i) {
        uint32_t v=load_parameter(parameters,offset+(size_t)i);
        unsigned k=v&31u,width=(v>>5)&63u,length=(v>>11)&2047u;
        if(v>>22 || k>30 || (width!=63u && width>31u) ||
           (width==63u && k>=(1u<<b)-1u) ||
           length!=(unsigned)(count/parts-(i==0 ? order : 0))) return -5;
        plan[4+3*i]=k; plan[5+3*i]=width==63u ? -1 : (int64_t)width; plan[6+3*i]=length;
    }
    expected_bits=8+16*(uint64_t)order+(lpc ? 9+(uint64_t)precision*order : 0)+plan_bits;
    expected_bytes=(expected_bits+7)/8;
    if(expected_bytes<1 || expected_bytes>65536 || capacity<expected_bytes) return -6;
    code=flac_pack_subframe(lpc,order,precision,shift,samples,coefficients,residual,plan,output,(size_t)expected_bytes,packed);
    if(code || packed[0]!=expected_bytes || packed[1]!=expected_bits || packed[2]!=(uint64_t)(count-order)) return -7;
    result[0]=packed[0]; result[1]=packed[1]; result[4]=1;
    return 0;
}
