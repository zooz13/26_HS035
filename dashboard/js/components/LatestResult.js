import { el, setChildren } from '../utils/dom.js';
import { formatDateTime, percent } from '../utils/time.js';

const STAGE1_LABEL = {
  empty: '사람 없음',
  occupied: '사람 있음',
  unknown: '판단 불가',
};

const STAGE2_LABEL = {
  static_person: '정적 인원',
  moving_person: '움직임',
  unknown: '판단 불가',
  skipped: '수행 안 함',
  disabled: '비활성화',
  null: '수행 안 함',
};

const STAGE2_STATUS_LABEL = {
  ok: '정상 수행',
  skipped: 'Stage 1 empty로 생략',
  disabled: '비활성화',
  unknown: '확인 필요',
};

const FINAL_LABEL = {
  empty: '사람 없음',
  occupied_detected: '사람 있음',
  occupied_static_person: '사람 있음 / 정적',
  occupied_moving_person: '사람 있음 / 움직임',
  unknown: '판단 불가',
};

function resultCard({ title, label, confidence, meta }) {
  const hasConfidence = confidence !== null && confidence !== undefined && !Number.isNaN(Number(confidence));
  const safeConfidence = hasConfidence ? Math.max(0, Math.min(1, Number(confidence))) : 0;

  return el('div', { className: 'result-card' }, [
    el('strong', { text: title }),
    el('div', { className: 'result-main', text: label || '-' }),
    el('div', { className: 'result-meta', text: `신뢰도 ${hasConfidence ? percent(confidence) : '-'}` }),
    el('div', { className: 'confidence-bar' }, [
      el('div', { className: 'confidence-fill', style: `width: ${safeConfidence * 100}%` }),
    ]),
    el('div', { className: 'result-meta', text: meta || '' }),
  ]);
}

export function renderLatestResult(container, state) {
  const data = state.latestResult;
  if (!data) {
    setChildren(container, [el('div', { className: 'empty-state', text: '아직 모델 분류 결과가 없습니다. 결과 조회를 누르거나 서버 처리를 기다리세요.' })]);
    return;
  }

  const stage1 = data.stage1 || {};
  const stage2 = data.stage2 || {};
  const finalResult = data.final || {};
  const decidedAt = data.decided_at || data.window?.end_at;
  const stage2LabelKey = stage2.label ?? stage2.status ?? 'unknown';

  setChildren(container, [
    resultCard({
      title: 'Stage 1 / 재실 여부',
      label: STAGE1_LABEL[stage1.label] || stage1.label,
      confidence: stage1.confidence,
      meta: `score: ${stage1.score ?? '-'} / threshold: ${stage1.threshold ?? '-'} / 판단 시각: ${formatDateTime(decidedAt)}`,
    }),
    resultCard({
      title: 'Stage 2 / 정적·움직임',
      label: STAGE2_LABEL[stage2LabelKey] || stage2LabelKey,
      confidence: stage2.confidence,
      meta: `${STAGE2_STATUS_LABEL[stage2.status] || stage2.status || '-'}${stage2.reason ? ` / ${stage2.reason}` : ''}`,
    }),
    resultCard({
      title: '최종 모델 판단',
      label: FINAL_LABEL[finalResult.label] || finalResult.label,
      confidence: finalResult.confidence,
      meta: `final_status: ${data.final_status || '-'} / room: ${data.room_id || '-'}`,
    }),
  ]);
}
