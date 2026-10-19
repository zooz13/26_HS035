# Fixed CSI receiver: Base64 output

RX firmware **2.0.0-rx-b64** targets ESP32-S3 with ESP-IDF 6.0.1. It receives
the existing fixed TX's four-byte sequence packets and relays the unchanged
`CSI_CTRL` commands. It does not implement RR4, role switching, or `NODE_*`
commands. The current physical boards remain on their existing firmware until
the user is ready to install this version.

The CSI callback validates the sender and sensing payload, snapshots AGC/FFT
gain, copies raw CSI and metadata into a **32-record queue with zero wait**, and
returns. A fixed buffer separately retains gain samples from the first 100
valid CSI packets, even if the output queue is full. Each queued record retains
the number of gain samples seen at its reception. The output task replays only
that prefix through the original gain-recording/compensation calls, including
samples whose CSI rows were dropped, then prints base64 CSV. Later samples
cannot change an earlier row's compensation metadata. Gain is captured at
callback time rather than extracted later from copied
RX control data. The callback no longer waits for the console mutex or prints
each CSI value. A full queue increments a counter and drops the new record; it
does not block the Wi-Fi task or guarantee lossless collection.
The library may establish its baseline automatically from the 50th sample;
the first 100 compensation values are not assumed to be 1.0. The explicit
baseline query is performed once when the output task first processes a record
received after the initial 100 samples. This preserves the original gain-call
order while leaving raw CSI unchanged.

## Radio and installation

Channel 11, HT40 with the secondary channel below, MCS0 long guard interval,
CSI acquisition settings, and gain settings remain unchanged. Forced gain is
disabled. CSI source MAC remains `1a:00:00:00:00:00`; the RX retains its factory
MAC so that TX control responses can return to it. TX rate defaults to 40 Hz,
but saved TX NVS settings take precedence and must be checked with GET.

Use the same `prebuilt/rx/` image for every RX. See [the root installation
instructions](../README.md); no per-board rebuild is required. The console is
**921600 baud, 8N1**. This fixed receiver starts receiving after boot, with no
application START/STOP session command. Flashing only the RX does not require a
TX firmware change. Keep the old flash/NVS backup and update the collector
before switching the physical RX to the new CSV format.

## ESP32-S3 CSV schema

The header appears once during boot:

```text
type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,fft_gain,agc_gain,compensate_gain_x1000000,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data_b64
```

There are **28 fields**, preserving the legacy ESP32-S3 column order. Data rows
start with `CSI_DATA_B64`. The old last column was `data`, containing a quoted
signed integer array; the new `data_b64` field is unquoted RFC 4648 base64.
Collectors should support both row types while old and new receivers coexist.
This schema differs from the round-robin firmware's `CSI_DATA_V2` schema.

| Field | Meaning |
| --- | --- |
| `id` | Little-endian uint32 counter from the fixed TX's four-byte payload; consecutive sends normally differ by one |
| `mac` | TX/source MAC, not the receiver's identity |
| `rate` | Per-packet PHY rate metadata; not the configured 40 Hz packet cadence |
| `local_timestamp` | RX-local uint32 Wi-Fi microseconds, wrapping about every 71 minutes; not a shared clock |
| `len` | Number of raw CSI bytes, valid from 1 through 768 |
| `first_word` | The IDF `first_word_invalid` flag; when 1, the first four CSI bytes are invalid |
| `fft_gain`, `agc_gain`, `compensate_gain_x1000000` | Gain metadata retained from the original output; raw CSI has no gain compensation applied |
| `sig_mode`, `mcs`, `bandwidth`, `rx_format` and other PHY fields | Preserve them with each record to interpret the CSI layout; `bandwidth` is the IDF `cwb` bit and the S3 `rx_format` repeats `sig_mode` |

Decode base64 strictly and verify that the decoded byte count equals `len`.
Convert bytes to signed int8 without rescaling: `b` if `b < 128`, otherwise
`b - 256`. For example, `AAF/gP8=` decodes to `[0, 1, 127, -128, -1]`, exactly
the values that the old S3 integer-array output would represent.

Do not assume 384 bytes. Keep original bytes, length, invalidity flag, PHY and
gain metadata; do not silently drop other valid lengths or pad/truncate them
into one layout. Retain invalid first-word bytes in the raw record and mask
them during analysis. Existing Gateway paths that require 384 I/Q values or
192 amplitudes need a separate parser/feature-extraction review.

