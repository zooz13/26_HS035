import { CONFIG } from '../config.js';

const ROOM_STATE_TO_BASELINE_STATUS = {
  uncalibrated: 'unset',
  baseline_collecting: 'collecting',
  baseline_ready: 'ready',
  calibration_collecting: 'calibration_collecting',
  ready: 'ready',
  running: 'ready',
  error: 'failed',
};

export function normalizeRoomRegistration(response = {}, requested = {}) {
  const roomId = response?.room_id || requested.roomId || requested.room_id || CONFIG.ROOM_ID;
  const roomName = response?.room_name || requested.roomName || requested.room_name || CONFIG.ROOM_NAME;
  const deviceGroupId = response?.device_group_id || requested.deviceGroupId || requested.device_group_id || CONFIG.DEVICE_GROUP_ID;
  return {
    room_id: roomId,
    room_name: roomName,
    device_group_id: deviceGroupId,
    status: 'registered',
    registered_at: response?.created_at || response?.registered_at || new Date().toISOString(),
    message: response?.message || '방 정보가 등록되었습니다. 이후 상태/결과/baseline API는 이 room_id 기준으로 호출됩니다.',
    raw: response,
  };
}

export function normalizeSystemStatus({ health, modelStatus, setupStatus, latestResult }) {
  const now = new Date().toISOString();
  const healthOk = isHealthy(health);
  const setupState = getSetupState(setupStatus);
  const baseline = normalizeBaselineStatus(setupStatus);
  const latestTimestamp = latestResult?.timestamp || latestResult?.window_end_time || latestResult?.decided_at || null;
  const csiReceiving = latestTimestamp ? Date.now() - new Date(latestTimestamp).getTime() <= CONFIG.CSI_STALE_MS : false;
  const modelServerStatus = healthOk ? normalizeModelStatus(modelStatus) : 'error';

  return {
    overall_status: healthOk && ['ready', 'running'].includes(setupState)
      ? 'normal'
      : healthOk
        ? 'degraded'
        : 'error',
    server_time: health?.server_time || health?.time || health?.timestamp || now,
    model_server: {
      status: modelServerStatus,
      last_inference_at: latestTimestamp,
      stage1_artifact_id: setupStatus?.stage1_artifact_id || modelStatus?.stage1_artifact_id || null,
      stage2_mode: setupStatus?.stage2_mode || modelStatus?.stage2_mode || null,
      error_message: healthOk ? null : 'Model Server health check 실패',
    },
    csi_input: {
      status: csiReceiving ? 'receiving' : 'disconnected',
      device_id: setupStatus?.device_group_id || CONFIG.DEVICE_GROUP_ID,
      last_received_at: latestTimestamp,
      fps: latestResult?.fps ?? null,
    },
    baseline,
    raw: {
      health,
      model_status: modelStatus,
      setup_status: setupStatus,
    },
  };
}

export function normalizeBaselineStatus(setupStatus = {}) {
  const setupState = getSetupState(setupStatus);
  const baselineStatus = ROOM_STATE_TO_BASELINE_STATUS[setupState] || setupStatus?.baseline_status || setupStatus?.status || 'unset';
  const fallbackDurationSec = setupState === 'baseline_collecting'
    ? CONFIG.BASELINE_DURATION_SEC
    : setupState === 'calibration_collecting'
      ? CONFIG.CALIBRATION_EXPECTED_WINDOWS * CONFIG.CALIBRATION_WINDOW_DURATION_SEC
      : null;

  return {
    status: baselineStatus,
    setup_state: setupState,
    room_id: setupStatus?.room_id || CONFIG.ROOM_ID,
    baseline_id: setupStatus?.baseline_id || null,
    calibration_id: setupStatus?.calibration_id || null,
    stage1_artifact_id: setupStatus?.stage1_artifact_id || null,
    stage2_mode: setupStatus?.stage2_mode || null,
    started_at: setupStatus?.baseline_started_at || setupStatus?.calibration_started_at || setupStatus?.started_at || null,
    completed_at: setupStatus?.baseline_completed_at || setupStatus?.calibration_completed_at || setupStatus?.completed_at || setupStatus?.created_at || null,
    updated_at: setupStatus?.updated_at || setupStatus?.created_at || null,
    duration_sec: setupStatus?.duration_sec || setupStatus?.baseline_duration_sec || setupStatus?.calibration_duration_sec || fallbackDurationSec,
    expected_windows: setupStatus?.expected_windows || setupStatus?.calibration_expected_windows || null,
    collected_windows: setupStatus?.collected_windows || setupStatus?.num_windows || setupStatus?.calibration_windows || null,
    window_duration_sec: setupStatus?.window_duration_sec || setupStatus?.calibration_window_duration_sec || null,
    message: setupMessage(setupState, setupStatus),
    raw: setupStatus,
  };
}

