/* SPDX-License-Identifier: Apache-2.0 */
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_private/wifi.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "node_protocol.h"

#if !CONFIG_IDF_TARGET_ESP32S3
#error "csi_node currently targets ESP32-S3 only"
#endif

#define SETTINGS_NS "csi_node_v2"
#define CSI_QUEUE_DEPTH 32
#define EVENT_QUEUE_DEPTH 24
#define COMMAND_QUEUE_DEPTH 8
#define NODE_UART_LINE_MAX 160
#define CHANNEL 11

typedef struct {
    node_packet_t packet;
    uint32_t timestamp;
    uint32_t generation;
    uint16_t len;
    int8_t rssi, noise;
    uint8_t receiver, channel, secondary, sig_mode, mcs, bandwidth, first_invalid;
    int8_t csi[NODE_MAX_CSI];
} csi_record_t;

typedef struct { char text[512]; } output_event_t;
typedef struct { char text[NODE_UART_LINE_MAX]; } command_t;

static const uint8_t broadcast[6] = {255,255,255,255,255,255};
static const char *TAG = "csi_node";
static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
static QueueHandle_t csi_queue, event_queue, command_queue;
static node_config_t config = {.id = 0, .mode = NODE_MODE_RR4, .rate_hz = 20, .phase_ms = 50};
static node_packet_t grant;
static uint8_t factory_mac[6], active_mac[6];
static bool running, fault, radio_up, have_grant, response_sent, tx_pending;
static bool sent_slot_valid, logged_slot_valid;
static uint32_t session, generation, next_slot, sent_slot, logged_slot;
static uint8_t pending_type;
static int64_t grant_received_us, send_started_us, next_slot_us;
static uint32_t tx_count, csi_count, csi_dropped, late_dropped, send_errors;
static uint32_t grant_rx_count, grant_age_min = UINT32_MAX, grant_age_max, grant_expired;
static uint32_t csi_len_min = UINT32_MAX, csi_len_max, csi_invalid_length_dropped;
static uint32_t tx_completions[2], tx_age_min[2] = {UINT32_MAX, UINT32_MAX}, tx_age_max[2];
static uint32_t guard_extensions, fault_type, fault_age_us;
static uint64_t guard_extension_us;

/* UART output belongs exclusively to output_task; Wi-Fi callbacks never print. */
static void emit(const char *fmt, ...)
{
    output_event_t event;
    va_list args;
    va_start(args, fmt);
    vsnprintf(event.text, sizeof(event.text), fmt, args);
    va_end(args);
    xQueueSend(event_queue, &event, 0);
}

