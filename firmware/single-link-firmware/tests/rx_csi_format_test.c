#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../csi_recv/main/rx_csi_format.h"

static size_t action(uint8_t *body, size_t app_len, uint32_t sequence)
{
    memset(body, 0, 64);
    body[0] = 127;
    body[1] = body[10] = 0x18;
    body[2] = body[11] = 0xfe;
    body[3] = body[12] = 0x34;
    body[8] = 221;
    body[9] = (uint8_t)(app_len + 5);
    body[13] = 4;
    body[14] = 2;
    for (unsigned i = 0; i < 4; i++) body[15 + i] = (uint8_t)(sequence >> (8 * i));
    return 15 + app_len;
}

static void test_parser(void)
{
    uint8_t body[64];
    uint32_t seq = 0;
    size_t size = action(body, 4, UINT32_C(0x78563412));
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_DATA);
    assert(seq == UINT32_C(0x78563412));
    for (size_t n = 0; n < size; n++) {
        assert(rx_csi_parse_action(body, n, &seq) == RX_CSI_PAYLOAD_INVALID);
    }
    assert(rx_csi_parse_action(body, size + 4, &seq) == RX_CSI_PAYLOAD_DATA);
    for (size_t n = size + 1; n <= size + 8; n++) {
        if (n != size + 4) assert(rx_csi_parse_action(body, n, &seq) == RX_CSI_PAYLOAD_INVALID);
    }
    assert(rx_csi_parse_action(NULL, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    assert(rx_csi_parse_action(body, size, NULL) == RX_CSI_PAYLOAD_INVALID);
    static const size_t fixed_bytes[] = {0, 1, 2, 3, 8, 10, 11, 12, 13};
    for (size_t i = 0; i < sizeof(fixed_bytes) / sizeof(fixed_bytes[0]); i++) {
        body[fixed_bytes[i]] ^= 1;
        assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
        body[fixed_bytes[i]] ^= 1;
    }
    body[14] = 1;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_DATA);
    body[14] = 0x12; /* Fragment/more-data bit: never interpret as a whole app. */
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    body[14] = 3;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    body[14] = 2;
    body[9] = 255;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    body[9] = 4;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);

    size = action(body, 4, UINT32_MAX);
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_DATA && seq == UINT32_MAX);
    size = action(body, 4, RX_CSI_CONTROL_MAGIC);
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_DATA && seq == RX_CSI_CONTROL_MAGIC);
    for (size_t n = 0; n <= 24; n++) {
        if (n == 4 || n == RX_CSI_CONTROL_BYTES) continue;
        size = action(body, n, 0);
        assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    }
    size = action(body, RX_CSI_CONTROL_BYTES, RX_CSI_CONTROL_MAGIC);
    body[19] = RX_CSI_CONTROL_VERSION;
    body[21] = RX_CSI_CONTROL_BYTES;
    for (uint8_t type = 1; type <= 4; type++) {
        body[20] = type;
        assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_CONTROL);
        assert(rx_csi_parse_action(body, size + 4, &seq) == RX_CSI_PAYLOAD_CONTROL);
    }
    for (size_t n = 0; n < size; n++) assert(rx_csi_parse_action(body, n, &seq) == RX_CSI_PAYLOAD_INVALID);
    static const size_t control_bytes[] = {15, 19, 21, 22};
    for (size_t i = 0; i < sizeof(control_bytes) / sizeof(control_bytes[0]); i++) {
        body[control_bytes[i]] ^= 1;
        assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
        body[control_bytes[i]] ^= 1;
    }
    body[20] = 0;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
    body[20] = 5;
    assert(rx_csi_parse_action(body, size, &seq) == RX_CSI_PAYLOAD_INVALID);
}

