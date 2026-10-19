# Unified CSI node (ESP32-S3 / ESP-IDF 6.0.1)

One binary supports logical nodes A, B, C, and D. The default `RR4` mode rotates the sensing transmitter A → B → C → D, with the other three nodes receiving each DATA packet. At **20 Hz per node**, a complete four-slot cycle is nominally **50 ms** and each slot is **12.5 ms**. This covers all six unordered physical links and twelve directional observations. The legacy `1X3` and `2X2` modes remain available. There is no `1X4` mode or node E in this firmware.

Radio contention can extend slots and reduce the measured rate; the firmware reports these extensions rather than shortening another node's response window. The new RR4 image has not been tested on physical boards. Keep the current fixed TX/RX images for the initial fixed 1TX/3RX and 1TX/4RX experiments; install this image later when the round-robin experiment is ready. See [the Korean handoff](../HANDOFF_KO.md).

The firmware always boots stopped, including after configuration has been saved. An unconfigured board reports `UNSET,RR4,20,50` and sends no application packets. Existing saved `csi_node_v2` settings override the defaults, so explicitly configure all four boards after installing this version. This namespace is independent of legacy `csi_ctrl`; legacy rate/power settings are never loaded or erased. The partition-table offset stays `0xa000`, matching the existing sender/receiver projects.

## Build

In an ESP-IDF 6.0.1 shell:

```sh
cd new-firmware/csi_node
idf.py set-target esp32s3
idf.py build
```

The console is UART at **921600 baud, 8N1**. Before replacing an existing device image, retain its flash/NVS backup. Flashing this project changes that device's firmware; configuring its role is a separate UART command. The node uses channel 11, HT40 with the secondary channel below, MCS0 long guard interval, and power setting 80 quarter-dBm units.

## UART commands

Each command is ASCII followed by LF. Tokens and node IDs are case sensitive. Configure each physical board with a **different** ID:

```text
NODE CONFIG A RR4 20 50
NODE CONFIG B RR4 20 50
NODE CONFIG C RR4 20 50
NODE CONFIG D RR4 20 50
```

The four commands above go to four respective boards. `CONFIG` stops that board, saves its ID/mode/rate/phase duration, and leaves it stopped. `NODE STATUS` must show the intended unique ID and common mode/rate/phase duration before START.

| Mode | Meaning of `rate_hz` | Meaning of `phase_ms` | Supported settings |
| --- | --- | --- | --- |
| `RR4` | DATA sends per second from each node | One complete A/B/C/D cycle | Exactly `(5,200)`, `(8,125)`, `(10,100)`, `(20,50)` |
| `1X3` | DATA sends per second from A | Stored compatibility setting; phase stays zero | Rate 5–40; phase 1000–600000 ms, subject to constraints below |
| `2X2` | DATA sends per second from each active TX | One A/B or C/D role phase | Rate 5–40; phase 1000–600000 ms, subject to constraints below |

All modes require `1000000 % (rate * slot_multiplier) == 0`, with multiplier 4 for RR4, 1 for 1X3, and 2 for 2X2. RR4 additionally requires `rate <= 20` and `phase_ms * rate == 1000`; legacy modes require `(phase_ms * rate) % 1000 == 0`. These constraints preserve integer microsecond slots and complete cycles. `RR4 40 25` and 1 ms / 0.5 ms cycles are unsupported by the current timing guards.

To start, send `NODE START 1234` to B, C, and D **first**, then to A. Session is a shared, nonzero unsigned 32-bit integer. Use a new session number for every experiment. A begins slot 0 after a 100 ms startup delay. Only A starts the over-the-air schedule; the other nodes wait for grants. Absent nodes leave empty slots; they do not shorten the cycle. A two-board test cannot validate all twelve RR4 directional observations.

```text
NODE STATUS
NODE STOP
NODE CONFIG A 2X2 40 5000
NODE START 1235
```

Apply the new mode and same settings to every participating board, retaining its own ID. Arm receivers/other nodes before A. `STOP` is local and disables that board's radio; stop A to remove all subsequent grants. A remote node's already granted, in-flight packet can finish before its lease expires. Stopping every board ends the session completely. Restart after a fault with a fresh session on all participants.

## Replies and status

