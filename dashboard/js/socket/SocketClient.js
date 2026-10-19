export class SocketClient extends EventTarget {
  constructor(url, { reconnectBaseMs = 800, reconnectMaxMs = 8000 } = {}) {
    super();
    this.url = url;
    this.reconnectBaseMs = reconnectBaseMs;
    this.reconnectMaxMs = reconnectMaxMs;
    this.reconnectTry = 0;
    this.ws = null;
    this.closedByUser = false;
  }

  connect() {
    this.closedByUser = false;
    this.#emitStatus('connecting');

    try {
      this.ws = new WebSocket(this.url);
    } catch (error) {
      this.#emitError(error);
      this.#scheduleReconnect();
      return;
    }

    this.ws.addEventListener('open', () => {
      this.reconnectTry = 0;
      this.#emitStatus('connected');
    });

    this.ws.addEventListener('message', (event) => {
      try {
        const message = JSON.parse(event.data);
        this.dispatchEvent(new CustomEvent('message', { detail: message }));
      } catch (error) {
        this.#emitError(new Error(`Socket JSON 파싱 실패: ${error.message}`));
      }
    });

    this.ws.addEventListener('error', () => {
      this.#emitStatus('error');
    });

    this.ws.addEventListener('close', () => {
      this.#emitStatus('disconnected');
      if (!this.closedByUser) this.#scheduleReconnect();
    });
  }

  close() {
    this.closedByUser = true;
    if (this.ws) this.ws.close();
  }

  send(message) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.#emitError(new Error('Socket이 아직 연결되지 않았습니다.'));
      return false;
    }

    this.ws.send(JSON.stringify(message));
    return true;
  }

  #scheduleReconnect() {
    const delay = Math.min(this.reconnectBaseMs * 2 ** this.reconnectTry, this.reconnectMaxMs);
    this.reconnectTry += 1;
    window.setTimeout(() => this.connect(), delay);
  }

  #emitStatus(status) {
    this.dispatchEvent(new CustomEvent('status', { detail: { status } }));
  }

  #emitError(error) {
    this.dispatchEvent(new CustomEvent('client_error', { detail: { error } }));
  }
}
