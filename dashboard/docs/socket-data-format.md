# Deprecated: WebSocket 데이터 포맷 문서

서버 API 정의서 반영 후 현재 대시보드는 WebSocket을 사용하지 않습니다.

현재 연동 방식은 HTTP API polling입니다. 자세한 내용은 `docs/server-api-integration.md`를 참고하세요.

주요 API:

- `GET /api/health`
- `GET /api/model/status`
- `GET /api/rooms/{room_id}/setup/status`
- `POST /api/rooms/{room_id}/baseline/start`
- `GET /api/rooms/{room_id}/result/latest`
- 인위적 재난 이벤트 버튼은 API 호출 없이 화면 내부에서 처리
- `GET /api/events`
