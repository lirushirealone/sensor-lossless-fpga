/* Feasibility reference ONLY: no production or FPGA wire format changed. */
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
static int token_ok(uint32_t t, uint32_t *expanded, uint32_t raw) {
    uint32_t n=1;
    if(t&0x80000000u) {
        n=(t>>16)&511u;
        uint32_t d=t&32767u;
        if((t&0x7e008000u)||n<3||n>258||!d||d>*expanded)return 3;
    } else if(t>255)return 3;
    if(n>raw-*expanded)return 4;
    *expanded+=n;return 0;
}
API int transport_encode(unsigned format,const uint32_t *tokens,uint32_t count,
                        uint32_t raw,uint8_t *out,uint32_t capacity,uint32_t *size) {
    if(!tokens||!out||!size||format<1||format>2||!raw||raw>16384||!count||count>raw)return 1;
    *size=0;uint32_t p=0,expanded=0;
    for(uint32_t i=0;i<count;) {
        uint32_t group=format==1?1:(count-i<8?count-i:8),flag_at=p;uint8_t flags=0;
        if(format==2) {if(p>=capacity)return 2;out[p++]=0;}
        for(uint32_t j=0;j<group;j++,i++) {
            uint32_t t=tokens[i];int code=token_ok(t,&expanded,raw);if(code)return code;
            unsigned match=t>>31,n=format==1||match?3:1;
            if(n>capacity-p)return 2;
            uint32_t v=match?((((t>>16)&511u)-3)<<15)|(t&32767u):t;
            if(format==1&&match)v|=0x800000u;
            if(match)flags|=(uint8_t)(1u<<j);
            out[p++]=(uint8_t)v;
            if(n==3){out[p++]=(uint8_t)(v>>8);out[p++]=(uint8_t)(v>>16);}
        }
        if(format==2)out[flag_at]=flags;
    }
    if(expanded!=raw)return 4;
    *size=p;return 0;
}
API int transport_decode(unsigned format,const uint8_t *body,uint32_t size,
                        uint32_t count,uint32_t raw,uint32_t *out,uint32_t capacity) {
    if(!body||!out||format<1||format>2||!raw||raw>16384||!count||count>raw||count>capacity)return 1;
    uint32_t p=0,expanded=0;
    for(uint32_t i=0;i<count;) {
        uint32_t group=format==1?1:(count-i<8?count-i:8);uint8_t flags=0;
        if(format==2) {
            if(p>=size)return 2;flags=body[p++];
            if(flags&~((1u<<group)-1))return 5;
        }
        for(uint32_t j=0;j<group;j++,i++) {
            unsigned n=format==1||((flags>>j)&1)?3:1;
            if(n>size-p)return 2;
            uint32_t v=body[p++];
            if(n==3){v|=(uint32_t)body[p++]<<8;v|=(uint32_t)body[p++]<<16;}
            unsigned match=format==1?(v>>23):((flags>>j)&1);
            if(format==2&&match&&(v&0x800000u))return 5;
            uint32_t t=match?0x80000000u|((((v>>15)&255u)+3)<<16)|(v&32767u):v;
            int code=token_ok(t,&expanded,raw);if(code)return code;
            out[i]=t;
        }
    }
    return p==size&&expanded==raw?0:4;
}
