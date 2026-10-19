"use strict";

const STATUS_URL = "status.json";
const REFRESH_MS = 1000;
const STALE_MS = 5000;
const FIRMWARE_API = "/api/firmware";

function getPath(object, path) {
  return path.split(".").reduce((value, key) => {
    if (value === null || value === undefined) return null;
    return value[key];
  }, object);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${hours}h ${minutes}m ${secs}s`;
}

function formatValue(value, format, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  if (format === "duration") return formatDuration(value);
  if (format === "number") return `${Number(value).toFixed(2)}${suffix}`;
  if (format === "integer") return Number(value).toLocaleString();
  if (format === "score") return Number(value).toFixed(4);
  if (format === "enabled") return value ? "Enabled" : "Disabled";
  if (format === "connected") return value ? "Connected" : "Disconnected";
  if (format === "yesno") return value ? "Yes" : "No";
  if (format === "met") return value ? "Met" : "Not met";
  if (format === "created") return value ? "Created" : "Not created";
  if (format === "skipped") return value ? "Skipped" : "Processed";
  if (format === "success") return value === true ? "Success" : value === false ? "Failed" : "-";
  if (Array.isArray(value)) return value.join(" × ");
  return `${value}${suffix}`;
}

function applyTone(element, value, format) {
  element.classList.remove("ok", "warn", "error", "muted");
  if (value === null || value === undefined) {
    element.classList.add("muted");
    return;
  }
  if (["enabled", "connected", "yesno", "met", "created", "success"].includes(format)) {
    element.classList.add(value ? "ok" : "warn");
  } else if (format === "skipped") {
    element.classList.add(value ? "warn" : "ok");
  }
}

function render(data) {
  document.querySelectorAll("[data-path]").forEach((element) => {
    const value = getPath(data, element.dataset.path);
    const format = element.dataset.format || "";
    const suffix = element.dataset.suffix || "";
    element.textContent = formatValue(value, format, suffix);
    applyTone(element, value, format);
  });

  const updated = new Date(data.updated_at);
  const age = Date.now() - updated.getTime();
  const badge = document.getElementById("snapshot-badge");
  const updatedAt = document.getElementById("updated-at");
  updatedAt.textContent = `Updated ${data.updated_at}`;
  if (Number.isNaN(age) || age > STALE_MS) {
    badge.textContent = "Stale";
    badge.className = "badge warn";
  } else {
    badge.textContent = "Live";
    badge.className = "badge ok";
  }
  renderFirmwareStatus(data.firmware || {}, data.uart || {});
}

function renderFirmwareStatus(firmware, uart) {
  const badge = document.getElementById("firmware-ready-badge");
  const form = document.getElementById("firmware-settings-form");
  const rate = document.getElementById("firmware-rate");
  const power = document.getElementById("firmware-power");
  const ready = firmware.protocol_ready === true;
  badge.textContent = ready ? "Ready" : "Not ready";
  badge.className = ready ? "badge ok" : "badge warn";
  form.querySelectorAll("button, input").forEach((element) => {
    element.disabled = uart.serial_connected !== true;
  });
  if (firmware.min_rate_hz !== null && firmware.min_rate_hz !== undefined) {
    rate.min = firmware.min_rate_hz;
    rate.max = firmware.max_rate_hz;
  }
  if (firmware.min_tx_power_dbm !== null && firmware.min_tx_power_dbm !== undefined) {
    power.min = firmware.min_tx_power_dbm;
    power.max = firmware.max_tx_power_dbm;
  }
  const message = document.getElementById("firmware-control-message");
  if (Number(message.dataset.holdUntil || 0) <= Date.now()) {
    if (firmware.command_pending) {
      message.textContent = `Request ${firmware.last_request_id} sent; waiting for TX ACK.`;
      message.className = "control-message muted";
    } else if (firmware.last_error) {
      message.textContent = `Command failed: ${firmware.last_error}`;
      message.className = "control-message error";
    } else if (firmware.last_ack_at) {
      message.textContent = `TX confirmed ${firmware.actual_rate_hz} Hz / ${firmware.actual_tx_power_dbm} dBm.`;
      message.className = "control-message ok";
    } else if (ready) {
      message.textContent = "RX control protocol ready. Read current settings or apply new values.";
      message.className = "control-message ok";
    }
  }
}

async function postFirmware(endpoint, body = {}) {
  const message = document.getElementById("firmware-control-message");
  message.dataset.holdUntil = "0";
  message.textContent = "Sending command…";
  message.className = "control-message muted";
  try {
    const response = await fetch(`${FIRMWARE_API}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    message.textContent = `Request ${result.request_id} accepted; waiting for TX ACK.`;
    message.className = "control-message ok";
    return result;
  } catch (error) {
    message.textContent = `Command failed: ${error.message}`;
    message.className = "control-message error";
    message.dataset.holdUntil = `${Date.now() + 5000}`;
    throw error;
  }
}

async function refresh() {
  const badge = document.getElementById("snapshot-badge");
  try {
    const response = await fetch(`${STATUS_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    badge.textContent = "Unavailable";
    badge.className = "badge error";
    document.getElementById("updated-at").textContent = `status.json: ${error.message}`;
  }
}

refresh();
setInterval(refresh, REFRESH_MS);

document.getElementById("firmware-settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  postFirmware("settings", {
    rate_hz: Number(document.getElementById("firmware-rate").value),
    tx_power_dbm: Number(document.getElementById("firmware-power").value),
  }).catch(() => {});
});

document.getElementById("firmware-refresh").addEventListener("click", () => {
  postFirmware("refresh").catch(() => {});
});

document.getElementById("firmware-reset").addEventListener("click", () => {
  postFirmware("reset").catch(() => {});
});