The TX counter increments for each send attempt and restarts after TX reboot.
Neither the row nor this firmware supplies a collector session ID. Use host
session metadata and boot boundaries to prevent accidental joins across runs.
Associate the UART port with the `rx_mac` in `CSI_CTRL_READY`; several RX ports
will report the same TX `mac`. RR4 sequence step four does not apply here.
New rows print `id` and `local_timestamp` as unsigned decimal. Legacy rows used
signed decimal formatting, so values with the high bit set can appear negative;
normalize those two legacy fields modulo `2^32` before rollover handling.
Sequence gaps can include TX send failures, RF loss, RX queue drops, and host
loss; use diagnostics to distinguish them rather than labeling every gap RF loss.

## Startup and local diagnostics

Alongside the unchanged `CSI_CTRL_READY`, boot emits:

```text
CSI_RX_READY,1,2.0.0-rx-b64,32,768
```

Fields are diagnostic format version, firmware version, CSI queue depth, and
maximum raw CSI length. Request a local snapshot with:

```text
CSI_RX,STATS,1001
```

This command stays on the RX; it does not send a control packet to the TX or
change its rate/power. Match the request ID in the reply:

```text
CSI_RX_STATS,request_id,matching_data,queued,output,queue_dropped,invalid_length,invalid_payload,control_filtered,min_len,max_len,status_queue_dropped
```

| Counter | Meaning |
| --- | --- |
| `matching_data` | Fixed-TX packets with a valid action frame and four-byte sequence payload |
| `queued` / `output` | CSI records queued / printed by the output task; queued records may still be waiting |
| `queue_dropped` | New CSI records dropped because the 32-record queue was full |
| `invalid_length` | Matching DATA with null CSI buffer, zero CSI length, or length above 768 |
| `invalid_payload` | Fixed-TX packets rejected by sensing payload validation |
| `control_filtered` | Control/status packets excluded from the sensing stream |
| `min_len` / `max_len` | Observed lengths for matching DATA, including rejected 0/oversize lengths; both zero before any matching DATA |
| `status_queue_dropped` | TX control responses lost because the RX status queue was full |

`output` confirms completion of the firmware's output operation, not successful
receipt or parsing by the PC. Check host record counts and parsing errors too.

Counters accumulate **since RX boot** with no reset command. Use before/after
snapshots for a capture window and record any reboot. At a settled snapshot,
`matching_data = queued + queue_dropped + invalid_length`; callbacks in flight
can briefly create a discrepancy. Zero queue or length drops do not establish
zero RF loss. Preserve diagnostics and raw logs, but parse neither these lines,
`GAIN`, boot logs, nor CSV headers as CSI samples. Avoid aggressive status
polling during a measurement.

## Verification boundary

On 2026-10-14, five native tests passed using the actual C parser/serializer,
including all 1–768-byte base64 round trips, the 28-field header and metadata,
payload validation, local statistics command parsing, and gain progression
across queue gaps and the 50/100/101-sample boundaries. The ESP-IDF 6.0.1 build
for ESP32-S3 also passed with firmware version `2.0.0-rx-b64`.
Package validation passed for the three binary hashes in flash dry-run, esptool
image checksums/version, and all eight source hashes plus app hash/size in build
metadata. The dry-run did not access a serial port, and TX source is unchanged.
Run the native checks from the repository root:

```sh
python -m pytest tests/test_rx_csi_format.py -q
```

The tests require a native C compiler; set `CSI_HOST_CC` if needed.

The new image has not been installed or measured on physical boards. Earlier
40 FPS and `CSI_CTRL` tests apply to the previous integer-array RX image.
Queueing and base64 address a possible processing/UART bottleneck; varying CSI
length was not established as the cause of earlier losses. The inspected
2026-10-08 recordings contained 384-byte records only.

When the user is ready, validate CSI/base64 lengths, raw-value equivalence,
control GET/SET replies, receiver identities, counters, and measured packet
rates with the updated collector. Test multiple RX ports without pausing UART
draining while waiting for control ACKs. Refer to the
[collector handoff](../COLLECTOR_FORMAT_HANDOFF_KO.md) for the migration scope.
