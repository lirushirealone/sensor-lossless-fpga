/* Research wrapper around system zlib. No zlib implementation is copied. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

typedef struct { z_stream stream; } gate_context;
typedef struct { uint32_t consumed, produced; int32_t zcode, cleanup; } gate_info;

const char *gate_zlib_version(void) { return zlibVersion(); }

void *gate_create(void) {
    gate_context *ctx=(gate_context *)calloc(1,sizeof(gate_context));
    if (!ctx) return NULL;
    if (deflateInit(&ctx->stream,6)!=Z_OK) { free(ctx); return NULL; }
    return ctx;
}

int gate_close(void *opaque) {
    gate_context *ctx=(gate_context *)opaque;
    if (!ctx) return Z_STREAM_ERROR;
    int result=deflateEnd(&ctx->stream);
    free(ctx);
    return result;
}

static int execute(z_stream *s,const uint8_t *in,uint32_t n,
                   uint8_t *out,uint32_t capacity,gate_info *info) {
    s->next_in=(Bytef *)in; s->avail_in=n;
    s->next_out=out; s->avail_out=capacity;
    int result=deflate(s,Z_FINISH);
    info->consumed=(uint32_t)s->total_in;
    info->produced=(uint32_t)s->total_out;
    info->zcode=result;
    if (result==Z_STREAM_END && s->avail_in==0) return 0;
    /* Use cap=native_payload. Merely producing this many bytes proves the
       candidate cannot be strictly smaller, even if an implementation needs
       another empty call before announcing stream end at an exact boundary.
       Pending bytes are discarded; they are never exposed as a valid stream. */
    if ((result==Z_OK || result==Z_BUF_ERROR) && s->avail_out==0) return 1;
    return -100+result;
}

int gate_run(void *opaque,const uint8_t *in,uint32_t n,uint8_t *out,
             uint32_t capacity,gate_info *info) {
    if (!in || !out || !info || n<2 || n>2048 || capacity<1 || capacity>4096) return -200;
    memset(info,0,sizeof(*info));
    if (opaque) {
        gate_context *ctx=(gate_context *)opaque;
        int reset=deflateReset(&ctx->stream);
        if (reset!=Z_OK) return -300+reset;
        return execute(&ctx->stream,in,n,out,capacity,info);
    }
    z_stream stream; memset(&stream,0,sizeof(stream));
    int init=deflateInit(&stream,6);
    if (init!=Z_OK) return -400+init;
    int result=execute(&stream,in,n,out,capacity,info);
    info->cleanup=deflateEnd(&stream);
    if (info->cleanup!=Z_OK && !(result==1 && info->cleanup==Z_DATA_ERROR)) return -500+info->cleanup;
    return result;
}
