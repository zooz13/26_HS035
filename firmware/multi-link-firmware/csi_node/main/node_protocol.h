#ifndef NODE_PROTOCOL_H
#define NODE_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define NODE_MAGIC UINT32_C(0x32495343)
#define NODE_VERSION 2
#define NODE_GRANT 1
#define NODE_DATA 2
#define NODE_MODE_1X3 1
#define NODE_MODE_2X2 2
#define NODE_MODE_RR4 3
#define NODE_RESPONSE_DELAY_US 1000
#define NODE_ENQUEUE_DEADLINE_US 2500
#define NODE_DATA_WATCHDOG_US 8000
#define NODE_GRANT_WATCHDOG_US 100000
#define NODE_SLOT_GUARD_US 500
#define NODE_POST_GRANT_QUIET_US (NODE_ENQUEUE_DEADLINE_US + NODE_DATA_WATCHDOG_US + NODE_SLOT_GUARD_US)
#define NODE_RX_LEASE_US NODE_POST_GRANT_QUIET_US
#define NODE_MAX_CSI 768
#define NODE_BASE64_CAPACITY (4 * ((NODE_MAX_CSI + 2) / 3) + 1)

typedef struct {
    uint8_t id;                 /* 1=A ... 4=D; 0 is unconfigured */
    uint8_t mode;
    uint16_t rate_hz;           /* RR4: per node; legacy: per active transmitter */
    uint32_t phase_ms;          /* RR4: full four-slot cycle; 2X2: role phase */
} node_config_t;

