/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* Get Start Example

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "esp_csi_gain_ctrl.h"
#include "esp_app_desc.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "rom/ets_sys.h"

#include "csi_control_protocol.h"
#include "rx_csi_format.h"

#if !CONFIG_IDF_TARGET_ESP32S3 || ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(6, 0, 1)
#error "RX Base64 firmware is validated for ESP32-S3 with ESP-IDF 6.0.1 only"
#endif

_Static_assert(sizeof(csi_ctrl_packet_t) == RX_CSI_CONTROL_BYTES, "control size changed");
_Static_assert(CSI_CTRL_MAGIC == RX_CSI_CONTROL_MAGIC, "control magic changed");
_Static_assert(CSI_CTRL_VERSION == RX_CSI_CONTROL_VERSION, "control version changed");

#define CONFIG_LESS_INTERFERENCE_CHANNEL 11
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 ||                 \
    (CONFIG_IDF_TARGET_ESP32C6 &&                                              \
     ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS WIFI_BW40
#define CONFIG_WIFI_5G_BANDWIDTHS WIFI_BW40
#define CONFIG_WIFI_2G_PROTOCOL WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH WIFI_BW40
#endif

#define CONFIG_ESP_NOW_PHYMODE WIFI_PHY_MODE_HT40
#define CONFIG_ESP_NOW_RATE WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_FORCE_GAIN 0

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF 0
#endif

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 ||                  \
    CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 ||                  \
    CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00,
                                              0x00, 0x00, 0x00};
static const uint8_t ESPNOW_BROADCAST_MAC[] = {0xff, 0xff, 0xff,
                                               0xff, 0xff, 0xff};
static const char *TAG = "csi_recv";
static QueueHandle_t s_status_queue;
static QueueHandle_t s_csi_queue;
static SemaphoreHandle_t s_console_mutex;
static portMUX_TYPE s_stats_lock = portMUX_INITIALIZER_UNLOCKED;

typedef struct {
  wifi_pkt_rx_ctrl_t rx_ctrl;
  uint32_t id;
  uint16_t len;
  uint8_t mac[6];
  uint8_t agc_gain;
  int8_t fft_gain;
  bool first_word_invalid;
  bool use_gain_baseline;
  uint8_t gain_samples_seen;
  int8_t data[RX_CSI_MAX_BYTES];
} rx_csi_record_t;

typedef struct { uint8_t agc; int8_t fft; } rx_gain_sample_t;
static rx_gain_sample_t s_gain_samples[100];
static unsigned s_gain_sample_count;

typedef struct {
  uint64_t matching_data, queued, output, queue_dropped;
  uint64_t invalid_length, invalid_payload, control_filtered;
  uint64_t status_queue_dropped;
  uint32_t min_len, max_len;
} rx_csi_stats_t;
static rx_csi_stats_t s_stats = {.min_len = UINT32_MAX};

#define RX_STATUS_QUEUE_DEPTH 8
#define RX_UART_LINE_MAX 128
#define RX_CONTROL_SEND_RETRIES 3

static void wifi_init() {
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  ESP_ERROR_CHECK(esp_netif_init());
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
  ESP_ERROR_CHECK(esp_wifi_start());
  esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
  wifi_protocols_t protocols = {.ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
                                .ghz_5g = CONFIG_WIFI_5G_PROTOCOL};
  ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
  wifi_bandwidths_t bandwidth = {.ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
                                 .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS};
  ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 &&                                            \
       ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) ||                     \
    CONFIG_IDF_TARGET_ESP32C61
  ESP_ERROR_CHECK(esp_wifi_start());
  esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
  wifi_protocols_t protocols = {
      .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
  };
  ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
  wifi_bandwidths_t bandwidth = {
      .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
  };
  ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#else
  ESP_ERROR_CHECK(
      esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));
  ESP_ERROR_CHECK(esp_wifi_start());
#endif

  ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
