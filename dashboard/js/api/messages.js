// WebSocket 메시지 생성 파일은 서버 API 정의서 반영 후 HTTP endpoint 정의 파일로 변경했습니다.
// 실제 호출은 ApiClient.js에서 수행합니다.

export const API_ENDPOINTS = {
  health: '/api/health',
  modelStatus: '/api/model/status',
  rooms: '/api/rooms',
  roomSetupStatus: (roomId) => `/api/rooms/${encodeURIComponent(roomId)}/setup/status`,
  roomBaselineStart: (roomId) => `/api/rooms/${encodeURIComponent(roomId)}/baseline/start`,
  roomCalibrationStart: (roomId) => `/api/rooms/${encodeURIComponent(roomId)}/calibration/start`,
  roomLatestResult: (roomId) => `/api/rooms/${encodeURIComponent(roomId)}/result/latest`,
  roomRiskAssessment: (roomId) => `/api/rooms/${encodeURIComponent(roomId)}/risk-assessment`,
  events: '/api/events',
};

export function makeBaselineStartBody({ durationSec = 300, windowDurationSec = 5 } = {}) {
  return {
    duration_sec: durationSec,
    window_duration_sec: windowDurationSec,
    mode: 'empty',
    requested_by: 'dashboard',
  };
}

export function makeRiskAssessmentBody({ eventTimestamp = new Date().toISOString(), recentPredictions = undefined } = {}) {
  const body = { event_timestamp: eventTimestamp };
  if (recentPredictions) body.recent_predictions = recentPredictions;
  return body;
}