/* Fixed little-endian ESP32 wire layout, below the single ESP-NOW IE limit. */
#ifdef _MSC_VER
#pragma pack(push, 1)
typedef struct {
#else
typedef struct __attribute__((packed)) {
#endif
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t size;
    uint32_t session;
    uint32_t phase;
    uint32_t slot;
    uint32_t seq;
    uint32_t phase_ms;
    uint16_t rate_hz;
    uint8_t mode;
    uint8_t tx_id;
    uint8_t rx_mask;
    uint8_t reserved[3];
} node_packet_t;
#ifdef _MSC_VER
#pragma pack(pop)
#endif

_Static_assert(sizeof(node_packet_t) == 36, "wire layout changed");

static inline const char *node_mode_name(uint8_t mode)
{
    switch (mode) {
        case NODE_MODE_1X3: return "1X3";
        case NODE_MODE_2X2: return "2X2";
        case NODE_MODE_RR4: return "RR4";
        default: return "UNKNOWN";
    }
}

static inline uint8_t node_mode_from_name(const char *name)
{
    if (!name) return 0;
    if (!strcmp(name, "1X3")) return NODE_MODE_1X3;
    if (!strcmp(name, "2X2")) return NODE_MODE_2X2;
    if (!strcmp(name, "RR4")) return NODE_MODE_RR4;
    return 0;
}

static inline uint32_t node_mode_slot_multiplier(uint8_t mode)
{
    switch (mode) {
        case NODE_MODE_1X3: return 1;
        case NODE_MODE_2X2: return 2;
        case NODE_MODE_RR4: return 4;
        default: return 0;
    }
}

static inline uint32_t node_slot_us(const node_config_t *c)
{
    uint32_t total = (uint32_t)c->rate_hz * node_mode_slot_multiplier(c->mode);
    return total ? 1000000u / total : 0;
}

static inline bool node_config_valid(const node_config_t *c)
{
    if (c->id < 1 || c->id > 4 || !node_mode_slot_multiplier(c->mode) ||
        c->rate_hz < 5 || c->rate_hz > 40) return false;
    uint32_t total = c->rate_hz * node_mode_slot_multiplier(c->mode);
    if (1000000u % total != 0) return false;
    if (c->mode == NODE_MODE_RR4) {
        return c->rate_hz <= 20 && (uint64_t)c->phase_ms * c->rate_hz == 1000;
    }
    return c->phase_ms >= 1000 && c->phase_ms <= 600000 &&
           ((uint64_t)c->phase_ms * c->rate_hz) % 1000 == 0;
}

static inline void node_mac(uint8_t id, uint8_t mac[6])
{
    const uint8_t prefix[5] = {0x02, 0x43, 0x53, 0x49, 0x02};
    memcpy(mac, prefix, 5);
    mac[5] = id;
}

static inline bool node_source_valid(uint8_t id, const uint8_t mac[6])
{
    uint8_t expected[6];
    node_mac(id, expected);
    return memcmp(mac, expected, 6) == 0;
}

static inline node_packet_t node_make_grant(const node_config_t *c,
                                           uint32_t session, uint32_t slot)
{
    uint32_t phase = 0;
    uint8_t tx = 1;
    uint8_t rx_mask = 0x0e;
    if (c->mode == NODE_MODE_RR4) {
        phase = slot / 4u;
        tx = (uint8_t)(1u + slot % 4u);
        rx_mask = (uint8_t)(0x0fu & ~(1u << (tx - 1u)));
    } else if (c->mode == NODE_MODE_2X2) {
        phase = slot / ((c->phase_ms * c->rate_hz * 2u) / 1000u);
        tx = (uint8_t)(1u + (phase % 2u) * 2u + slot % 2u);
        rx_mask = phase % 2u ? 0x03 : 0x0c;
    }
    node_packet_t p = {
        .magic = NODE_MAGIC, .version = NODE_VERSION, .type = NODE_GRANT,
        .size = sizeof(node_packet_t), .session = session, .phase = phase,
        .slot = slot, .seq = slot, .phase_ms = c->phase_ms,
        .rate_hz = c->rate_hz, .mode = c->mode, .tx_id = tx,
        .rx_mask = rx_mask,
    };
    return p;
}

static inline bool node_packet_valid(const node_packet_t *p, size_t len,
                                      const node_config_t *c, uint32_t session)
{
    if (!node_config_valid(c) || len != sizeof(*p) || p->magic != NODE_MAGIC ||
        p->version != NODE_VERSION || p->size != sizeof(*p) ||
        (p->type != NODE_GRANT && p->type != NODE_DATA) || !session ||
        p->session != session || p->mode != c->mode ||
        p->rate_hz != c->rate_hz || p->phase_ms != c->phase_ms ||
        p->reserved[0] || p->reserved[1] || p->reserved[2]) return false;
    node_packet_t expected = node_make_grant(c, session, p->slot);
    return p->phase == expected.phase && p->seq == expected.seq &&
           p->tx_id == expected.tx_id && p->rx_mask == expected.rx_mask;
}

static inline bool node_same_slot(const node_packet_t *a, const node_packet_t *b)
{
    return a->session == b->session && a->phase == b->phase &&
           a->slot == b->slot && a->seq == b->seq &&
           a->tx_id == b->tx_id && a->rx_mask == b->rx_mask;
}

static inline bool node_can_respond(const node_packet_t *p, uint8_t id,
                                    int64_t age_us, bool sent, bool tx_pending)
{
    return p->type == NODE_GRANT && p->tx_id == id && !sent && !tx_pending &&
           age_us >= NODE_RESPONSE_DELAY_US && age_us <= NODE_ENQUEUE_DEADLINE_US;
}

/* RX timestamp and the Wi-Fi MAC clock are 32-bit microseconds. Unsigned
 * subtraction preserves short packet ages across their ~71 minute rollover. */
static inline uint32_t node_radio_age_us(uint32_t mac_now, uint32_t received)
{
    return mac_now - received;
}

/* A late control transmission must not shorten the slave's response window.
 * This shifts an upcoming deadline forward only; it never creates catch-up TX. */
static inline int64_t node_next_grant_us(int64_t nominal, int64_t grant_completed)
{
    int64_t earliest = grant_completed + NODE_POST_GRANT_QUIET_US;
    return nominal > earliest ? nominal : earliest;
}

static inline uint32_t node_send_watchdog_us(uint8_t packet_type)
{
    return packet_type == NODE_GRANT ? NODE_GRANT_WATCHDOG_US : NODE_DATA_WATCHDOG_US;
}

/* Parse the documented ESP-NOW action body, never sig_len or unchecked +15.
 * IDF 6 wifi_csi_info_t.payload points to Category Code and supplies payload_len.
 * Accept one unfragmented ESP-NOW v1/v2 vendor IE; all our frames are 36 bytes. */
static inline bool node_parse_action(const uint8_t *b, size_t len, node_packet_t *p)
{
    if (!b || len < 15u + sizeof(*p) || b[0] != 127 ||
        b[1] != 0x18 || b[2] != 0xfe || b[3] != 0x34 ||
        b[8] != 221 || b[9] != 5u + sizeof(*p) ||
        b[10] != 0x18 || b[11] != 0xfe || b[12] != 0x34 || b[13] != 4 ||
        ((b[14] & 0x0f) != 1 && (b[14] & 0x0f) != 2) || (b[14] & 0xf0)) return false;
    if ((size_t)b[9] + 10u > len) return false;
    memcpy(p, b + 15, sizeof(*p));
    return p->magic == NODE_MAGIC && p->version == NODE_VERSION && p->size == sizeof(*p);
}

/* RFC 4648, preserves the original signed int8 CSI byte representation. */
static inline bool node_csi_length_valid(size_t len)
{
    return len > 0 && len <= NODE_MAX_CSI;
}

static inline bool node_csi_base64(const int8_t *data, size_t len, char *out, size_t capacity)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    if (!data || !out || !node_csi_length_valid(len) || capacity < 4 * ((len + 2) / 3) + 1) return false;
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
#endif
