import { CONFIG } from './config.js';
import { ApiClient } from './api/ApiClient.js';
import {
  normalizeRoomRegistration,
  normalizeSystemStatus,
  normalizeBaselineStatus,
  normalizeLatestResult,
  normalizeRiskAssessment,
  alertFromRiskAssessment,
  normalizeEventLogs,
  apiErrorToAlert,
} from './api/normalizers.js';
import { AppState } from './store/AppState.js';
import { renderRiskOverview } from './components/RiskOverview.js';
import { renderRoomRegistration } from './components/RoomRegistration.js';
import { renderSystemStatus } from './components/SystemStatus.js';
import { renderLatestResult } from './components/LatestResult.js';
import { renderEventLog } from './components/EventLog.js';
import { renderBaselinePanel } from './components/BaselinePanel.js';
import {
  makeMockRoomRegistered,
  makeMockSystemStatus,
  makeMockInferenceResult,
  makeMockEventLogs,
  makeMockBaselineCollecting,
  makeMockBaselineReady,
  makeMockCalibrationCollecting,
  makeMockCalibrationReady,
} from './mock/mockMessages.js';

const query = new URLSearchParams(window.location.search);
CONFIG.MOCK_MODE = CONFIG.MOCK_MODE || ['1', 'true', 'yes'].includes(String(query.get('mock') || '').toLowerCase());

const state = new AppState();
const api = new ApiClient(CONFIG.API_BASE_URL);

const elements = {
  connectionBox: document.getElementById('connectionBox'),
  connectionText: document.getElementById('connectionText'),
  riskOverviewPanel: document.getElementById('riskOverviewPanel'),
  roomRegistrationForm: document.getElementById('roomRegistrationForm'),
  roomRegistrationPanel: document.getElementById('roomRegistrationPanel'),
  roomIdInput: document.getElementById('roomIdInput'),
  roomNameInput: document.getElementById('roomNameInput'),
  deviceGroupIdInput: document.getElementById('deviceGroupIdInput'),
  registerRoomBtn: document.getElementById('registerRoomBtn'),
  systemStatusPanel: document.getElementById('systemStatusPanel'),
  latestResultPanel: document.getElementById('latestResultPanel'),
  baselinePanel: document.getElementById('baselinePanel'),
  eventLogBody: document.getElementById('eventLogBody'),
  refreshStatusBtn: document.getElementById('refreshStatusBtn'),
  refreshResultBtn: document.getElementById('refreshResultBtn'),
  clearAlertsBtn: document.getElementById('clearAlertsBtn'),
  loadLogsBtn: document.getElementById('loadLogsBtn'),
  resetBaselineBtn: document.getElementById('resetBaselineBtn'),
  startCalibrationBtn: document.getElementById('startCalibrationBtn'),
  manualDisasterBtn: document.getElementById('manualDisasterBtn'),
};

function updateConnectionUi(apiStatus) {
  const dot = elements.connectionBox.querySelector('.dot');
  dot.className = 'dot dot-gray';

  const labelMap = {
    idle: 'API 대기 중',
    connecting: 'API 요청 중',
    connected: CONFIG.MOCK_MODE ? 'Mock 모드 실행 중' : 'API 연결됨',
    disconnected: 'API 연결 끊김',
    error: 'API 오류',
  };

  if (apiStatus === 'connected') dot.className = 'dot dot-green';
  else if (apiStatus === 'connecting') dot.className = 'dot dot-yellow';
  else if (apiStatus === 'error' || apiStatus === 'disconnected') dot.className = 'dot dot-red';

  elements.connectionText.textContent = labelMap[apiStatus] || apiStatus;
}

