# CSI firmware control protocol

This protocol controls the fixed CSI sender's packet rate and Wi-Fi transmit-power
ceiling. RX firmware `2.0.0-rx-b64` preserves the existing `CSI_CTRL` commands,
responses, four-byte sensing sequence payload, and gain metadata. Its CSI UART
encoding changes from a quoted integer array (`CSI_DATA`) to base64
(`CSI_DATA_B64`); this is a receiver output change, not a TX control version change.

## Defaults and limits

- Default packet rate: `40 Hz`
- Allowed packet rate: `1..200 Hz`
- Default TX power ceiling: `80 qdBm` (`20 dBm`)
- Allowed TX power ceiling: `8..80 qdBm` (`2..20 dBm`)
- `qdBm` is the unit required by ESP-IDF: `qdBm / 4 = dBm`

The TX power value is a configured upper limit. The radio can use a lower
instantaneous power because of regulatory, calibration, or PHY constraints.

## Gateway to RX UART commands

Commands are newline-terminated ASCII at the receiver's configured UART baud
rate (`921600`). `request_id` is an unsigned 32-bit number selected by the
gateway.

```text
CSI_CTRL,SET,<request_id>,<rate_hz>,<tx_power_qdbm>
CSI_CTRL,GET,<request_id>
CSI_CTRL,RESET,<request_id>
```

Examples:

```text
CSI_CTRL,SET,1001,40,60
CSI_CTRL,GET,1002
CSI_CTRL,RESET,1003
```

The first example selects 40 packets/s and a 15 dBm ceiling (`60 / 4`). `SET`
and `RESET` persist their values in the TX NVS partition. `RESET` restores
40 Hz and 20 dBm.

## RX UART responses

The RX reports that the driver accepted an ESP-NOW control submission:

```text
CSI_CTRL_SENT,<request_id>,0,<requested_rate_hz>,<requested_tx_power_qdbm>
```

The TX returns the applied values, which the RX forwards to UART:

```text
CSI_CTRL_ACK,<request_id>,<esp_err>,<actual_rate_hz>,<actual_tx_power_qdbm>
```

`esp_err` is `0` on success. A gateway should treat `CSI_CTRL_SENT` only as an
intermediate state and wait for the matching `CSI_CTRL_ACK`. It should time out
if the ACK is not received, because ESP-NOW broadcast delivery is not
guaranteed.

At boot the RX advertises the protocol version and limits:

```text
CSI_CTRL_READY,<version>,<min_rate>,<max_rate>,<min_power_qdbm>,<max_power_qdbm>,<rx_mac>
```

The control version remains `1`. `rx_mac` identifies the receiver attached to
that UART port. The `mac` field in CSI data rows is the transmitter MAC instead.
Collectors must separate `CSI_CTRL_*`, RX diagnostics, `GAIN`, CSV headers, and
boot/debug logs from CSI records while continuing to drain UART during commands.

## RX-local diagnostics (Base64 RX only)

The new receiver also emits this line once during boot:

```text
CSI_RX_READY,1,2.0.0-rx-b64,32,768
```

Its fields are RX diagnostic format version, firmware version, CSI queue depth,
and maximum raw CSI byte length. It does not replace `CSI_CTRL_READY` or change
the ESP-NOW control protocol version.

Send a local statistics request and match its request ID in the reply:

```text
CSI_RX,STATS,<request_id>
CSI_RX_STATS,<request_id>,<matching_data>,<queued>,<output>,<queue_dropped>,<invalid_length>,<invalid_payload>,<control_filtered>,<min_len>,<max_len>,<status_queue_dropped>
```

This command performs no TX/radio control. Counters accumulate since RX boot
and have no reset command. `matching_data` counts valid fixed-TX four-byte
sensing payloads; `queued`, `queue_dropped`, and `invalid_length` classify their
CSI handling. `output` counts completed firmware output operations, not PC
receipt or successful parsing. `min_len`/`max_len` include
rejected CSI lengths and are both zero before matching DATA arrives.
`invalid_payload` counts sensing payload validation failures, `control_filtered`
counts excluded control/status packets, and `status_queue_dropped` counts TX
responses lost at the RX status queue. See the [RX counter definitions](csi_recv/README.md).

Collectors connected to old RX firmware must not require this optional
diagnostic command. Continue using the unchanged `CSI_CTRL` commands for TX
configuration, and tolerate mixed boot/log/data/control lines on the UART.

## CSI data compatibility

The ESP32-S3 output has 28 CSV fields in the existing order. The row type is now
`CSI_DATA_B64`, and only the last column name changes from `data` to `data_b64`.
Gain values remain metadata; base64 decodes to the original signed int8 CSI
bytes without multiplying by `compensate_gain_x1000000`.

Support both the legacy `CSI_DATA` integer-array format and the new
`CSI_DATA_B64` format during migration. Decode base64 strictly, check that its
byte count equals `len`, and retain raw bytes, lengths, PHY/gain metadata, and
the `first_word` invalidity flag. Length is variable, 1–768 bytes, not a fixed 384.
See [the RX schema](csi_recv/README.md) and
[the Korean collector handoff](COLLECTOR_FORMAT_HANDOFF_KO.md).

These rows are unrelated to the separate RR4 firmware's `CSI_DATA_V2` schema.
The fixed TX's four-byte counter appears as `id` and normally advances by one
for successive DATA sends from that TX, rather than RR4's global slot step four.
This protocol has no `NODE CONFIG`, `NODE START`, or `NODE STOP` commands.

## ESP-NOW wire format

RX and TX exchange a packed, versioned 20-byte `csi_ctrl_packet_t`. The shared
definition is `main/csi_control_protocol.h` in both projects. Control packets
use magic `CSIC`, and the RX excludes TX status frames from the sensing dataset.

The RX retains its factory MAC so that bidirectional control works. The TX
retains the fixed CSI sender MAC `1a:00:00:00:00:00`, preserving the existing
CSI source filter.
