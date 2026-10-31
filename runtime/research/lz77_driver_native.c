/* One call, twelve ordered volatile 32-bit reads of the same five GPIOs.
 * No DMA register access, no hardware counter omission, no status writes.
 */
#include <stdint.h>
#ifdef _WIN32
#include <windows.h>
#define API __declspec(dllexport)
#define BARRIER() MemoryBarrier()
#else
#define API __attribute__((visibility("default")))
#define BARRIER() __sync_synchronize()
#endif
API int lz77_stats_snapshot(const volatile uint32_t *const *ports,uint32_t nports,
                           uint32_t *out,uint32_t capacity) {
    uint32_t i;
    if(!ports || !out || nports!=5 || capacity<12)return -1;
    for(i=0;i<5;i++)if(!ports[i] || ((uintptr_t)ports[i]&3))return -2;
    BARRIER();out[0]=ports[3][0];BARRIER();
    for(i=0;i<5;i++) {out[1+2*i]=ports[i][0];out[2+2*i]=ports[i][2];}
    BARRIER();out[11]=ports[3][0];BARRIER();
    return 0;
}
