# ESP32-S3 CSI Firmware

ESP32-S3를 고정 TX와 RX로 사용해 Wi-Fi CSI를 수집하고, Gateway에서 TX의
패킷 전송률과 송신 전력 상한을 런타임에 변경하는 펌웨어입니다. RX 버전
**2.0.0-rx-b64**는 CSI를 32개 큐에 대기 없이 복사하고 별도 출력 태스크에서
Base64 CSV로 기록합니다. TX의 40 Hz 기본값, 채널/PHY, gain 설정과
`CSI_CTRL` 제어 프로토콜은 유지합니다.

**현재 연결된 보드는 변경하지 않습니다.** 수집기가 새 형식을 지원하고
사용자가 실험을 시작할 준비가 된 뒤 RX에 설치하세요. 이 고정 TX/RX
펌웨어와 별도 `new-firmware` 저장소의 RR4 순환 펌웨어는 서로 다릅니다.

## 구성

```text
Gateway PC --USB/UART--> RX --ESP-NOW control--> TX
Gateway PC <--CSI UART-- RX <--Wi-Fi packets----- TX
```

- `csi_send/`: TX 펌웨어. CSI 유도 패킷 송신 및 설정 저장/적용
- `csi_recv/`: RX 펌웨어. CSI 큐, Base64 UART 출력, Gateway 명령 중계
- `CONTROL_PROTOCOL.md`: UART 및 ESP-NOW 제어 프로토콜
- `COLLECTOR_FORMAT_HANDOFF_KO.md`: 기존/새 CSV를 지원할 수집기 담당자 인계
- `prebuilt/rx/`, `scripts/`: RX 배포 이미지와 빌드/설치 스크립트

## 기본 설정

- 패킷 전송률: `40 Hz`
- TX 전력 상한: `20 dBm`
- 허용 전송률: `1..200 Hz`
- 허용 전력 상한: `2..20 dBm`, `0.25 dBm` 단위
- RX UART baudrate: `921600`
- 대상 칩: `ESP32-S3`

`200 Hz`는 설정 허용 상한이며 UART 구간의 무손실 수신을 보장하지 않습니다.
기본 `40 Hz`에서 실제 FPS와 parse error를 확인한 뒤 단계적으로 높이십시오.
TX의 NVS에 저장된 설정이 소스 기본값보다 우선합니다. 실제 적용값은
`CSI_CTRL,GET,<request_id>`에 대한 TX ACK로 확인합니다.

## 바뀐 RX 출력

기존 `CSI_DATA`의 quoted 정수 배열을 새 `CSI_DATA_B64`의 `data_b64`로
바꿉니다. ESP32-S3 CSV는 기존 순서의 **28필드**이며 `fft_gain`,
`agc_gain`, `compensate_gain_x1000000`도 유지합니다. Base64를 디코딩한
signed int8 값은 기존 원본 CSI 배열과 같고 gain 보정을 적용한 값이 아닙니다.

`len`은 원본 CSI 바이트 수이며 **1–768바이트의 가변값**입니다. 수집기에서
384로 고정하거나 다른 길이를 조용히 버리지 마세요. 자세한 규약과 전환
절차는 [수집기 인계서](COLLECTOR_FORMAT_HANDOFF_KO.md), 정확한 필드는
[RX 문서](csi_recv/README.md)에 있습니다. RR4의 `CSI_DATA_V2`와는 다른 형식입니다.

## RX는 빌드 없이 설치

같은 버전의 `prebuilt/rx/`와 `scripts/`를 함께 받으면 Python/esptool 5.x만으로
고정 1TX·3RX 또는 1TX·4RX의 여러 RX에 같은 이미지를 설치할 수 있습니다.
보드마다 ESP-IDF를 설치하거나
다시 빌드할 필요가 없습니다. 기존 플래시/NVS 백업을 보관하고, 실제 포트에
맞춰 저장소 루트의 PowerShell에서 실행합니다.

```powershell
python -m pip install 'esptool>=5,<6'
.\scripts\flash-rx.ps1 -Port COM_RX -DryRun
# 수집기가 준비되고 사용자가 실험을 시작할 때:
.\scripts\flash-rx.ps1 -Port COM_RX
```

