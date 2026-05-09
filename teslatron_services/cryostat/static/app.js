const state = {
  config: null,
  lastMode: null,
  lastError: null,
  history: [],
  customPlotMinutes: 120,
};

const el = (id) => document.getElementById(id);
const RECENT_PLOT_WINDOW_S = 30 * 60;
const MAX_HISTORY_WINDOW_S = 24 * 60 * 60;
const TEMPERATURE_SERIES = [
  { key: "sample_K", label: "Sample", color: "#f44336" },
  { key: "vti_K", label: "VTI", color: "#ffffff" },
  { key: "magnet_K", label: "Magnet", color: "#52d273" },
  { key: "pt1_K", label: "PT1", color: "#24c6dc" },
  { key: "pt2_K", label: "PT2", color: "#ffd43b" },
];
const MAGNETICS_SERIES = [
  { key: "field_T", label: "Field", color: "#00ff4c", axis: "left" },
  { key: "current_A", label: "Current", color: "#4cc9f0", axis: "left" },
  { key: "voltage_V", label: "Voltage", color: "#f72585", axis: "left" },
  { key: "pressure_mbar", label: "Pressure", color: "#ffb000", axis: "right" },
  { key: "needle_percent", label: "Needle", color: "#c15cff", axis: "right" },
];

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

function formatBool(value) {
  if (value === null || value === undefined) {
    return "--";
  }
  return value ? "Yes" : "No";
}

function formatText(value) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  return String(value);
}

function formatTermination(readTermination, writeTermination) {
  return `${JSON.stringify(readTermination || "")} / ${JSON.stringify(writeTermination || "")}`;
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
  applyConfigSnapshot(state.config);
}

function applyConfigSnapshot(config) {
  state.config = config;
  setText("subtitle", `${config.backend} backend`);
  setText("readOnlyNotice", config.read_only ? "Read only" : "Writable mock/session");
  applyCapabilities(config);
  setControlsEnabled(!config.read_only);
  renderConfig(config);
}

function setControlsEnabled(enabled) {
  document.querySelectorAll(".command-panel input, .command-panel select, .command-panel button")
    .forEach((node) => {
      if (node.id !== "clearEvents") {
        const capabilityOwner = node.closest("[data-capability]");
        const capabilityEnabled = capabilityOwner ? !capabilityOwner.classList.contains("hidden-by-capability") : true;
        node.disabled = !enabled || !capabilityEnabled;
      }
    });
  document.querySelectorAll("[data-command-control]").forEach((node) => {
    const capabilityOwner = node.closest("[data-capability]");
    const capabilityEnabled = capabilityOwner ? !capabilityOwner.classList.contains("hidden-by-capability") : true;
    node.disabled = !enabled || !capabilityEnabled;
  });
}

function activeCapabilities(config) {
  const profiles = config && config.insert_profiles ? config.insert_profiles : {};
  const profile = config && config.active_insert && profiles[config.active_insert]
    ? profiles[config.active_insert]
    : {};
  return {
    temperature_control: true,
    sample_loop: true,
    vti_loop: true,
    gas_control: true,
    field_control: true,
    pid_control: true,
    fixed_heater: true,
    ...(profile.capabilities || {}),
  };
}

function applyCapabilities(config) {
  const capabilities = activeCapabilities(config);
  document.querySelectorAll("[data-capability]").forEach((node) => {
    const capabilityKey = node.dataset.capability;
    const visible = capabilities[capabilityKey] !== false;
    node.classList.toggle("hidden-by-capability", !visible);
  });
  syncLoopOptions(capabilities);
  const notice = el("commandCapabilitiesNotice");
  if (notice) {
    const hiddenCount = document.querySelectorAll(".command-panel [data-capability].hidden-by-capability").length;
    notice.classList.toggle("hidden", hiddenCount === 0);
  }
}