function renderAll(snapshot) {
  updateConnectionUi(snapshot.socketStatus);
  renderRiskOverview(elements.riskOverviewPanel, snapshot);
  renderRoomRegistration(elements.roomRegistrationPanel, snapshot);
  renderSystemStatus(elements.systemStatusPanel, snapshot);
  renderLatestResult(elements.latestResultPanel, snapshot);
  renderBaselinePanel(elements.baselinePanel, snapshot);
  renderEventLog(elements.eventLogBody, snapshot);

  const status = snapshot.baselineStatus?.status;
  const setupState = snapshot.baselineStatus?.setup_state;
  const baselineId = snapshot.baselineStatus?.baseline_id;
  const calibrationId = snapshot.baselineStatus?.calibration_id;
  const baselineBusy = ['collecting'].includes(status) || setupState === 'baseline_collecting';
  const calibrationBusy = status === 'calibration_collecting' || setupState === 'calibration_collecting';
  const canStartCalibration = Boolean(baselineId)
    && ['baseline_ready', 'ready', 'running'].includes(setupState)
    && !baselineBusy
    && !calibrationBusy;

  elements.resetBaselineBtn.disabled = baselineBusy || calibrationBusy;
  elements.startCalibrationBtn.disabled = !canStartCalibration;
  elements.startCalibrationBtn.textContent = calibrationBusy
    ? 'calibration 수집 중...'
    : !baselineId
      ? 'baseline 완료 후 calibration 가능'
      : calibrationId
        ? 'calibration 재시작 요청'
        : 'calibration 시작 요청';
  elements.registerRoomBtn.disabled = snapshot.roomInfo?.status === 'pending';
}

function bindUiEvents() {
  elements.roomRegistrationForm.addEventListener('submit', (event) => {
    event.preventDefault();
    registerRoomFromForm();
  });

  elements.refreshStatusBtn.addEventListener('click', () => refreshSystemStatus());
  elements.refreshResultBtn.addEventListener('click', () => refreshLatestResult());
  elements.loadLogsBtn.addEventListener('click', () => refreshEventLogs());
  elements.clearAlertsBtn.addEventListener('click', () => {
    state.clearAlerts();
    state.clearRiskAssessment();
  });
  elements.resetBaselineBtn.addEventListener('click', () => startBaselineCollection());
  elements.startCalibrationBtn.addEventListener('click', () => startCalibrationCollection());
  elements.manualDisasterBtn.addEventListener('click', () => triggerManualDisasterEvent());
}

function initRoomForm() {
  elements.roomIdInput.value = CONFIG.ROOM_ID;
  elements.roomNameInput.value = CONFIG.ROOM_NAME;
  elements.deviceGroupIdInput.value = CONFIG.DEVICE_GROUP_ID;
}

function readRoomForm() {
  return {
    roomId: elements.roomIdInput.value.trim(),
    roomName: elements.roomNameInput.value.trim(),
    deviceGroupId: elements.deviceGroupIdInput.value.trim(),
  };
}

function applyRoomConfig(room) {
  CONFIG.ROOM_ID = room.room_id || room.roomId || CONFIG.ROOM_ID;
  CONFIG.ROOM_NAME = room.room_name || room.roomName || CONFIG.ROOM_NAME;
  CONFIG.DEVICE_GROUP_ID = room.device_group_id || room.deviceGroupId || CONFIG.DEVICE_GROUP_ID;
}

function validateRoomInput(room) {
  if (!room.roomId) return 'Room ID를 입력하세요.';
  if (!room.roomName) return 'Room 이름을 입력하세요.';
  if (!room.deviceGroupId) return 'Device Group ID를 입력하세요.';
  return null;
}

async function registerRoomFromForm() {
  const room = readRoomForm();
  const validationMessage = validateRoomInput(room);

  if (validationMessage) {
    state.handleServerMessage({
      type: 'alert_event',
      payload: apiErrorToAlert(new Error(validationMessage), '[방 등록 입력 오류]'),
    });
    return;
  }

  if (CONFIG.MOCK_MODE) {
    const message = makeMockRoomRegistered(room);
    applyRoomConfig(message.payload);
    state.handleServerMessage(message);
    state.handleServerMessage(makeMockSystemStatus());
    state.handleServerMessage(makeMockInferenceResult());
    return;
  }

  state.setRoomInfo({
    room_id: room.roomId,
    room_name: room.roomName,
    device_group_id: room.deviceGroupId,
    status: 'pending',
    message: 'Model Server에 방 등록 요청 중입니다.',
  });

  try {
    await runApiTask(async () => {
      const response = await api.registerRoom(room);
      const normalized = normalizeRoomRegistration(response, room);
      applyRoomConfig(normalized);
      state.handleServerMessage({ type: 'room_registered', payload: normalized });

      await Promise.allSettled([
        refreshSystemStatus({ silent: true }),
        refreshLatestResult({ silent: true }),
        refreshEventLogs({ silent: true }),
      ]);
    }, { title: '[방 등록 오류]' });
  } catch (error) {
    state.setRoomInfo({
      status: 'failed',
      message: error?.message || '방 정보 등록에 실패했습니다.',
    });
  }
}