#if CONFIG_IDF_TARGET_ESP32C5
  if ((CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY &&
       CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW20) ||
      (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_5G_ONLY &&
       CONFIG_WIFI_5G_BANDWIDTHS == WIFI_BW20)) {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_NONE));
  } else {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_BELOW));
  }
#elif (CONFIG_IDF_TARGET_ESP32C6 &&                                            \
       ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) ||                     \
    CONFIG_IDF_TARGET_ESP32C61
  if (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY &&
      CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW20) {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_NONE));
  } else {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_BELOW));
  }
#else
  if (CONFIG_WIFI_BANDWIDTH == WIFI_BW20) {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_NONE));
  } else {
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL,
                                         WIFI_SECOND_CHAN_BELOW));
  }
#endif

  /* Keep the receiver's factory MAC. The fixed 1a:00:... MAC belongs only to
   * the CSI sender; using it on both devices prevents reliable bidirectional
   * ESP-NOW control. */
}

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
static void espnow_recv_cb(const esp_now_recv_info_t *recv_info,
                           const uint8_t *data, int data_len) {
  if (recv_info == NULL || recv_info->src_addr == NULL ||
      memcmp(recv_info->src_addr, CONFIG_CSI_SEND_MAC, 6) != 0) {
    return;
  }
#else
static void espnow_recv_cb(const uint8_t *src_addr, const uint8_t *data,
                           int data_len) {
  if (src_addr == NULL || memcmp(src_addr, CONFIG_CSI_SEND_MAC, 6) != 0) {
    return;
  }
#endif
  if (data == NULL || data_len != sizeof(csi_ctrl_packet_t)) {
    return;
  }

  csi_ctrl_packet_t response;
  memcpy(&response, data, sizeof(response));
  if (!csi_ctrl_packet_is_valid(&response, data_len) ||
      response.type != CSI_CTRL_MSG_STATUS) {
    return;
  }

  if (xQueueSend(s_status_queue, &response, 0) != pdTRUE) {
    portENTER_CRITICAL(&s_stats_lock);
    s_stats.status_queue_dropped++;
    portEXIT_CRITICAL(&s_stats_lock);
  }
}

static void wifi_esp_now_init(esp_now_peer_info_t peer) {
  ESP_ERROR_CHECK(esp_now_init());
  ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
  esp_now_rate_config_t rate_config = {
      .phymode = CONFIG_ESP_NOW_PHYMODE,
      .rate = CONFIG_ESP_NOW_RATE, //  WIFI_PHY_RATE_MCS0_LGI,
      .ersu = false,
      .dcm = false};
  ESP_ERROR_CHECK(esp_now_add_peer(&peer));
  ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
  ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_recv_cb));
}

