/* Host executable exercises the exact header used by the firmware. */
#include <assert.h>
#include <stdio.h>
#include "../csi_node/main/node_protocol.h"

static void schedules(void)
{
    node_config_t c = {.id = 1, .mode = 2, .rate_hz = 40, .phase_ms = 5000};
    assert(node_config_valid(&c));
    assert(node_slot_us(&c) == 12500);
    unsigned counts[4] = {0};
    for (uint32_t slot = 0; slot < 1600; slot++) {
        node_packet_t p = node_make_grant(&c, 123, slot);
        assert(node_packet_valid(&p, sizeof(p), &c, 123));
        assert(p.phase == slot / 400);
        assert(p.tx_id == (slot / 400 % 2 ? 3 : 1) + slot % 2);
        assert(p.rx_mask == (slot / 400 % 2 ? 3 : 12));
        assert(!(p.rx_mask & (1u << (p.tx_id - 1))));
        counts[p.tx_id - 1]++;
    }
    for (unsigned id = 0; id < 4; id++) assert(counts[id] == 400);
    c.mode = 1;
    assert(node_slot_us(&c) == 25000);
    for (uint32_t slot = 0; slot < 500; slot++) {
        node_packet_t p = node_make_grant(&c, 123, slot);
        assert(p.tx_id == 1 && p.phase == 0 && p.rx_mask == 14);
    }
    c.mode = 2; c.rate_hz = 60; assert(!node_config_valid(&c));
    c.rate_hz = 39; assert(!node_config_valid(&c));
    c.rate_hz = 40; c.phase_ms = 1001; assert(!node_config_valid(&c));
    c.phase_ms = 1025; assert(node_config_valid(&c));
    c.id = 0; assert(!node_config_valid(&c));
}

static void rr4_configuration(void)
{
    assert(NODE_MODE_RR4 == 3 && NODE_VERSION == 2);
    assert(sizeof(node_packet_t) == 36);
    assert(node_mode_from_name("RR4") == NODE_MODE_RR4);
    assert(node_mode_from_name("1X3") == NODE_MODE_1X3);
    assert(node_mode_from_name("2X2") == NODE_MODE_2X2);
    assert(node_mode_from_name("rr4") == 0);
    assert(node_mode_from_name("1X4") == 0);
    assert(node_mode_from_name(NULL) == 0);
    assert(!strcmp(node_mode_name(NODE_MODE_RR4), "RR4"));
    assert(!strcmp(node_mode_name(NODE_MODE_1X3), "1X3"));
    assert(!strcmp(node_mode_name(NODE_MODE_2X2), "2X2"));
    assert(!strcmp(node_mode_name(255), "UNKNOWN"));
    node_config_t c = {.id = 1, .mode = NODE_MODE_RR4, .rate_hz = 20, .phase_ms = 50};
    assert(node_config_valid(&c) && node_slot_us(&c) == 12500);
    const uint16_t rates[] = {5, 8, 10, 20};
    const uint32_t cycles[] = {200, 125, 100, 50};
    for (unsigned i = 0; i < 4; i++) {
        c.rate_hz = rates[i]; c.phase_ms = cycles[i];
        assert(node_config_valid(&c));
        assert(node_slot_us(&c) * 4 == c.phase_ms * 1000);
    }
    c.rate_hz = 20; c.phase_ms = 5000; assert(!node_config_valid(&c));
    c.rate_hz = 40; c.phase_ms = 50; assert(!node_config_valid(&c));
    c.rate_hz = 40; c.phase_ms = 5000; assert(!node_config_valid(&c));
    c.rate_hz = 20; c.phase_ms = 49; assert(!node_config_valid(&c));
    c.phase_ms = 51; assert(!node_config_valid(&c));
    c.phase_ms = UINT32_MAX; assert(!node_config_valid(&c));
    c.phase_ms = 50; c.rate_hz = 21; assert(!node_config_valid(&c));
    c.rate_hz = 16; c.phase_ms = 62; assert(!node_config_valid(&c));
    c.rate_hz = 4; c.phase_ms = 250; assert(!node_config_valid(&c));
    c.rate_hz = 20; c.phase_ms = 50;
    for (uint8_t id = 1; id <= 4; id++) { c.id = id; assert(node_config_valid(&c)); }
    c.id = 0; assert(!node_config_valid(&c));
    c.id = 5; assert(!node_config_valid(&c));
    c.id = 1; c.mode = 255; assert(!node_config_valid(&c));
    assert(node_slot_us(&c) == 0);
    /* Stored valid legacy configuration remains accepted after the upgrade. */
    c.mode = NODE_MODE_1X3; c.rate_hz = 40; c.phase_ms = 5000;
    assert(node_config_valid(&c) && node_slot_us(&c) == 25000);
    c.mode = NODE_MODE_2X2;
    assert(node_config_valid(&c) && node_slot_us(&c) == 12500);
}

