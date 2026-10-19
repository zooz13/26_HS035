/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"

#include "csi_control_protocol.h"

#define CONFIG_LESS_INTERFERENCE_CHANNEL 11

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 ||                \
    (CONFIG_IDF_TARGET_ESP32C6 &&                                             \
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

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

#define TX_SETTINGS_NAMESPACE "csi_ctrl"
#define TX_RATE_KEY "rate_hz"
#define TX_POWER_KEY "power_qdbm"
#define TX_CONTROL_QUEUE_DEPTH 8
#define TX_CONTROL_SEND_RETRIES 3

static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00,
                                              0x00, 0x00, 0x00};
static const uint8_t ESPNOW_BROADCAST_MAC[] = {0xff, 0xff, 0xff,
                                               0xff, 0xff, 0xff};
static const char *TAG = "csi_send";

static QueueHandle_t s_control_queue;
static volatile uint16_t s_rate_hz = CSI_CTRL_DEFAULT_RATE_HZ;
static volatile int16_t s_tx_power_qdbm = CSI_CTRL_DEFAULT_TX_POWER_QDBM;

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE));
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
        .ghz_5g = CONFIG_WIFI_5G_PROTOCOL,
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
        .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS,
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 &&                                        \
       ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) ||                  \
    CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE));
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
#elif (CONFIG_IDF_TARGET_ESP32C6 &&                                        \
       ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) ||                  \
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
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC));
}

static esp_err_t save_settings(uint16_t rate_hz, int16_t tx_power_qdbm)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(TX_SETTINGS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_u16(handle, TX_RATE_KEY, rate_hz);
    if (err == ESP_OK) {
        err = nvs_set_i16(handle, TX_POWER_KEY, tx_power_qdbm);
    }
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

static esp_err_t apply_settings(uint16_t rate_hz, int16_t tx_power_qdbm,
                                bool persist)
{
    if (!csi_ctrl_settings_are_valid(rate_hz, tx_power_qdbm)) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = esp_wifi_set_max_tx_power((int8_t)tx_power_qdbm);
    if (err != ESP_OK) {
        return err;
    }

    s_rate_hz = rate_hz;
    s_tx_power_qdbm = tx_power_qdbm;

    if (persist) {
        err = save_settings(rate_hz, tx_power_qdbm);
    }
    return err;
}

static void load_settings(void)
{
    uint16_t rate_hz = CSI_CTRL_DEFAULT_RATE_HZ;
    int16_t tx_power_qdbm = CSI_CTRL_DEFAULT_TX_POWER_QDBM;
    nvs_handle_t handle;

    esp_err_t err = nvs_open(TX_SETTINGS_NAMESPACE, NVS_READONLY, &handle);
    if (err == ESP_OK) {
        if (nvs_get_u16(handle, TX_RATE_KEY, &rate_hz) != ESP_OK ||
            nvs_get_i16(handle, TX_POWER_KEY, &tx_power_qdbm) != ESP_OK ||
            !csi_ctrl_settings_are_valid(rate_hz, tx_power_qdbm)) {
            rate_hz = CSI_CTRL_DEFAULT_RATE_HZ;
            tx_power_qdbm = CSI_CTRL_DEFAULT_TX_POWER_QDBM;
        }
        nvs_close(handle);
    }

    err = apply_settings(rate_hz, tx_power_qdbm, false);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "failed to apply stored settings: %s",
                 esp_err_to_name(err));
        ESP_ERROR_CHECK(apply_settings(CSI_CTRL_DEFAULT_RATE_HZ,
                                       CSI_CTRL_DEFAULT_TX_POWER_QDBM, false));
    }
}

static void send_status(uint32_t request_id, esp_err_t status)
{
    int8_t actual_power_qdbm = (int8_t)s_tx_power_qdbm;
    esp_err_t power_err = esp_wifi_get_max_tx_power(&actual_power_qdbm);
    if (status == ESP_OK && power_err != ESP_OK) {
        status = power_err;
    }

    csi_ctrl_packet_t response = {
        .magic = CSI_CTRL_MAGIC,
        .version = CSI_CTRL_VERSION,
        .type = CSI_CTRL_MSG_STATUS,
        .size = sizeof(response),
        .request_id = request_id,
        .rate_hz = s_rate_hz,
        .tx_power_qdbm = actual_power_qdbm,
        .status = status,
    };

    esp_err_t err = ESP_FAIL;
    for (int attempt = 0; attempt < TX_CONTROL_SEND_RETRIES; ++attempt) {
        err = esp_now_send(ESPNOW_BROADCAST_MAC,
                           (const uint8_t *)&response, sizeof(response));
        if (err == ESP_OK) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "control status send failed: %s", esp_err_to_name(err));
    }

    ESP_LOGI(TAG,
             "CTRL_STATUS request=%" PRIu32 " status=%s rate_hz=%u "
             "tx_power_qdbm=%d",
             request_id, esp_err_to_name(status), response.rate_hz,
             response.tx_power_qdbm);
}