async function initApi() {
  initRoomForm();

  if (CONFIG.MOCK_MODE) {
    state.setSocketStatus('connected');
    state.handleServerMessage(makeMockSystemStatus());
    state.handleServerMessage(makeMockInferenceResult());
    state.handleServerMessage(makeMockEventLogs());

    window.setInterval(() => state.handleServerMessage(makeMockSystemStatus()), CONFIG.STATUS_POLL_MS);
    window.setInterval(() => state.handleServerMessage(makeMockInferenceResult()), CONFIG.RESULT_POLL_MS);
    return;
  }

  state.setSocketStatus('connecting');

  if (CONFIG.AUTO_REGISTER_ROOM) {
    try {
      await runApiTask(async () => {
        const response = await api.registerRoom({
          roomId: CONFIG.ROOM_ID,
          roomName: CONFIG.ROOM_NAME,
          deviceGroupId: CONFIG.DEVICE_GROUP_ID,
        });
        const normalized = normalizeRoomRegistration(response, {
          roomId: CONFIG.ROOM_ID,
          roomName: CONFIG.ROOM_NAME,
          deviceGroupId: CONFIG.DEVICE_GROUP_ID,
        });
        state.handleServerMessage({ type: 'room_registered', payload: normalized });
      }, { silent: true });
    } catch (error) {
      // 이미 등록된 room이면 서버가 400 "room already exists"를 반환한다.
      // 자동 등록 실패는 치명적이지 않으므로(기존 room을 그대로 사용) 폴링을 계속 진행한다.
      // 이 catch가 없으면 throw가 initApi 전체를 중단시켜 폴링이 시작되지 않고
      // 연결 표시가 "API 오류"로 고정되는 버그가 있었다.
      console.warn('자동 방 등록 건너뜀(이미 존재하거나 일시 오류):', error?.message || error);
    }
  }

  await Promise.allSettled([
    refreshSystemStatus(),
    refreshLatestResult(),
    refreshEventLogs(),
  ]);

  window.setInterval(() => refreshSystemStatus({ silent: true }), CONFIG.STATUS_POLL_MS);
  window.setInterval(() => refreshLatestResult({ silent: true }), CONFIG.RESULT_POLL_MS);
}

async function refreshSystemStatus({ silent = false } = {}) {
  if (CONFIG.MOCK_MODE) {
    state.handleServerMessage(makeMockSystemStatus());
    return;
  }

  const task = async () => {
    const [healthResult, modelResult, setupResult, latestResult] = await Promise.allSettled([
      api.health(),
      api.getModelStatus(),
      api.getSetupStatus(CONFIG.ROOM_ID),
      api.getLatestResult(CONFIG.ROOM_ID),
    ]);

    const health = unwrapSettled(healthResult);
    const modelStatus = unwrapSettled(modelResult);
    const setupStatus = unwrapSettled(setupResult);
    const latestRaw = unwrapSettled(latestResult);

    const systemStatus = normalizeSystemStatus({ health, modelStatus, setupStatus, latestResult: latestRaw });
    state.handleServerMessage({ type: 'system_status', payload: systemStatus });

    if (setupStatus) {
      state.handleServerMessage({ type: 'baseline_status', payload: normalizeBaselineStatus(setupStatus) });
    }
  };

  await runApiTask(task, { title: '[상태 조회 오류]', silent });
}

async function refreshLatestResult({ silent = false } = {}) {
  if (CONFIG.MOCK_MODE) {
    state.handleServerMessage(makeMockInferenceResult());
    return;
  }

  await runApiTask(async () => {
    const result = await api.getLatestResult(CONFIG.ROOM_ID);
    state.handleServerMessage({ type: 'inference_result', payload: normalizeLatestResult(result) });
  }, { title: '[결과 조회 오류]', silent });
}

async function refreshEventLogs({ silent = false } = {}) {
  if (CONFIG.MOCK_MODE) {
    state.handleServerMessage(makeMockEventLogs());
    return;
  }

  await runApiTask(async () => {
    const result = await api.getEvents({ limit: CONFIG.EVENT_LOG_LIMIT });
    state.handleServerMessage({ type: 'event_log_list', payload: { items: normalizeEventLogs(result) } });
  }, { title: '[로그 조회 오류]', silent });
}