function syncLoopOptions(capabilities) {
  const sampleEnabled = capabilities.sample_loop !== false;
  const vtiEnabled = capabilities.vti_loop !== false;
  document.querySelectorAll("select[name='loop']").forEach((select) => {
    const currentValue = select.value;
    const sampleOption = select.querySelector("option[value='sample']");
    const vtiOption = select.querySelector("option[value='vti']");
    const bothOption = select.querySelector("option[value='both']");
    if (sampleOption) {
      sampleOption.disabled = !sampleEnabled;
      sampleOption.hidden = !sampleEnabled;
    }
    if (vtiOption) {
      vtiOption.disabled = !vtiEnabled;
      vtiOption.hidden = !vtiEnabled;
    }
    if (bothOption) {
      const bothEnabled = sampleEnabled && vtiEnabled;
      bothOption.disabled = !bothEnabled;
      bothOption.hidden = !bothEnabled;
    }
    const availableOption = Array.from(select.options).find((option) => !option.disabled && !option.hidden);
    const selectedOption = select.selectedOptions && select.selectedOptions.length
      ? select.selectedOptions[0]
      : null;
    if ((selectedOption && (selectedOption.disabled || selectedOption.hidden)) && availableOption) {
      select.value = availableOption.value;
    } else if (!select.value && availableOption) {
      select.value = availableOption.value;
    } else {
      const currentOption = currentValue
        ? select.querySelector(`option[value='${currentValue}']`)
        : null;
      if (currentOption && currentOption.disabled === false) {
      select.value = currentValue;
      }
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
  recordHistory(data);
  renderCharts();

  if (state.lastMode !== data.mode) {
    addEvent(`Mode ${data.mode}`);
    state.lastMode = data.mode;
  }
  if (data.error && state.lastError !== data.error) {
    addEvent(data.error);
    state.lastError = data.error;
  }
}

function recordHistory(data) {
  state.history.push({
    timestamp: data.timestamp,
    sample_K: data.temperature.sample.temperature_K,
    vti_K: data.temperature.vti.temperature_K,
    magnet_K: data.field.magnet_temperature_K,
    pt1_K: data.field.pt1_temperature_K,
    pt2_K: data.field.pt2_temperature_K,
    field_T: data.field.B_T,
    current_A: data.field.output_current_A,
    voltage_V: data.field.output_voltage_V,
    pressure_mbar: data.pressure.mbar,
    needle_percent: data.pressure.needle_valve_percent,
  });
  const cutoff = data.timestamp - MAX_HISTORY_WINDOW_S;
  state.history = state.history.filter((point) => point.timestamp >= cutoff);
  setText("plotWindow", `${state.history.length} stored points`);
}

function renderLoop(prefix, loop) {
  setText(`${prefix}TemperatureDetail`, formatUnit(loop.temperature_K, "K", 4));
  setText(`${prefix}TargetDetail`, formatUnit(loop.target_K, "K", 4));
  setText(`${prefix}Rate`, formatUnit(loop.rate_K_per_min, "K/min", 3));
  setText(`${prefix}Heater`, formatUnit(loop.heater_percent, "%", 2));
  setText(`${prefix}HeaterMode`, loop.heater_mode || "--");
  setText(`${prefix}LoopEnabled`, formatBool(loop.loop_enabled));
  setText(`${prefix}RampEnabled`, formatBool(loop.ramp_enabled));
  setText(`${prefix}TargetReached`, formatBool(loop.target_reached));
  setText(`${prefix}Pid`, formatPid(loop.pid));
  setText(`${prefix}Mode`, loop.mode);
  setText(`${prefix}State`, loop.ramping ? "Ramping" : loop.stable ? "Stable" : "Tracking");
}

function formatPid(pid) {
  if (!pid) {
    return "--";
  }
  return `${pid.mode || "UNKNOWN"} P ${formatNumber(pid.p, 3)} I ${formatNumber(pid.i, 3)} D ${formatNumber(pid.d, 3)}`;
}

function renderField(field, switchHeater) {
  setText("fieldGaugeValue", formatUnit(field.B_T, "T", 4));
  setText("fieldCurrent", formatUnit(field.output_current_A, "A", 4));
  setText("fieldVoltage", formatUnit(field.output_voltage_V, "V", 4));
  setText("fieldRate", formatUnit(field.rate_T_per_min, "T/min", 4));
  setText("magnetTemperature", formatUnit(field.magnet_temperature_K, "K", 3));
  setText("pt1Temperature", formatUnit(field.pt1_temperature_K, "K", 3));
  setText("pt2Temperature", formatUnit(field.pt2_temperature_K, "K", 3));
  setText("fieldAction", field.action || "--");
  setText("fieldAtSetpoint", formatBool(field.at_setpoint));
  setText("fieldAtZero", formatBool(field.at_zero));
  setText("fieldClamped", formatBool(field.clamped));
  renderSwitchHeater(switchHeater);
  const normalized = Math.max(-1, Math.min(1, (field.B_T || 0) / 12));
  el("fieldNeedle").style.transform = `rotate(${normalized * 90}deg)`;
}

function renderSwitchHeater(switchHeater) {
  const status = formatText(switchHeater.status);
  const readiness = switchHeater.ready ? "ready" : "waiting";
  const remainingText = formatSwitchHeaterRemaining(switchHeater);
  setText("switchHeater", remainingText ? `${status} ${readiness}, ${remainingText}` : `${status} ${readiness}`);

  let badgeLevel = "neutral";
  let badgeText = `Switch ${status}`;
  if (switchHeater.ready) {
    badgeLevel = "ok";
    badgeText = `Switch ${status} ready`;
  } else if (switchHeater.status === "ON") {
    badgeLevel = "warn";
    badgeText = "Switch ON waiting";
  } else if (switchHeater.status === "OFF") {
    badgeLevel = "neutral";
    badgeText = "Switch OFF waiting";
  }
  if (remainingText) {
    badgeText = `${badgeText} ${remainingText}`;
  }
  setBadge("switchHeaterIpsBadge", badgeText, badgeLevel);
  setBadge("switchHeaterCommandBadge", badgeText, badgeLevel);

  const button = el("switchHeaterButton");
  if (!button) {
    return;
  }
  const shouldEnable = switchHeater.target_status !== "ON";
  button.dataset.nextEnabled = shouldEnable ? "true" : "false";
  button.textContent = shouldEnable ? "Switch ON" : "Switch OFF";
}

function formatSwitchHeaterRemaining(switchHeater) {
  if (switchHeater.ready) {
    return "";
  }
  const delayS = Number(switchHeater.delay_s);
  const elapsedS = Number(switchHeater.elapsed_s);
  if (!Number.isFinite(delayS)) {
    return "";
  }
  const remainingS = Math.max(0, Math.ceil(delayS - (Number.isFinite(elapsedS) ? elapsedS : 0)));
  return remainingS > 0 ? `${remainingS}s left` : "0s left";
}

function renderPressure(pressure) {
  setText("pressureMode", pressure.mode);
  setText("pressureDetail", formatUnit(pressure.mbar, "mbar", 4));
  setText("pressureTargetDetail", formatUnit(pressure.target_mbar, "mbar", 4));
  setText("needleValue", formatUnit(pressure.needle_valve_percent, "%", 1));
  el("needleBar").style.width = `${Math.max(0, Math.min(100, pressure.needle_valve_percent || 0))}%`;
}

function renderConfig(config) {
  const itc = config.itc || {};
  const ips = config.ips || {};
  const safety = config.safety || {};
  const capabilities = activeCapabilities(config);

  setText("configBackend", formatText(config.backend));
  setText("configReadOnly", formatBool(config.read_only));
  setText("configActiveInsert", formatText(config.active_insert));
  setText("configSampleThermometer", formatText(config.sample_thermometer));
  setText("configLogPath", formatText(config.log_path));
  setText("configPollInterval", formatUnit(config.poll_interval_s, "s", 3));
  setText("configLogInterval", formatUnit(config.log_interval_s, "s", 3));

  setText("configItcAddress", formatText(itc.address));
  setText("configItcTimeout", formatUnit(itc.timeout_ms, "ms", 0));
  setText("configItcTermination", formatTermination(itc.read_termination, itc.write_termination));
  setText("configItcProbeSignal", formatText(itc.probe_signal));
  setText("configItcProbeLoop", formatText(itc.probe_loop));
  setText("configItcVtiSignal", formatText(itc.vti_signal));
  setText("configItcVtiLoop", formatText(itc.vti_loop));
  setText("configItcPressure", formatText(itc.pressure));

  setText("configIpsAddress", formatText(ips.address));
  setText("configIpsTimeout", formatUnit(ips.timeout_ms, "ms", 0));
  setText("configIpsTermination", formatTermination(ips.read_termination, ips.write_termination));
  setText("configIpsCommandDelay", formatUnit(ips.command_delay_s, "s", 3));
  setText("configIpsMagnetGroup", formatText(ips.magnet_group));
  setText("configIpsMagnetTemperature", formatText(ips.magnet_temperature));
  setText("configIpsPt1Temperature", formatText(ips.pt1_temperature));
  setText("configIpsPt2Temperature", formatText(ips.pt2_temperature));
  setText("configIpsSwitchOnDelay", formatUnit(ips.switch_on_delay_s, "s", 1));
  setText("configIpsSwitchOffDelay", formatUnit(ips.switch_off_delay_s, "s", 1));

  setText("configMinTemperature", formatUnit(safety.min_temperature_K, "K", 3));
  setText("configMaxTemperature", formatUnit(safety.max_temperature_K, "K", 3));
  setText("configMaxTemperatureRate", formatUnit(safety.max_temperature_rate_K_per_min, "K/min", 3));
  setText("configMaxField", formatUnit(safety.max_field_T, "T", 4));
  setText("configMaxFieldRate", formatUnit(safety.max_field_rate_T_per_min, "T/min", 4));
  renderInsertProfiles(config, capabilities);
  renderSampleSensorControls(config);
  setText("configJson", JSON.stringify(config, null, 2));
}

function renderInsertProfiles(config, capabilities) {
  const container = el("insertProfilesList");
  const empty = el("insertProfilesEmpty");
  const profiles = config.insert_profiles || {};
  const presets = config.sample_sensor_presets || {};
  const activeProfileId = config.active_insert;

  container.replaceChildren();
  const entries = Object.entries(profiles);
  if (!entries.length) {
    empty.hidden = false;
    return;
  }

  empty.hidden = true;
  for (const [profileId, profile] of entries) {
    const card = document.createElement("article");
    card.className = `profile-card${profileId === activeProfileId ? " active" : ""}`;

    const header = document.createElement("div");
    header.className = "profile-card-header";

    const titleBlock = document.createElement("div");
    const title = document.createElement("h4");
    title.textContent = profile.name || profileId;
    const subtitle = document.createElement("p");
    subtitle.className = "muted";
    subtitle.textContent = profile.description || profileId;
    titleBlock.append(title, subtitle);

    const badge = document.createElement("span");
    badge.className = `badge ${profileId === activeProfileId ? "ok" : "neutral"}`;
    badge.textContent = profileId === activeProfileId ? "Active" : profileId;

    header.append(titleBlock, badge);
    card.appendChild(header);

    const details = document.createElement("dl");
    details.className = "dense-list";
    appendProfileField(details, "Thermometer", profile.sample_thermometer);
    appendProfileField(details, "Sample signal", profile.itc && profile.itc.probe_signal);
    appendProfileField(details, "Sample loop", profile.itc && profile.itc.probe_loop);
    appendProfileField(details, "Default sensor", profile.default_sample_sensor);
    appendProfileField(details, "Sensor presets", summarizeSensorOptions(profile.sample_sensor_options, presets));
    appendProfileField(details, "VTI signal", profile.itc && profile.itc.vti_signal);
    appendProfileField(details, "VTI loop", profile.itc && profile.itc.vti_loop);
    appendProfileField(details, "Pressure", profile.itc && profile.itc.pressure);
    appendProfileField(details, "Magnet group", profile.ips && profile.ips.magnet_group);
    card.appendChild(details);

    const capabilityList = document.createElement("p");
    capabilityList.className = "profile-notes";
    capabilityList.textContent = `Functions: ${summarizeCapabilities(profile.capabilities || capabilities)}`;
    card.appendChild(capabilityList);

    if (profile.notes) {
      const notes = document.createElement("p");
      notes.className = "profile-notes";
      notes.textContent = profile.notes;
      card.appendChild(notes);
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = profileId === activeProfileId ? "Selected" : state.config && state.config.read_only ? "Read only" : "Activate";
    button.disabled = profileId === activeProfileId || Boolean(state.config && state.config.read_only);
    button.addEventListener("click", async () => {
      await runCommand(
        () => activateInsertProfile(profileId),
        `Insert profile ${profile.name || profileId}`,
      );
    });
    card.appendChild(button);
    container.appendChild(card);
  }
}

function appendProfileField(list, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = formatText(value);
  list.append(dt, dd);
}

function renderSampleSensorControls(config) {
  const select = el("sampleSensorPreset");
  const details = el("sampleSensorDetails");
  const activeLabel = el("activeSampleSensor");
  const applyButton = el("applySampleSensorButton");
  if (!select || !details || !activeLabel || !applyButton) {
    return;
  }

  const profiles = config.insert_profiles || {};
  const profile = config.active_insert ? profiles[config.active_insert] || {} : {};
  const presets = config.sample_sensor_presets || {};
  const allowedIds = Array.isArray(profile.sample_sensor_options) && profile.sample_sensor_options.length
    ? profile.sample_sensor_options.filter((presetId) => presets[presetId])
    : Object.keys(presets);
  const activePresetId = config.active_sample_sensor || "";

  select.replaceChildren();
  if (!allowedIds.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No saved sensors";
    select.appendChild(option);
    select.value = "";
    details.textContent = "No sample sensor presets available for this insert.";
    activeLabel.textContent = "None";
    applyButton.disabled = true;
    return;
  }

  for (const presetId of allowedIds) {
    const preset = presets[presetId] || {};
    const option = document.createElement("option");
    option.value = presetId;
    option.textContent = preset.calibration ? `${presetId} (${preset.calibration})` : presetId;
    select.appendChild(option);
  }

  const selectedPresetId = allowedIds.includes(activePresetId) ? activePresetId : allowedIds[0];
  select.value = selectedPresetId;
  activeLabel.textContent = activePresetId || "None";
  details.textContent = sampleSensorPresetSummary(presets[selectedPresetId]);
  applyButton.disabled = Boolean(config.read_only) || !selectedPresetId;
}

function sampleSensorPresetSummary(preset) {
  if (!preset) {
    return "No sample sensor preset selected.";
  }
  const parts = [
    preset.sensor_type,
    preset.excitation_type,
    preset.excitation_magnitude,
    preset.calibration,
  ].filter(Boolean);
  return parts.length ? parts.join(" | ") : "Preset is present but incomplete.";
}

async function activateInsertProfile(profileId) {
  const config = await postJson("/config/activate-insert", { profile_id: profileId });
  applyConfigSnapshot(config);
  addEvent(`Active insert ${profileId}`);
}

async function applySampleSensorPreset(presetId) {
  const config = await postJson("/config/apply-sample-sensor", { preset_id: presetId });
  applyConfigSnapshot(config);
  addEvent(`Sample sensor ${presetId} applied`);
}

function summarizeCapabilities(capabilities) {
  const labels = [
    ["temperature_control", "temperature"],
    ["sample_loop", "sample loop"],
    ["vti_loop", "VTI loop"],
    ["gas_control", "gas"],
    ["field_control", "field"],
    ["pid_control", "PID"],
    ["fixed_heater", "fixed heater"],
  ];
  return labels
    .filter(([key]) => capabilities[key] !== false)
    .map(([, label]) => label)
    .join(", ");
}

function joinSensorExcitation(sensor) {
  if (!sensor) {
    return "--";
  }
  const excitationType = sensor.excitation_type || "";
  const excitationMagnitude = sensor.excitation_magnitude || "";
  return formatText([excitationType, excitationMagnitude].filter(Boolean).join(" "));
}

function summarizeSensorOptions(sensorOptionIds, presets) {
  if (!Array.isArray(sensorOptionIds) || !sensorOptionIds.length) {
    return "--";
  }
  return sensorOptionIds
    .map((presetId) => {
      const preset = presets[presetId] || {};
      return preset.calibration ? `${presetId} (${preset.calibration})` : presetId;
    })
    .join(", ");
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

  el("targetTemperatureForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const loop = form.get("loop");
    await runCommand(() => postJson(`/commands/temperature/${loop}/target`, {
      target_K: Number(form.get("target_K")),
    }), `Temperature target ${loop}`);
  });

  el("fixedHeaterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const loop = form.get("loop");
    await runCommand(() => postJson(`/commands/temperature/${loop}/fixed-heater`, {
      heater_percent: Number(form.get("heater_percent")),
    }), `Fixed heater ${loop}`);
  });

  el("pidForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const loop = form.get("loop");
    await runCommand(() => postJson(`/commands/temperature/${loop}/pid`, {
      p: Number(form.get("p")),
      i: Number(form.get("i")),
      d: Number(form.get("d")),
      auto: form.get("auto") === "on",
    }), `PID ${loop}`);
  });

  el("gasForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const mode = form.get("mode");
    const endpoint = mode === "needle" ? "/commands/vti/gas/set-needle" : "/commands/vti/gas/set-pressure";
    const payload = mode === "needle"
      ? { needle_valve_percent: Number(form.get("needle_valve_percent")) }
      : { pressure_mbar: Number(form.get("pressure_mbar")) };
    await runCommand(() => postJson(endpoint, payload), `VTI gas ${mode}`);
  });

  el("fieldForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await runCommand(() => postJson("/commands/ramp-field", {
      target_T: Number(form.get("target_T")),
      rate_T_per_min: Number(form.get("rate_T_per_min")),
    }), "Field ramp");
  });

  el("switchHeaterButton").addEventListener("click", async () => {
    const enabled = el("switchHeaterButton").dataset.nextEnabled === "true";
    await runCommand(() => postJson("/commands/ips/switch-heater", {
      enabled,
    }), enabled ? "Switch heater ON" : "Switch heater OFF");
  });

  el("toZeroButton").addEventListener("click", () => {
    const rate = Number(new FormData(el("fieldForm")).get("rate_T_per_min"));
    return runCommand(() => postJson("/commands/ramp-to-zero", {
      rate_T_per_min: rate,
    }), "To zero");
  });
  el("holdButton").addEventListener("click", () => runCommand(() => postJson("/commands/hold"), "Hold"));
  el("clampButton").addEventListener("click", () => runCommand(() => postJson("/commands/clamp"), "Clamp"));
  el("abortButton").addEventListener("click", () => runCommand(() => postJson("/commands/abort"), "Abort"));
  el("clearEvents").addEventListener("click", () => {
    el("events").replaceChildren();
  });
  el("clearPlots").addEventListener("click", () => {
    state.history = [];
    renderCharts();
    addEvent("Plots cleared");
  });
  el("sampleSensorPreset").addEventListener("change", (event) => {
    const presetId = event.currentTarget.value;
    const presets = state.config && state.config.sample_sensor_presets ? state.config.sample_sensor_presets : {};
    setText("sampleSensorDetails", sampleSensorPresetSummary(presets[presetId]));
  });
  el("applySampleSensorButton").addEventListener("click", async () => {
    const presetId = el("sampleSensorPreset").value;
    await runCommand(
      () => applySampleSensorPreset(presetId),
      `Sample sensor ${presetId}`,
    );
  });
  el("customPlotMinutes").addEventListener("change", (event) => {
    state.customPlotMinutes = clampCustomPlotMinutes(event.currentTarget.value);
    event.currentTarget.value = state.customPlotMinutes;
    renderCharts();
  });
}

function bindTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });
  showTab("overview");
}

function showTab(tab) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".view-section").forEach((section) => {
    section.classList.toggle("hidden", section.dataset.view !== tab);
  });
  renderCharts();
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
    bindTabs();
    bindCommands();
    connectWebSocket();
  })
  .catch((error) => {
    setBadge("connectionBadge", "Config error", "error");
    addEvent(error.message);
  });

function renderCharts() {
  const recentPoints = historyForWindow(RECENT_PLOT_WINDOW_S);
  const customWindowS = state.customPlotMinutes * 60;
  const customPoints = historyForWindow(customWindowS);

  setText("recentPlotWindow", plotWindowLabel(30, recentPoints.length));
  setText("customPlotWindow", plotWindowLabel(state.customPlotMinutes, customPoints.length));

  drawPlotPair("recent", recentPoints);
  drawPlotPair("custom", customPoints);
}

function drawPlotPair(prefix, points) {
  drawTimeSeries(
    el(`${prefix}TemperatureChart`),
    points,
    TEMPERATURE_SERIES,
    "Temperature (K)",
  );
  drawTimeSeries(
    el(`${prefix}MagneticsChart`),
    points,
    MAGNETICS_SERIES,
    "B (T), I (A), V (V)",
    "Pressure (mbar), Needle (%)",
  );
}