static void control_task(void *arg)
{
    (void)arg;
    csi_ctrl_packet_t request;

    while (true) {
        if (xQueueReceive(s_control_queue, &request, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        esp_err_t status = ESP_OK;
        switch ((csi_ctrl_message_type_t)request.type) {
        case CSI_CTRL_MSG_SET:
            status = apply_settings(request.rate_hz, request.tx_power_qdbm,
                                    true);
            break;
        case CSI_CTRL_MSG_GET:
            break;
        case CSI_CTRL_MSG_RESET:
            status = apply_settings(CSI_CTRL_DEFAULT_RATE_HZ,
                                    CSI_CTRL_DEFAULT_TX_POWER_QDBM, true);
            break;
        default:
            status = ESP_ERR_INVALID_ARG;
            break;
        }
        send_status(request.request_id, status);
    }
}

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
static void espnow_recv_cb(const esp_now_recv_info_t *recv_info,
                           const uint8_t *data, int data_len)
{
    (void)recv_info;
#else
static void espnow_recv_cb(const uint8_t *src_addr, const uint8_t *data,
                           int data_len)
{
    (void)src_addr;
#endif
    if (data == NULL || data_len != sizeof(csi_ctrl_packet_t)) {
        return;
    }

    csi_ctrl_packet_t request;
    memcpy(&request, data, sizeof(request));
    if (!csi_ctrl_packet_is_valid(&request, data_len) ||
        request.type == CSI_CTRL_MSG_STATUS) {
        return;
    }

    if (xQueueSend(s_control_queue, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "control queue full, request=%" PRIu32,
                 request.request_id);
    }
}

static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE,
        .ersu = false,
        .dcm = false,
    };
    ESP_ERROR_CHECK(
        esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_recv_cb));
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    s_control_queue = xQueueCreate(TX_CONTROL_QUEUE_DEPTH,
                                   sizeof(csi_ctrl_packet_t));
    ESP_ERROR_CHECK(s_control_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);

    wifi_init();
    load_settings();

    esp_now_peer_info_t peer = {
        .channel = CONFIG_LESS_INTERFERENCE_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(peer);

    BaseType_t task_created = xTaskCreate(control_task, "csi_control", 4096,
                                          NULL, 5, NULL);
    ESP_ERROR_CHECK(task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);

    int8_t actual_power_qdbm = 0;
    ESP_ERROR_CHECK(esp_wifi_get_max_tx_power(&actual_power_qdbm));
    ESP_LOGI(TAG, "================ CSI SEND ================");
    ESP_LOGI(TAG,
             "wifi_channel: %d, send_frequency: %u, tx_power_qdbm: %d, "
             "mac: " MACSTR,
             CONFIG_LESS_INTERFERENCE_CHANNEL, s_rate_hz, actual_power_qdbm,
             MAC2STR(CONFIG_CSI_SEND_MAC));

    int64_t next_send_us = esp_timer_get_time();
    for (uint32_t count = 0;; ++count) {
        ret = esp_now_send(peer.peer_addr, (const uint8_t *)&count,
                           sizeof(count));
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "free_heap: %" PRIu32 " <%s> ESP-NOW send error",
                     esp_get_free_heap_size(), esp_err_to_name(ret));
        }

        uint16_t rate_hz = s_rate_hz;
        int64_t period_us = 1000LL * 1000LL / rate_hz;
        next_send_us += period_us;
        int64_t sleep_us = next_send_us - esp_timer_get_time();
        if (sleep_us > 0) {
            usleep((useconds_t)sleep_us);
        } else if (sleep_us < -period_us) {
            /* Recover cleanly after a long scheduler or radio stall. */
            next_send_us = esp_timer_get_time();
        }
    }
}
