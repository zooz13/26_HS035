// 이 파일을 js/config.js 로 복사한 뒤 환경에 맞게 값을 수정하세요.
//   - Windows:  Copy-Item js/config.example.js js/config.js
//   - Linux:    cp js/config.example.js js/config.js
// js/config.js 는 .gitignore 에 포함되어 커밋되지 않습니다(환경별 로컬 설정).
export const CONFIG = {
  // 모델 서버 API 베이스 URL.
  //   - 대시보드를 모델 서버가 같은 origin(/)에서 정적 서빙하면 '' (상대경로)로 둡니다. (현재 배포 구성)
  //   - 별도 origin/포트면 'https://api.example.com' 형태로 지정합니다. 이 경우 모델 서버에 CORS 설정이 필요합니다.
  API_BASE_URL: '',
  MOCK_MODE: false,
  ROOM_ID: 'room_101',
  ROOM_NAME: '실험실 101',
  DEVICE_GROUP_ID: 'esp_group_101',
  STATUS_POLL_MS: 3000,
  RESULT_POLL_MS: 2000,
  // 페이지 로드 시 ROOM_ID를 자동 등록 시도합니다. 이미 존재하면 서버가 400을 반환하지만,
  // 자동 등록 실패는 무시하고 기존 room으로 폴링을 계속합니다.
  AUTO_REGISTER_ROOM: true,
  EVENT_LOG_LIMIT: 50,
  CSI_STALE_MS: 15000,
  BASELINE_DURATION_SEC: 300,
  BASELINE_WINDOW_DURATION_SEC: 5,
  // 모델 서버가 허용하는 calibration method만 사용해야 합니다:
  //   'guided_baseline_merge' | 'empty_quantile_threshold' | 'no_calibration'
  // (과거 'percentile' 값은 서버가 거부하여 calibration 시작 버튼이 400으로 실패했습니다.)
  CALIBRATION_METHOD: 'guided_baseline_merge',
  CALIBRATION_EXPECTED_WINDOWS: 12,
  CALIBRATION_WINDOW_DURATION_SEC: 5,
};
