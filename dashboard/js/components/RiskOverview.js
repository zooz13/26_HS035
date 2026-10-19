import { el, setChildren, badge } from '../utils/dom.js';
import { formatDateTime, percent } from '../utils/time.js';

const RISK_LABEL = {
  danger: '위험 높음',
  high: '위험 높음',
  risk_high: '위험 높음',
  warning: '주의',
  medium: '주의',
  risk_medium: '주의',
  low: '위험 낮음',
  risk_low: '위험 낮음',
  none: '정상',
  normal: '정상',
  ok: '정상',
  unknown: '판단 불가',
};

const RESIDUAL_LABEL = {
  high: '실내 잔류 위험이 높습니다.',
  possible: '실내 잔류 가능성이 있습니다.',
  low: '실내 잔류 위험이 낮습니다.',
  none: '실내 잔류 가능성이 낮습니다.',
  unknown: '실내 잔류 위험도 판단 결과를 확인하세요.',
};

function riskClass(riskLevel, residualPresence) {
  const level = String(riskLevel || '').toLowerCase();
  const residual = String(residualPresence || '').toLowerCase();

  if (['danger', 'high', 'risk_high'].includes(level) || residual === 'high') return 'danger';
  if (['warning', 'medium', 'risk_medium'].includes(level) || residual === 'possible') return 'warning';
  if (['low', 'risk_low'].includes(level) || residual === 'low') return 'low';
  if (['none', 'normal', 'ok'].includes(level) || residual === 'none') return 'normal';
  if (level === 'error') return 'error';
  return 'unknown';
}

function variantByRiskClass(value) {
  if (value === 'danger') return 'danger';
  if (value === 'warning') return 'warning';
  if (value === 'error') return 'error';
  if (value === 'low' || value === 'normal') return 'ok';
  return 'muted';
}

function detailItem(label, value) {
  return el('div', { className: 'risk-detail-item' }, [
    el('span', { className: 'metric-label', text: label }),
    el('strong', { text: value || '-' }),
  ]);
}

export function renderRiskOverview(container, state) {
  const risk = state.latestRiskAssessment;
  const latestResult = state.latestResult;

  const isBeforeDisaster = !risk;
  const cssClass = isBeforeDisaster ? 'normal' : riskClass(risk.risk_level, risk.residual_presence);
  const riskLevel = String(risk?.risk_level || 'normal').toLowerCase();
  const residualPresence = String(risk?.residual_presence || 'none').toLowerCase();
  const label = isBeforeDisaster ? '정상' : (RISK_LABEL[riskLevel] || RISK_LABEL[residualPresence] || risk.risk_level || '판단 불가');
  const badgeText = isBeforeDisaster ? 'NORMAL' : String(risk.risk_level || 'UNKNOWN').toUpperCase();
  const message = isBeforeDisaster
    ? '재난 이벤트 발생 전입니다. 현재는 최신 모델 결과를 모니터링하며 정상 상태로 표시합니다.'
    : (RESIDUAL_LABEL[residualPresence] || `실내 잔류 판단: ${risk.residual_presence || 'unknown'}`);
  const eventTime = isBeforeDisaster ? null : risk.event_timestamp;
  const resultTime = latestResult?.decided_at || latestResult?.window?.end_at || null;

  setChildren(container, [
    el('div', { className: `risk-overview risk-overview-${cssClass}` }, [
      el('div', { className: 'risk-circle-wrap' }, [
        el('div', { className: 'risk-circle' }, [
          el('span', { className: 'risk-circle-kicker', text: '위험도 분류' }),
          el('strong', { text: label }),
          badge(badgeText, variantByRiskClass(cssClass)),
        ]),
      ]),
      el('div', { className: 'risk-summary' }, [
        el('p', { className: 'eyebrow', text: 'UC-D3 / Disaster Risk' }),
        el('h2', { text: isBeforeDisaster ? '현재 위험도: 정상' : `현재 위험도: ${label}` }),
        el('p', { className: 'risk-message', text: message }),
        el('div', { className: 'risk-detail-grid' }, [
          detailItem('재난 이벤트 상태', isBeforeDisaster ? '대기 중' : '인위적 발생 / 화면 계산 완료'),
          detailItem('재난 이벤트 시각', isBeforeDisaster ? '-' : formatDateTime(eventTime)),
          detailItem('최근 모델 판단', resultTime ? formatDateTime(resultTime) : '-'),
          detailItem('Room / 장치', `${risk?.room_id || latestResult?.room_id || '-'} / ${risk?.device_id || latestResult?.device_id || '-'}`),
          detailItem('잔류 판단', isBeforeDisaster ? '정상' : (risk.residual_presence || '-')),
          detailItem('신뢰도', isBeforeDisaster ? '-' : percent(risk.confidence)),
        ]),
        el('p', {
          className: 'risk-note',
          text: isBeforeDisaster
            ? '인위적 재난 이벤트 버튼을 누르면 최신 모델 분류 결과를 기준으로 화면에서 위험도를 계산하고 원 색상이 변경됩니다.'
            : `판단 사유: ${risk.reason || '-'}`,
        }),
      ]),
    ]),
  ]);
}
