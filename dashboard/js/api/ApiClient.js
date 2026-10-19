export class ApiError extends Error {
  constructor(message, { status = null, body = null, path = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.path = path;
  }
}

export class ApiClient {
  constructor(baseUrl) {
    this.baseUrl = String(baseUrl || '').replace(/\/$/, '');
  }

  async request(path, { method = 'GET', body = undefined, signal = undefined } = {}) {
    const url = `${this.baseUrl}${path}`;
    const options = {
      method,
      headers: {
        Accept: 'application/json',
      },
      signal,
    };

    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw new ApiError(`API 연결 실패: ${error.message}`, { path });
    }

    const text = await response.text();
    const parsedBody = text ? safeJsonParse(text) : null;

    if (!response.ok) {
      const serverMessage = parsedBody?.detail || parsedBody?.message || text || response.statusText;
      throw new ApiError(`API 오류 ${response.status}: ${serverMessage}`, {
        status: response.status,
        body: parsedBody || text,
        path,
      });
    }

    return parsedBody;
  }

  health() {
    return this.request('/api/health');
  }

  getModelStatus() {
    return this.request('/api/model/status');
  }

  registerRoom({ roomId, roomName, deviceGroupId }) {
    return this.request('/api/rooms', {
      method: 'POST',
      body: {
        room_id: roomId,
        room_name: roomName,
        device_group_id: deviceGroupId,
      },
    });
  }

  getSetupStatus(roomId) {
    return this.request(`/api/rooms/${encodeURIComponent(roomId)}/setup/status`);
  }

  startBaseline(roomId, { durationSec, windowDurationSec }) {
    return this.request(`/api/rooms/${encodeURIComponent(roomId)}/baseline/start`, {
      method: 'POST',
      body: {
        duration_sec: durationSec,
        window_duration_sec: windowDurationSec,
        mode: 'empty',
        requested_by: 'dashboard',
      },
    });
  }

  startCalibration(roomId, { baselineId, method, expectedWindows, windowDurationSec }) {
    return this.request(`/api/rooms/${encodeURIComponent(roomId)}/calibration/start`, {
      method: 'POST',
      body: {
        baseline_id: baselineId,
        method,
        expected_windows: expectedWindows,
        window_duration_sec: windowDurationSec,
      },
    });
  }

  async getLatestResult(roomId) {
    const response = await this.request(`/api/rooms/${encodeURIComponent(roomId)}/result/latest`);
    // Server wraps the room result as { room_id, latest_result: {...} | null }.
    // Unwrap to the inner result so normalizers receive the flat shape they expect.
    return response?.latest_result ?? null;
  }

  assessRisk(roomId, { eventTimestamp, recentPredictions = undefined }) {
    const body = {
      event_timestamp: eventTimestamp,
    };

    if (recentPredictions !== undefined) {
      body.recent_predictions = recentPredictions;
    }

    return this.request(`/api/rooms/${encodeURIComponent(roomId)}/risk-assessment`, {
      method: 'POST',
      body,
    });
  }

  getEvents({ limit = 50 } = {}) {
    return this.request(`/api/events?limit=${encodeURIComponent(limit)}`);
  }
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
