import { el, setChildren, badge, statusDot } from '../utils/dom.js';
import { formatDateTime, relativeTime } from '../utils/time.js';

const MODEL_LABEL = {
  running: '실행 중',
  stopped: '중지',
  error: '오류',
};

const CSI_LABEL = {
  receiving: '수신 중',
  disconnected: '수신 끊김',
};

const BASELINE_LABEL = {
  unset: '미설정',
  collecting: 'baseline 수집 중',
  calibration_collecting: 'calibration 수집 중',
  ready: '설정 완료',
  failed: '설정 실패',
};

const ROOM_STATE_LABEL = {
  uncalibrated: '미설정',
  baseline_collecting: 'baseline 수집 중',
  baseline_ready: 'baseline 준비 완료',
  calibration_collecting: 'calibration 수집 중',
  ready: '준비 완료',
  running: '실행 중',
  error: '오류',
};

function variantByStatus(value) {
  if (['running', 'receiving', 'ready', 'normal', 'baseline_ready'].includes(value)) return 'ok';
  if (['collecting', 'calibration_collecting', 'degraded', 'baseline_collecting', 'uncalibrated'].includes(value)) return 'warning';
  if (['error', 'failed', 'stopped', 'disconnected'].includes(value)) return 'error';
  return 'muted';
}

function dotByOverall(value) {
  if (value === 'normal') return 'green';
  if (value === 'degraded') return 'yellow';
  if (value === 'error') return 'red';
  return 'gray';
}

function metric({ label, value, subtext, variant = 'muted', dot = false }) {
  return el('div', { className: 'metric-card' }, [
    el('div', { className: 'metric-label', text: label }),
    el('div', { className: 'metric-value' }, [
      dot ? statusDot(dot) : null,
      el('span', { text: value }),
      badge(variant === 'ok' ? '정상' : variant === 'warning' ? '주의' : variant === 'error' ? '오류' : '확인', variant),
    ]),
    el('div', { className: 'metric-subtext', text: subtext || '-' }),
  ]);
}

export function renderSystemStatus(container, state) {
  const data = state.systemStatus;
  if (!data) {
    setChildren(container, [el('div', { className: 'empty-state', text: '아직 시스템 상태 데이터가 없습니다. 상태 조회를 누르거나 API 서버 연결을 확인하세요.' })]);
    return;
  }

  const model = data.model_server || {};
  const csi = data.csi_input || {};
  const baseline = data.baseline || state.baselineStatus || {};
  const setupState = baseline.setup_state || baseline.status;

  setChildren(container, [
    metric({
      label: '시스템 상태',
      value: data.overall_status === 'normal' ? '정상' : data.overall_status === 'degraded' ? '주의' : data.overall_status === 'error' ? '오류' : '확인 필요',
      subtext: `서버 기준 시각: ${formatDateTime(data.server_time)}`,
      variant: variantByStatus(data.overall_status),
      dot: dotByOverall(data.overall_status),
    }),
    metric({
      label: '모델 서버 상태',
      value: MODEL_LABEL[model.status] || model.status || '-',
      subtext: model.error_message || `마지막 추론: ${relativeTime(model.last_inference_at)} / Stage2: ${model.stage2_mode || '-'}`,
      variant: variantByStatus(model.status),
    }),
    metric({
      label: 'CSI 입력 상태',
      value: CSI_LABEL[csi.status] || csi.status || '-',
      subtext: `마지막 입력: ${relativeTime(csi.last_received_at)} / 장치 그룹: ${csi.device_id || '-'}`,
      variant: variantByStatus(csi.status),
    }),
    metric({
      label: '마지막 데이터 수신 시간',
      value: relativeTime(csi.last_received_at),
      subtext: formatDateTime(csi.last_received_at),
      variant: csi.status === 'receiving' ? 'ok' : 'error',
    }),
    metric({
      label: '마지막 모델 추론 시간',
      value: relativeTime(model.last_inference_at),
      subtext: formatDateTime(model.last_inference_at),
      variant: model.status === 'running' ? 'ok' : 'error',
    }),
    metric({
      label: 'Room setup / baseline 상태',
      value: ROOM_STATE_LABEL[setupState] || BASELINE_LABEL[baseline.status] || setupState || '-',
      subtext: baseline.baseline_id ? `baseline: ${baseline.baseline_id}` : 'baseline 설정 여부 확인',
      variant: variantByStatus(setupState || baseline.status),
    }),
  ]);
}