async function startBaselineCollection() {
  if (CONFIG.MOCK_MODE) {
    const collecting = makeMockBaselineCollecting();
    state.handleServerMessage(collecting);
    window.setTimeout(() => state.handleServerMessage(makeMockBaselineReady(collecting.payload.started_at)), 2500);
    return;
  }

  await runApiTask(async () => {
    const startedAt = new Date().toISOString();
    const response = await api.startBaseline(CONFIG.ROOM_ID, {
      durationSec: CONFIG.BASELINE_DURATION_SEC,
      windowDurationSec: CONFIG.BASELINE_WINDOW_DURATION_SEC,
    });
    state.handleServerMessage({
      type: 'baseline_status',
      payload: normalizeBaselineStatus({
        ...response,
        room_id: CONFIG.ROOM_ID,
        setup_state: response?.setup_state || response?.room_state || response?.state || 'baseline_collecting',
        status: response?.status || 'baseline_collecting',
        started_at: response?.started_at || response?.baseline_started_at || startedAt,
        duration_sec: response?.duration_sec || CONFIG.BASELINE_DURATION_SEC,
        window_duration_sec: response?.window_duration_sec || CONFIG.BASELINE_WINDOW_DURATION_SEC,
      }),
    });
    await refreshSystemStatus({ silent: true });
  }, { title: '[baseline 시작 오류]' });
}

async function startCalibrationCollection() {
  if (!CONFIG.MOCK_MODE) {
    await refreshSystemStatus({ silent: true });
  }

  const baseline = state.snapshot.baselineStatus || {};
  const baselineId = baseline.baseline_id;
  const setupState = baseline.setup_state || baseline.status;

  if (!baselineId) {
    state.handleServerMessage({
      type: 'alert_event',
      payload: apiErrorToAlert(new Error('baseline ID가 없어 calibration을 시작할 수 없습니다.'), '[calibration 시작 오류]'),
    });
    return;
  }

  if (['baseline_collecting', 'calibration_collecting', 'collecting'].includes(setupState)) {
    state.handleServerMessage({
      type: 'alert_event',
      payload: apiErrorToAlert(new Error(`현재 room 상태가 ${setupState}라 calibration을 시작할 수 없습니다.`), '[calibration 시작 오류]'),
    });
    return;
  }

  if (baseline.calibration_id) {
    const proceed = window.confirm('기존 calibration이 있습니다. 같은 baseline 기준으로 calibration을 다시 시작할까요?');
    if (!proceed) return;
  }

  if (CONFIG.MOCK_MODE) {
    const collecting = makeMockCalibrationCollecting(baselineId);
    state.handleServerMessage(collecting);
    window.setTimeout(() => state.handleServerMessage(makeMockCalibrationReady(baselineId)), 2500);
    return;
  }

  await runApiTask(async () => {
    const startedAt = new Date().toISOString();
    const response = await api.startCalibration(CONFIG.ROOM_ID, {
      baselineId,
      method: CONFIG.CALIBRATION_METHOD,
      expectedWindows: CONFIG.CALIBRATION_EXPECTED_WINDOWS,
      windowDurationSec: CONFIG.CALIBRATION_WINDOW_DURATION_SEC,
    });
    state.handleServerMessage({
      type: 'baseline_status',
      payload: normalizeBaselineStatus({
        ...response,
        room_id: CONFIG.ROOM_ID,
        baseline_id: response?.baseline_id || baselineId,
        setup_state: response?.setup_state || response?.room_state || response?.state || 'calibration_collecting',
        status: response?.status || 'calibration_collecting',
        started_at: response?.started_at || response?.calibration_started_at || startedAt,
        duration_sec: response?.duration_sec || CONFIG.CALIBRATION_EXPECTED_WINDOWS * CONFIG.CALIBRATION_WINDOW_DURATION_SEC,
        expected_windows: response?.expected_windows || CONFIG.CALIBRATION_EXPECTED_WINDOWS,
        window_duration_sec: response?.window_duration_sec || CONFIG.CALIBRATION_WINDOW_DURATION_SEC,
      }),
    });
    await refreshSystemStatus({ silent: true });
  }, { title: '[calibration 시작 오류]' });
}

