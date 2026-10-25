/* GPL-2.0: encoding-only adaptation of tpmullan/plex-nvenc-rpc.
 * See NOTICE.md and LICENSE.
 * Uses the upstream IPC structs and FFmpeg 6.1 codec-plugin technique.
 * Explicitly pinned to the inspected Plex transcoder. No decoder replacement.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <libavcodec/avcodec.h>
#include <libavcodec/codec_internal.h>
#include <libavutil/opt.h>
#include "ipc_protocol.h"
#include "ipc_io.h"

typedef struct PacketNode {
    struct PacketNode *next;
    struct ipc_packet_hdr header;
    uint8_t data[];
} PacketNode;
typedef struct {
    const AVClass *class;
    int fd;
    float crf;
    char *preset;
    char *x264opts;
    PacketNode *head,*tail;
    int flushed;
} State;
#define VE (AV_OPT_FLAG_VIDEO_PARAM | AV_OPT_FLAG_ENCODING_PARAM)
static const AVOption options[] = {
    {"crf", "Accepted; prototype uses explicit bitrate or maxrate", offsetof(State, crf), AV_OPT_TYPE_FLOAT, {.dbl=-1}, -1, 51, VE},
    {"preset", "Accepted; prototype uses Jetson default hardware preset", offsetof(State, preset), AV_OPT_TYPE_STRING, {.str=NULL}, 0, 0, VE},
    {"x264opts", "Accepted for prototype; not applied to Jetson", offsetof(State, x264opts), AV_OPT_TYPE_STRING, {.str=NULL}, 0, 0, VE},
    {NULL}
};
static const AVClass klass = {.class_name="jetson_h264", .item_name=av_default_item_name,
    .option=options, .version=LIBAVUTIL_VERSION_INT};

static int receive(int fd, uint8_t *type, uint8_t **data, uint32_t *len) {
    if (ipc_recv_header(fd,type,len)<0 || *len>32*1024*1024) return AVERROR(EIO);
    *data=av_malloc(*len+1);
    if (!*data) return AVERROR(ENOMEM);
    if (ipc_recv_payload(fd,*data,*len)<0) {av_freep(data);return AVERROR(EIO);}
    (*data)[*len]=0;
    return 0;
}
static int close_encoder(AVCodecContext *ctx) {
    State *s=ctx->priv_data;
    if(s->fd>=0){shutdown(s->fd,SHUT_RDWR);close(s->fd);s->fd=-1;}
    while(s->head){PacketNode *node=s->head;s->head=node->next;av_free(node);}
    s->tail=NULL;
    return 0;
}
static int open_encoder(AVCodecContext *ctx) {
    State *s=ctx->priv_data;s->fd=-1;
    const char *path=getenv("JETSON_ENCODER_SOCKET");
    if(!path || ctx->pix_fmt!=AV_PIX_FMT_YUV420P || ctx->width%2 || ctx->height%2)
        return AVERROR(EINVAL);
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    if(strlen(path)>=sizeof(addr.sun_path))return AVERROR(EINVAL);
    strcpy(addr.sun_path,path);
    s->fd=socket(AF_UNIX,SOCK_STREAM,0);
    if(s->fd<0)return AVERROR(EIO);
    struct timeval timeout={30,0};
    setsockopt(s->fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(s->fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    if(connect(s->fd,(struct sockaddr*)&addr,sizeof(addr))<0){close_encoder(ctx);return AVERROR(EIO);}
    struct ipc_init_req req={.codec_id=0,.width=ctx->width,.height=ctx->height,.pix_fmt=ctx->pix_fmt,
        .bit_rate=ctx->bit_rate,.gop_size=ctx->gop_size>0?ctx->gop_size:30,
        .framerate_num=ctx->framerate.num,.framerate_den=ctx->framerate.den,
        .timebase_num=ctx->time_base.num,.timebase_den=ctx->time_base.den,
        .rc_max_rate=ctx->rc_max_rate,.rc_buffer_size=ctx->rc_buffer_size,.crf=s->crf,
        .global_header=!!(ctx->flags & AV_CODEC_FLAG_GLOBAL_HEADER)};
    if(ipc_send_msg(s->fd,IPC_MSG_INIT,&req,sizeof(req))<0){close_encoder(ctx);return AVERROR(EIO);}
    uint8_t type,*data=NULL;uint32_t len;
    int rc=receive(s->fd,&type,&data,&len);
    if(rc<0){close_encoder(ctx);return rc;}
    if(type!=IPC_MSG_INIT_OK || len<4){av_log(ctx,AV_LOG_ERROR,"Jetson init failed: %s\n",data);av_free(data);close_encoder(ctx);return AVERROR_EXTERNAL;}
    uint32_t size;memcpy(&size,data,4);
    if(!size || size>len-4){av_free(data);close_encoder(ctx);return AVERROR_INVALIDDATA;}
    ctx->extradata=av_mallocz(size+AV_INPUT_BUFFER_PADDING_SIZE);
    if(!ctx->extradata){av_free(data);close_encoder(ctx);return AVERROR(ENOMEM);}
    memcpy(ctx->extradata,data+4,size);ctx->extradata_size=size;av_free(data);
    av_log(ctx,AV_LOG_INFO,"Jetson hardware H.264 encoder connected (%dx%d)\n",ctx->width,ctx->height);
    return 0;
}
static int encode(AVCodecContext *ctx,AVPacket *pkt,const AVFrame *frame,int *got) {
    State *s=ctx->priv_data;*got=0;int sent=0;
    if(frame){
        if(s->flushed)return AVERROR(EINVAL);
        int w=ctx->width,h=ctx->height;
        size_t len=8+(size_t)w*h*3/2;
        uint8_t *data=av_malloc(len);if(!data)return AVERROR(ENOMEM);
        memcpy(data,&frame->pts,8);uint8_t *p=data+8;
        for(int plane=0;plane<3;plane++){
            int pw=plane?w/2:w,ph=plane?h/2:h;
            for(int row=0;row<ph;row++){
                memcpy(p,frame->data[plane]+(ptrdiff_t)row*frame->linesize[plane],pw);p+=pw;
            }
        }
        int rc=ipc_send_msg(s->fd,IPC_MSG_FRAME,data,len);av_free(data);
        if(rc<0)return AVERROR(EIO);
        sent=1;
    }else if(!s->flushed){
        if(ipc_send_msg(s->fd,IPC_MSG_FLUSH,NULL,0)<0)return AVERROR(EIO);
        s->flushed=1;
        sent=1;
    }
    if(sent) while(1){
        uint8_t type,*data=NULL;uint32_t len;
        int rc=receive(s->fd,&type,&data,&len);if(rc<0)return rc;
        if(type==IPC_MSG_FRAME_DONE){av_free(data);break;}
        if(type!=IPC_MSG_PACKET || len<sizeof(struct ipc_packet_hdr)){
            av_log(ctx,AV_LOG_ERROR,"Jetson protocol error (type %d): %.120s\n",type,data);
            av_free(data);return AVERROR_EXTERNAL;
        }
        struct ipc_packet_hdr header;memcpy(&header,data,sizeof(header));
        if(header.size!=len-sizeof(header)){av_free(data);return AVERROR_INVALIDDATA;}
        PacketNode *node=av_malloc(sizeof(*node)+header.size);
        if(!node){av_free(data);return AVERROR(ENOMEM);}
        node->next=NULL;node->header=header;
        memcpy(node->data,data+sizeof(header),header.size);
        if(s->tail)s->tail->next=node;else s->head=node;
        s->tail=node;av_free(data);
    }
    if(s->head){
        PacketNode *node=s->head;s->head=node->next;if(!s->head)s->tail=NULL;
        int rc=av_new_packet(pkt,node->header.size);
        if(rc<0){av_free(node);return rc;}
        memcpy(pkt->data,node->data,node->header.size);
        pkt->pts=node->header.pts;pkt->dts=node->header.dts;
        pkt->flags=node->header.flags;pkt->duration=1;*got=1;
        av_free(node);
    }
    return 0;
}
static const enum AVPixelFormat formats[]={AV_PIX_FMT_YUV420P,AV_PIX_FMT_NONE};
#define ENCODER(NAME) { .p.name=NAME, .p.long_name="Jetson V4L2 H264 experimental RPC", \
    .p.type=AVMEDIA_TYPE_VIDEO,.p.id=AV_CODEC_ID_H264,.p.capabilities=AV_CODEC_CAP_DELAY, \
    .p.pix_fmts=formats,.p.priv_class=&klass,.priv_data_size=sizeof(State), \
    .init=open_encoder,.cb_type=FF_CODEC_CB_TYPE_ENCODE,.cb.encode=encode,.close=close_encoder }
static const FFCodec explicit_encoder=ENCODER("jetson_h264");
static const FFCodec replacement_encoder=ENCODER("libx264");
int av_init_library(void **ctx,unsigned int level){
    unsigned int (*version)(void)=ctx[4];const char *(*hash)(void)=ctx[3];
    void (*reg)(void*)=ctx[5];(void)level;
    if(version()!=0x3c1f66u || strcmp(hash(),"a336ba9-16a9aaf440ee9a92b75f070b")){
        av_log(NULL,AV_LOG_ERROR,"Jetson plugin refuses untested Plex ABI: %x %s\n",version(),hash());return -1;
    }
    reg((void*)&explicit_encoder);
    if(getenv("JETSON_REPLACE_X264") && !strcmp(getenv("JETSON_REPLACE_X264"),"1"))
        reg((void*)&replacement_encoder);
    return 0;
}