```text
NODE_ACK,CONFIG,OK,saved
NODE_ACK,START,OK,armed
NODE_ACK,STOP,OK,stopped
NODE_ACK,STATUS,OK,status
NODE_ACK,<operation>,ERROR,<reason>
```

Successful commands are followed by these four lines:

```text
NODE_STATUS,id,mode,rate_hz,phase_ms,state,session,phase,slot,tx_count,csi_count,csi_dropped,late_dropped,send_errors,mac,factory_mac
NODE_TIMING,received_grants,grant_age_min_us,grant_age_max_us,expired_grants
NODE_TX_TIMING,grant_completions,data_completions,grant_min_us,grant_max_us,data_min_us,data_max_us,guard_extensions,guard_extension_us,fault_packet,fault_age_us
NODE_CSI_STATS,min_len,max_len,invalid_length_dropped
```

Those examples name fields; actual lines contain values. `id` is A/B/C/D or `UNSET`. `state` is STOPPED, ARMED (started slave), RUNNING (started coordinator A), or FAULT. RUNNING/ARMED identify scheduling state, not the current sensing role. `tx_count` counts successful local DATA send callbacks, `csi_count` counts records queued, and `csi_dropped` counts full CSI queues. `late_dropped` counts expired/skipped submissions or deadline faults, and `send_errors` counts driver submission/send failures. Counts reset on START. Phase/slot identify the latest accepted/issued grant and remain available after STOP/FAULT; no-grant status reports zero.

`NODE_TX_TIMING` measures submission-to-callback durations separately for GRANT and DATA, including failed callbacks. Minimum zero means no completed transmission of that type. `guard_extensions` counts coordinator slot deadlines pushed forward after a delayed GRANT; `guard_extension_us` sums these shifts. `fault_packet` is NONE, GRANT, DATA, or SLOT; `fault_age_us` records the first fault's elapsed send time (zero for slot exhaustion). A watchdog fault before a callback can appear in fault fields without a completion count.

`NODE_CSI_STATS` observes only otherwise eligible DATA for the current grant/session/source/receiver mask. `min_len` and `max_len` include rejected lengths to expose an unexpected PHY length; before any matching DATA they are both zero. `invalid_length_dropped` counts matching callbacks with a null CSI buffer, zero length, or length above 768. These statistics reset on START. They are distinct from queue overflow in `csi_dropped`; neither measures every over-the-air loss.

Configured IDs map to locally administered MACs `02:43:53:49:02:01` through `02:43:53:49:02:04`. The immutable factory STA MAC is also reported to identify each physical board. These logical MACs must be unique in the experiment; two boards configured with A conflict. This explicit mapping lets every node reject packets whose logical sender and source MAC disagree.

`NODE_EVENT,BOOT,stopped` marks boot. `NODE_EVENT,FAULT,radio_deadline_or_slot_exhausted` marks a stopped radio after an overlong send or exhausted 32-bit slot counter. Fault recovery requires an explicit START; there is no automatic restart.

## Scheduling and packet safety

At `RR4 20 50`, the schedule repeats every four slots. `phase` is the cycle counter `slot / 4`, not a multi-second role phase:

| Global slot | Phase | Sensing TX | Sensing RX | Receiver mask |
| --- | --- | --- | --- | --- |
| 0 | 0 | A | B, C, D | `0x0e` |
| 1 | 0 | B | A, C, D | `0x0d` |
| 2 | 0 | C | A, B, D | `0x0b` |
| 3 | 0 | D | A, B, C | `0x07` |
| 4 | 1 | A | B, C, D | `0x0e` |

Nominal totals are 80 DATA packets and 80 control GRANTs per second. Each directional link receives 20 CSI records/s; each node receives 60 records/s; four nodes together receive 240 records/s. Three receive slots are 12.5 ms apart and a node receives no sensing DATA in its own TX slot, so 60 Hz is an average over the cycle. Relative to `2X2 40 5000`, the RF packet count is unchanged but the total recorded CSI count rises from 160 to 240/s. These are targets before losses and guard extensions, not guaranteed delivery rates.

For legacy comparison, at 40 Hz `1X3` has 25,000 µs slots, A sending one DATA packet per slot to B/C/D. `2X2` has 12,500 µs slots, alternating the two active transmitters. With 5000 ms phases:

| Global slots | Phase | Sensing TX | Sensing RX |
| --- | --- | --- | --- |
| 0–399 | 0 | A on even slots, B on odd slots | C and D |
| 400–799 | 1 | C on even slots, D on odd slots | A and B |
| 800–1199 | 2 | A on even slots, B on odd slots | C and D |

A broadcasts one GRANT per slot. Only its named transmitter may submit one DATA response, 1000–2500 µs after the grant. Slaves never transmit from their own periodic timers. Missing, duplicate, wrong-session, invalid-schedule, or expired grants do not permit DATA. Coordinator timing uses an absolute schedule and skips missed deadlines; it never sends catch-up bursts. Missing boards and isolated packet loss do not stop the schedule.

The grant carries the full session, phase, slot, sequence, mode/rate/phase duration, transmitter ID, and receiver mask. Receivers keep only DATA matching their current grant and receiver mask, within an 11,000 µs receive lease, once per slot. Control packets are excluded before CSI buffering. A still emits these short control grants while logically acting as a sensing receiver, including B/C/D slots in RR4.

Each radio has at most one pending ESP-NOW transmission. A DATA submission rechecks its grant and deadline immediately before calling the driver. DATA has an **8000 µs** completion watchdog. Coordinator GRANT has a separate **100,000 µs** driver-failure watchdog: a delayed GRANT stalls the schedule, and A issues neither its DATA response nor another GRANT while that send is pending. Slot IDs are not advanced or replaced during this wait. After its completion, the full response window below is reserved. Exceeding the corresponding watchdog faults that node and stops its radio. There are no application retransmissions.

After every GRANT completion, A sets the next slot deadline to `max(nominal_deadline, grant_completion + 11000 µs)`. This reserves 2500 µs for a response submission, 8000 µs for that sender's DATA completion, and a 500 µs guard; the longer GRANT watchdog does not increase another node's DATA allowance. A GRANT completing within the first 1500 µs of a nominal 12.5 ms slot leaves the target cadence unchanged. Slower GRANTs extend the next deadline. The extension carries forward, so the scheduler never bursts to catch up. Cycle/phase boundaries remain based on slot count: four slots per RR4 cycle, or 400 slots per `2X2 40 5000` phase. Contention can make the actual cycle longer than 50 ms or legacy phase longer than 5 seconds. Report measured per-link rates and cycle/phase spans alongside the nominal configuration.

TX/RX roles are selected from each grant; changing roles or reaching a phase boundary does not restart Wi-Fi and adds no separate switching barrier. The response and guard budgets apply on every slot. The 12.5 ms minimum slot is an implementation constraint here, not a measured lower bound for every ESP32 radio or a claim that faster sampling is scientifically unnecessary.

Grant age is measured from the RF receive timestamp, using `esp_wifi_internal_get_mac_clock_time() - rx_ctrl.timestamp` modulo 32 bits; callback-delivery delay cannot renew a lease. This private API is intentionally tied to the specified IDF version. `NODE_TIMING` exposes its measured ages and rejection count for real-board verification. Wi-Fi power saving is disabled. The ESP-NOW driver does not expose a hard per-frame RF deadline, so software guards and fault shutdown are not a hard real-time RF guarantee under arbitrary driver/interrupt stalls. Validate send-deadline and grant-age counters on the actual boards under channel interference.

## CSI CSV v2

The UART header and row prefix are:

```text
type,session,phase,slot,tx_id,rx_id,seq,rx_timestamp,rssi,channel,secondary_channel,sig_mode,mcs,bandwidth,noise_floor,first_word_invalid,csi_len,csi_b64
CSI_DATA_V2,...
```

`tx_id`/`rx_id` are letters. `seq` equals the **global slot**, not a contiguous per-transmitter counter. In RR4, the same TX appears every four slots and `phase = slot / 4`; a step of four is normal, not three missing packets. In 2X2, same-TX sequence steps are two within an active role phase; inactive phases are intentional gaps. Phase is always zero in 1X3. Preserve mode/rate/cycle settings in session metadata because they are not repeated in each CSI row.

An observation is identified by `session + slot + tx_id + rx_id`. Match one TX packet across receivers with `session + slot + tx_id`. The twelve directions represent six physical links; opposite directions should not automatically be combined because hardware gains can differ. `rx_timestamp` is the receiver's 32-bit local Wi-Fi microsecond timestamp and wraps approximately every 71 minutes; it is not a shared wall clock. Use a receiver's unwrapped timestamp for local cadence and slot IDs for cross-board association. RSSI/noise are signed dBm fields. `bandwidth` is the IDF `cwb` bit, and `first_word_invalid=1` means the first four CSI bytes are invalid.