static void rr4_schedule_and_wire(void)
{
    node_config_t c = {.id = 1, .mode = NODE_MODE_RR4, .rate_hz = 20, .phase_ms = 50};
    unsigned links[4][4] = {{0}};
    unsigned sent[4] = {0};
    uint32_t last_slot[4] = {0};
    const uint8_t masks[4] = {0x0e, 0x0d, 0x0b, 0x07};
    for (uint32_t slot = 0; slot < 400; slot++) {
        node_packet_t p = node_make_grant(&c, 321, slot);
        assert(node_packet_valid(&p, sizeof(p), &c, 321));
        assert(p.phase == slot / 4 && p.tx_id == 1 + slot % 4);
        assert(p.slot == slot && p.seq == slot && p.rx_mask == masks[slot % 4]);
        unsigned sender = p.tx_id - 1;
        if (sent[sender]) assert(slot - last_slot[sender] == 4);
        last_slot[sender] = slot; sent[sender]++;
        for (unsigned receiver = 0; receiver < 4; receiver++) {
            bool receives = (p.rx_mask & (1u << receiver)) != 0;
            assert(receives == (receiver != sender));
            if (receives) links[sender][receiver]++;
            assert(node_can_respond(&p, (uint8_t)(receiver + 1), 1000, false, false) ==
                   (receiver == sender));
        }
    }
    /* Every 50 ms cycle samples all 12 directed links exactly once. */
    for (unsigned tx = 0; tx < 4; tx++) {
        assert(sent[tx] == 100);
        for (unsigned rx = 0; rx < 4; rx++) assert(links[tx][rx] == (tx == rx ? 0u : 100u));
    }
    const uint32_t boundaries[] = {0, 3, 4, 7, 8, 255, 256, UINT32_MAX - 4,
                                  UINT32_MAX - 3, UINT32_MAX - 2, UINT32_MAX - 1, UINT32_MAX};
    for (unsigned i = 0; i < sizeof(boundaries) / sizeof(boundaries[0]); i++) {
        uint32_t slot = boundaries[i];
        node_packet_t p = node_make_grant(&c, 321, slot);
        assert(node_packet_valid(&p, sizeof(p), &c, 321));
        assert(p.phase == slot / 4 && p.tx_id == 1 + slot % 4);
        assert(p.rx_mask == masks[slot % 4] && p.seq == slot);
    }
    node_packet_t p = node_make_grant(&c, 321, 5), bad = p;
    assert(p.phase == 1 && p.tx_id == 2 && p.rx_mask == 0x0d);
    bad.rx_mask = 0x0f; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.tx_id = 1; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.phase = 0; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.seq++; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.phase_ms = 5000; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.mode = NODE_MODE_2X2; assert(!node_packet_valid(&bad, sizeof(bad), &c, 321));
    bad = p; bad.type = NODE_DATA; assert(node_packet_valid(&bad, sizeof(bad), &c, 321));
    /* A receiver configured in a legacy mode rejects a new-mode packet. */
    c.mode = NODE_MODE_2X2; c.rate_hz = 40; c.phase_ms = 5000;
    assert(!node_packet_valid(&p, sizeof(p), &c, 321));
    c.mode = NODE_MODE_1X3;
    assert(!node_packet_valid(&p, sizeof(p), &c, 321));
}

