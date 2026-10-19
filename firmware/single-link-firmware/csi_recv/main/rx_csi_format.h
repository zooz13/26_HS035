#ifndef RX_CSI_FORMAT_H
#define RX_CSI_FORMAT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#define RX_CSI_QUEUE_DEPTH 32
#define RX_CSI_MAX_BYTES 768
#define RX_CSI_BASE64_CAPACITY (4 * ((RX_CSI_MAX_BYTES + 2) / 3) + 1)
#define RX_CSI_LINE_CAPACITY 1536
#define RX_CSI_CONTROL_MAGIC UINT32_C(0x43534943)
#define RX_CSI_CONTROL_VERSION 1
#define RX_CSI_CONTROL_BYTES 20
#define RX_CSI_CSV_FIELD_COUNT 28
#define RX_CSI_CSV_HEADER "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,fft_gain,agc_gain,compensate_gain_x1000000,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data_b64"

typedef struct {
    uint8_t replayed;
    bool baseline_requested;
} rx_csi_gain_progress_t;

typedef struct {
    uint8_t begin, end; /* [begin,end) prefix of the first 100 received samples */
    bool request_baseline;
} rx_csi_gain_step_t;

/* Queue drops must neither omit an early calibration sample nor expose a
 * future sample to an older record. The caller replays record+compensation for
 * every sample in this interval, then explicitly requests baseline on the
 * first delivered record whose receive ordinal was greater than 100. */
static inline bool rx_csi_gain_advance(rx_csi_gain_progress_t *progress,
                                      uint8_t samples_seen, bool after_first_100,
                                      rx_csi_gain_step_t *step)
{
    if (!progress || !step || !samples_seen || samples_seen > 100 ||
        samples_seen < progress->replayed || (after_first_100 && samples_seen != 100) ||
        (!after_first_100 && (samples_seen == progress->replayed || progress->baseline_requested)))
        return false;
    step->begin = progress->replayed;
    step->end = samples_seen;
    step->request_baseline = after_first_100 && !progress->baseline_requested;
    progress->replayed = samples_seen;
    progress->baseline_requested |= step->request_baseline;
    return true;
}

typedef enum {
    RX_CSI_PAYLOAD_INVALID = 0,
    RX_CSI_PAYLOAD_DATA,
    RX_CSI_PAYLOAD_CONTROL,
} rx_csi_payload_kind_t;

/* Application data follows the bounded, single ESP-NOW vendor IE. The CSI
 * payload may include its four-byte FCS, but no second IE or arbitrary tail. */
static inline rx_csi_payload_kind_t rx_csi_parse_action(const uint8_t *body,
                                                       size_t len, uint32_t *seq)
{
    if (!body || !seq || len < 15 || body[0] != 127 ||
        body[1] != 0x18 || body[2] != 0xfe || body[3] != 0x34 ||
        body[8] != 221 || body[9] < 5 || body[10] != 0x18 ||
        body[11] != 0xfe || body[12] != 0x34 || body[13] != 4 ||
        ((body[14] & 0x0f) != 1 && (body[14] & 0x0f) != 2) || (body[14] & 0xf0))
        return RX_CSI_PAYLOAD_INVALID;
    size_t end = 10u + body[9];
    if (end > len || (len != end && len - end != 4)) return RX_CSI_PAYLOAD_INVALID;
    size_t app_len = body[9] - 5u;
    if (app_len != 4 && app_len != RX_CSI_CONTROL_BYTES) return RX_CSI_PAYLOAD_INVALID;
    const uint8_t *app = body + 15;
    uint32_t first = (uint32_t)app[0] | ((uint32_t)app[1] << 8) |
                     ((uint32_t)app[2] << 16) | ((uint32_t)app[3] << 24);
    if (app_len == 4) {
        *seq = first;
        return RX_CSI_PAYLOAD_DATA;
    }
    if (first == RX_CSI_CONTROL_MAGIC && app[4] == RX_CSI_CONTROL_VERSION &&
        app[5] >= 1 && app[5] <= 4 && app[6] == RX_CSI_CONTROL_BYTES && app[7] == 0)
        return RX_CSI_PAYLOAD_CONTROL;
    return RX_CSI_PAYLOAD_INVALID;
}

