/* Project adapter/oracle for pinned zlib1.2.11, not a modification to zlib.
 * Matching rules follow deflate_slow/longest_match by Jean-loup Gailly and
 * Mark Adler (zlib license in third_party/zlib-1.2.11/zlib.h).
 * Dynamic/fixed/stored packing is performed by that library's own trees.c.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "deflate.h"
#ifdef _WIN32
#define API __declspec(dllexport)
#define TLS __declspec(thread)
#else
#define API __attribute__((visibility("default")))
#define TLS __thread
#endif
static TLS uint32_t *capture;
static TLS uint32_t capture_size,capture_capacity;
static TLS int capture_error;

int lz_capture_tally(deflate_state *s,unsigned dist,unsigned lc) {
    if(capture) {
        if(capture_size>=capture_capacity) capture_error=1;
        else capture[capture_size++]=dist ? 0x80000000u|((lc+3u)<<16)|dist : lc;
    }
    return _tr_tally(s,dist,lc);
}

API int lz_reference(const uint8_t *raw,uint32_t n,uint32_t *out,uint32_t capacity,
                     uint32_t *count,uint8_t *coded,uint32_t coded_capacity,uint32_t *coded_size) {
    z_stream stream;int code;
    if(!n || n>16384 || capacity<n || coded_capacity<n+128) return -1;
    memset(&stream,0,sizeof(stream));
    code=deflateInit(&stream,6);if(code!=Z_OK)return -2;
    capture=out;capture_size=0;capture_capacity=capacity;capture_error=0;
    stream.next_in=(Bytef*)raw;stream.avail_in=n;stream.next_out=coded;stream.avail_out=coded_capacity;
    code=deflate(&stream,Z_FINISH);
    capture=NULL;*count=capture_size;*coded_size=(uint32_t)stream.total_out;
    deflateEnd(&stream);
    return code==Z_STREAM_END && !capture_error ? 0 : -3;
}

API int lz_oracle(const uint8_t *raw,uint32_t n,uint32_t *out,uint32_t capacity,
                  uint32_t *count,uint64_t *work) {
    uint8_t *data;uint16_t *heads,*previous;
    uint32_t pos=0,match_length=2,match_start=0,pending=0,used=0,consumed=0;
    uint64_t probes=0,compares=0;
    if(!n || n>16384 || capacity<n)return -1;
    data=(uint8_t*)calloc(n+258,1);heads=(uint16_t*)calloc(32768,sizeof(uint16_t));
    previous=(uint16_t*)calloc(n,sizeof(uint16_t));
    if(!data||!heads||!previous){free(data);free(heads);free(previous);return -2;}
    memcpy(data,raw,n);
    while(pos<n) {
        uint32_t look=n-pos,head=0,prev_length=match_length,prev_match=match_start;
        if(look>=3) {
            uint32_t hash=((uint32_t)data[pos]<<10)^((uint32_t)data[pos+1]<<5)^data[pos+2];
            hash&=32767;head=heads[hash];previous[pos]=(uint16_t)head;heads[hash]=(uint16_t)pos;
        }
        match_length=2;
        if(head && prev_length<16) {
            uint32_t best=prev_length,candidate=head,budget=prev_length>=8 ? 32 : 128;
            uint32_t nice=look<128 ? look : 128;
            do {
                uint32_t length=0;probes++;
                /* Same improvement/tie/early-stop decisions as zlib. The RTL
                 * may compare extra prefix bytes instead of endpoint skipping. */
                if(data[candidate+best]==data[pos+best] && data[candidate+best-1]==data[pos+best-1] &&
                   data[candidate]==data[pos] && data[candidate+1]==data[pos+1]) {
                    while(length<258 && data[candidate+length]==data[pos+length]){length++;compares++;}
                    if(length>best) {match_start=candidate;best=length;if(length>=nice)break;}
                }
                candidate=previous[candidate];
            } while(candidate>0 && --budget);
            match_length=best>look ? look : best;
            if(match_length==3 && pos-match_start>4096)match_length=2;
        }
        if(prev_length>=3 && match_length<=prev_length) {
            uint32_t left=prev_length-2,max_insert=n>=3 ? n-3 : 0;
            uint32_t distance=pos-1-prev_match;
            if(!distance || distance>16383 || used>=capacity){free(data);free(heads);free(previous);return -3;}
            out[used++]=0x80000000u|(prev_length<<16)|distance;consumed+=prev_length;
            do {
                pos++;
                if(pos<=max_insert) {
                    uint32_t hash=(((uint32_t)data[pos]<<10)^((uint32_t)data[pos+1]<<5)^data[pos+2])&32767;
                    previous[pos]=heads[hash];heads[hash]=(uint16_t)pos;
                }
            }while(--left);
            pending=0;match_length=2;pos++;
        } else if(pending) {out[used++]=data[pos-1];consumed++;pos++;}
        else {pending=1;pos++;}
    }
    if(pending){out[used++]=data[pos-1];consumed++;}
    free(data);free(heads);free(previous);
    *count=used;work[0]=probes;work[1]=compares;
    return consumed==n ? 0 : -4;
}

