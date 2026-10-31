/* Diagnostic only: preserve the frozen token consumer's decisions.
 * Timing calls are outside token loops, apart from actual block flushes.
 */
#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif
#include "lz77_pack_native.c"
#ifdef _WIN32
#include <windows.h>
static uint64_t ticks(void) {
    LARGE_INTEGER value,frequency;
    QueryPerformanceCounter(&value);QueryPerformanceFrequency(&frequency);
    return (uint64_t)((double)value.QuadPart*1e9/(double)frequency.QuadPart);
}
#else
#include <time.h>
static uint64_t ticks(void) {
    struct timespec value;clock_gettime(CLOCK_MONOTONIC,&value);
    return (uint64_t)value.tv_sec*1000000000ULL+(uint64_t)value.tv_nsec;
}
#endif

/* stages: validation, init, tally, trees/bit output, drain, trailer, free. */
API int entropy_profile_pack(const uint32_t *tokens,uint32_t nt,uint32_t n,
        uint32_t adler,uint8_t *out,uint32_t capacity,uint32_t *size,uint64_t *stages) {
    z_stream stream;deflate_state *s;uint32_t i,pos=0,start=0,used=2;
    uint64_t before=ticks(),now,loop_start;int code,flushed=0;
    memset(stages,0,7*sizeof(*stages));
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
    now=ticks();stages[0]=now-before;before=now;
    memset(&stream,0,sizeof(stream));code=deflateInit2(&stream,6,Z_DEFLATED,-15,8,Z_DEFAULT_STRATEGY);
    if(code!=Z_OK)return -5;
    now=ticks();stages[1]=now-before;
    s=(deflate_state*)stream.state;out[0]=0x78;out[1]=0x9c;pos=0;loop_start=ticks();
    for(i=0;i<nt;i++) {
        uint32_t token=tokens[i],dist=0,lc=token;
        if(token&0x80000000u){dist=token&32767;lc=((token>>16)&511)-3;pos+=lc+3;}else pos++;
        if(_tr_tally(s,dist,lc)) {
            int last=i==nt-1;
            now=ticks();stages[2]+=now-loop_start;before=now;
            _tr_flush_block(s,NULL,pos-start,last);
            now=ticks();stages[3]+=now-before;before=now;
            if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -6;}
            now=ticks();stages[4]+=now-before;
            start=pos;flushed=last;loop_start=ticks();
        }
    }
    now=ticks();stages[2]+=now-loop_start;
    if(!flushed) {
        before=ticks();_tr_flush_block(s,NULL,pos-start,1);
        now=ticks();stages[3]+=now-before;before=now;
        if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -7;}
        stages[4]+=ticks()-before;
    }
    before=ticks();
    if(capacity-used<4){deflateEnd(&stream);return -8;}
    out[used++]=(uint8_t)(adler>>24);out[used++]=(uint8_t)(adler>>16);
    out[used++]=(uint8_t)(adler>>8);out[used++]=(uint8_t)adler;
    s->status=FINISH_STATE;stages[5]=ticks()-before;
    before=ticks();deflateEnd(&stream);stages[6]=ticks()-before;
    *size=used;return 0;
}