function historyForWindow(windowS) {
  if (!state.history.length) {
    return [];
  }
  const latest = state.history[state.history.length - 1].timestamp;
  const cutoff = latest - windowS;
  return state.history.filter((point) => point.timestamp >= cutoff);
}

function plotWindowLabel(minutes, pointCount) {
  return `${minutes} min, ${pointCount} points`;
}

function clampCustomPlotMinutes(value) {
  const minutes = Number(value);
  if (!Number.isFinite(minutes)) {
    return 120;
  }
  return Math.max(1, Math.min(1440, Math.round(minutes)));
}

function drawTimeSeries(canvas, points, series, leftUnit, rightUnit = null) {
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  const width = Math.max(420, Math.floor(rect.width * scale));
  const height = Math.max(312, Math.floor(rect.height * scale));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#070b0f";
  ctx.fillRect(0, 0, width, height);

  const padding = {
    left: 68 * scale,
    right: (rightUnit ? 76 : 22) * scale,
    top: 20 * scale,
    bottom: 56 * scale,
  };
  const plot = {
    x: padding.left,
    y: padding.top,
    w: width - padding.left - padding.right,
    h: height - padding.top - padding.bottom,
  };

  drawGrid(ctx, plot, width, height, scale);
  if (points.length < 2) {
    drawNoData(ctx, plot, scale);
    return;
  }

  const minTime = points[0].timestamp;
  const maxTime = points[points.length - 1].timestamp;
  const leftSeries = series.filter((item) => item.axis !== "right");
  const rightSeries = series.filter((item) => item.axis === "right");
  const leftRange = valueRange(points, leftSeries);
  const rightRange = rightSeries.length ? valueRange(points, rightSeries) : null;

  drawAxisLabels(ctx, plot, scale, leftRange, leftUnit, rightRange, rightUnit);
  drawTimeLabels(ctx, plot, scale, minTime, maxTime);

  for (const item of series) {
    const range = item.axis === "right" ? rightRange : leftRange;
    if (!range) {
      continue;
    }
    drawSeries(ctx, plot, points, minTime, maxTime, range, item);
  }
}