`csi_b64` is RFC 4648 base64 of exactly `csi_len` raw CSI bytes, with valid lengths **1–768**. Validate base64 strictly and require the decoded length to match the row. Convert each byte to signed int8 (`b` if `b < 128`, otherwise `b - 256`); for example `AAF/gP8=` represents `[0,1,127,-128,-1]`. The firmware does not filter to a fixed 384-byte length. Preserve raw bytes, length, PHY metadata, and the invalid-word flag. Do not silently discard other valid lengths, pad/truncate them into one shape, or combine incompatible PHY layouts. Retain the first four bytes when `first_word_invalid=1`, but mask them as invalid during analysis. The firmware performs no gain compensation.

Base64 uses 512 characters for 384 CSI bytes and 1024 for 768 bytes, plus CSV metadata. Compact encoding and the output queue reduce UART pressure at 921600 baud; they do not prevent RF loss. RR4 targets 60 records/s per node, while legacy 2X2 targets 80 records/s at an active receiver. A 768-byte legacy stream has little UART headroom; monitor `NODE_CSI_STATS`, `csi_dropped`, and host parsing counts, and avoid sustained status polling during collection.

Variable CSI length has not been established as the cause of earlier losses: the inspected 2026-10-08 saved records were all 384 bytes. Lengthy integer printing and waits inside the legacy RX Wi-Fi callback were a possible bottleneck. Queueing and base64 mitigate that processing pressure; the new changes have not been installed on the current fixed-role boards.

The Wi-Fi CSI callback validates the bounded ESP-NOW action body using the IDF `payload_len`, copies a record into a 32-entry queue with a zero wait, and returns. A separate task performs base64 encoding and all CSV printing. Queue overflow increments counters instead of blocking the Wi-Fi task. `NODE_*` diagnostics and boot logs are not CSI records.

ESP-NOW framing is based on the [Espressif v6.0 protocol description](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32/api-reference/network/esp_now.html). The parser validates the category, OUIs, vendor IE size/type/version, protocol header, and full packet size before accessing fields. Only a single unfragmented vendor IE is accepted; all protocol packets are 36 bytes.

## Verification

On 2026-10-14, the native C protocol test (`1 passed`), ESP-IDF 6.0.1 build, version 3.0.0 image inspection, checksums, and flash dry-run passed. The new RR4 image has **not been flashed or tested on physical boards**. Those checks do not validate four-board RF behavior, receive rates, or loss rates. Current fixed TX/RX boards are intentionally retained for the earlier experiments.

On 2026-10-14, the preceding 1X3/2X2 image on two ESP32-S3 boards configured as A and C passed a physical pair test:
301 A-to-C records in an 8-second `1X3` test; 499 A-to-C and 373 C-to-A
records across phases 0–4 in a 24-second `2X2` test. No malformed CSI,
firmware CSI queue overflow, send errors, or fault state was observed in
the final run. Stopping A stopped further C transmissions. Both boards
retain configuration and boot STOPPED after reset.

Those results apply to the previous image only, with B/D absent. They do not validate the new RR4 image or a full four-board/long-duration run. `2X2` measured schedule rate was approximately 39.05–39.09 Hz per
active transmitter; recorded receive rate was 35.75–37.09 Hz. Radio/grant loss
remains, and the timing guard extended the schedule by about 0.555 seconds
over the run. Do not interpret zero queue overflow as zero over-the-air loss.

```sh
cd new-firmware
python -m pytest tests/test_node_protocol.py -q
```

The test compiles the actual firmware protocol header with a native C compiler. It covers short-frame truncation, legacy and RR4 schedule counts and masks, configuration boundaries, wrong-session/source/schedule rejection, lease boundaries, MAC-clock rollover, base64 round trips for all valid CSI lengths, and next-slot guards. A firmware build checks the IDF API integration. When physical RR4 testing is authorized, additionally verify all twelve directional streams, correct sequence step four, UART integrity, CSI lengths, `NODE_TIMING` ages, `NODE_TX_TIMING` durations/extensions, reboot-stopped behavior, and no continuing DATA when grants stop. Preserve raw logs and settings alongside measured link rates.
