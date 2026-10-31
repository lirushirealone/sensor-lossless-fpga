/* Integer-only acceleration of the frozen FSR1 cost search and bit packing.
 * LPC coefficient fitting remains in the frozen Python/NumPy implementation.
 * Cost ties retain the original order: smaller k, Rice over escape, smaller
 * partition order, then the four-bit parameter representation.
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
#define MAX_N 1024
#define MAX_PARTS 64
#define PLAN_SIZE (4+3*MAX_PARTS)

static unsigned bit_length(uint64_t x) {
    unsigned n=0;
    while (x) {++n; x >>= 1;}
    return n;
}

API int flac_partition_plans(const int64_t *residual, int count, int order, int64_t *out) {
    uint64_t cumulative[31][MAX_N+1], folded[MAX_N], max_folded=0;
    int n=count-order, max_k, k, i, p, parameter_bits;
    uint64_t best_global=UINT64_MAX, best_partition=UINT64_MAX;
    if (!residual || !out || count<1 || count>MAX_N || order<0 || order>12 || n<1) return -1;
    for (i=0;i<n;++i) {
        int64_t r=residual[i];
        if (r <= -INT64_C(2147483648) || r >= INT64_C(2147483648)) return -2;
        folded[i]=(uint64_t)(r<0 ? -2*r-1 : 2*r);
        if (folded[i]>max_folded) max_folded=folded[i];
    }
    max_k=(int)bit_length(max_folded);
    if (max_k>30) max_k=30;
    for (k=0;k<=max_k;++k) {
        cumulative[k][0]=0;
        for (i=0;i<n;++i) cumulative[k][i+1]=cumulative[k][i]+(folded[i]>>k);
    }
    memset(out,0,2*PLAN_SIZE*sizeof(int64_t));
    for (p=0;p<=6;++p) {
        int parts=1<<p, step;
        int starts[MAX_PARTS], lengths[MAX_PARTS], widths[MAX_PARTS];
        if (count%parts || count/parts<=order) break;
        step=count/parts;
        for (i=0;i<parts;++i) {
            int start=i*step-order, end=(i+1)*step-order, j;
            int64_t lo, hi, mag;
            if (start<0) start=0;
            starts[i]=start; lengths[i]=end-start;
            lo=hi=residual[start];
            for (j=start+1;j<end;++j) {
                if (residual[j]<lo) lo=residual[j];
                if (residual[j]>hi) hi=residual[j];
            }
            mag=hi>~lo ? hi : ~lo;
            widths[i]=(lo==0 && hi==0) ? 0 : (int)bit_length((uint64_t)(mag>0 ? mag : 0))+1;
        }
        for (parameter_bits=4;parameter_bits<=5;++parameter_bits) {
            int64_t plan[PLAN_SIZE];
            uint64_t total=6;
            int usable=max_k+1, limit=(1<<parameter_bits)-1;
            if (usable>limit) usable=limit;
            memset(plan,0,sizeof(plan));
            plan[0]=p; plan[1]=parameter_bits; plan[2]=parts;
            for (i=0;i<parts;++i) {
                uint64_t best=UINT64_MAX, escape;
                int best_k=0, start=starts[i], end=start+lengths[i], width=-1;
                for (k=0;k<usable;++k) {
                    uint64_t cost=cumulative[k][end]-cumulative[k][start]+(uint64_t)lengths[i]*(k+1)+parameter_bits;
                    if (cost<best) {best=cost; best_k=k;}
                }
                escape=(uint64_t)parameter_bits+5+(uint64_t)lengths[i]*widths[i];
                if (widths[i]<=31 && escape<best) {best=escape; width=widths[i];}
                plan[4+3*i]=best_k; plan[5+3*i]=width; plan[6+3*i]=lengths[i];
                total+=best;
            }
            plan[3]=(int64_t)total;
            if (p==0 && total<best_global) {best_global=total; memcpy(out,plan,sizeof(plan));}
            if (total<best_partition) {best_partition=total; memcpy(out+PLAN_SIZE,plan,sizeof(plan));}
        }
    }
    return 0;
}

typedef struct {
    uint8_t *output;
    size_t capacity, used;
    uint64_t pending, total_bits;
    unsigned bits;
    int error;
} Writer;

static void put_bits(Writer *w, uint64_t value, unsigned width) {
    if (w->error) return;
    if (width>32 || (width<64 && value >= (UINT64_C(1)<<width))) {w->error=1; return;}
    w->pending=(w->pending<<width)|value;
    w->bits+=width; w->total_bits+=width;
    while (w->bits>=8) {
        if (w->used>=w->capacity) {w->error=1; return;}
        w->bits-=8;
        w->output[w->used++]=(uint8_t)(w->pending>>w->bits);
    }
    w->pending &= (UINT64_C(1)<<w->bits)-1;
}

static void zeros(Writer *w, uint64_t count) {
    uint64_t bytes;
    if (w->error) return;
    if (count>(uint64_t)w->capacity*8-w->total_bits) {w->error=1; return;}
    w->total_bits+=count;
    if (w->bits) {
        unsigned available=8-w->bits;
        if (count<available) {w->pending<<=(unsigned)count; w->bits+=(unsigned)count; return;}
        if (w->used>=w->capacity) {w->error=1; return;}
        w->output[w->used++]=(uint8_t)(w->pending<<available);
        w->pending=0; w->bits=0; count-=available;
    }
    bytes=count/8;
    if (bytes>w->capacity-w->used) {w->error=1; return;}
    memset(w->output+w->used,0,(size_t)bytes);
    w->used+=(size_t)bytes;
    w->bits=(unsigned)(count%8);
}

API int flac_pack_subframe(int lpc, int order, int precision, int shift,
        const int16_t *seeds, const int32_t *coefficients, const int64_t *residual,
        const int64_t *plan, uint8_t *output, size_t capacity, uint64_t *result) {
    Writer w;
    int i, j, offset=0, parts, parameter_bits;
    if (!output || !result || !plan || !residual || order<0 || order>12 || !capacity) return -1;
    if (order && !seeds) return -1;
    if (lpc && (!coefficients || order<1 || precision<1 || precision>15 || shift<0 || shift>15)) return -1;
    parts=(int)plan[2]; parameter_bits=(int)plan[1];
    if (parts<1 || parts>64 || (parameter_bits!=4 && parameter_bits!=5)) return -1;
    memset(&w,0,sizeof(w)); w.output=output; w.capacity=capacity;
    put_bits(&w,(uint64_t)((lpc ? 32+order-1 : 8+order)<<1),8);
    for (i=0;i<order;++i) put_bits(&w,(uint16_t)seeds[i],16);
    if (lpc) {
        put_bits(&w,(uint64_t)precision-1,4); put_bits(&w,(uint64_t)shift,5);
        for (i=0;i<order;++i) put_bits(&w,(uint32_t)coefficients[i]&((UINT32_C(1)<<precision)-1),(unsigned)precision);
    }
    put_bits(&w,(uint64_t)parameter_bits-4,2); put_bits(&w,(uint64_t)plan[0],4);
    for (i=0;i<parts;++i) {
        int k=(int)plan[4+3*i], width=(int)plan[5+3*i], length=(int)plan[6+3*i];
        if (k<0 || k>30 || width < -1 || width>31 || length<1 || offset+length>MAX_N) return -2;
        put_bits(&w,width>=0 ? (UINT32_C(1)<<parameter_bits)-1 : (uint64_t)k,(unsigned)parameter_bits);
        if (width>=0) put_bits(&w,(uint64_t)width,5);
        for (j=0;j<length;++j) {
            int64_t r=residual[offset+j];
            if (width>=0) {
                if (width==0 && r!=0) return -2;
                if (width && (r<-(INT64_C(1)<<(width-1)) || r>=(INT64_C(1)<<(width-1)))) return -2;
                put_bits(&w,(uint64_t)r&((UINT64_C(1)<<width)-1),(unsigned)width);
            } else {
                uint64_t folded=(uint64_t)(r<0 ? -2*r-1 : 2*r);
                zeros(&w,folded>>k); put_bits(&w,1,1);
                put_bits(&w,folded&((UINT64_C(1)<<k)-1),(unsigned)k);
            }
        }
        offset+=length;
    }
    if (w.bits) {
        if (w.used>=w.capacity) return -3;
        w.output[w.used++]=(uint8_t)(w.pending<<(8-w.bits));
    }
    if (w.error) return -3;
    result[0]=w.used; result[1]=w.total_bits; result[2]=(uint64_t)offset;
    return 0;
}