static void status(void)
{
    node_config_t c;
    uint32_t sid, phase, slot, tx, rx, drops, late, errors;
    uint32_t grants, youngest, oldest, expired;
    uint32_t min_csi_len, max_csi_len, invalid_csi_lengths;
    uint32_t completed[2], tx_min[2], tx_max[2], extensions, fault_packet, fault_age;
    uint64_t extension_us;
    bool run, bad;
    portENTER_CRITICAL(&lock);
    c = config; sid = session; phase = have_grant ? grant.phase : 0;
    slot = have_grant ? grant.slot : 0; run = running; bad = fault;
    tx = tx_count; rx = csi_count; drops = csi_dropped;
    late = late_dropped; errors = send_errors;
    grants = grant_rx_count; youngest = grant_age_min; oldest = grant_age_max; expired = grant_expired;
    min_csi_len = csi_len_min; max_csi_len = csi_len_max;
    invalid_csi_lengths = csi_invalid_length_dropped;
    memcpy(completed, tx_completions, sizeof(completed));
    memcpy(tx_min, tx_age_min, sizeof(tx_min));
    memcpy(tx_max, tx_age_max, sizeof(tx_max));
    extensions = guard_extensions; extension_us = guard_extension_us;
    fault_packet = fault_type; fault_age = fault_age_us;
    portEXIT_CRITICAL(&lock);
    char id[6] = "UNSET";
    if (c.id) { id[0] = 'A' + c.id - 1; id[1] = 0; }
    emit("NODE_STATUS,%s,%s,%u,%" PRIu32 ",%s,%" PRIu32 ",%" PRIu32 ",%" PRIu32
         ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
         "," MACSTR "," MACSTR,
         id, node_mode_name(c.mode), c.rate_hz, c.phase_ms,
         bad ? "FAULT" : run ? (c.id == 1 ? "RUNNING" : "ARMED") : "STOPPED",
         sid, phase, slot, tx, rx, drops, late, errors,
         MAC2STR(active_mac), MAC2STR(factory_mac));
    emit("NODE_TIMING,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
         grants, youngest == UINT32_MAX ? 0 : youngest, oldest, expired);
    emit("NODE_TX_TIMING,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
         ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 ",%s,%" PRIu32,
         completed[0], completed[1], tx_min[0] == UINT32_MAX ? 0 : tx_min[0], tx_max[0],
         tx_min[1] == UINT32_MAX ? 0 : tx_min[1], tx_max[1], extensions, extension_us,
         fault_packet == NODE_GRANT ? "GRANT" : fault_packet == NODE_DATA ? "DATA" :
         fault_packet == 3 ? "SLOT" : "NONE", fault_age);
    emit("NODE_CSI_STATS,%" PRIu32 ",%" PRIu32 ",%" PRIu32,
         min_csi_len == UINT32_MAX ? 0 : min_csi_len, max_csi_len, invalid_csi_lengths);
}

static void mark_fault_locked(uint32_t type, uint32_t age)
{
    if (!fault) {
        late_dropped++;
        fault_type = type;
        fault_age_us = age;
    }
    fault = true;
    running = false;
}

static bool current_data_locked(const node_packet_t *p, int64_t now)
{
    return running && have_grant && p->type == NODE_DATA &&
           node_packet_valid(p, sizeof(*p), &config, session) &&
           node_same_slot(p, &grant) && now >= grant_received_us &&
           now - grant_received_us <= NODE_RX_LEASE_US;
}

static void receive_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    if (!info || !info->src_addr || !info->rx_ctrl || !data || len != sizeof(node_packet_t)) return;
    node_packet_t p;
    memcpy(&p, data, sizeof(p));
    int64_t now = esp_timer_get_time();
    uint32_t age = node_radio_age_us(esp_wifi_internal_get_mac_clock_time(), info->rx_ctrl->timestamp);
    portENTER_CRITICAL(&lock);
    if (running && config.id != 1 && p.type == NODE_GRANT &&
        node_source_valid(1, info->src_addr) &&
        node_packet_valid(&p, sizeof(p), &config, session) &&
        (!have_grant || p.slot > grant.slot)) {
        grant_rx_count++;
        if (age < grant_age_min) grant_age_min = age;
        if (age > grant_age_max) grant_age_max = age;
        if (age <= NODE_ENQUEUE_DEADLINE_US) {
            grant = p;
            grant_received_us = now - age;
            have_grant = true;
            response_sent = false;
        } else {
            grant_expired++; late_dropped++;
        }
    }
    portEXIT_CRITICAL(&lock);
}

static void send_cb(const esp_now_send_info_t *info, esp_now_send_status_t result)
{
    (void)info;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    if (tx_pending) {
        uint32_t age = (uint32_t)(now - send_started_us);
        unsigned index = pending_type == NODE_GRANT ? 0 : 1;
        tx_completions[index]++;
        if (age < tx_age_min[index]) tx_age_min[index] = age;
        if (age > tx_age_max[index]) tx_age_max[index] = age;
        if (age > node_send_watchdog_us(pending_type)) mark_fault_locked(pending_type, age);
        if (result != ESP_NOW_SEND_SUCCESS) send_errors++;
        if (pending_type == NODE_DATA && result == ESP_NOW_SEND_SUCCESS) tx_count++;
        /* A's local grant epoch follows actual grant TX completion, so its data
         * cannot overtake the grant in the Wi-Fi driver queue. */
        if (pending_type == NODE_GRANT) {
            grant_received_us = now;
            if (result != ESP_NOW_SEND_SUCCESS) response_sent = true;
            /* Reserve the full response + driver-completion + guard budget,
             * including when GRANT itself was delayed by channel contention. */
            int64_t safe_deadline = node_next_grant_us(next_slot_us, now);
            if (safe_deadline > next_slot_us) {
                guard_extensions++;
                guard_extension_us += (uint64_t)(safe_deadline - next_slot_us);
                next_slot_us = safe_deadline;
            }
        }
        tx_pending = false;
    }
    portEXIT_CRITICAL(&lock);
}