async function triggerManualDisasterEvent() {
  const occurredAt = new Date().toISOString();
  const eventPayload = {
    occurred_at: occurredAt,
    source: 'dashboard_manual_button',
    message: '대시보드에서 인위적으로 재난 이벤트를 발생시켰습니다.',
  };

  state.handleServerMessage({
    type: 'manual_disaster_event',
    payload: eventPayload,
  });

  if (CONFIG.MOCK_MODE) {
    const risk = buildManualRiskAssessment(state.snapshot.latestResult, occurredAt);
    applyRiskAssessmentToState(risk);
    return;
  }

  elements.manualDisasterBtn.disabled = true;

  try {
    await runApiTask(async () => {
      const response = await api.assessRisk(CONFIG.ROOM_ID, {
        eventTimestamp: occurredAt,
      });
      const risk = normalizeRiskAssessment(response);
      applyRiskAssessmentToState(risk);
    }, { title: '[위험도 평가 오류]' });
  } finally {
    elements.manualDisasterBtn.disabled = false;
  }
}

function applyRiskAssessmentToState(risk) {
  const alert = alertFromRiskAssessment(risk);

  state.handleServerMessage({ type: 'risk_assessment', payload: risk });
  state.handleServerMessage({ type: 'alert_event', payload: alert });
  state.handleServerMessage({
    type: 'event_log_item',
    payload: {
      id: alert.id,
      occurred_at: alert.occurred_at,
      level: alert.level,
      device_id: alert.device_id,
      room_id: alert.room_id,
      stage1_label: alert.stage1_label,
      stage2_label: alert.stage2_label,
      final_label: alert.final_label,
      risk_level: alert.risk_level,
      message: alert.message,
    },
  });
}

function buildManualRiskAssessment(latestResult, eventTimestamp) {
  const stage1Label = latestResult?.stage1?.label || 'unknown';
  const stage2Label = latestResult?.stage2?.label || 'unknown';
  const confidence = latestResult?.final?.confidence ?? latestResult?.stage2?.confidence ?? latestResult?.stage1?.confidence ?? null;

  let residualPresence = 'unknown';
  let riskLevel = 'unknown';
  let reason = 'manual_disaster_event_no_latest_result';

  if (stage1Label === 'empty') {
    residualPresence = 'none';
    riskLevel = 'normal';
    reason = 'manual_disaster_event_but_room_empty';
  } else if (stage1Label === 'occupied' && stage2Label === 'static_person') {
    residualPresence = 'high';
    riskLevel = 'danger';
    reason = 'manual_disaster_event_static_person_detected';
  } else if (stage1Label === 'occupied' && stage2Label === 'moving_person') {
    residualPresence = 'possible';
    riskLevel = 'warning';
    reason = 'manual_disaster_event_moving_person_detected';
  } else if (stage1Label === 'occupied') {
    residualPresence = 'possible';
    riskLevel = 'warning';
    reason = 'manual_disaster_event_occupied_detected';
  }

  return {
    room_id: latestResult?.room_id || CONFIG.ROOM_ID,
    device_id: latestResult?.device_id || CONFIG.DEVICE_GROUP_ID,
    event_timestamp: eventTimestamp,
    residual_presence: residualPresence,
    risk_level: riskLevel,
    confidence: confidence ?? (riskLevel === 'unknown' ? null : 0.8),
    reason,
    occupancy_summary: {
      recent_occupied: stage1Label === 'occupied',
      occupied_ratio: stage1Label === 'occupied' ? 1.0 : 0.0,
      empty_ratio: stage1Label === 'empty' ? 1.0 : 0.0,
    },
    motion_summary: {
      static_ratio: stage2Label === 'static_person' ? 1.0 : 0.0,
      moving_ratio: stage2Label === 'moving_person' ? 1.0 : 0.0,
      motion_state: stage2Label,
    },
    limitations: latestResult ? ['dashboard_manual_disaster_event'] : ['no_latest_model_result', 'dashboard_manual_disaster_event'],
    source: 'dashboard_manual_button',
  };
}

async function runApiTask(task, { title = '[API 오류]', silent = false } = {}) {
  if (!silent) state.setSocketStatus('connecting');

  try {
    const result = await task();
    state.setSocketStatus('connected');
    return result;
  } catch (error) {
    state.setSocketStatus('error');
    state.setError(error);
    if (!silent) state.handleServerMessage({ type: 'alert_event', payload: apiErrorToAlert(error, title) });
    throw error;
  }
}

function unwrapSettled(result) {
  if (result.status === 'fulfilled') return result.value;
  return null;
}

state.addEventListener('change', (event) => renderAll(event.detail));
renderAll(state.snapshot);
bindUiEvents();
window.setInterval(() => renderAll(state.snapshot), 1000);
initApi().catch((error) => console.error(error));