static int drain(deflate_state *s,uint8_t *out,uint32_t capacity,uint32_t *used) {
    if(s->pending>capacity-*used)return -1;
    memcpy(out+*used,s->pending_buf,(size_t)s->pending);*used+=(uint32_t)s->pending;
    s->pending=0;s->pending_out=s->pending_buf;return 0;
}

API int lz_encode(const uint8_t *raw,uint32_t n,const uint32_t *tokens,uint32_t nt,
                  uint8_t *out,uint32_t capacity,uint32_t *size) {
    z_stream stream;deflate_state *s;uint32_t i,j,pos=0,start=0,used=2;uLong adler;
    int code,flushed=0;
    if(!n || n>16384 || !nt || nt>n || capacity<n+128)return -1;
    /* Validate identities before touching internal Huffman tally arrays. */
    for(i=0;i<nt;i++) {
        uint32_t token=tokens[i];
        if(token&0x80000000u) {
            uint32_t length=(token>>16)&511,distance=token&32767;
            if((token&~0x81ff7fffu)||length<3||length>258||!distance||distance>pos||length>n-pos)return -2;
            for(j=0;j<length;j++)if(raw[pos+j]!=raw[pos+j-distance])return -3;
            pos+=length;
        } else {
            if(token>255||pos>=n||raw[pos]!=(uint8_t)token)return -4;
            pos++;
        }
    }
    if(pos!=n)return -5;
    memset(&stream,0,sizeof(stream));code=deflateInit2(&stream,6,Z_DEFLATED,-15,8,Z_DEFAULT_STRATEGY);
    if(code!=Z_OK)return -6;
    s=(deflate_state*)stream.state;out[0]=0x78;out[1]=0x9c;pos=0;
    for(i=0;i<nt;i++) {
        uint32_t token=tokens[i],dist=0,lc=token;
        if(token&0x80000000u){dist=token&32767;lc=((token>>16)&511)-3;pos+=lc+3;}
        else pos++;
        if(_tr_tally(s,dist,lc)) {
            int last=i==nt-1;
            _tr_flush_block(s,(charf*)(raw+start),pos-start,last);
            if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -7;}
            start=pos;flushed=last;
        }
    }
    if(!flushed) {
        _tr_flush_block(s,(charf*)(raw+start),pos-start,1);
        if(drain(s,out,capacity,&used)){deflateEnd(&stream);return -8;}
    }
    if(capacity-used<4){deflateEnd(&stream);return -9;}
    adler=adler32(1,raw,n);
    out[used++]=(uint8_t)(adler>>24);out[used++]=(uint8_t)(adler>>16);
    out[used++]=(uint8_t)(adler>>8);out[used++]=(uint8_t)adler;
    s->status=FINISH_STATE;deflateEnd(&stream);*size=used;return 0;
}
