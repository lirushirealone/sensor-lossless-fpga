/* Exact integer FIR residuals for up to 29 unchanged FSR1 model candidates.
 * No floating fitting, Rice choice or bitstream generation occurs here.
 */
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

static int64_t floor_shift(int64_t x, unsigned shift) {
    if (!shift) return x;
    if (x>=0) return x/(INT64_C(1)<<shift);
    return -((-x+(INT64_C(1)<<shift)-1)/(INT64_C(1)<<shift));
}

API int flac_predict_many(const int16_t *samples, int count, int jobs,
        const int32_t *orders, const int32_t *shifts,
        const int32_t *coefficients, int64_t *output, uint8_t *valid) {
    int job,i,lag;
    if (!samples || !orders || !shifts || !coefficients || !output || !valid ||
            count<1 || count>1024 || jobs<1 || jobs>29) return -1;
    for (job=0;job<jobs;++job) {
        int order=orders[job], shift=shifts[job];
        if (order<0 || order>12 || count<=order || shift<0 || shift>15) return -2;
        for (lag=0;lag<order;++lag)
            if (coefficients[job*12+lag]<-32768 || coefficients[job*12+lag]>32767) return -3;
    }
    for (job=0;job<jobs;++job) {
        int order=orders[job], shift=shifts[job];
        valid[job]=1;
        for (i=order;i<count;++i) {
            int64_t accumulator=0,residual;
            for (lag=1;lag<=order;++lag)
                accumulator+=(int64_t)coefficients[job*12+lag-1]*samples[i-lag];
            residual=(int64_t)samples[i]-floor_shift(accumulator,(unsigned)shift);
            output[job*1024+i-order]=residual;
            if (residual<=-INT64_C(2147483648) || residual>=INT64_C(2147483648)) valid[job]=0;
        }
    }
    return 0;
}