export function normalizeLatestResult(result) {
  if (!result) return null;

  const stage1 = result.stage1 || {};
  const stage2 = result.stage2 || {};
  const timestamp = result.timestamp || result.window_end_time || result.decided_at || null;
  const finalStatus = result.final_status || result.final?.label || inferFinalStatus(stage1, stage2);

  return {
    room_id: result.room_id || CONFIG.ROOM_ID,
    device_id: result.device_id || result.device_group_id || CONFIG.DEVICE_GROUP_ID,
    window_id: result.window_id || null,
    window: {
      start_at: result.window_start_time || result.window?.start_at || null,
      end_at: timestamp,
      duration_sec: result.window_duration_sec || result.window?.duration_sec || null,
    },
    stage1: {
      label: stage1.label || 'unknown',
      score: stage1.score ?? null,
      threshold: stage1.threshold ?? null,
      artifact_id: stage1.artifact_id || null,
      confidence: stage1.confidence ?? null,
    },
    stage2: {
      label: stage2.label ?? null,
      status: stage2.status || 'unknown',
      confidence: stage2.confidence ?? null,
      artifact_id: stage2.artifact_id || null,
      reason: stage2.reason || null,
    },
    final: {
      label: finalStatus,
      confidence: result.confidence ?? stage2.confidence ?? stage1.confidence ?? null,
    },
    final_status: finalStatus,
    decided_at: timestamp,
    raw: result,
  };
}

export function normalizeRiskAssessment(result) {
  if (!result) return null;

  return {
    room_id: result.room_id || CONFIG.ROOM_ID,
    device_id: result.device_id || CONFIG.DEVICE_GROUP_ID,
    event_timestamp: result.event_timestamp || new Date().toISOString(),
    residual_presence: result.residual_presence || 'unknown',
    risk_level: result.risk_level || 'unknown',
    confidence: result.confidence ?? null,
    reason: result.reason || null,
    occupancy_summary: result.occupancy_summary || {},
    motion_summary: result.motion_summary || {},
    limitations: result.limitations || [],
    raw: result,
  };
}

export function alertFromRiskAssessment(risk) {
  const level = riskLevelToAlertLevel(risk?.risk_level);
  const residualText = residualPresenceLabel(risk?.residual_presence);

  return {
    id: `risk_${risk?.room_id || CONFIG.ROOM_ID}_${Date.now()}`,
    level,
    title: level === 'DANGER' ? '[위험 알람]' : level === 'WARNING' ? '[주의 알람]' : '[위험도 평가]',
    message: `${residualText}${risk?.reason ? ` / 사유: ${risk.reason}` : ''}`,
    device_id: risk?.device_id || risk?.room_id || CONFIG.ROOM_ID,
    room_id: risk?.room_id || CONFIG.ROOM_ID,
    stage1_label: risk?.occupancy_summary?.recent_occupied ? 'occupied' : 'unknown',
    stage2_label: summarizeMotion(risk?.motion_summary),
    risk_level: risk?.risk_level || 'unknown',
    final_label: risk?.residual_presence || 'unknown',
    confidence: risk?.confidence ?? null,
    occurred_at: risk?.event_timestamp || new Date().toISOString(),
  };
}

export function normalizeEventLogs(response) {
  const items = Array.isArray(response) ? response : response?.items || response?.events || [];

  return items.map((event, index) => ({
    id: event.id || event.event_id || `event_${index}`,
    occurred_at: event.occurred_at || event.timestamp || event.event_timestamp || event.created_at || null,
    level: normalizeEventLevel(event.level || event.risk_level || event.severity),
    device_id: event.device_id || event.room_id || CONFIG.ROOM_ID,
    stage1_label: event.stage1_label || event.stage1?.label || event.occupancy_state || '-',
    stage2_label: event.stage2_label || event.stage2?.label || event.motion_state || event.stage2_risk_level || '-',
    final_label: event.final_label || event.final_status || event.residual_presence || event.risk_level || '-',
    message: event.message || event.reason || event.final_status || '-',
    raw: event,
  }));
}

