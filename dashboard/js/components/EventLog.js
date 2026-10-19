import { el, setChildren, badge } from '../utils/dom.js';
import { formatDateTime } from '../utils/time.js';

function levelVariant(level) {
  if (level === 'DANGER') return 'danger';
  if (level === 'WARNING') return 'warning';
  if (level === 'ERROR') return 'error';
  return 'muted';
}

export function renderEventLog(tbody, state) {
  const logs = state.eventLogs || [];
  if (logs.length === 0) {
    setChildren(tbody, [
      el('tr', {}, [
        el('td', { colspan: '7' }, [el('div', { className: 'empty-state', text: '조회된 이벤트 로그가 없습니다.' })]),
      ]),
    ]);
    return;
  }

  setChildren(tbody, logs.map((log) => el('tr', {}, [
    el('td', { text: formatDateTime(log.occurred_at) }),
    el('td', {}, [badge(log.level || '-', levelVariant(log.level))]),
    el('td', { text: log.device_id || '-' }),
    el('td', { text: log.stage1_label || '-' }),
    el('td', { text: log.stage2_label || log.stage2_risk_level || '-' }),
    el('td', { text: log.final_label || log.risk_level || '-' }),
    el('td', { text: log.message || '-' }),
  ])));
}
