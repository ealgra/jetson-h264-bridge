/* Encoding-only wire format adapted from tpmullan/plex-nvenc-rpc.
 * GPL-2.0; see NOTICE.md and LICENSE.
 *
 * A local AF_UNIX SOCK_STREAM connection carries messages framed as:
 *   u32 little-endian length (type byte plus payload)
 *   u8 message type
 *   payload
 * Multibyte struct fields use the little-endian aarch64 layout. This is the
 * prototype's v0 wire format; changing fields requires updating both peers.
 */
#ifndef JETSON_H264_IPC_PROTOCOL_H
#define JETSON_H264_IPC_PROTOCOL_H

#include <stdint.h>

enum {
    IPC_MSG_INIT       = 1,
    IPC_MSG_FRAME      = 2,
    IPC_MSG_FLUSH      = 3,
    IPC_MSG_SHUTDOWN   = 4,
    IPC_MSG_INIT_OK    = 5,
    IPC_MSG_INIT_ERR   = 6,
    IPC_MSG_PACKET     = 7,
    IPC_MSG_FRAME_DONE = 8,
    IPC_MSG_ERR        = 9,
};

/* codec_id=0 means H.264 encode; pix_fmt=0 means FFmpeg AV_PIX_FMT_YUV420P.
 * crf<0 means unset. The service currently uses bit_rate, or max_rate when
 * Plex supplies CRF+maxrate. preset_len and x264opts_len are reserved and
 * must both be zero in this implementation. */
struct ipc_init_req {
    uint32_t codec_id;
    uint32_t width;
    uint32_t height;
    int32_t  pix_fmt;
    int64_t  bit_rate;
    uint32_t gop_size;
    uint32_t framerate_num;
    uint32_t framerate_den;
    uint32_t timebase_num;
    uint32_t timebase_den;
    int64_t  rc_max_rate;
    int64_t  rc_buffer_size;
    float    crf;
    uint32_t preset_len;
    uint32_t x264opts_len;
    uint32_t global_header;
};

/* FRAME: this header then tightly packed width*height*3/2 I420 bytes. */
struct ipc_frame_hdr { int64_t pts; };

/* INIT_OK: this header then H.264 SPS/PPS extradata. */
struct ipc_init_ok { uint32_t extradata_size; };

/* PACKET: this header then encoded H.264 access-unit bytes. */
struct ipc_packet_hdr {
    int64_t pts;
    int64_t dts;
    uint32_t size;
    uint32_t flags;
};

#endif