static void grants_and_sessions(void)
{
    node_config_t c = {.id = 3, .mode = 2, .rate_hz = 40, .phase_ms = 5000};
    node_packet_t p = node_make_grant(&c, 123, 400);
    assert(p.tx_id == 3 && p.phase == 1);
    assert(!node_can_respond(&p, 3, -1, false, false));
    assert(!node_can_respond(&p, 3, 999, false, false));
    assert(node_can_respond(&p, 3, 1000, false, false));
    assert(node_can_respond(&p, 3, 2500, false, false));
    assert(!node_can_respond(&p, 3, 2501, false, false));
    assert(!node_can_respond(&p, 3, 1000000, false, false));
    assert(!node_can_respond(&p, 1, 1000, false, false));
    assert(!node_can_respond(&p, 3, 1000, true, false));
    assert(!node_can_respond(&p, 3, 1000, false, true));
    assert(node_radio_age_us(1000, 900) == 100);
    assert(node_radio_age_us(200, UINT32_MAX - 99) == 300);
    assert(node_radio_age_us(900, 1000) > NODE_ENQUEUE_DEADLINE_US);
    assert(!node_packet_valid(&p, sizeof(p), &c, 0));
    assert(!node_packet_valid(&p, sizeof(p), &c, 124));
    assert(!node_packet_valid(&p, sizeof(p) - 1, &c, 123));
    node_packet_t stale = node_make_grant(&c, 123, 399);
    assert(!node_same_slot(&stale, &p));
    node_packet_t data = p; data.type = NODE_DATA;
    assert(node_same_slot(&data, &p));
    assert(!node_can_respond(&data, 3, 1000, false, false));
    data.rx_mask = 15; assert(!node_packet_valid(&data, sizeof(data), &c, 123));
    data = p; data.tx_id = 4; assert(!node_packet_valid(&data, sizeof(data), &c, 123));
    data = p; data.phase = 0; assert(!node_packet_valid(&data, sizeof(data), &c, 123));
    data = p; data.seq++; assert(!node_packet_valid(&data, sizeof(data), &c, 123));
    data = p; data.phase_ms = 10000; assert(!node_packet_valid(&data, sizeof(data), &c, 123));
    for (uint8_t id = 1; id <= 4; id++) {
        uint8_t mac[6]; node_mac(id, mac);
        assert(node_source_valid(id, mac));
        assert(!node_source_valid(id % 4 + 1, mac));
        assert((mac[0] & 3) == 2);
    }
}

static void bounded_parser(void)
{
    node_config_t c = {.id = 2, .mode = 1, .rate_hz = 40, .phase_ms = 5000};
    node_packet_t p = node_make_grant(&c, 987, 123), decoded;
    uint8_t body[15 + sizeof(p)] = {127,0x18,0xfe,0x34,0,0,0,0,221,
                                   5 + sizeof(p),0x18,0xfe,0x34,4,2};
    memcpy(body + 15, &p, sizeof(p));
    assert(!node_parse_action(NULL, sizeof(body), &decoded));
    for (size_t len = 0; len < sizeof(body); len++) assert(!node_parse_action(body, len, &decoded));
    assert(node_parse_action(body, sizeof(body), &decoded));
    assert(node_same_slot(&p, &decoded));
    body[14] = 1; assert(node_parse_action(body, sizeof(body), &decoded));
    body[14] = 0x12; assert(!node_parse_action(body, sizeof(body), &decoded));
    body[14] = 2;
    const unsigned checked[] = {0,1,2,3,8,9,10,11,12,13,14,15,19,21};
    for (unsigned i = 0; i < sizeof(checked)/sizeof(checked[0]); i++) {
        unsigned offset = checked[i]; uint8_t old = body[offset];
        body[offset] = 255;
        assert(!node_parse_action(body, sizeof(body), &decoded));
        body[offset] = old;
    }
    p.type = NODE_DATA; memcpy(body + 15, &p, sizeof(p));
    assert(node_parse_action(body, sizeof(body), &decoded));
    assert(decoded.type == NODE_DATA);
}

static size_t decode_base64_for_test(const char *text, uint8_t *out, size_t capacity)
{
    const char *alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    uint32_t accumulator = 0;
    unsigned bits = 0;
    size_t count = 0;
    for (const char *p = text; *p && *p != '='; p++) {
        const char *digit = strchr(alphabet, *p);
        assert(digit != NULL);
        accumulator = (accumulator << 6) | (uint32_t)(digit - alphabet);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            assert(count < capacity);
            out[count++] = (uint8_t)(accumulator >> bits);
        }
    }
    return count;
}

