import { el, setChildren, badge } from '../utils/dom.js';
import { formatDateTime } from '../utils/time.js';

const LABEL = {
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

function variant(status) {
  if (['ready', 'running', 'baseline_ready'].includes(status)) return 'ok';
  if (['collecting', 'calibration_collecting', 'baseline_collecting', 'uncalibrated'].includes(status)) return 'warning';
  if (status === 'failed' || status === 'error') return 'error';
  return 'muted';
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '-';
  const value = Math.max(0, Math.ceil(Number(seconds)));
  const min = Math.floor(value / 60);
  const sec = value % 60;
  if (min <= 0) return `${sec}초`;
  return `${min}분 ${String(sec).padStart(2, '0')}초`;
}

function buildProgressInfo(baseline, setupState) {
  const isCollecting = ['baseline_collecting', 'calibration_collecting', 'collecting'].includes(setupState)
    || ['collecting', 'calibration_collecting'].includes(baseline.status);
  if (!isCollecting) return null;

  const startedAt = baseline.started_at ? new Date(baseline.started_at) : null;
  const startedMs = startedAt && !Number.isNaN(startedAt.getTime()) ? startedAt.getTime() : null;
  const durationSec = Number(baseline.duration_sec);
  const hasDuration = durationSec > 0;
  const elapsedSec = startedMs ? Math.max(0, (Date.now() - startedMs) / 1000) : null;
  const remainingSec = elapsedSec !== null && hasDuration ? Math.max(0, durationSec - elapsedSec) : null;
  const progress = elapsedSec !== null && hasDuration ? Math.max(0, Math.min(1, elapsedSec / durationSec)) : null;
  const expectedEnd = startedMs && hasDuration ? new Date(startedMs + durationSec * 1000).toISOString() : null;
  const kind = setupState === 'calibration_collecting' || baseline.status === 'calibration_collecting'
    ? 'calibration'
    : 'baseline';

  return {
    kind,
    progress,
    elapsedSec,
    remainingSec,
    durationSec: hasDuration ? durationSec : null,
    expectedEnd,
  };
}

function renderProgressPanel(baseline, setupState) {
  const progress = buildProgressInfo(baseline, setupState);
  if (!progress) return null;

  const percentValue = progress.progress === null ? null : Math.round(progress.progress * 100);
  const percentText = percentValue === null ? '진행률 계산 중' : `${percentValue}%`;
  const progressStyle = `width: ${percentValue ?? 0}%`;
  const title = progress.kind === 'calibration' ? 'Calibration 수집 진행' : 'Baseline 수집 진행';

  return el('div', { className: 'collection-progress' }, [
    el('div', { className: 'collection-progress-head' }, [
      el('strong', { text: title }),
      badge(percentText, 'warning'),
    ]),
    el('div', { className: 'collection-progress-bar' }, [
      el('div', { className: 'collection-progress-fill', style: progressStyle }),
    ]),
    el('div', { className: 'collection-progress-grid' }, [
      el('div', {}, [
        el('span', { className: 'metric-label', text: '경과' }),
        el('strong', { text: formatDuration(progress.elapsedSec) }),
      ]),
      el('div', {}, [
        el('span', { className: 'metric-label', text: '남은 시간' }),
        el('strong', { text: formatDuration(progress.remainingSec) }),
      ]),
      el('div', {}, [
        el('span', { className: 'metric-label', text: '전체 예상' }),
        el('strong', { text: formatDuration(progress.durationSec) }),
      ]),
      el('div', {}, [
        el('span', { className: 'metric-label', text: '예상 종료' }),
        el('strong', { text: formatDateTime(progress.expectedEnd) }),
      ]),
    ]),
    baseline.expected_windows
      ? el('p', {
        className: 'collection-progress-note',
        text: `수집 window: ${baseline.collected_windows ?? 0} / ${baseline.expected_windows}`,
      })
      : null,
  ]);
}

export function renderBaselinePanel(container, state) {
  const baseline = state.baselineStatus || state.systemStatus?.baseline || null;
  if (!baseline) {
    setChildren(container, [el('div', { className: 'empty-state', text: 'setup/baseline 상태 데이터가 없습니다.' })]);
    return;
  }

  const setupState = baseline.setup_state || baseline.status;

  setChildren(container, [
    el('div', { className: 'baseline-box' }, [
      el('div', {}, [
        el('span', { className: 'metric-label', text: '현재 setup 상태 ' }),
        badge(ROOM_STATE_LABEL[setupState] || LABEL[baseline.status] || setupState || '-', variant(setupState || baseline.status)),
      ]),
      renderProgressPanel(baseline, setupState),
      el('dl', {}, [
        el('dt', { text: 'Room' }),
        el('dd', { text: baseline.room_id || '-' }),
        el('dt', { text: 'Baseline ID' }),
        el('dd', { text: baseline.baseline_id || '-' }),
        el('dt', { text: 'Calibration ID' }),
        el('dd', { text: baseline.calibration_id || '-' }),
        el('dt', { text: 'Stage1 Artifact' }),
        el('dd', { text: baseline.stage1_artifact_id || '-' }),
        el('dt', { text: 'Stage2 Mode' }),
        el('dd', { text: baseline.stage2_mode || '-' }),
        el('dt', { text: '완료/갱신' }),
        el('dd', { text: formatDateTime(baseline.completed_at || baseline.updated_at) }),
        el('dt', { text: '메시지' }),
        el('dd', { text: baseline.message || '-' }),
      ]),
    ]),
  ]);
}
