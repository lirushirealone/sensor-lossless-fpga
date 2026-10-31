/* Adaptive Huffman consumer of already-matched tokens. No LZ search here.
 * Internal stored blocks are unnecessary for an independent <=16KiB block:
 * the project compares the resulting candidate with the original raw block.
 * Both fixed and dynamic Huffman remain enabled, as in default zlib6.
 */
#include "lz77_native.c"
API int lz_pack(const uint32_t *tokens,uint32_t nt,uint32_t n,uint32_t adler,
                uint8_t *out,uint32_t capacity,uint32_t *size) {
    z_stream stream;deflate_state *s;uint32_t i,pos=0,start=0,used=2;
    int code,flushed=0;
    if(!n||n>16384||!nt||nt>n||capacity<65536)return -1;
    for(i=0;i<nt;i++) {
        uint32_t token=tokens[i];
        if(token&0x80000000u) {
            uint32_t length=(token>>16)&511,distance=token&32767;
            if((token&~0x81ff7fffu)||length<3||length>258||!distance||distance>pos||length>n-pos)return -2;
            pos+=length;
        } else {if(token>255||pos>=n)return -3;pos++;}
    }
    if(pos!=n)return -4;
    memset(&stream,0,sizeof(stream));code=deflateInit2(&stream,6,Z_DEFLATED,-15,8,Z_DEFAULT_STRATEGY);
    if(code!=Z_OK)return -5;
    s=(deflate_state*)stream.state;out[0]=0x78;out[1]=0x9c;pos=0;
    for(i=0;i<nt;i++) {
        uint32_t token=tokens[i],dist=0,lc=token;
        if(token&0x80000000u){dist=token&32767;lc=((token>>16)&511)-3;pos+=lc+3;}else pos++;
        if(_tr_tally(s,dist,lc)) {
            int last=i==nt-1;_tr_flush_block(s,NULL,pos-start,last);
            if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -6;}
            start=pos;flushed=last;
        }
    }
    if(!flushed){_tr_flush_block(s,NULL,pos-start,1);if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -7;}}
    if(capacity-used<4){deflateEnd(&stream);return -8;}
    out[used++]=(uint8_t)(adler>>24);out[used++]=(uint8_t)(adler>>16);
    out[used++]=(uint8_t)(adler>>8);out[used++]=(uint8_t)adler;
    s->status=FINISH_STATE;deflateEnd(&stream);*size=used;return 0;
}
