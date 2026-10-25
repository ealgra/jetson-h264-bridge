/* GPL-2.0: Jetson codec adapter adapted from tpmullan/plex-nvenc-rpc.
 * See NOTICE.md and LICENSE.
 * Uses the upstream IPC structs and FFmpeg 6.1 codec-plugin technique.
 * Explicitly pinned to the inspected Plex transcoder.
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
    char *x265opts;
    PacketNode *head,*tail;
    int flushed;
} State;
#define VE (AV_OPT_FLAG_VIDEO_PARAM | AV_OPT_FLAG_ENCODING_PARAM)
static const AVOption options[] = {
    {"crf", "Accepted; prototype uses explicit bitrate or maxrate", offsetof(State, crf), AV_OPT_TYPE_FLOAT, {.dbl=-1}, -1, 51, VE},
    {"preset", "Accepted; prototype uses Jetson default hardware preset", offsetof(State, preset), AV_OPT_TYPE_STRING, {.str=NULL}, 0, 0, VE},
    {"x264opts", "Accepted for prototype; not applied to Jetson", offsetof(State, x264opts), AV_OPT_TYPE_STRING, {.str=NULL}, 0, 0, VE},
    {"x265-params", "Accepted for prototype; not applied to Jetson", offsetof(State, x265opts), AV_OPT_TYPE_STRING, {.str=NULL}, 0, 0, VE},
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
    struct ipc_init_req req={.codec_id=ctx->codec_id==AV_CODEC_ID_HEVC?1:0,.width=ctx->width,.height=ctx->height,.pix_fmt=ctx->pix_fmt,
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
    av_log(ctx,AV_LOG_INFO,"Jetson hardware %s encoder connected (%dx%d)\n",
           ctx->codec_id==AV_CODEC_ID_HEVC?"H.265":"H.264",ctx->width,ctx->height);
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
#define ENCODER(NAME,ID) { .p.name=NAME, .p.long_name="Jetson V4L2 H.264/H.265 experimental RPC", \
    .p.type=AVMEDIA_TYPE_VIDEO,.p.id=ID,.p.capabilities=AV_CODEC_CAP_DELAY, \
    .p.pix_fmts=formats,.p.priv_class=&klass,.priv_data_size=sizeof(State), \
    .init=open_encoder,.cb_type=FF_CODEC_CB_TYPE_ENCODE,.cb.encode=encode,.close=close_encoder }
static const FFCodec explicit_h264_encoder=ENCODER("jetson_h264",AV_CODEC_ID_H264);
static const FFCodec explicit_h265_encoder=ENCODER("jetson_h265",AV_CODEC_ID_HEVC);
static const FFCodec replacement_h264_encoder=ENCODER("libx264",AV_CODEC_ID_H264);
static const FFCodec replacement_h265_encoder=ENCODER("libx265",AV_CODEC_ID_HEVC);

typedef struct FrameNode {
    struct FrameNode *next;
    struct ipc_dec_frame_hdr header;
    uint8_t data[];
} FrameNode;
typedef struct {
    int fd;
    int drained;
    FrameNode *head,*tail;
} DecodeState;

static int close_decoder(AVCodecContext *ctx) {
    DecodeState *s=ctx->priv_data;
    if(s->fd>=0){shutdown(s->fd,SHUT_RDWR);close(s->fd);s->fd=-1;}
    while(s->head){FrameNode *n=s->head;s->head=n->next;av_free(n);}
    s->tail=NULL;
    return 0;
}
static int open_decoder(AVCodecContext *ctx) {
    DecodeState *s=ctx->priv_data;s->fd=-1;
    const char *path=getenv("JETSON_ENCODER_SOCKET");
    if(!path || ctx->extradata_size<0 || ctx->extradata_size>65536)return AVERROR(EINVAL);
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    if(strlen(path)>=sizeof(addr.sun_path))return AVERROR(EINVAL);
    strcpy(addr.sun_path,path);
    s->fd=socket(AF_UNIX,SOCK_STREAM,0);
    if(s->fd<0)return AVERROR(EIO);
    struct timeval timeout={30,0};
    setsockopt(s->fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(s->fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    if(connect(s->fd,(struct sockaddr*)&addr,sizeof(addr))<0){close_decoder(ctx);return AVERROR(EIO);}
    AVRational tb=ctx->pkt_timebase.num>0 && ctx->pkt_timebase.den>0?ctx->pkt_timebase:ctx->time_base;
    if(tb.num<=0 || tb.den<=0){close_decoder(ctx);return AVERROR(EINVAL);}
    struct ipc_dec_init_req req={.codec_id=ctx->codec_id==AV_CODEC_ID_HEVC?1:0,
        .extradata_size=(uint32_t)ctx->extradata_size,.timebase_num=tb.num,.timebase_den=tb.den};
    size_t size=sizeof(req)+req.extradata_size;
    uint8_t *buf=av_malloc(size);if(!buf){close_decoder(ctx);return AVERROR(ENOMEM);}
    memcpy(buf,&req,sizeof(req));
    if(req.extradata_size)memcpy(buf+sizeof(req),ctx->extradata,req.extradata_size);
    int rc=ipc_send_msg(s->fd,IPC_MSG_DEC_INIT,buf,size);av_free(buf);
    if(rc<0){close_decoder(ctx);return AVERROR(EIO);}
    uint8_t type,*data=NULL;uint32_t len;
    rc=receive(s->fd,&type,&data,&len);
    if(rc<0){close_decoder(ctx);return rc;}
    if(type!=IPC_MSG_DEC_INIT_OK){av_log(ctx,AV_LOG_ERROR,"Jetson decoder init failed: %.120s\n",data);av_free(data);close_decoder(ctx);return AVERROR_EXTERNAL;}
    av_free(data);
    ctx->pix_fmt=AV_PIX_FMT_YUV420P;
    av_log(ctx,AV_LOG_INFO,"Jetson hardware %s decoder connected\n",ctx->codec_id==AV_CODEC_ID_HEVC?"H.265":"H.264");
    return 0;
}
static int pop_frame(DecodeState *s,AVFrame *frame) {
    FrameNode *n=s->head;if(!n)return 0;
    s->head=n->next;if(!s->head)s->tail=NULL;
    int w=n->header.width,h=n->header.height;
    frame->format=AV_PIX_FMT_YUV420P;frame->width=w;frame->height=h;
    int rc=av_frame_get_buffer(frame,32);
    if(rc<0){av_free(n);return rc;}
    const uint8_t *src=n->data;
    for(int plane=0;plane<3;plane++){
        int pw=plane?w/2:w,ph=plane?h/2:h;
        for(int row=0;row<ph;row++){
            memcpy(frame->data[plane]+(ptrdiff_t)row*frame->linesize[plane],src,pw);src+=pw;
        }
    }
    frame->pts=n->header.pts;av_free(n);return 1;
}
static int decode(AVCodecContext *ctx,AVFrame *frame,int *got,AVPacket *pkt) {
    DecodeState *s=ctx->priv_data;*got=0;
    if(s->drained){int rc=pop_frame(s,frame);if(rc<0)return rc;*got=rc;return 0;}
    if(pkt->size<0 || pkt->size>16*1024*1024)return AVERROR(EINVAL);
    struct ipc_dec_packet_hdr hdr={.pts=pkt->pts,.size=pkt->size};
    size_t size=sizeof(hdr)+(size_t)pkt->size;
    uint8_t *buf=av_malloc(size);if(!buf)return AVERROR(ENOMEM);
    memcpy(buf,&hdr,sizeof(hdr));if(pkt->size)memcpy(buf+sizeof(hdr),pkt->data,pkt->size);
    int rc=ipc_send_msg(s->fd,IPC_MSG_DEC_PACKET,buf,size);av_free(buf);
    if(rc<0)return AVERROR(EIO);
    if(!pkt->size)s->drained=1;
    while(1){
        uint8_t type,*data=NULL;uint32_t len;
        rc=receive(s->fd,&type,&data,&len);if(rc<0)return rc;
        if(type==IPC_MSG_DEC_DONE){av_free(data);break;}
        if(type!=IPC_MSG_DEC_FRAME || len<sizeof(struct ipc_dec_frame_hdr)){
            av_log(ctx,AV_LOG_ERROR,"Jetson decoder protocol error (type %d): %.120s\n",type,data);
            av_free(data);return AVERROR_EXTERNAL;
        }
        struct ipc_dec_frame_hdr fh;memcpy(&fh,data,sizeof(fh));
        if(fh.width<16 || fh.width>4096 || fh.width%2 || fh.height<16 || fh.height>2160 || fh.height%2 ||
           (size_t)len-sizeof(fh)!=(size_t)fh.width*fh.height*3/2){av_free(data);return AVERROR_INVALIDDATA;}
        FrameNode *n=av_malloc(sizeof(*n)+(size_t)len-sizeof(fh));
        if(!n){av_free(data);return AVERROR(ENOMEM);}
        n->next=NULL;n->header=fh;memcpy(n->data,data+sizeof(fh),len-sizeof(fh));av_free(data);
        if(s->tail)s->tail->next=n;else s->head=n;s->tail=n;
    }
    rc=pop_frame(s,frame);if(rc<0)return rc;*got=rc;
    return pkt->size;
}
#define DECODER(NAME,ID) { .p.name=NAME,.p.long_name="Jetson V4L2 H.264/H.265 experimental RPC decoder", \
    .p.type=AVMEDIA_TYPE_VIDEO,.p.id=ID,.p.capabilities=AV_CODEC_CAP_DELAY, \
    .priv_data_size=sizeof(DecodeState),.init=open_decoder,.cb_type=FF_CODEC_CB_TYPE_DECODE, \
    .cb.decode=decode,.close=close_decoder }
static const FFCodec explicit_h264_decoder=DECODER("jetson_h264_dec",AV_CODEC_ID_H264);
static const FFCodec explicit_h265_decoder=DECODER("jetson_h265_dec",AV_CODEC_ID_HEVC);
int av_init_library(void **ctx,unsigned int level){
    unsigned int (*version)(void)=ctx[4];const char *(*hash)(void)=ctx[3];
    void (*reg)(void*)=ctx[5];(void)level;
    if(version()!=0x3c1f66u || strcmp(hash(),"a336ba9-16a9aaf440ee9a92b75f070b")){
        av_log(NULL,AV_LOG_ERROR,"Jetson plugin refuses untested Plex ABI: %x %s\n",version(),hash());return -1;
    }
    reg((void*)&explicit_h264_encoder);
    reg((void*)&explicit_h265_encoder);
    reg((void*)&explicit_h264_decoder);
    reg((void*)&explicit_h265_decoder);
    if(getenv("JETSON_REPLACE_X264") && !strcmp(getenv("JETSON_REPLACE_X264"),"1"))
        reg((void*)&replacement_h264_encoder);
    if(getenv("JETSON_REPLACE_X265") && !strcmp(getenv("JETSON_REPLACE_X265"),"1"))
        reg((void*)&replacement_h265_encoder);
    return 0;
}
