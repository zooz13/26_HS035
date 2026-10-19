import { el, setChildren, badge } from '../utils/dom.js';
import { formatDateTime } from '../utils/time.js';

const STATUS_LABEL = {
    idle: '등록 전',
    pending: '등록 요청 중',
    registered: '등록 완료',
    failed: '등록 실패',
};

function statusVariant(status) {
    if (status === 'registered') return 'ok';
    if (status === 'pending' || status === 'idle') return 'warning';
    if (status === 'failed') return 'error';
    return 'muted';
}

export function renderRoomRegistration(container, state) {
    const room = state.roomInfo;

    if (!room) {
        setChildren(container, [el('div', { className: 'empty-state', text: '등록된 방 정보가 없습니다.' })]);
        return;
    }

    const statusText = STATUS_LABEL[room.status] || room.status || '상태 확인';

    setChildren(container, [
        el('div', { className: 'room-summary-box' }, [
            el('div', { className: 'room-summary-head' }, [
                el('span', { className: 'metric-label', text: '현재 API 기준 Room' }),
            ]),
            el('dl', {}, [
                el('dt', { text: '등록 상태' }),
                el('dd', {}, badge(statusText, statusVariant(room.status))),
                el('dt', { text: '등록 시간' }),
                el('dd', { text: formatDateTime(room.registered_at) }),
            ]),
        ]),
    ]);
}