/* Wi-Fi task: bounded validation/snapshot only. Never format, log, allocate,
 * wait on a mutex, or invoke gain calibration here. */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info) {
  (void)ctx;
  if (info == NULL || memcmp(info->mac, CONFIG_CSI_SEND_MAC, 6) != 0) {
    return;
  }

  uint32_t sequence = 0;
  rx_csi_payload_kind_t kind =
      rx_csi_parse_action(info->payload, info->payload_len, &sequence);
  if (kind != RX_CSI_PAYLOAD_DATA) {
    portENTER_CRITICAL(&s_stats_lock);
    if (kind == RX_CSI_PAYLOAD_CONTROL) {
      s_stats.control_filtered++;
    } else {
      s_stats.invalid_payload++;
    }
    portEXIT_CRITICAL(&s_stats_lock);
    return;
  }

  bool length_valid = info->buf != NULL && rx_csi_length_valid(info->len);
  portENTER_CRITICAL(&s_stats_lock);
  s_stats.matching_data++;
  if (info->len < s_stats.min_len) s_stats.min_len = info->len;
  if (info->len > s_stats.max_len) s_stats.max_len = info->len;
  if (!length_valid) s_stats.invalid_length++;
  portEXIT_CRITICAL(&s_stats_lock);
  if (!length_valid) return;

  rx_csi_record_t record = {
      .rx_ctrl = info->rx_ctrl,
      .id = sequence,
      .len = info->len,
      .first_word_invalid = info->first_word_invalid,
  };
  memcpy(record.mac, info->mac, sizeof(record.mac));
  memcpy(record.data, info->buf, record.len);
  /* gain_ctrl 0.1.5, IDF 6.0/S3: with non-null arguments this getter only
   * snapshots the two gain bytes from rx_ctrl. Calibration may allocate/log. */
  esp_csi_gain_ctrl_get_rx_gain(&info->rx_ctrl, &record.agc_gain, &record.fft_gain);
#if CONFIG_GAIN_CONTROL
  portENTER_CRITICAL(&s_stats_lock);
  record.use_gain_baseline = s_gain_sample_count == 100;
  if (s_gain_sample_count < 100) {
    s_gain_samples[s_gain_sample_count++] =
        (rx_gain_sample_t){.agc = record.agc_gain, .fft = record.fft_gain};
  }
  record.gain_samples_seen = (uint8_t)s_gain_sample_count;
  portEXIT_CRITICAL(&s_stats_lock);
#endif
  /* Capture the first 100 gain samples even when output is backlogged, so
   * queue drops cannot change which received samples define the baseline. */
  BaseType_t queued = xQueueSend(s_csi_queue, &record, 0);
  portENTER_CRITICAL(&s_stats_lock);
  if (queued == pdTRUE) s_stats.queued++;
  else s_stats.queue_dropped++;
  portEXIT_CRITICAL(&s_stats_lock);
}

static void csi_output_task(void *arg) {
  (void)arg;
  rx_csi_record_t record;
  char line[RX_CSI_LINE_CAPACITY];
#if CONFIG_GAIN_CONTROL
  rx_csi_gain_progress_t gain_progress = {0};
#endif
  uint64_t output_count = 0;
  if (xSemaphoreTake(s_console_mutex, portMAX_DELAY) == pdTRUE) {
    printf("CSI_RX_READY,1,%s,%u,%u\n", esp_app_get_description()->version,
           (unsigned)RX_CSI_QUEUE_DEPTH, (unsigned)RX_CSI_MAX_BYTES);
    puts(RX_CSI_CSV_HEADER);
    fflush(stdout);
    xSemaphoreGive(s_console_mutex);
  }

  while (true) {
    if (xQueueReceive(s_csi_queue, &record, portMAX_DELAY) != pdTRUE) continue;
    if (xSemaphoreTake(s_console_mutex, portMAX_DELAY) != pdTRUE) continue;
    float compensate_gain = 1.0f;
#if CONFIG_GAIN_CONTROL
    rx_csi_gain_step_t gain_step;
    bool gain_order_valid = rx_csi_gain_advance(&gain_progress,
        record.gain_samples_seen, record.use_gain_baseline, &gain_step);
    configASSERT(gain_order_valid);
    if (gain_order_valid) {
      rx_gain_sample_t samples[100];
      if (gain_step.begin < gain_step.end) {
        portENTER_CRITICAL(&s_stats_lock);
        memcpy(samples, s_gain_samples, sizeof(samples));
        portEXIT_CRITICAL(&s_stats_lock);
      }
      for (unsigned i = gain_step.begin; i < gain_step.end; i++) {
        float sample_compensation = 1.0f;
        esp_csi_gain_ctrl_record_rx_gain(samples[i].agc, samples[i].fft);
        /* The library can create/retry its baseline inside either call.
         * Replay both even for CSI records dropped by the output queue. */
        esp_csi_gain_ctrl_get_gain_compensation(&sample_compensation,
                                                samples[i].agc, samples[i].fft);
        if (!record.use_gain_baseline && i + 1 == gain_step.end) {
          compensate_gain = sample_compensation;
        }
      }
      if (gain_step.request_baseline) {
        uint8_t agc_gain_baseline = 0;
        int8_t fft_gain_baseline = 0;
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline,
                                               &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %u", fft_gain_baseline,
                 (unsigned)agc_gain_baseline);
#endif
      }
      if (record.use_gain_baseline) {
        esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, record.agc_gain,
                                                record.fft_gain);
      }
    }