static void test_base64_and_lengths(void)
{
    int8_t data[RX_CSI_MAX_BYTES + 1];
    char encoded[RX_CSI_BASE64_CAPACITY + 1];
    for (size_t len = 1; len <= RX_CSI_MAX_BYTES; len++) {
        for (size_t i = 0; i < len; i++) data[i] = (int8_t)((i * 73 + len) & 255);
        size_t needed = 4 * ((len + 2) / 3) + 1;
        memset(encoded, '#', sizeof(encoded));
        assert(rx_csi_base64(data, len, encoded, needed));
        assert(strlen(encoded) == needed - 1 && encoded[needed] == '#');
        printf("B64,%zu,%s\n", len, encoded);
        assert(!rx_csi_base64(data, len, encoded, needed - 1));
        assert(rx_csi_length_valid(len));
    }
    assert(!rx_csi_length_valid(0));
    assert(!rx_csi_length_valid(RX_CSI_MAX_BYTES + 1));
    assert(!rx_csi_length_valid(SIZE_MAX));
    assert(!rx_csi_base64(NULL, 1, encoded, sizeof(encoded)));
    assert(!rx_csi_base64(data, 1, NULL, sizeof(encoded)));
    assert(!rx_csi_base64(data, 1, encoded, 0));
    assert(!rx_csi_base64(data, 0, encoded, sizeof(encoded)));
    assert(!rx_csi_base64(data, sizeof(data), encoded, sizeof(encoded)));
    assert(!rx_csi_base64(data, SIZE_MAX, encoded, sizeof(encoded)));
}

static void test_serializer(void)
{
    const int8_t raw[] = {0, 1, 127, -128, -1};
    rx_csi_fields_t m = {
        .id = 42, .mac = {0x1a, 0, 0, 0, 0, 0}, .rssi = -47, .rate = 11,
        .sig_mode = 1, .bandwidth = 1, .not_sounding = 1, .noise_floor = -96,
        .fft_gain = -2, .agc_gain = 19, .compensate_gain_x1000000 = 1000000,
        .channel = 11, .secondary_channel = 2, .local_timestamp = 123456789,
        .sig_len = 47, .rx_format = 1, .first_word = 1,
    };
    char line[RX_CSI_LINE_CAPACITY];
    assert(rx_csi_format_line(&m, raw, sizeof(raw), line, sizeof(line)));
    assert(!strcmp(line, "CSI_DATA_B64,42,1a:00:00:00:00:00,-47,11,1,0,1,0,1,0,0,0,0,-96,-2,19,1000000,0,11,2,123456789,0,47,1,5,1,AAF/gP8=\n"));
    printf("HEADER,%s\n", RX_CSI_CSV_HEADER);
    printf("FIXTURE,%s", line);
    size_t required = strlen(line) + 1;
    assert(rx_csi_format_line(&m, raw, sizeof(raw), line, required));
    assert(!rx_csi_format_line(&m, raw, sizeof(raw), line, required - 1));
    assert(!rx_csi_format_line(NULL, raw, sizeof(raw), line, sizeof(line)));
    assert(!rx_csi_format_line(&m, NULL, sizeof(raw), line, sizeof(line)));
    assert(!rx_csi_format_line(&m, raw, 0, line, sizeof(line)));
    assert(!rx_csi_format_line(&m, raw, RX_CSI_MAX_BYTES + 1, line, sizeof(line)));
    assert(!rx_csi_format_line(&m, raw, sizeof(raw), NULL, sizeof(line)));
    assert(!rx_csi_format_line(&m, raw, sizeof(raw), line, 0));

    /* All metadata type limits must survive formatting as their true values. */
    memset(&m, 0xff, sizeof(m));
    m.rssi = m.noise_floor = m.fft_gain = INT8_MIN;
    m.compensate_gain_x1000000 = INT32_MIN;
    m.local_timestamp = UINT32_C(2147483648);
    assert(rx_csi_format_line(&m, raw, sizeof(raw), line, sizeof(line)));
    printf("EXTREMES,%s", line);
    int8_t maximum[RX_CSI_MAX_BYTES] = {0};
    assert(rx_csi_format_line(&m, maximum, sizeof(maximum), line, sizeof(line)));
    printf("MAXIMUM,%s", line);
}