static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    (void)ctx;
    if (!info) return;
    csi_record_t r;
    if (!node_parse_action(info->payload, info->payload_len, &r.packet) ||
        r.packet.type != NODE_DATA || !node_source_valid(r.packet.tx_id, info->mac)) return;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    bool accept = current_data_locked(&r.packet, now) &&
                  (r.packet.rx_mask & (1u << (config.id - 1u))) &&
                  (!logged_slot_valid || logged_slot != r.packet.slot);
    if (accept) {
        /* Observe only the expected DATA link, including rejected CSI sizes.
         * Controls, unrelated stations, and old grants never affect stats. */
        if (info->len < csi_len_min) csi_len_min = info->len;
        if (info->len > csi_len_max) csi_len_max = info->len;
        if (!info->buf || !node_csi_length_valid(info->len)) {
            csi_invalid_length_dropped++;
            accept = false;
        } else {
            r.receiver = config.id;
            r.generation = generation;
            logged_slot = r.packet.slot;
            logged_slot_valid = true;
        }
    }
    portEXIT_CRITICAL(&lock);
    if (!accept) return;
    r.timestamp = info->rx_ctrl.timestamp;
    r.len = info->len;
    r.rssi = info->rx_ctrl.rssi;
    r.noise = info->rx_ctrl.noise_floor;
    r.channel = info->rx_ctrl.channel;
    r.secondary = info->rx_ctrl.secondary_channel;
    r.sig_mode = info->rx_ctrl.sig_mode;
    r.mcs = info->rx_ctrl.mcs;
    r.bandwidth = info->rx_ctrl.cwb;
    r.first_invalid = info->first_word_invalid;
    memcpy(r.csi, info->buf, r.len);
    bool queued = xQueueSend(csi_queue, &r, 0) == pdTRUE;
    portENTER_CRITICAL(&lock);
    if (queued) csi_count++; else csi_dropped++;
    portEXIT_CRITICAL(&lock);
}

static void radio_start(void)
{
    if (radio_up) return;
    if (config.id) node_mac(config.id, active_mac); else memcpy(active_mac, factory_mac, 6);
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, active_mac));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(80));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_BELOW));
    ESP_ERROR_CHECK(esp_now_init());
    esp_now_peer_info_t peer = {.channel = CHANNEL, .ifidx = WIFI_IF_STA};
    memcpy(peer.peer_addr, broadcast, 6);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    esp_now_rate_config_t rate = {
        .phymode = WIFI_PHY_MODE_HT40, .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false, .dcm = false
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(broadcast, &rate));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(receive_cb));
    ESP_ERROR_CHECK(esp_now_register_send_cb(send_cb));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    wifi_csi_config_t csi = {
        .lltf_en = true, .htltf_en = true, .stbc_htltf2_en = true,
        .ltf_merge_en = true, .channel_filter_en = true, .manu_scale = false,
        .shift = 0
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    radio_up = true;
}

static void radio_stop(void)
{
    if (!radio_up) return;
    ESP_ERROR_CHECK(esp_wifi_set_csi(false));
    ESP_ERROR_CHECK(esp_now_deinit());
    ESP_ERROR_CHECK(esp_wifi_stop());
    portENTER_CRITICAL(&lock);
    tx_pending = false;
    portEXIT_CRITICAL(&lock);
    radio_up = false;
}