#endif
    const wifi_pkt_rx_ctrl_t *r = &record.rx_ctrl;
    rx_csi_fields_t fields = {
        .id = record.id, .rssi = r->rssi, .rate = r->rate,
        .sig_mode = r->sig_mode, .mcs = r->mcs, .bandwidth = r->cwb,
        .smoothing = r->smoothing, .not_sounding = r->not_sounding,
        .aggregation = r->aggregation, .stbc = r->stbc,
        .fec_coding = r->fec_coding, .sgi = r->sgi,
        .noise_floor = r->noise_floor, .fft_gain = record.fft_gain,
        .agc_gain = record.agc_gain,
        .compensate_gain_x1000000 = (int32_t)(compensate_gain * 1000000.0f + 0.5f),
        .ampdu_cnt = r->ampdu_cnt, .channel = r->channel,
        .secondary_channel = r->secondary_channel,
        .local_timestamp = r->timestamp, .ant = r->ant, .sig_len = r->sig_len,
        .rx_format = r->sig_mode, .first_word = record.first_word_invalid,
    };
    memcpy(fields.mac, record.mac, sizeof(fields.mac));
    bool formatted = rx_csi_format_line(&fields, record.data, record.len,
                                         line, sizeof(line));
    configASSERT(formatted);
    if (formatted) {
      if ((output_count % 100) == 0) {
        printf("GAIN,%d,%u,%d,%" PRId32 "\n", (int)r->rssi,
               (unsigned)record.agc_gain, (int)record.fft_gain,
               fields.compensate_gain_x1000000);
        ESP_LOGI(TAG, "compensate_gain_x1000000 %" PRId32
                 ", agc_gain %u, fft_gain %d", fields.compensate_gain_x1000000,
                 (unsigned)record.agc_gain, (int)record.fft_gain);
      }
      int result = fputs(line, stdout);
      int flushed = fflush(stdout);
      if (result >= 0 && flushed == 0) {
        portENTER_CRITICAL(&s_stats_lock);
        s_stats.output++;
        portEXIT_CRITICAL(&s_stats_lock);
      }
      output_count++;
    }
    xSemaphoreGive(s_console_mutex);
  }
}

