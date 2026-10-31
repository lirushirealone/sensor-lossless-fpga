/* Validation only. Independent bounded MSB-first decoder; no encoder code. */
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
typedef struct {const uint8_t *p;uint32_t bits,pos;int bad;} reader;
static uint32_t get(reader *r,unsigned n){
    uint32_t v=0;if(n>17||n>r->bits-r->pos){r->bad=1;return 0;}
    while(n--){v=(v<<1)|((r->p[r->pos>>3]>>(7-(r->pos&7)))&1);r->pos++;}return v;
}
API int validation_decode(const uint8_t *p,uint32_t size,unsigned mode,unsigned k,unsigned n,int16_t *out){
    reader r;unsigned i=1,left=0,width=0;int previous_width=-1;
    if(!p||!out||n<1||n>1024||mode<1||mode>4||size<2||size>=2*n||(mode<=2?k>16:k!=0))return -1;
    out[0]=(int16_t)((unsigned)p[0]|((unsigned)p[1]<<8));r.p=p+2;r.bits=(size-2)*8;r.pos=0;r.bad=0;
    while(i<n){
        int error,pred,value;
        if(mode<=2){
            uint32_t q=0,u;
            while(!r.bad&&get(&r,1)==0){q++;if(q>(131070U>>k))return -2;}
            u=(q<<k)|get(&r,k);if(r.bad||u>131070)return -3;error=(u&1)?-(int)((u+1)/2):(int)(u/2);
        }else{
            uint32_t u;
            if(left==0){
                if(previous_width<0)width=get(&r,5);
                else{unsigned code=get(&r,2);int w=code==3?(int)get(&r,5):previous_width+(int)code-1;if(w<0||w>17)return -4;width=(unsigned)w;}
                if(width>17||r.bad)return -5;previous_width=(int)width;left=n-i<4?n-i:4;
            }
            u=get(&r,width);error=(width&&(u&(1U<<(width-1))))?(int)u-(int)(1U<<width):(int)u;left--;
            if(r.bad||error<-65535||error>65535)return -6;
        }
        pred=out[i-1];if((mode==2||mode==4)&&i>=2){pred=2*(int)out[i-1]-(int)out[i-2];if(pred<-32768)pred=-32768;if(pred>32767)pred=32767;}
        value=pred+error;if(value<-32768||value>32767)return -7;out[i++]=(int16_t)value;
    }
    if(r.bad||r.bits-r.pos>=8)return -8;
    while(r.pos<r.bits)if(get(&r,1))return -9;
    return r.bad?-10:0;
}