static void test_command(void)
{
    uint32_t id = 0;
    assert(rx_csi_parse_stats_command("CSI_RX,STATS,0", &id) && id == 0);
    assert(rx_csi_parse_stats_command("CSI_RX,STATS,4294967295", &id) && id == UINT32_MAX);
    const char *invalid[] = {"CSI_RX,STATS,4294967296", "CSI_RX,STATS,", "CSI_RX,STATS,-1",
        "CSI_RX,STATS,+1", "CSI_RX,STATS,1x", "CSI_RX,STATS,1,2", "CSI_CTRL,GET,1",
        "CSI_RX,RESET,1", "", "CSI_RX,STATS, 1", "CSI_RX,STATS,1 "};
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++)
        assert(!rx_csi_parse_stats_command(invalid[i], &id));
    assert(!rx_csi_parse_stats_command(NULL, &id));
    assert(!rx_csi_parse_stats_command("CSI_RX,STATS,1", NULL));
}

static void test_gain_prefix_with_queue_drops(void)
{
    rx_csi_gain_progress_t progress = {0};
    rx_csi_gain_step_t step;
    /* Without drops every initial sample gets one record+compensation call.
     * The library's automatic 50th-sample baseline stays at the same point. */
    for (uint8_t ordinal = 1; ordinal <= 100; ordinal++) {
        assert(rx_csi_gain_advance(&progress, ordinal, false, &step));
        assert(step.begin == ordinal - 1 && step.end == ordinal);
        assert(!step.request_baseline);
    }
    assert(rx_csi_gain_advance(&progress, 100, true, &step));
    assert(step.begin == 100 && step.end == 100 && step.request_baseline);
    assert(rx_csi_gain_advance(&progress, 100, true, &step));
    assert(step.begin == step.end && !step.request_baseline);

    /* Delivered records are 1,49,50,52,99,101. A full snapshot array may
     * already exist, but each output record can only replay its own prefix. */
    progress = (rx_csi_gain_progress_t){0};
    const uint8_t delivered[] = {1, 49, 50, 52, 99, 100};
    unsigned replay_count[100] = {0};
    for (unsigned n = 0; n < sizeof(delivered) / sizeof(delivered[0]); n++) {
        bool after_100 = n == 5;
        assert(rx_csi_gain_advance(&progress, delivered[n], after_100, &step));
        assert(step.end == delivered[n]);
        assert(step.request_baseline == after_100);
        for (unsigned i = step.begin; i < step.end; i++) {
            assert(i < delivered[n]);
            replay_count[i]++;
        }
    }
    for (unsigned i = 0; i < 100; i++) assert(replay_count[i] == 1);

    /* Even if all first100 CSI rows are dropped, their gain calls still
     * precede explicit baseline lookup and compensation of packet101. */
    progress = (rx_csi_gain_progress_t){0};
    assert(rx_csi_gain_advance(&progress, 100, true, &step));
    assert(step.begin == 0 && step.end == 100 && step.request_baseline);
    assert(!rx_csi_gain_advance(&progress, 100, false, &step));
    assert(!rx_csi_gain_advance(&progress, 99, false, &step));
    assert(!rx_csi_gain_advance(&progress, 99, true, &step));
    assert(!rx_csi_gain_advance(&progress, 101, true, &step));
    assert(!rx_csi_gain_advance(&progress, 0, false, &step));
    assert(!rx_csi_gain_advance(NULL, 1, false, &step));
    assert(!rx_csi_gain_advance(&progress, 100, true, NULL));
    puts("GAIN_PROGRESS,first100,50-boundary,101-boundary,queue-gaps,passed");
}

int main(void)
{
    test_parser();
    test_base64_and_lengths();
    test_serializer();
    test_command();
    test_gain_prefix_with_queue_drops();
    puts("RX CSI format tests passed");
    return 0;
}
