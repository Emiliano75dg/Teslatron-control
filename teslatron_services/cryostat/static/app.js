const state = {
  config: null,
  lastMode: null,
  lastError: null,
};

const el = (id) => document.getElementById(id);

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toFixed(digits);
}

function formatUnit(value, unit, digits = 3) {
  const formatted = formatNumber(value, digits);
  return formatted === "--" ? "--" : `${formatted} ${unit}`;
}

function setText(id, value) {
  const node = el(id);
  if (node) {
    node.textContent = value;
  }
}

function setBadge(id, text, level = "neutral") {
  const node = el(id);
  node.textContent = text;
  node.className = `badge ${level}`;
}

function addEvent(message) {
  const events = el("events");
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()}  ${message}`;
  events.prepend(item);
  while (events.children.length > 40) {
    events.removeChild(events.lastChild);
  }
}

async function loadConfig() {
  const response = await fetch("/config");
  state.config = await response.json();
  const cryostat = state.config.cryostat;
  setText("subtitle", `${cryostat.backend} backend`);
  setText("readOnlyNotice", cryostat.read_only ? "Read only" : "Writable mock/session");
  setControlsEnabled(!cryostat.read_only);
}

function setControlsEnabled(enabled) {
  document.querySelectorAll(".command-panel input, .command-panel select, .command-panel button")
    .forEach((node) => {
      if (node.id !== "clearEvents") {
        node.disabled = !enabled;
      }
    });
}

function render(data) {
  const sample = data.temperature.sample;
  const vti = data.temperature.vti;
  const field = data.field;
  const pressure = data.pressure;
  const switchHeater = data.switch_heater;

  setBadge("connectionBadge", "Live", "ok");
  setBadge("backendBadge", data.backend, data.backend === "mock" ? "neutral" : "ok");
  setBadge("modeBadge", data.mode, data.mode.includes("ERROR") ? "error" : "neutral");
  setBadge("safetyBadge", data.safety.level, data.safety.level === "ok" ? "ok" : "error");

  setText("timestamp", new Date(data.timestamp * 1000).toLocaleString());
  setText("sampleTemperature", formatUnit(sample.temperature_K, "K", 3));
  setText("sampleTarget", `Target ${formatUnit(sample.target_K, "K", 3)}`);
  setText("vtiTemperature", formatUnit(vti.temperature_K, "K", 3));
  setText("vtiTarget", `Target ${formatUnit(vti.target_K, "K", 3)}`);
  setText("fieldValue", formatUnit(field.B_T, "T", 4));
  setText("fieldTarget", `Target ${formatUnit(field.target_T, "T", 4)}`);
  setText("pressureValue", formatUnit(pressure.mbar, "mbar", 4));
  setText("pressureTarget", `Target ${formatUnit(pressure.target_mbar, "mbar", 4)}`);

  renderLoop("sample", sample);
  renderLoop("vti", vti);
  renderField(field, switchHeater);
  renderPressure(pressure);

  if (state.lastMode !== data.mode) {
    addEvent(`Mode ${data.mode}`);
    state.lastMode = data.mode;
  }
  if (data.error && state.lastError !== data.error) {
    addEvent(data.error);
    state.lastError = data.error;
  }
}

function renderLoop(prefix, loop) {
  setText(`${prefix}TemperatureDetail`, formatUnit(loop.temperature_K, "K", 4));
  setText(`${prefix}TargetDetail`, formatUnit(loop.target_K, "K", 4));
  setText(`${prefix}Rate`, formatUnit(loop.rate_K_per_min, "K/min", 3));
  setText(`${prefix}Heater`, formatUnit(loop.heater_percent, "%", 2));
  setText(`${prefix}Mode`, loop.mode);
  setText(`${prefix}State`, loop.ramping ? "Ramping" : loop.stable ? "Stable" : "Tracking");
}

function renderField(field, switchHeater) {
  setText("fieldGaugeValue", formatUnit(field.B_T, "T", 4));
  setText("fieldCurrent", formatUnit(field.output_current_A, "A", 4));
  setText("fieldVoltage", formatUnit(field.output_voltage_V, "V", 4));
  setText("fieldRate", formatUnit(field.rate_T_per_min, "T/min", 4));
  setText("magnetTemperature", formatUnit(field.magnet_temperature_K, "K", 3));
  setText("pt1Temperature", formatUnit(field.pt1_temperature_K, "K", 3));
  setText("pt2Temperature", formatUnit(field.pt2_temperature_K, "K", 3));
  setText("switchHeater", `${switchHeater.status} ${switchHeater.ready ? "ready" : "waiting"}`);
  const normalized = Math.max(-1, Math.min(1, (field.B_T || 0) / 12));
  el("fieldNeedle").style.transform = `rotate(${normalized * 90}deg)`;
}

function renderPressure(pressure) {
  setText("pressureMode", pressure.mode);
  setText("pressureDetail", formatUnit(pressure.mbar, "mbar", 4));
  setText("pressureTargetDetail", formatUnit(pressure.target_mbar, "mbar", 4));
  setText("needleValue", formatUnit(pressure.needle_valve_percent, "%", 1));
  el("needleBar").style.width = `${Math.max(0, Math.min(100, pressure.needle_valve_percent || 0))}%`;
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/state`);

  socket.addEventListener("open", () => {
    setBadge("connectionBadge", "Live", "ok");
    addEvent("Connected");
  });

  socket.addEventListener("message", (event) => {
    render(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    setBadge("connectionBadge", "Offline", "error");
    addEvent("Disconnected");
    setTimeout(connectWebSocket, 1500);
  });

  socket.addEventListener("error", () => {
    setBadge("connectionBadge", "Error", "error");
  });
}

async function postJson(url, payload = null) {
  const options = { method: "POST", headers: {} };
  if (payload !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(url, options);
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text || response.statusText);
  }
  return text ? JSON.parse(text) : {};
}

function bindCommands() {
  el("temperatureForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const loop = form.get("loop");
    await runCommand(() => postJson(`/commands/temperature/${loop}/ramp`, {
      target_K: Number(form.get("target_K")),
      rate_K_per_min: Number(form.get("rate_K_per_min")),
    }), `Temperature ramp ${loop}`);
  });

  el("fieldForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await runCommand(() => postJson("/commands/ramp-field", {
      target_T: Number(form.get("target_T")),
      rate_T_per_min: Number(form.get("rate_T_per_min")),
    }), "Field ramp");
  });

  el("holdButton").addEventListener("click", () => runCommand(() => postJson("/commands/hold"), "Hold"));
  el("abortButton").addEventListener("click", () => runCommand(() => postJson("/commands/abort"), "Abort"));
  el("clearEvents").addEventListener("click", () => {
    el("events").replaceChildren();
  });
}

async function runCommand(action, label) {
  const message = el("commandMessage");
  message.className = "message";
  message.textContent = `${label}...`;
  try {
    await action();
    message.textContent = `${label} accepted`;
    addEvent(`${label} accepted`);
  } catch (error) {
    message.className = "message error";
    message.textContent = error.message;
    addEvent(`${label} failed`);
  }
}

loadConfig()
  .then(() => {
    bindCommands();
    connectWebSocket();
  })
  .catch((error) => {
    setBadge("connectionBadge", "Config error", "error");
    addEvent(error.message);
  });