`-DryRun`은 체크섬과 설치 명령을 검사하고 장치에 쓰지 않습니다. 설치 속도는
기본 460800 baud이며 통신 오류가 있을 때 `-Baud 115200`으로 재시도합니다. 배포물의
`SHA256SUMS`와 빌드 정보를 함께 보관하세요. 다른 시리얼 모니터를 닫고 설치하며,
콘솔은 **921600 baud, 8N1**입니다. 이 RX 변경만을 위해 TX를 다시 설치할 필요는
없습니다. RX는 부팅 후 CSI 수신을 시작하며 RR4의 `NODE START/STOP`을 사용하지 않습니다.

## 빌드 및 플래시

소스를 수정할 때는 ESP-IDF **v6.0.1** 환경에서 빌드합니다. 새 RX의
빌드 검사와 실제 보드 검증은 아래에서 구분합니다.

Windows에서는 일부 ESP-IDF/CMake 도구가 한글 등 비 ASCII 경로에서
비정상 종료할 수 있습니다. `C:\Espressif\projects\firmware`처럼 영문 경로에
저장소를 복제하는 것을 권장합니다.

TX:

```powershell
cd csi_send
idf.py set-target esp32s3
idf.py build
idf.py -p COM_TX flash monitor
```

RX:

```powershell
# 저장소 루트에서 RX 배포 이미지 생성
.\scripts\build-rx.ps1
```

RX 빌드는 `esp_csi_gain_ctrl 0.1.5`를 사용합니다. 스크립트는 영문 staging
경로의 빌드 캐시를 재사용합니다. 설정을 바꿨다면 새 전용 경로를
`-StageDirectory C:\temp\ige-csi-rx-new`처럼 지정해 기존 `sdkconfig` 값이
우선하는 것을 피하세요. 성공한 배포물의 `build-info.json`,
`sdkconfig.build`, `SHA256SUMS`에 소스/설정/바이너리 정보를 남깁니다.

포트 이름은 PC에서 확인한 실제 포트로 바꾸십시오. RX 모니터는
`921600` baudrate를 사용합니다.

## 런타임 제어

Gateway는 RX UART에 `GET`, `SET`, `RESET` 명령을 보내고, RX는 ESP-NOW로
TX에 전달합니다. 설정 적용 성공 여부는 TX가 반환한 `CSI_CTRL_ACK`으로
판정합니다. 상세 형식은 [CONTROL_PROTOCOL.md](CONTROL_PROTOCOL.md)를
참조하십시오.

## 검증 상태

2026-10-14 실제 C 직렬화/파서 함수를 실행한 네이티브 테스트 **5개를 통과**했습니다.
1–768바이트 전체 Base64 왕복 복원, 28필드 헤더와 메타데이터, payload 검증,
로컬 통계 명령 파싱, 큐 누락과 50/100/101번째 표본 경계의 gain 처리 순서를
확인했습니다. **ESP-IDF 6.0.1 / ESP32-S3 빌드도 성공**했으며 이미지 버전은
`2.0.0-rx-b64`입니다.
설치 dry-run의 바이너리 3개 해시, esptool 이미지 체크섬·버전, 빌드 정보의
소스 8개 및 앱 해시·크기도 확인했습니다. TX 소스 변경은 없습니다.

새 Base64 RX 이미지는 **현재 보드에 설치하지 않았으며 실기기 미검증**입니다.
기존 정수 배열 RX에서 확인했던 부팅/CSI 수신, `GET`의 `40 Hz / 20 dBm`,
동일 값 `SET`의 TX ACK, 약 40 FPS 결과는 이전 이미지의 결과입니다.
새 이미지의 수신률·제어 응답·장시간 큐/파싱 오류는 후속 실험에서 확인해야 합니다.

과거 누락이 CSI 길이 변화 때문이었다고 확정하지 않았습니다. 확인한
2026-10-08 저장 자료는 모두 384바이트였습니다. 기존 RX의 Wi-Fi 콜백에서
긴 정수열을 출력하며 대기하는 처리가 병목 가능성이었으며, 이번 큐·Base64는
그 부담을 줄입니다. 이 변경이 무선 패킷 손실까지 없앤다는 뜻은 아닙니다.

## 저장소 관리

`sdkconfig.defaults`는 버전 관리하지만 생성된 `sdkconfig`, `build/`, 보드
전체 플래시 백업과 측정 데이터는 커밋하지 않습니다. RX 배포 이미지는
`prebuilt/rx/`의 체크섬 및 소스 정보와 함께 관리합니다. 수집기 구현은 이
변경에 포함하지 않습니다.

이 프로젝트는 Espressif의 `esp-csi` 예제를 기반으로 하며 원 라이선스는
[LICENSE](LICENSE)를 따릅니다.
