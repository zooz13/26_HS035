# 고정 RX Base64 출력: 수집기 담당자 인계

## 적용 대상과 현재 상태

이 문서는 [`team-ige-wae-andwae/firmware`](https://github.com/team-ige-wae-andwae/firmware)의
고정 RX **2.0.0-rx-b64** 출력을 수집기에 연결하기 위한 인계입니다. 작업용
로컬 폴더는 `firmware-rx-base64`일 수 있습니다. 수집기 구현은 다른 담당자의
작업이며 이 펌웨어 변경에는 포함하지 않습니다.

**현재 연결된 보드는 그대로 유지합니다. 수집기가 준비되고 사용자가 실험을
시작할 때까지 플래시하거나 설정을 임의로 바꾸지 마세요.** 새 RX 이미지는
실기기 미설치·미실측입니다. 이전 정수 배열 RX의 약 40 FPS와 제어 ACK 확인을
새 이미지의 검증 결과로 사용하면 안 됩니다.

2026-10-14 실제 C 파서/직렬화 함수의 네이티브 테스트 **5개**와
**ESP-IDF 6.0.1 / ESP32-S3 빌드**를 통과했습니다. 이미지 버전은
`2.0.0-rx-b64`입니다. 테스트는 1–768바이트 Base64 전체 복원, 28필드 형식,
큐 누락과 50/100/101번째 표본 경계의 gain 처리 순서 등을 확인합니다.
설치 dry-run의 바이너리 3개 해시, esptool 이미지 체크섬·버전, 빌드 정보의
소스 8개 및 앱 해시·크기도 확인했고 TX 소스 변경은 없습니다. Dry-run은
실제 시리얼 포트에 접근하지 않았습니다.
이 소프트웨어 검사는 실제 무선 수신률이나 수집기 연동 검증을 대신하지 않습니다.

TX 소스/기본 40 Hz, 4바이트 순번, 채널 11·HT40·MCS0 LGI, RX gain 설정,
`CSI_CTRL` 제어 명령은 유지합니다. 기존 TX는 다시 설치할 필요가 없습니다.
RX는 CSI 콜백에서 대기 없이 32개 큐에 복사하고 별도 태스크에서 Base64를 출력합니다.
큐가 꽉 차면 새 기록을 버리고 카운터를 증가시킵니다.
AGC/FFT gain은 콜백 시점에 즉시 읽고, 첫 100개 정상 CSI의 gain은 별도 고정
배열에 저장해 출력 큐가 꽉 차도 표본을 유지합니다. 각 행에는 수신 당시까지의
표본 개수를 남기고, 출력 태스크가 그 시점까지의 표본만 원래의
gain 기록→보정 계산 순서로 재현합니다. 큐에서 빠진 행의 표본도 포함하므로
나중에 받은 gain이 과거 행의 메타데이터에 영향을 주지 않습니다. 라이브러리는
50번째 표본부터 자동 baseline을 만들 수 있어 처음 100개 계수를 모두 1.0으로
가정하면 안 됩니다. raw CSI에는 이 계수를 곱하지 않습니다.

## 수집기에서 두 형식을 함께 지원

ESP32-S3 기존 CSV와 새 CSV는 **28필드**입니다. 순서는 그대로이고 행의
`type` 및 마지막 필드 이름/인코딩만 바뀝니다.

| 출력 | type | 마지막 필드 | 복원 방법 |
| --- | --- | --- | --- |
| 기존 RX | `CSI_DATA` | `data` | CSV quoted 문자열 안의 signed 정수 배열 |
| 새 RX | `CSI_DATA_B64` | `data_b64` | RFC 4648 Base64 → bytes → signed int8 |

새 헤더는 부팅 때 한 번 출력됩니다. 중간에 연결한 수집기도 행의 type과
정해진 스키마로 파싱할 수 있어야 합니다.

```text
type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,fft_gain,agc_gain,compensate_gain_x1000000,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data_b64
```

CSV 파서로 필드를 나눈 후 타입별로 마지막 필드만 디코딩합니다. 기존 배열에는
쉼표가 있으므로 단순 `split(',')`로 나누지 마세요. Base64는 엄격하게 검증하고
디코딩한 바이트 수가 `len`과 같은지 검사합니다. 각 바이트 `b`는 `b < 128`이면
그대로, 아니면 `b - 256`으로 signed int8가 됩니다. 예를 들어 `AAF/gP8=`은
기존 배열 `[0,1,127,-128,-1]`과 같습니다.

복원된 값은 gain 보정 전 원본 CSI입니다. `fft_gain`, `agc_gain`,
`compensate_gain_x1000000`은 기존과 같이 메타데이터이며 Base64에 미리 곱해진
값이 아닙니다. 별도 보정이 필요하면 분석 단계에서 정책을 정하고 기록하세요.

`len`은 **원본 바이트 수 1–768의 가변값**입니다. 원본 bytes, 길이, PHY/gain
메타데이터를 함께 보존하고 384바이트 고정 필터를 두지 마세요. 다른 길이를
조용히 버리거나 임의로 패딩/잘라서 동일한 부반송파 배열처럼 섞지 마세요.
`first_word=1`은 첫 4바이트가 무효라는 뜻입니다. 원본은 보존하고 분석에서
해당 바이트를 마스킹합니다. 기존 Gateway의 **384 I/Q 값·192 amplitude 고정
처리**는 별도 제약이므로 입력 파서뿐 아니라 특징 추출도 검토해야 합니다.

## 순번·수신기·시간 식별

- `id`는 고정 TX가 보낸 little-endian uint32 카운터입니다. 같은 TX의 연속
  송신 시도는 보통 1씩 증가하며 TX 재부팅 때 다시 시작합니다. 별도 RR4의
  전역 슬롯·seq 간격 4 규칙을 적용하지 마세요. 순번 공백에는 TX 송신 실패,
  무선 누락, RX 큐 폐기, 호스트 누락이 섞일 수 있어 전부 무선 손실로 단정하지 않습니다.
- 행의 `mac`은 **TX MAC**입니다. 각 RX는 UART 포트와 부팅 때 받은
  `CSI_CTRL_READY`의 `rx_mac`을 연결해 구분합니다. 여러 RX에 같은 TX MAC이
  보이는 것은 정상입니다.
- `local_timestamp`는 각 RX의 로컬 32비트 마이크로초 시계이며 약 71분마다
  넘칩니다. 보드 사이의 공통 절대시각으로 사용하지 마세요. 시간 펼침과
  재부팅 경계를 처리한 뒤 같은 RX에서 수신 간격을 계산합니다.
- 새 출력의 `id`와 `local_timestamp`는 unsigned 십진수입니다. 구형 출력은
  signed 형식이라 상위 비트가 켜지면 음수로 보일 수 있으므로, 구형 두
  필드는 `mod 2^32`로 복원한 뒤 순번·시간 넘침을 처리합니다.
- CSV에는 실험 session ID가 없습니다. 호스트 세션, RX MAC/포트, TX MAC,
  펌웨어 버전, 적용 전송률/전력, 수집 시각과 원본 로그를 따로 보존합니다.
  다른 RX에서 같은 패킷을 맞출 때는 동일 TX 부팅 구간의 `id`를 사용합니다.
- `rate` CSV 필드는 PHY 메타데이터이지 40 Hz 전송률이 아닙니다. 실제 TX 설정은
  `CSI_CTRL,GET,<request_id>`의 ACK에서 읽습니다. NVS 값이 소스 기본값보다 우선합니다.

이 출력은 별도 `new-firmware`의 RR4 **`CSI_DATA_V2`와 다른 스키마**입니다.
RR4의 `session/phase/slot/tx_id/rx_id`를 이 28필드에 끼워 넣지 마세요.

## 제어·진단·로그 분리

UART는 **921600 baud / 8N1**, 명령은 LF로 끝납니다. `CSI_CTRL_SENT`는 전송
중간 상태이고, 실제 적용 성공은 같은 request ID의 `CSI_CTRL_ACK`에서 판정합니다.
기존 GET/SET/RESET 형식과 ESP-NOW control version 1은 그대로입니다.
[전체 제어 규약](CONTROL_PROTOCOL.md)을 따르세요. RX는 부팅 후 바로 수신하며
RR4의 `NODE START/STOP` 명령은 없습니다.

새 RX는 기존 `CSI_CTRL_READY`에 더해 아래를 한 번 출력합니다.

```text
CSI_RX_READY,1,2.0.0-rx-b64,32,768
```

각 값은 RX 진단 규약 버전, 펌웨어 버전, CSI 큐 깊이, 최대 원본 길이입니다.
다음 명령은 RX 로컬 통계만 조회하며 TX에 전달하거나 설정을 변경하지 않습니다.

```text
CSI_RX,STATS,1001
CSI_RX_STATS,1001,matching_data,queued,output,queue_dropped,invalid_length,invalid_payload,control_filtered,min_len,max_len,status_queue_dropped
```

응답 예시는 값 대신 필드 이름을 보여줍니다. 카운터는 **RX 부팅 이후 누적이며
초기화 명령이 없습니다**. 수집 전후 차이로 계산하고 중간 재부팅을 표시하세요.
`matching_data`는 고정 TX의 정상 action/4바이트 순번을 가진 DATA이고,
`queued`, `queue_dropped`, `invalid_length`로 CSI 처리 결과를 나눕니다.
`output`은 출력한 행 수이므로 큐에 남은 수만큼 queued보다 작을 수 있습니다.
이 값은 펌웨어의 출력 완료이지 PC의 수신·파싱 성공 확인은 아닙니다.
`invalid_length`는 0/768 초과 길이 또는 null 버퍼, `invalid_payload`는 감지
payload 검증 실패, `control_filtered`는 제외한 제어 패킷입니다.
`min_len/max_len`은 거부 길이를 포함한 DATA 관측값이며 미관측 시 둘 다 0입니다.
`status_queue_dropped`는 RX의 TX 응답 큐 넘침입니다.

`CSI_CTRL_*`, `CSI_RX_*`, `GAIN`, 헤더, 부트/디버그 로그를 CSI 샘플로 세지
마세요. 원본 로그는 보존하고 malformed CSI와 정상 제어행을 구분합니다.
ACK를 기다리는 동안에도 모든 RX UART를 계속 읽어야 합니다. 구형 RX에는 새
통계 명령을 필수 요구하지 않으며, 수집 중 과도한 통계 폴링은 피합니다.

## 나중에 설치하고 확인할 순서

1. 두 형식의 정상/잘못된 CSV, Base64, 길이 불일치, 가변 길이, 무효 첫 워드,
   혼합 로그, 여러 RX·TX 재부팅을 수집기에서 검증합니다.
2. 사용자가 실험을 시작할 준비가 되면 같은 버전 `prebuilt/rx/`·`scripts/`를
   받고 `flash-rx.ps1 -DryRun`으로 SHA-256/명령을 검사한 뒤 RX만 설치합니다.
   기존 보드 백업을 보존합니다. RX마다 다시 빌드할 필요는 없습니다.
3. 실제 GET ACK가 40 Hz인지 확인하고, 원본 데이터와 진단을 함께 남겨 수신률,
   Base64/길이 오류, 큐/상태 응답 누락을 측정합니다. 그 다음 RX를 늘립니다.

과거 CSI 누락의 원인을 길이 변화로 확정하지 마세요. 확인한 2026-10-08 자료는
모두 384바이트였고, Wi-Fi 콜백의 긴 정수열 출력·대기는 병목 가능성이었습니다.
큐와 Base64는 그 부담을 줄이지만 무선 손실을 없애지는 않습니다. 길이 폐기·큐
넘침·파싱 오류가 0인 것과 전파 패킷 누락이 0인 것은 서로 다른 결과입니다.