static void base64(void)
{
    int8_t values[] = {0, 1, 127, -128, -1};
    char out[NODE_BASE64_CAPACITY];
    assert(node_csi_base64(values, 5, out, sizeof(out)) && !strcmp(out, "AAF/gP8="));
    assert(node_csi_base64(values, 3, out, sizeof(out)) && !strcmp(out, "AAF/"));
    assert(node_csi_base64(values, 1, out, sizeof(out)) && !strcmp(out, "AA=="));
    assert(!node_csi_base64(values, 5, out, 8));
    int8_t maximum[NODE_MAX_CSI] = {0};
    assert(node_csi_base64(maximum, sizeof(maximum), out, sizeof(out)));
    assert(strlen(out) == 1024);
    assert(!node_csi_base64(maximum, sizeof(maximum) + 1, out, sizeof(out)));
    assert(!node_csi_length_valid(0));
    assert(!node_csi_length_valid(NODE_MAX_CSI + 1));
    assert(!node_csi_length_valid(SIZE_MAX));
    assert(!node_csi_base64(values, 0, out, sizeof(out)));
    assert(!node_csi_base64(NULL, 1, out, sizeof(out)));
    assert(!node_csi_base64(values, 1, NULL, sizeof(out)));
    assert(!node_csi_base64(values, SIZE_MAX, out, sizeof(out)));
    uint8_t decoded[NODE_MAX_CSI];
    for (size_t i = 0; i < sizeof(maximum); i++) maximum[i] = (int8_t)(uint8_t)(i * 37u);
    for (size_t len = 1; len <= NODE_MAX_CSI; len++) {
        assert(node_csi_length_valid(len));
        size_t encoded_len = 4 * ((len + 2) / 3);
        assert(node_csi_base64(maximum, len, out, encoded_len + 1));
        assert(strlen(out) == encoded_len);
        assert(decode_base64_for_test(out, decoded, sizeof(decoded)) == len);
        for (size_t i = 0; i < len; i++) assert(decoded[i] == (uint8_t)maximum[i]);
        if (len % 3 == 1) assert(out[encoded_len - 1] == '=' && out[encoded_len - 2] == '=');
        if (len % 3 == 2) assert(out[encoded_len - 1] == '=' && out[encoded_len - 2] != '=');
        if (len % 3 == 0) assert(out[encoded_len - 1] != '=');
        assert(!node_csi_base64(maximum, len, out, encoded_len));
    }
}

static void late_radio_guard(void)
{
    assert(NODE_POST_GRANT_QUIET_US == 11000);
    assert(NODE_RX_LEASE_US >= NODE_ENQUEUE_DEADLINE_US + NODE_DATA_WATCHDOG_US);
    assert(node_send_watchdog_us(NODE_GRANT) == 100000);
    assert(node_send_watchdog_us(NODE_DATA) == 8000);
    /* 12.5 ms nominal slot: ordinary grant callback leaves cadence unchanged. */
    assert(node_next_grant_us(12500, 400) == 12500);
    assert(node_next_grant_us(12500, 1500) == 12500);
    /* Radio contention extends the slot instead of truncating slave airtime. */
    assert(node_next_grant_us(12500, 1501) == 12501);
    assert(node_next_grant_us(12500, 5000) == 16000);
    assert(node_next_grant_us(12500, 8000) == 19000);
    assert(node_next_grant_us(12500, 9000) == 20000);
    assert(node_next_grant_us(12500, 100000) == 111000);
    assert(node_next_grant_us(25000, 8000) == 25000);
    /* Later nominal deadlines are never pulled backward or burst to catch up. */
    assert(node_next_grant_us(16000 + 12500, 16400) == 28500);
    for (int64_t completion = 0; completion <= 8000; completion++) {
        int64_t next = node_next_grant_us(12500, completion);
        assert(next >= 12500);
        assert(next - completion >= NODE_ENQUEUE_DEADLINE_US + NODE_DATA_WATCHDOG_US + NODE_SLOT_GUARD_US);
    }
}

int main(void)
{
    schedules(); rr4_configuration(); rr4_schedule_and_wire();
    grants_and_sessions(); bounded_parser(); base64(); late_radio_guard();
    puts("node_protocol: schedule/phase, grant expiry, wrong-session/source, parser bounds passed");
    return 0;
}
