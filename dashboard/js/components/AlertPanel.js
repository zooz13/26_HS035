import { el, setChildren, badge } from '../utils/dom.js';
import { formatDateTime } from '../utils/time.js';

const ALERTS_PER_PAGE = 3;
let currentPage = 1;
let lastFirstAlertId = null;

function alertVariant(level) {
  if (level === 'DANGER') return 'danger';
  if (level === 'WARNING') return 'warning';
  if (level === 'ERROR') return 'error';
  return 'muted';
}

function getAlertId(alert) {
  return alert?.id || `${alert?.level || ''}_${alert?.occurred_at || ''}_${alert?.message || ''}`;
}

function renderAlertItem(alert) {
  return el('article', { className: `alert-item ${alert.level || ''}` }, [
    el('div', { className: 'alert-title' }, [
      el('span', { text: alert.title || '[알람]' }),
      badge(alert.level || 'INFO', alertVariant(alert.level)),
    ]),
    el('div', { className: 'alert-message', text: alert.message || '-' }),
    el('div', {
      className: 'result-meta',
      text: `장치: ${alert.device_id || alert.room_id || '-'} / 위험도: ${alert.risk_level || alert.final_label || '-'} / 발생 시각: ${formatDateTime(alert.occurred_at)}`,
    }),
  ]);
}

function renderPagination(container, state, totalPages, totalCount) {
  const pageInfo = `페이지 ${currentPage} / ${totalPages} · 총 ${totalCount}개`;

  const prevButton = el('button', {
    className: 'pagination-button',
    text: '이전',
    type: 'button',
  });

  const nextButton = el('button', {
    className: 'pagination-button',
    text: '다음',
    type: 'button',
  });

  prevButton.disabled = currentPage <= 1;
  nextButton.disabled = currentPage >= totalPages;

  prevButton.addEventListener('click', () => {
    currentPage = Math.max(1, currentPage - 1);
    renderAlertPanel(container, state);
  });

  nextButton.addEventListener('click', () => {
    currentPage = Math.min(totalPages, currentPage + 1);
    renderAlertPanel(container, state);
  });

  return el('div', { className: 'pagination-bar' }, [
    el('span', { className: 'pagination-info', text: pageInfo }),
    el('div', { className: 'pagination-actions' }, [prevButton, nextButton]),
  ]);
}

export function renderAlertPanel(container, state) {
  const alerts = state.alerts || [];
  const firstAlertId = getAlertId(alerts[0]);

  if (alerts.length > 0 && firstAlertId !== lastFirstAlertId) {
    currentPage = 1;
    lastFirstAlertId = firstAlertId;
  }

  if (alerts.length === 0) {
    currentPage = 1;
    lastFirstAlertId = null;
    setChildren(container, [el('div', { className: 'empty-state', text: '현재 표시할 알람이 없습니다.' })]);
    return;
  }

  const totalPages = Math.max(1, Math.ceil(alerts.length / ALERTS_PER_PAGE));
  currentPage = Math.min(Math.max(currentPage, 1), totalPages);

  const startIndex = (currentPage - 1) * ALERTS_PER_PAGE;
  const pageAlerts = alerts.slice(startIndex, startIndex + ALERTS_PER_PAGE);
  const children = pageAlerts.map(renderAlertItem);

  if (totalPages > 1) {
    children.push(renderPagination(container, state, totalPages, alerts.length));
  }

  setChildren(container, children);
}