function drawGrid(ctx, plot, width, height, scale) {
  ctx.strokeStyle = "#24313c";
  ctx.lineWidth = 1 * scale;
  ctx.beginPath();
  for (let i = 0; i <= 5; i += 1) {
    const y = plot.y + (plot.h * i) / 5;
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
  }
  for (let i = 0; i <= 6; i += 1) {
    const x = plot.x + (plot.w * i) / 6;
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
  }
  ctx.stroke();
  ctx.strokeStyle = "#53616f";
  ctx.strokeRect(plot.x, plot.y, plot.w, plot.h);
  ctx.fillStyle = "#070b0f";
  ctx.fillRect(0, 0, width, plot.y - 1);
  ctx.fillRect(0, plot.y + plot.h + 1, width, height - plot.y - plot.h);
}

function drawNoData(ctx, plot, scale) {
  ctx.fillStyle = "#93a2af";
  ctx.font = `${13 * scale}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillText("Waiting for data", plot.x + plot.w / 2, plot.y + plot.h / 2);
}

function valueRange(points, series) {
  const values = [];
  for (const point of points) {
    for (const item of series) {
      const value = point[item.key];
      if (value !== null && value !== undefined && Number.isFinite(Number(value))) {
        values.push(Number(value));
      }
    }
  }
  if (!values.length) {
    return { min: 0, max: 1 };
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.05, 1);
    min -= pad;
    max += pad;
  } else {
    const pad = (max - min) * 0.08;
    min -= pad;
    max += pad;
  }
  return { min, max };
}

function drawAxisLabels(ctx, plot, scale, leftRange, leftUnit, rightRange, rightUnit) {
  ctx.font = `${11 * scale}px ui-monospace, monospace`;
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#b8c4ce";
  ctx.textAlign = "right";
  for (let i = 0; i <= 5; i += 1) {
    const value = leftRange.max - ((leftRange.max - leftRange.min) * i) / 5;
    const y = plot.y + (plot.h * i) / 5;
    ctx.fillText(compact(value), plot.x - 8 * scale, y);
  }
  ctx.save();
  ctx.translate(14 * scale, plot.y + plot.h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText(leftUnit, 0, 0);
  ctx.restore();

  if (!rightRange || !rightUnit) {
    return;
  }
  ctx.textAlign = "left";
  for (let i = 0; i <= 5; i += 1) {
    const value = rightRange.max - ((rightRange.max - rightRange.min) * i) / 5;
    const y = plot.y + (plot.h * i) / 5;
    ctx.fillText(compact(value), plot.x + plot.w + 8 * scale, y);
  }
  ctx.save();
  ctx.translate(plot.x + plot.w + 45 * scale, plot.y + plot.h / 2);
  ctx.rotate(Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText(rightUnit, 0, 0);
  ctx.restore();
}

function drawTimeLabels(ctx, plot, scale, minTime, maxTime) {
  ctx.font = `${11 * scale}px ui-monospace, monospace`;
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  ctx.fillStyle = "#b8c4ce";
  const labels = [
    { x: plot.x, value: minTime },
    { x: plot.x + plot.w / 2, value: (minTime + maxTime) / 2 },
    { x: plot.x + plot.w, value: maxTime },
  ];
  for (const label of labels) {
    ctx.fillText(formatClockTime(label.value), label.x, plot.y + plot.h + 10 * scale);
  }
  ctx.font = `${12 * scale}px system-ui, sans-serif`;
  ctx.fillStyle = "#d7e0e8";
  ctx.fillText("Time", plot.x + plot.w / 2, plot.y + plot.h + 32 * scale);
}

function formatClockTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function drawSeries(ctx, plot, points, minTime, maxTime, range, item) {
  const timeSpan = Math.max(maxTime - minTime, 1);
  const valueSpan = Math.max(range.max - range.min, 1e-12);
  ctx.strokeStyle = item.color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  for (const point of points) {
    const value = point[item.key];
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      started = false;
      continue;
    }
    const x = plot.x + ((point.timestamp - minTime) / timeSpan) * plot.w;
    const y = plot.y + (1 - (Number(value) - range.min) / valueSpan) * plot.h;
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.stroke();
}

function compact(value) {
  const abs = Math.abs(value);
  if (abs >= 1000 || (abs > 0 && abs < 0.01)) {
    return value.toExponential(1);
  }
  if (abs >= 100) {
    return value.toFixed(0);
  }
  if (abs >= 10) {
    return value.toFixed(1);
  }
  return value.toFixed(3);
}

window.addEventListener("resize", renderCharts);