export function apiErrorToAlert(error, title = '[시스템 오류]') {
  return {
    id: `api_error_${Date.now()}`,
    level: 'ERROR',
    title,
    message: error?.message || String(error),
    device_id: CONFIG.DEVICE_GROUP_ID,
    room_id: CONFIG.ROOM_ID,
    stage1_label: 'unknown',
    stage2_label: 'unknown',
    final_label: 'system_error',
    occurred_at: new Date().toISOString(),
  };
}

function isHealthy(health) {
  if (!health) return false;
  if (health.ok === true || health.healthy === true) return true;
  if (['ok', 'healthy', 'running', 'up'].includes(String(health.status || '').toLowerCase())) return true;
  return false;
}

function normalizeModelStatus(modelStatus = {}) {
  const rawStatus = String(modelStatus.status || modelStatus.model_status || 'running').toLowerCase();
  if (['running', 'ready', 'ok', 'healthy', 'loaded'].includes(rawStatus)) return 'running';
  if (['stopped', 'disabled'].includes(rawStatus)) return 'stopped';
  if (['error', 'failed'].includes(rawStatus)) return 'error';
  return 'running';
}

function getSetupState(setupStatus = {}) {
  return setupStatus?.setup_state || setupStatus?.room_state || setupStatus?.state || setupStatus?.status || 'uncalibrated';
}

function setupMessage(setupState, setupStatus) {
  if (setupStatus?.message) return setupStatus.message;
  if (setupState === 'uncalibrated') return '방 설정이 아직 완료되지 않았습니다.';
  if (setupState === 'baseline_collecting') return 'baseline 수집 상태입니다. 실제 수집과 완료 등록은 하드웨어/전처리기가 수행합니다.';
  if (setupState === 'baseline_ready') return 'baseline profile이 등록되었습니다. calibration 시작이 필요합니다.';
  if (setupState === 'calibration_collecting') return 'empty calibration window 수집 중입니다.';
  if (setupState === 'ready') return 'baseline과 calibration 설정이 완료되었습니다.';
  if (setupState === 'running') return '실시간 추론 실행 중입니다.';
  if (setupState === 'error') return '방 설정 또는 모델 서버 상태 오류입니다.';
  return '상태 확인 필요';
}

function inferFinalStatus(stage1, stage2) {
  if (stage1?.label === 'empty') return 'empty';
  if (stage1?.label === 'occupied' && stage2?.label === 'static_person') return 'occupied_static_person';
  if (stage1?.label === 'occupied' && stage2?.label === 'moving_person') return 'occupied_moving_person';
  if (stage1?.label === 'occupied') return 'occupied_detected';
  return 'unknown';
}

function riskLevelToAlertLevel(riskLevel) {
  const value = String(riskLevel || '').toLowerCase();
  if (['danger', 'high', 'risk_high'].includes(value)) return 'DANGER';
  if (['warning', 'medium', 'risk_medium', 'low', 'risk_low'].includes(value)) return 'WARNING';
  if (['error'].includes(value)) return 'ERROR';
  return 'INFO';
}

function residualPresenceLabel(value) {
  const map = {
    high: '실내 잔류 위험이 높습니다.',
    possible: '실내 잔류 가능성이 있습니다.',
    low: '실내 잔류 위험이 낮습니다.',
    none: '실내 잔류 가능성이 낮습니다.',
    unknown: '실내 잔류 위험도 판단 결과를 확인하세요.',
  };
  return map[value] || `실내 잔류 판단: ${value || 'unknown'}`;
}

function summarizeMotion(summary = {}) {
  if (summary.static_ratio !== undefined && summary.moving_ratio !== undefined) {
    return `static ${Math.round(summary.static_ratio * 100)}% / moving ${Math.round(summary.moving_ratio * 100)}%`;
  }
  return 'unknown';
}

function normalizeEventLevel(level) {
  const value = String(level || '').toLowerCase();
  if (['danger', 'high', 'risk_high'].includes(value)) return 'DANGER';
  if (['warning', 'medium', 'risk_medium', 'low', 'risk_low'].includes(value)) return 'WARNING';
  if (['error', 'failed'].includes(value)) return 'ERROR';
  return String(level || 'INFO').toUpperCase();
}
