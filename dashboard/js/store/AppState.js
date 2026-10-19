import { CONFIG } from '../config.js';

export class AppState extends EventTarget {
  constructor() {
    super();
    this.state = {
      socketStatus: 'idle', // 기존 UI 호환용 이름. HTTP API 연결 상태를 담습니다.
      roomInfo: {
        room_id: CONFIG.ROOM_ID,
        room_name: CONFIG.ROOM_NAME,
        device_group_id: CONFIG.DEVICE_GROUP_ID,
        status: 'idle',
        registered_at: null,
        message: '아직 이번 화면에서 방 등록 API를 호출하지 않았습니다.',
      },
      systemStatus: null,
      latestResult: null,
      latestRiskAssessment: null,
      alerts: [],
      eventLogs: [],
      manualDisasterEvent: null,
      baselineStatus: null,
      lastError: null,
    };
  }

  get snapshot() {
    return structuredClone(this.state);
  }

  setSocketStatus(socketStatus) {
    this.#patch({ socketStatus });
  }

  setRoomInfo(roomInfo) {
    this.#patch({ roomInfo: { ...(this.state.roomInfo || {}), ...(roomInfo || {}) } });
  }

  setSystemStatus(systemStatus) {
    this.#patch({ systemStatus });
  }

  setLatestResult(latestResult) {
    this.#patch({ latestResult });
  }

  setRiskAssessment(latestRiskAssessment) {
    this.#patch({ latestRiskAssessment });
  }

  clearRiskAssessment() {
    this.#patch({ latestRiskAssessment: null, manualDisasterEvent: null });
  }

  setManualDisasterEvent(manualDisasterEvent) {
    this.#patch({ manualDisasterEvent });
  }

  setBaselineStatus(baselineStatus) {
    const previous = this.state.baselineStatus || {};
    const setupState = baselineStatus?.setup_state || baselineStatus?.status;
    const isCollecting = ['baseline_collecting', 'calibration_collecting', 'collecting'].includes(setupState);

    this.#patch({
      baselineStatus: isCollecting
        ? {
          ...previous,
          ...baselineStatus,
          started_at: baselineStatus?.started_at || previous.started_at || null,
          duration_sec: baselineStatus?.duration_sec || previous.duration_sec || null,
          expected_windows: baselineStatus?.expected_windows || previous.expected_windows || null,
          collected_windows: baselineStatus?.collected_windows ?? previous.collected_windows ?? null,
          window_duration_sec: baselineStatus?.window_duration_sec || previous.window_duration_sec || null,
        }
        : baselineStatus,
    });
  }

  prependAlert(alert) {
    const alerts = [alert, ...this.state.alerts].slice(0, 20);
    this.#patch({ alerts });
  }

  clearAlerts() {
    this.#patch({ alerts: [] });
  }

  setEventLogs(eventLogs) {
    this.#patch({ eventLogs });
  }

  prependEventLog(eventLog) {
    const eventLogs = [eventLog, ...this.state.eventLogs].slice(0, 100);
    this.#patch({ eventLogs });
  }

  setError(error) {
    this.#patch({ lastError: error?.message || String(error) });
  }

  handleServerMessage(message) {
    switch (message.type) {
      case 'room_registered':
        this.setRoomInfo(message.payload);
        break;
      case 'system_status':
        this.setSystemStatus(message.payload);
        if (message.payload?.baseline) this.setBaselineStatus(message.payload.baseline);
        break;
      case 'inference_result':
        this.setLatestResult(message.payload);
        break;
      case 'risk_assessment':
        this.setRiskAssessment(message.payload);
        break;
      case 'manual_disaster_event':
        this.setManualDisasterEvent(message.payload);
        break;
      case 'alert_event':
        this.prependAlert(message.payload);
        break;
      case 'event_log_list':
        this.setEventLogs(message.payload?.items || []);
        break;
      case 'event_log_item':
        this.prependEventLog(message.payload);
        break;
      case 'baseline_status':
        this.setBaselineStatus(message.payload);
        break;
      case 'error':
      case 'api_error':
        this.setError(new Error(message.payload?.message || '서버 오류'));
        break;
      case 'pong':
      case 'ack':
        break;
      default:
        console.warn('알 수 없는 메시지 타입:', message);
    }
  }

  #patch(partial) {
    this.state = { ...this.state, ...partial };
    this.dispatchEvent(new CustomEvent('change', { detail: this.snapshot }));
  }
}