static void stop_session(void)
{
    portENTER_CRITICAL(&lock);
    running = false; generation++;
    portEXIT_CRITICAL(&lock);
    radio_stop();
    xQueueReset(csi_queue);
}

static esp_err_t save_config(const node_config_t *c)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(SETTINGS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(h, "config", c, sizeof(*c));
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

static void load_config(void)
{
    nvs_handle_t h;
    if (nvs_open(SETTINGS_NS, NVS_READONLY, &h) != ESP_OK) return;
    node_config_t candidate;
    size_t size = sizeof(candidate);
    if (nvs_get_blob(h, "config", &candidate, &size) == ESP_OK &&
        size == sizeof(candidate) && node_config_valid(&candidate)) config = candidate;
    nvs_close(h);
}

static void handle_command(char *line)
{
    char *tokens[8];
    size_t n = 0;
    char *state;
    for (char *t = strtok_r(line, " \t\r\n", &state); t && n < 8;
         t = strtok_r(NULL, " \t\r\n", &state)) tokens[n++] = t;
    if (n < 2 || strcmp(tokens[0], "NODE")) {
        emit("NODE_ACK,UNKNOWN,ERROR,syntax"); return;
    }
    const char *cmd = tokens[1];
    if (!strcmp(cmd, "CONFIG") && n == 6) {
        char *end_rate, *end_phase;
        unsigned long rate = strtoul(tokens[4], &end_rate, 10);
        unsigned long phase = strtoul(tokens[5], &end_phase, 10);
        node_config_t candidate = {
            .id = (uint8_t)(tokens[2][0] - 'A' + 1),
            .mode = node_mode_from_name(tokens[3]),
            .rate_hz = rate <= UINT16_MAX ? (uint16_t)rate : 0,
            .phase_ms = phase <= 600000 ? (uint32_t)phase : 0
        };
        if (tokens[2][1] || *end_rate || *end_phase || !node_config_valid(&candidate)) {
            emit("NODE_ACK,CONFIG,ERROR,invalid_config"); return;
        }
        stop_session();
        esp_err_t err = save_config(&candidate);
        if (err != ESP_OK) { emit("NODE_ACK,CONFIG,ERROR,%s", esp_err_to_name(err)); return; }
        portENTER_CRITICAL(&lock);
        config = candidate; fault = false; session = 0; have_grant = false;
        portEXIT_CRITICAL(&lock);
        radio_start();
        emit("NODE_ACK,CONFIG,OK,saved");
        status();
    } else if (!strcmp(cmd, "START") && n == 3) {
        char *end;
        unsigned long long requested = strtoull(tokens[2], &end, 10);
        if (*end || !requested || requested > UINT32_MAX || !node_config_valid(&config)) {
            emit("NODE_ACK,START,ERROR,invalid_session_or_unconfigured"); return;
        }
        stop_session();
        radio_start();
        portENTER_CRITICAL(&lock);
        session = (uint32_t)requested; fault = false;
        have_grant = response_sent = sent_slot_valid = logged_slot_valid = false;
        tx_count = csi_count = csi_dropped = late_dropped = send_errors = 0;
        grant_rx_count = grant_age_max = grant_expired = 0; grant_age_min = UINT32_MAX;
        csi_len_min = UINT32_MAX; csi_len_max = csi_invalid_length_dropped = 0;
        tx_completions[0] = tx_completions[1] = tx_age_max[0] = tx_age_max[1] = 0;
        tx_age_min[0] = tx_age_min[1] = UINT32_MAX;
        guard_extensions = fault_type = fault_age_us = 0; guard_extension_us = 0;
        next_slot = 0; next_slot_us = esp_timer_get_time() + 100000;
        generation++; running = true;
        portEXIT_CRITICAL(&lock);
        emit("NODE_ACK,START,OK,armed"); status();
    } else if (!strcmp(cmd, "STOP") && n == 2) {
        stop_session();
        portENTER_CRITICAL(&lock); fault = false; portEXIT_CRITICAL(&lock);
        emit("NODE_ACK,STOP,OK,stopped"); status();
    } else if (!strcmp(cmd, "STATUS") && n == 2) {
        emit("NODE_ACK,STATUS,OK,status"); status();
    } else emit("NODE_ACK,UNKNOWN,ERROR,syntax");
}

static void send_packet(const node_packet_t *p)
{
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    bool valid = running && !fault && !tx_pending && have_grant &&
                 node_same_slot(p, &grant) &&
                 (p->type == NODE_GRANT ? now - grant_received_us <= NODE_ENQUEUE_DEADLINE_US :
                  now - grant_received_us >= NODE_RESPONSE_DELAY_US &&
                  now - grant_received_us <= NODE_ENQUEUE_DEADLINE_US);
    if (!valid) {
        late_dropped++;
        portEXIT_CRITICAL(&lock);
        return;
    }
    tx_pending = true;
    pending_type = p->type;
    send_started_us = now;
    portEXIT_CRITICAL(&lock);
    esp_err_t err = esp_now_send(broadcast, (const uint8_t *)p, sizeof(*p));
    if (err != ESP_OK) {
        portENTER_CRITICAL(&lock);
        tx_pending = false; send_errors++;
        /* A failed grant must never authorize its own response. */
        if (p->type == NODE_GRANT) response_sent = true;
        portEXIT_CRITICAL(&lock);
    }
}

static void protocol_task(void *arg)
{
    (void)arg;
    bool fault_reported = false;
    while (true) {
        command_t command;
        if (xQueueReceive(command_queue, &command, 0) == pdTRUE) {
            handle_command(command.text);
            portENTER_CRITICAL(&lock);
            if (!fault) fault_reported = false;
            portEXIT_CRITICAL(&lock);
        }
        int64_t now = esp_timer_get_time();
        node_packet_t to_send;
        bool should_send = false;
        portENTER_CRITICAL(&lock);
        if (running && tx_pending && now - send_started_us > node_send_watchdog_us(pending_type)) {
            mark_fault_locked(pending_type, (uint32_t)(now - send_started_us));
        }
        /* A delayed GRANT holds the scheduler: its callback will establish the
         * next safe deadline. Do not advance slots or replace the grant while
         * any local send is pending. */
        if (running && config.id == 1 && !tx_pending && now >= next_slot_us) {
            const uint32_t interval = node_slot_us(&config);
            /* Advance the absolute schedule; never catch up by bursting frames. */
            uint64_t skipped = (uint64_t)(now - next_slot_us) / interval;
            if (skipped > UINT32_MAX - next_slot || next_slot == UINT32_MAX) {
                mark_fault_locked(3, 0);
            } else {
                next_slot += (uint32_t)skipped;
                late_dropped += (uint32_t)skipped;
                next_slot_us += (int64_t)skipped * interval;
                if (!tx_pending && now - next_slot_us <= NODE_ENQUEUE_DEADLINE_US) {
                    grant = node_make_grant(&config, session, next_slot);
                    grant_received_us = now; have_grant = true; response_sent = false;
                    to_send = grant; should_send = true;
                } else late_dropped++;
                next_slot++;
                next_slot_us += interval;
            }
        } else if (running && have_grant &&
                   node_can_respond(&grant, config.id, now - grant_received_us,
                       response_sent || (sent_slot_valid && sent_slot == grant.slot), tx_pending)) {
            to_send = grant; to_send.type = NODE_DATA;
            response_sent = true; sent_slot = grant.slot; sent_slot_valid = true;
            should_send = true;
        } else if (running && have_grant && grant.tx_id == config.id &&
                   !response_sent && now - grant_received_us > NODE_ENQUEUE_DEADLINE_US &&
                   !(config.id == 1 && tx_pending && pending_type == NODE_GRANT)) {
            response_sent = true; late_dropped++;
        }
        bool bad = fault;
        portEXIT_CRITICAL(&lock);
        if (bad && !fault_reported) {
            stop_session();
            emit("NODE_EVENT,FAULT,radio_deadline_or_slot_exhausted"); status();
            fault_reported = true;
        } else if (should_send) send_packet(&to_send);
        vTaskDelay(1);
    }
}

static void command_task(void *arg)
{
    (void)arg;
    command_t command;
    size_t used = 0;
    bool overflow = false;
    setvbuf(stdin, NULL, _IONBF, 0);
    while (true) {
        int ch = getchar();
        if (ch == EOF) { clearerr(stdin); vTaskDelay(pdMS_TO_TICKS(10)); continue; }
        if (ch == '\r') continue;
        if (ch == '\n') {
            if (overflow) emit("NODE_ACK,UNKNOWN,ERROR,line_too_long");
            else if (used) {
                command.text[used] = 0;
                if (xQueueSend(command_queue, &command, 0) != pdTRUE)
                    emit("NODE_ACK,UNKNOWN,ERROR,command_queue_full");
            }
            used = 0; overflow = false;
        } else if (used + 1 < sizeof(command.text) && !overflow) command.text[used++] = (char)ch;
        else overflow = true;
    }
}

static void output_task(void *arg)
{
    (void)arg;
    setvbuf(stdout, NULL, _IOFBF, 8192);
    puts("type,session,phase,slot,tx_id,rx_id,seq,rx_timestamp,rssi,channel,secondary_channel,sig_mode,mcs,bandwidth,noise_floor,first_word_invalid,csi_len,csi_b64");
    fflush(stdout);
    while (true) {
        output_event_t event;
        while (xQueueReceive(event_queue, &event, 0) == pdTRUE) {
            puts(event.text); fflush(stdout);
        }
        csi_record_t r;
        if (xQueueReceive(csi_queue, &r, pdMS_TO_TICKS(5)) != pdTRUE) continue;
        portENTER_CRITICAL(&lock);
        bool current = r.generation == generation && running;
        portEXIT_CRITICAL(&lock);
        if (!current) continue;
        char encoded[NODE_BASE64_CAPACITY];
        if (!node_csi_base64(r.csi, r.len, encoded, sizeof(encoded))) continue;
        printf("CSI_DATA_V2,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%c,%c,%" PRIu32
               ",%" PRIu32 ",%d,%u,%u,%u,%u,%u,%d,%u,%u,%s\n",
               r.packet.session, r.packet.phase, r.packet.slot,
               'A' + r.packet.tx_id - 1, 'A' + r.receiver - 1, r.packet.seq,
               r.timestamp, r.rssi, r.channel, r.secondary, r.sig_mode,
               r.mcs, r.bandwidth, r.noise, r.first_invalid, r.len, encoded);
        fflush(stdout);
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    /* Do not erase shared NVS: legacy settings are preserved and never read. */
    if (err != ESP_OK) { ESP_LOGE(TAG, "NVS init failed: %s", esp_err_to_name(err)); return; }
    load_config();
    csi_queue = xQueueCreate(CSI_QUEUE_DEPTH, sizeof(csi_record_t));
    event_queue = xQueueCreate(EVENT_QUEUE_DEPTH, sizeof(output_event_t));
    command_queue = xQueueCreate(COMMAND_QUEUE_DEPTH, sizeof(command_t));
    assert(csi_queue && event_queue && command_queue);
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    wifi_init_config_t wifi = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW40));
    ESP_ERROR_CHECK(esp_read_mac(factory_mac, ESP_MAC_WIFI_STA));
    radio_start();
    assert(xTaskCreate(output_task, "node_output", 6144, NULL, 4, NULL) == pdPASS);
    assert(xTaskCreate(command_task, "node_uart", 4096, NULL, 5, NULL) == pdPASS);
    assert(xTaskCreate(protocol_task, "node_protocol", 6144, NULL, 18, NULL) == pdPASS);
    emit("NODE_EVENT,BOOT,stopped"); status();
}
