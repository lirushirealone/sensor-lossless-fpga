/* Exact-order scalar Levinson/quantization. Autocorrelation stays in NumPy.
 * Compile with FP contraction/reassociation disabled. No fast-math.
 * mode 0 follows CPython 3.10/3.11 left-to-right float sum; mode 1 follows
 * CPython 3.12's compensated summation of float terms (Neumaier algorithm).
 */
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
#define MAX_ORDER 12
#define ROW 15

API double lpc_round_even(double value) {
    double base=floor(value), fraction=value-base;
    if (fraction>0.5 || (fraction==0.5 && fmod(base,2.0)!=0.0)) base+=1.0;
    return base;
}

static double correlation_sum(const double *coefficients,const double *ac,int order,int mode) {
    double main=0.0,correction=0.0;
    int i;
    for (i=0;i<order-1;++i) {
        double product=coefficients[i]*ac[order-i-1];
        double next=main+product;
        /* The initial Python int zero + first float establishes main.
         * Compensation starts with subsequent float items, not the first.
         */
        if (mode && i) {
            double lost=(fabs(main)>=fabs(product)) ? (main-next)+product : (product-next)+main;
            correction+=lost;
        }
        main=next;
    }
    if (mode && correction!=0.0 && isfinite(correction)) main+=correction;
    return main;
}

API int lpc_fit_from_ac(const double *ac,int ac_count,int mode,int32_t *out,size_t capacity) {
    double coefficients[MAX_ORDER]={0},previous[MAX_ORDER],error,threshold;
    int order,total=0;
    if (!ac || !out || ac_count<2 || ac_count>MAX_ORDER+1 || (mode!=0 && mode!=1) ||
        capacity<2u*(size_t)(ac_count-1)*ROW) return -1;
    if (!isfinite(ac[0]) || ac[0]<=0.0) return 0;
    error=ac[0]; threshold=ac[0]*1e-12;
    for (order=1;order<ac_count;++order) {
        double sum,reflection,peak=0.0;
        int i,precision;
        if (error<=threshold) break;
        sum=correlation_sum(coefficients,ac,order,mode);
        reflection=(ac[order]-sum)/error;
        if (!isfinite(reflection) || fabs(reflection)>=1.0) break;
        for (i=0;i<order-1;++i) previous[i]=coefficients[i];
        for (i=0;i<order-1;++i) coefficients[i]=previous[i]-reflection*previous[order-2-i];
        coefficients[order-1]=reflection;
        error*=1.0-reflection*reflection;
        for (i=0;i<order;++i) if (fabs(coefficients[i])>peak) peak=fabs(coefficients[i]);
        for (precision=12;precision<=15;precision+=3) {
            int bound=(1<<(precision-1))-1,shift=15,valid=1;
            int32_t quantized[MAX_ORDER]={0};
            double feedback=0.0;
            if (peak!=0.0) {
                double proposed=floor(log2((double)bound/peak));
                if (proposed<0.0) continue;
                if (proposed<15.0) shift=(int)proposed;
            }
            for (i=0;i<order;++i) {
                double scaled=coefficients[i]*(double)(1<<shift)+feedback;
                double rounded=lpc_round_even(scaled);
                feedback=scaled-rounded;
                if (rounded<-(1<<(precision-1)) || rounded>bound) valid=0;
                quantized[i]=(int32_t)rounded;
            }
            if (valid) {
                int32_t *row=out+(size_t)total*ROW;
                row[0]=order; row[1]=precision; row[2]=shift;
                for (i=0;i<MAX_ORDER;++i) row[3+i]=quantized[i];
                ++total;
            }
        }
    }
    return total;
}
