#ifndef CSI_CONTROL_PROTOCOL_H
#define CSI_CONTROL_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CSI_CTRL_MAGIC 0x43534943UL /* "CSIC" */
#define CSI_CTRL_VERSION 1

#define CSI_CTRL_DEFAULT_RATE_HZ 40
#define CSI_CTRL_MIN_RATE_HZ 1
#define CSI_CTRL_MAX_RATE_HZ 200

/* esp_wifi_set_max_tx_power() uses units of 0.25 dBm. */
#define CSI_CTRL_DEFAULT_TX_POWER_QDBM 80 /* 20 dBm */
#define CSI_CTRL_MIN_TX_POWER_QDBM 8     /* 2 dBm */
#define CSI_CTRL_MAX_TX_POWER_QDBM 80    /* 20 dBm */

typedef enum {
    CSI_CTRL_MSG_SET = 1,
    CSI_CTRL_MSG_GET = 2,
    CSI_CTRL_MSG_RESET = 3,
    CSI_CTRL_MSG_STATUS = 4,
} csi_ctrl_message_type_t;

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t size;
    uint32_t request_id;
    uint16_t rate_hz;
    int16_t tx_power_qdbm;
    int32_t status;
} csi_ctrl_packet_t;

_Static_assert(sizeof(csi_ctrl_packet_t) == 20,
               "csi_ctrl_packet_t wire format changed");

static inline bool csi_ctrl_packet_is_valid(const csi_ctrl_packet_t *packet,
                                            size_t data_len)
{
    return packet != NULL && data_len == sizeof(*packet) &&
           packet->magic == CSI_CTRL_MAGIC &&
           packet->version == CSI_CTRL_VERSION &&
           packet->size == sizeof(*packet);
}

static inline bool csi_ctrl_settings_are_valid(uint16_t rate_hz,
                                               int16_t tx_power_qdbm)
{
    return rate_hz >= CSI_CTRL_MIN_RATE_HZ &&
           rate_hz <= CSI_CTRL_MAX_RATE_HZ &&
           tx_power_qdbm >= CSI_CTRL_MIN_TX_POWER_QDBM &&
           tx_power_qdbm <= CSI_CTRL_MAX_TX_POWER_QDBM;
}

#endif /* CSI_CONTROL_PROTOCOL_H */