static void wifi_csi_init() {
  ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

  /**< default config */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
  wifi_csi_config_t csi_config = {.enable = true,
                                  .acquire_csi_legacy = false,
                                  .acquire_csi_force_lltf = CSI_FORCE_LLTF,
                                  .acquire_csi_ht20 = true,
                                  .acquire_csi_ht40 = true,
                                  .acquire_csi_vht = false,
                                  .acquire_csi_su = false,
                                  .acquire_csi_mu = false,
                                  .acquire_csi_dcm = false,
                                  .acquire_csi_beamformed = false,
                                  .acquire_csi_he_stbc_mode = 2,
                                  .val_scale_cfg = 0,
                                  .dump_ack_en = false,
                                  .reserved = false};
#elif CONFIG_IDF_TARGET_ESP32C6
  wifi_csi_config_t csi_config = {.enable = true,
                                  .acquire_csi_legacy = false,
                                  .acquire_csi_ht20 = true,
                                  .acquire_csi_ht40 = true,
                                  .acquire_csi_su = true,
                                  .acquire_csi_mu = true,
                                  .acquire_csi_dcm = true,
                                  .acquire_csi_beamformed = true,
                                  .acquire_csi_he_stbc = 2,
                                  .val_scale_cfg = false,
                                  .dump_ack_en = false,
                                  .reserved = false};
#else
  wifi_csi_config_t csi_config = {
      .lltf_en = true,
      .htltf_en = true,
      .stbc_htltf2_en = true,
      .ltf_merge_en = true,
      .channel_filter_en = true,
      .manu_scale = false,
      .shift = false,
  };
#endif
  ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
  ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
  ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void console_print_rx_stats(uint32_t request_id) {
  rx_csi_stats_t stats;
  portENTER_CRITICAL(&s_stats_lock);
  stats = s_stats;
  portEXIT_CRITICAL(&s_stats_lock);
  if (stats.min_len == UINT32_MAX) stats.min_len = 0;
  if (xSemaphoreTake(s_console_mutex, portMAX_DELAY) != pdTRUE) return;
  printf("CSI_RX_STATS,%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
         ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu32
         ",%" PRIu32 ",%" PRIu64 "\n",
         request_id, stats.matching_data, stats.queued, stats.output,
         stats.queue_dropped, stats.invalid_length, stats.invalid_payload,
         stats.control_filtered, stats.min_len, stats.max_len,
         stats.status_queue_dropped);
  fflush(stdout);
  xSemaphoreGive(s_console_mutex);
}

static void console_print_status(const char *prefix,
                                 const csi_ctrl_packet_t *packet) {
  if (xSemaphoreTake(s_console_mutex, portMAX_DELAY) != pdTRUE) {
    return;
  }
  printf("%s,%" PRIu32 ",%" PRId32 ",%u,%d\n", prefix,
         packet->request_id, packet->status, packet->rate_hz,
         packet->tx_power_qdbm);
  fflush(stdout);
  xSemaphoreGive(s_console_mutex);
}

static void console_print_error(uint32_t request_id, esp_err_t status) {
  csi_ctrl_packet_t response = {
      .request_id = request_id,
      .rate_hz = 0,
      .tx_power_qdbm = 0,
      .status = status,
  };
  console_print_status("CSI_CTRL_ACK", &response);
}

static esp_err_t send_control_request(csi_ctrl_message_type_t type,
                                      uint32_t request_id, uint16_t rate_hz,
                                      int16_t tx_power_qdbm) {
  if (type == CSI_CTRL_MSG_SET &&
      !csi_ctrl_settings_are_valid(rate_hz, tx_power_qdbm)) {
    return ESP_ERR_INVALID_ARG;
  }

  csi_ctrl_packet_t request = {
      .magic = CSI_CTRL_MAGIC,
      .version = CSI_CTRL_VERSION,
      .type = type,
      .size = sizeof(request),
      .request_id = request_id,
      .rate_hz = rate_hz,
      .tx_power_qdbm = tx_power_qdbm,
      .status = ESP_OK,
  };
  esp_err_t err = ESP_FAIL;
  for (int attempt = 0; attempt < RX_CONTROL_SEND_RETRIES; ++attempt) {
    err = esp_now_send(ESPNOW_BROADCAST_MAC, (const uint8_t *)&request,
                       sizeof(request));
    if (err == ESP_OK) {
      break;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return err;
}

static void status_output_task(void *arg) {
  (void)arg;
  csi_ctrl_packet_t response;

  while (true) {
    if (xQueueReceive(s_status_queue, &response, portMAX_DELAY) == pdTRUE) {
      console_print_status("CSI_CTRL_ACK", &response);
    }
  }
}

static void uart_command_task(void *arg) {
  (void)arg;
  char line[RX_UART_LINE_MAX];
  setvbuf(stdin, NULL, _IONBF, 0);

  while (true) {
    if (fgets(line, sizeof(line), stdin) == NULL) {
      clearerr(stdin);
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    line[strcspn(line, "\r\n")] = '\0';
    uint32_t request_id = 0;
    if (rx_csi_parse_stats_command(line, &request_id)) {
      console_print_rx_stats(request_id);
      continue;
    }
    unsigned int rate_hz = 0;
    int tx_power_qdbm = 0;
    int consumed = 0;
    csi_ctrl_message_type_t type = 0;

    int matched = sscanf(line,
                         "CSI_CTRL,SET,%" SCNu32 ",%u,%d%n",
                         &request_id, &rate_hz, &tx_power_qdbm, &consumed);
    if (matched == 3 && consumed > 0 && line[consumed] == '\0' &&
        rate_hz <= UINT16_MAX && tx_power_qdbm >= INT16_MIN &&
        tx_power_qdbm <= INT16_MAX) {
      type = CSI_CTRL_MSG_SET;
    } else {
      consumed = 0;
      matched = sscanf(line, "CSI_CTRL,GET,%" SCNu32 "%n", &request_id,
                       &consumed);
      if (matched == 1 && consumed > 0 && line[consumed] == '\0') {
        type = CSI_CTRL_MSG_GET;
      } else {
        consumed = 0;
        matched = sscanf(line, "CSI_CTRL,RESET,%" SCNu32 "%n", &request_id,
                         &consumed);
        if (matched == 1 && consumed > 0 && line[consumed] == '\0') {
          type = CSI_CTRL_MSG_RESET;
        }
      }
    }

    if (type == 0) {
      console_print_error(request_id, ESP_ERR_INVALID_ARG);
      continue;
    }

    esp_err_t err = send_control_request(type, request_id, (uint16_t)rate_hz,
                                         (int16_t)tx_power_qdbm);
    if (err != ESP_OK) {
      console_print_error(request_id, err);
      continue;
    }

    csi_ctrl_packet_t sent = {
        .request_id = request_id,
        .rate_hz = (uint16_t)rate_hz,
        .tx_power_qdbm = (int16_t)tx_power_qdbm,
        .status = ESP_OK,
    };
    console_print_status("CSI_CTRL_SENT", &sent);
  }
}

void app_main(void) {
  /**
   * @brief Initialize NVS
   */
  esp_err_t ret = nvs_flash_init();
  if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
      ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ret = nvs_flash_init();
  }
  ESP_ERROR_CHECK(ret);

  s_status_queue =
      xQueueCreate(RX_STATUS_QUEUE_DEPTH, sizeof(csi_ctrl_packet_t));
  ESP_ERROR_CHECK(s_status_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);
  s_csi_queue = xQueueCreate(RX_CSI_QUEUE_DEPTH, sizeof(rx_csi_record_t));
  ESP_ERROR_CHECK(s_csi_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);
  s_console_mutex = xSemaphoreCreateMutex();
  ESP_ERROR_CHECK(s_console_mutex != NULL ? ESP_OK : ESP_ERR_NO_MEM);

  /**
   * @brief Initialize Wi-Fi
   */
  wifi_init();

  /**
   * @brief Initialize ESP-NOW
   *        ESP-NOW protocol see:
   * https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
   */

  esp_now_peer_info_t peer = {
      .channel = CONFIG_LESS_INTERFERENCE_CHANNEL,
      .ifidx = WIFI_IF_STA,
      .encrypt = false,
      .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
  };

  wifi_esp_now_init(peer);

  BaseType_t csi_task_created =
      xTaskCreate(csi_output_task, "csi_output", 6144, NULL, 4, NULL);
  ESP_ERROR_CHECK(csi_task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
  wifi_csi_init();

  BaseType_t status_task_created =
      xTaskCreate(status_output_task, "ctrl_status", 3072, NULL, 5, NULL);
  ESP_ERROR_CHECK(status_task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
  BaseType_t uart_task_created =
      xTaskCreate(uart_command_task, "ctrl_uart", 4096, NULL, 5, NULL);
  ESP_ERROR_CHECK(uart_task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);

  uint8_t receiver_mac[6];
  ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, receiver_mac));
  if (xSemaphoreTake(s_console_mutex, portMAX_DELAY) == pdTRUE) {
    printf("CSI_CTRL_READY,%d,%d,%d,%d,%d," MACSTR "\n", CSI_CTRL_VERSION,
           CSI_CTRL_MIN_RATE_HZ, CSI_CTRL_MAX_RATE_HZ,
           CSI_CTRL_MIN_TX_POWER_QDBM, CSI_CTRL_MAX_TX_POWER_QDBM,
           MAC2STR(receiver_mac));
    fflush(stdout);
    xSemaphoreGive(s_console_mutex);
  }
}