static inline bool rx_csi_length_valid(size_t len)
{
    return len > 0 && len <= RX_CSI_MAX_BYTES;
}

static inline bool rx_csi_base64(const int8_t *data, size_t len, char *out, size_t capacity)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    if (!data || !out || !rx_csi_length_valid(len) || capacity < 4 * ((len + 2) / 3) + 1)
        return false;
    size_t at = 0;
    for (size_t i = 0; i < len; i += 3) {
        uint32_t bits = (uint32_t)(uint8_t)data[i] << 16;
        if (i + 1 < len) bits |= (uint32_t)(uint8_t)data[i + 1] << 8;
        if (i + 2 < len) bits |= (uint8_t)data[i + 2];
        out[at++] = alphabet[(bits >> 18) & 63];
        out[at++] = alphabet[(bits >> 12) & 63];
        out[at++] = i + 1 < len ? alphabet[(bits >> 6) & 63] : '=';
        out[at++] = i + 2 < len ? alphabet[bits & 63] : '=';
    }
    out[at] = 0;
    return true;
}

typedef struct {
    uint32_t id;
    uint8_t mac[6];
    int8_t rssi;
    uint8_t rate, sig_mode, mcs, bandwidth, smoothing, not_sounding;
    uint8_t aggregation, stbc, fec_coding, sgi;
    int8_t noise_floor, fft_gain;
    uint8_t agc_gain;
    int32_t compensate_gain_x1000000;
    uint8_t ampdu_cnt, channel, secondary_channel;
    uint32_t local_timestamp;
    uint8_t ant;
    uint16_t sig_len;
    uint8_t rx_format, first_word;
} rx_csi_fields_t;

/* The actual S3 serializer is shared with native tests. No gain is applied to
 * data; the coefficient is metadata for an explicitly chosen downstream step. */
static inline bool rx_csi_format_line(const rx_csi_fields_t *m, const int8_t *data,
                                      size_t len, char *out, size_t capacity)
{
    if (!m || !out || !capacity) return false;
    char encoded[RX_CSI_BASE64_CAPACITY];
    if (!rx_csi_base64(data, len, encoded, sizeof(encoded))) return false;
    int written = snprintf(out, capacity,
        "CSI_DATA_B64,%" PRIu32 ",%02x:%02x:%02x:%02x:%02x:%02x"
        ",%d,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%d,%d,%u,%" PRId32
        ",%u,%u,%u,%" PRIu32 ",%u,%u,%u,%zu,%u,%s\n",
        m->id, (unsigned)m->mac[0], (unsigned)m->mac[1], (unsigned)m->mac[2],
        (unsigned)m->mac[3], (unsigned)m->mac[4], (unsigned)m->mac[5],
        (int)m->rssi, (unsigned)m->rate, (unsigned)m->sig_mode, (unsigned)m->mcs,
        (unsigned)m->bandwidth, (unsigned)m->smoothing, (unsigned)m->not_sounding,
        (unsigned)m->aggregation, (unsigned)m->stbc, (unsigned)m->fec_coding,
        (unsigned)m->sgi, (int)m->noise_floor, (int)m->fft_gain, (unsigned)m->agc_gain,
        m->compensate_gain_x1000000, (unsigned)m->ampdu_cnt, (unsigned)m->channel,
        (unsigned)m->secondary_channel, m->local_timestamp, (unsigned)m->ant,
        (unsigned)m->sig_len, (unsigned)m->rx_format, len, (unsigned)m->first_word, encoded);
    return written >= 0 && (size_t)written < capacity;
}

static inline bool rx_csi_parse_stats_command(const char *line, uint32_t *request_id)
{
    const char prefix[] = "CSI_RX,STATS,";
    if (!line || !request_id || strncmp(line, prefix, sizeof(prefix) - 1)) return false;
    const char *p = line + sizeof(prefix) - 1;
    if (!*p) return false;
    uint32_t value = 0;
    for (; *p; p++) {
        if (*p < '0' || *p > '9') return false;
        uint32_t digit = (uint32_t)(*p - '0');
        if (value > (UINT32_MAX - digit) / 10u) return false;
        value = value * 10u + digit;
    }
    *request_id = value;
    return true;
}
#endif
