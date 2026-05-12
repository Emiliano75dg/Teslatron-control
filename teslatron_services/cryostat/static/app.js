const state = {
  config: null,
  lastMode: null,
  lastError: null,
  helioxDetails: null,
  helioxMetadata: null,
  helioxDiagnosticsPending: false,
  helioxDiagnosticsAt: 0,
  helioxMetadataPending: false,
  helioxMetadataAt: 0,
  history: [],
  customPlotMinutes: 120,
  plotSeriesEnabled: {},
  recipeSteps: [],
  savedRecipes: [],
  pollingTimer: null,
  reconnectTimer: null,
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
const PLOT_SERIES_GROUPS = {
  temperature: TEMPERATURE_SERIES,
  magnetics: MAGNETICS_SERIES,
};

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

async function fetchStateSnapshot() {
  const response = await fetch("/state");
  if (!response.ok) {
    throw new Error(`State request failed: ${response.status}`);
  }
  return response.json();
}

async function fetchJson(url) {
  const response = await fetch(url);
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text || response.statusText);
  }
  return text ? JSON.parse(text) : {};
}

async function deleteJson(url) {
  const response = await fetch(url, { method: "DELETE" });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text || response.statusText);
  }
  return text ? JSON.parse(text) : {};
}

function applyConfigSnapshot(config) {
  state.config = config;
  setText("subtitle", `${config.backend} backend`);
  setText("readOnlyNotice", config.read_only ? "Read only" : "Writable mock/session");
  applyCapabilities(config);
  setControlsEnabled(!config.read_only);
  setRecipeControlsEnabled(!config.read_only);
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

function setRecipeControlsEnabled(enabled) {
  document.querySelectorAll(".recipe-panel input, .recipe-panel select, .recipe-panel button")
    .forEach((node) => {
      node.disabled = !enabled;
    });
  updateRecipeControlState();
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
  renderHelioxDetails(data);
  renderField(field, switchHeater);
  renderPressure(pressure);
  renderRecipeStatus(data.recipe || {});
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

function renderRecipeStatus(recipe) {
  const status = recipe.status || "idle";
  const level = status === "completed"
    ? "ok"
    : status === "error" || status === "aborted"
      ? "error"
      : status === "waiting_signal"
        ? "warn"
        : "neutral";
  setBadge("recipeStatusBadge", status, level);
  setText("recipeName", formatText(recipe.name));
  setText("recipeStatus", status);
  const stepIndex = recipe.current_step_index;
  const stepCount = Array.isArray(recipe.steps) ? recipe.steps.length : 0;
  const stepLabel = stepIndex === null || stepIndex === undefined
    ? "--"
    : `${Number(stepIndex) + 1}/${stepCount} ${recipeStepSummary(recipe.current_step || {})}`;
  setText("recipeStep", stepLabel);
  setText("recipeMessageDetail", formatText(recipe.message || recipe.error));
  updateRecipeControlState(status);
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

function renderHelioxDetails(data) {
  const panel = el("helioxDetailsPanel");
  if (!panel) {
    return;
  }
  const isHeliox = data.backend === "heliox";
  panel.classList.toggle("hidden", !isHeliox);
  if (!isHeliox) {
    state.helioxDetails = null;
    state.helioxMetadata = null;
    return;
  }
  maybeRefreshHelioxDiagnostics();
  const details = state.helioxDetails;
  const metadata = state.helioxMetadata;
  const fallbackMode = data.temperature && data.temperature.sample
    ? data.temperature.sample.heater_mode
    : null;
  setText("helioxStatus", details && details.status ? details.status : formatText(fallbackMode));
  setText("helioxControlPath", formatHelioxControlPath(details, fallbackMode));
  setText("helioxControlCrossover", formatUnit(metadata && metadata.controlModeCrossoverK, "K", 4));
  setText(
    "helioxSampleRegion",
    formatHelioxSampleRegion(
      data.temperature && data.temperature.sample ? data.temperature.sample.temperature_K : null,
      metadata,
    ),
  );
  setText(
    "helioxOperationHint",
    formatHelioxOperationHint(
      data.temperature && data.temperature.sample ? data.temperature.sample.temperature_K : null,
      metadata,
    ),
  );
  setText("helioxNeedleRefs", formatHelioxNeedleRefs(metadata));
  setText("helioxSorbTemperature", formatUnit(details && details.sorbTemperature, "K", 4));
  setText("helioxSorbHeater", formatUnit(details && details.sorbHeaterPercent, "%", 2));
  setText("helioxSorbStable", formatBool(details && details.sorbStable));
  setText("helioxPotTemperature", formatUnit(details && details.potTemperature, "K", 4));
  setText("helioxPotHeater", formatUnit(details && details.potHeaterPercent, "%", 2));
  setText("helioxPotStable", formatBool(details && details.potStable));
  setText("helioxRawPotHighTemperature", formatUnit(details && details.rawPotHighTemperature, "K", 4));
  setText("helioxRawPotLowTemperature", formatUnit(details && details.rawPotLowTemperature, "K", 4));
  setText("helioxRawVtiTemperature", formatUnit(details && details.rawVtiTemperature, "K", 4));
  setText("helioxRawSorbTemperature", formatUnit(details && details.rawSorbTemperature, "K", 4));
}

function maybeRefreshHelioxDiagnostics() {
  const now = Date.now();
  if (state.helioxDiagnosticsPending || now - state.helioxDiagnosticsAt < 5000) {
    return;
  }
  state.helioxDiagnosticsPending = true;
  state.helioxDiagnosticsAt = now;
  fetchJson("/diagnostics/readings")
    .then((readings) => {
      state.helioxDetails = {
        status: diagnosticToken(readings.heliox_status),
        potStable: diagnosticBool(readings.heliox_pot_stable),
        potTemperature: diagnosticFloat(readings.heliox_pot_temperature),
        potHeaterPercent: diagnosticFloat(readings.heliox_pot_heater_percent),
        sorbStable: diagnosticBool(readings.heliox_sorb_stable),
        sorbTemperature: diagnosticFloat(readings.heliox_sorb_temperature),
        sorbHeaterPercent: diagnosticFloat(readings.heliox_sorb_heater_percent),
        rawPotHighTemperature: diagnosticFloat(readings.heliox_raw_pot_high_temperature),
        rawPotLowTemperature: diagnosticFloat(readings.heliox_raw_pot_low_temperature),
        rawVtiTemperature: diagnosticFloat(readings.heliox_raw_vti_temperature),
        rawSorbTemperature: diagnosticFloat(readings.heliox_raw_sorb_temperature),
      };
    })
    .catch(() => {
      state.helioxDetails = null;
    })
    .finally(() => {
      state.helioxDiagnosticsPending = false;
    });
  maybeRefreshHelioxMetadata();
}

function maybeRefreshHelioxMetadata() {
  const now = Date.now();
  if (state.helioxMetadataPending || now - state.helioxMetadataAt < 30000) {
    return;
  }
  state.helioxMetadataPending = true;
  state.helioxMetadataAt = now;
  fetchJson("/diagnostics")
    .then((diagnostics) => {
      const thresholds = diagnostics && diagnostics.heliox_template_thresholds;
      state.helioxMetadata = thresholds ? {
        controlModeCrossoverK: thresholds.control_mode_crossover_K,
        condensedTempK: thresholds.condensed_temp_K,
        potEmptyK: thresholds.pot_empty_K,
        he3PotBoiloffK: thresholds.he3_pot_boiloff_K,
        he3SorbHighTempControlK: thresholds.he3_sorb_high_temp_control_K,
        he3SorbRegenK: thresholds.he3_sorb_regen_K,
        needleValveLowTempMbar: thresholds.needle_valve_low_temp_mbar,
        needleValveRecondenseMbar: thresholds.needle_valve_recondense_mbar,
        needleValveHighTempMbar: thresholds.needle_valve_high_temp_mbar,
      } : null;
    })
    .catch(() => {
      state.helioxMetadata = null;
    })
    .finally(() => {
      state.helioxMetadataPending = false;
    });
}

function diagnosticResponse(reading) {
  if (!reading || typeof reading.response !== "string") {
    return null;
  }
  return reading.response;
}

function diagnosticToken(reading) {
  const response = diagnosticResponse(reading);
  if (!response) {
    return null;
  }
  const token = response.split(":").pop();
  return token ? token.trim() : null;
}

function diagnosticFloat(reading) {
  const token = diagnosticToken(reading);
  if (!token) {
    return null;
  }
  const match = token.match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?/);
  return match ? Number(match[0]) : null;
}

function diagnosticBool(reading) {
  const token = diagnosticToken(reading);
  if (!token) {
    return null;
  }
  if (token === "ON") {
    return true;
  }
  if (token === "OFF") {
    return false;
  }
  return null;
}

function formatHelioxControlPath(details, fallbackMode) {
  if (details) {
    const sorb = details.sorbHeaterPercent;
    const pot = details.potHeaterPercent;
    if (typeof sorb === "number" && typeof pot === "number" && Math.abs(sorb - pot) > 0.1) {
      return pot > sorb ? "He3 pot heater" : "He3 sorb heater";
    }
    if (typeof pot === "number" && pot > 0.1) {
      return "He3 pot heater";
    }
    if (typeof sorb === "number" && sorb > 0.1) {
      return "He3 sorb heater";
    }
  }
  const status = (details && details.status ? details.status : fallbackMode || "").toUpperCase();
  if (status.includes("HIGH")) {
    return "He3 pot heater";
  }
  if (status.includes("LOW") || status.includes("REGEN") || status.includes("COOL")) {
    return "He3 sorb heater";
  }
  if (fallbackMode) {
    return fallbackMode;
  }
  return "--";
}

function formatHelioxSampleRegion(sampleTemperatureK, metadata) {
  if (sampleTemperatureK === null || sampleTemperatureK === undefined || !metadata) {
    return "--";
  }
  if (
    metadata.controlModeCrossoverK !== null
    && metadata.controlModeCrossoverK !== undefined
  ) {
    if (sampleTemperatureK < metadata.controlModeCrossoverK) {
      return "Below crossover";
    }
    if (sampleTemperatureK > metadata.controlModeCrossoverK) {
      return "Above crossover";
    }
  }
  return "Near crossover";
}

function formatHelioxOperationHint(sampleTemperatureK, metadata) {
  if (sampleTemperatureK === null || sampleTemperatureK === undefined || !metadata) {
    return "--";
  }
  if (
    metadata.he3SorbRegenK !== null
    && metadata.he3SorbRegenK !== undefined
    && sampleTemperatureK >= metadata.he3SorbRegenK
  ) {
    return "Regen range";
  }
  if (
    metadata.he3SorbHighTempControlK !== null
    && metadata.he3SorbHighTempControlK !== undefined
    && sampleTemperatureK >= metadata.he3SorbHighTempControlK
  ) {
    return "High-temp sorb range";
  }
  if (
    metadata.he3PotBoiloffK !== null
    && metadata.he3PotBoiloffK !== undefined
    && sampleTemperatureK >= metadata.he3PotBoiloffK
  ) {
    return "Boiloff-sensitive range";
  }
  if (
    metadata.potEmptyK !== null
    && metadata.potEmptyK !== undefined
    && sampleTemperatureK <= metadata.potEmptyK
  ) {
    return "Near empty-pot threshold";
  }
  if (
    metadata.condensedTempK !== null
    && metadata.condensedTempK !== undefined
    && Math.abs(sampleTemperatureK - metadata.condensedTempK) <= 0.1
  ) {
    return "Near condensed temperature";
  }
  return "Normal operating range";
}

function formatHelioxNeedleRefs(metadata) {
  if (!metadata) {
    return "--";
  }
  const lt = formatNumber(metadata.needleValveLowTempMbar, 1);
  const rc = formatNumber(metadata.needleValveRecondenseMbar, 1);
  const ht = formatNumber(metadata.needleValveHighTempMbar, 1);
  if (lt === "--" && rc === "--" && ht === "--") {
    return "--";
  }
  return `LT ${lt} / RCON ${rc} / HT ${ht} mB`;
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
  setText("configLogPath", formatText(config.log_dir));
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
  stopReconnect();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/state`);

  socket.addEventListener("open", () => {
    stopPolling();
    setBadge("connectionBadge", "Live", "ok");
    addEvent("Connected");
  });

  socket.addEventListener("message", (event) => {
    render(JSON.parse(event.data));
  });

  socket.addEventListener("close", () => {
    const wasPolling = state.pollingTimer !== null;
    setBadge("connectionBadge", "Polling", "warn");
    if (!wasPolling) {
      addEvent("Disconnected");
    }
    startPolling();
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    setBadge("connectionBadge", "Polling", "warn");
  });
}

function initializePlotSeriesSelection() {
  for (const series of Object.values(PLOT_SERIES_GROUPS)) {
    for (const item of series) {
      if (!(item.key in state.plotSeriesEnabled)) {
        state.plotSeriesEnabled[item.key] = true;
      }
    }
  }
}

function selectedPlotSeries(group) {
  const definitions = PLOT_SERIES_GROUPS[group] || [];
  return definitions.filter((item) => state.plotSeriesEnabled[item.key] !== false);
}

function renderPlotLegendControls() {
  document.querySelectorAll(".legend[data-series-group]").forEach((container) => {
    const group = container.dataset.seriesGroup;
    const definitions = PLOT_SERIES_GROUPS[group] || [];
    container.replaceChildren();
    for (const item of definitions) {
      const label = document.createElement("label");
      label.className = `legend-toggle${state.plotSeriesEnabled[item.key] === false ? " disabled" : ""}`;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.plotSeriesEnabled[item.key] !== false;
      checkbox.addEventListener("change", () => {
        state.plotSeriesEnabled[item.key] = checkbox.checked;
        renderPlotLegendControls();
        renderCharts();
      });

      const swatch = document.createElement("i");
      swatch.style.setProperty("--series-color", item.color);

      const text = document.createElement("span");
      text.textContent = item.label;

      label.append(checkbox, swatch, text);
      container.appendChild(label);
    }
  });
}

function pollingIntervalMs() {
  const pollSeconds = state.config && Number.isFinite(Number(state.config.poll_interval_s))
    ? Number(state.config.poll_interval_s)
    : 1;
  return Math.max(500, Math.round(pollSeconds * 1000));
}

function stopPolling() {
  if (state.pollingTimer !== null) {
    window.clearInterval(state.pollingTimer);
    state.pollingTimer = null;
  }
}

function stopReconnect() {
  if (state.reconnectTimer !== null) {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function scheduleReconnect() {
  if (state.reconnectTimer !== null) {
    return;
  }
  state.reconnectTimer = window.setTimeout(() => {
    state.reconnectTimer = null;
    connectWebSocket();
  }, 1500);
}

function startPolling() {
  if (state.pollingTimer !== null) {
    return;
  }
  const poll = async () => {
    try {
      render(await fetchStateSnapshot());
      setBadge("connectionBadge", "Polling", "warn");
    } catch (error) {
      setBadge("connectionBadge", "Offline", "error");
    }
  };
  poll();
  state.pollingTimer = window.setInterval(poll, pollingIntervalMs());
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

async function shutdownService() {
  await postJson("/shutdown");
  stopPolling();
  stopReconnect();
  setBadge("connectionBadge", "Stopping", "warn");
  addEvent("Shutdown requested");
}

function currentRecipeStepFromForm() {
  const type = el("recipeStepType").value;
  if (type === "ramp_temperature") {
    return {
      type,
      loop: el("recipeLoop").value,
      target_K: Number(el("recipeTargetK").value),
      rate_K_per_min: Number(el("recipeRateK").value),
      tolerance_K: Number(el("recipeToleranceK").value),
      stable_s: Number(el("recipeStableS").value),
    };
  }
  if (type === "set_temperature_target") {
    return {
      type,
      loop: el("recipeLoop").value,
      target_K: Number(el("recipeTargetK").value),
    };
  }
  if (type === "ramp_field") {
    return {
      type,
      target_T: Number(el("recipeTargetT").value),
      rate_T_per_min: Number(el("recipeRateT").value),
      tolerance_T: Number(el("recipeToleranceT").value),
      stable_s: Number(el("recipeStableS").value),
    };
  }
  if (type === "ramp_to_zero") {
    return {
      type,
      rate_T_per_min: Number(el("recipeRateT").value),
      tolerance_T: Number(el("recipeToleranceT").value),
      stable_s: Number(el("recipeStableS").value),
    };
  }
  if (type === "wait") {
    return {
      type,
      duration_s: Number(el("recipeWaitS").value),
    };
  }
  return {
    type: "signal",
    signal: el("recipeSignal").value || "measurement_done",
    message: el("recipeNotice").value || "Continue when ready",
  };
}

function recipeFormNameInput() {
  return document.querySelector("#recipeForm input[name='name']");
}

function applyRecipeDefinition(recipe) {
  const nameInput = recipeFormNameInput();
  if (nameInput) {
    nameInput.value = recipe.name || "Recipe";
  }
  state.recipeSteps = Array.isArray(recipe.steps) ? recipe.steps : [];
  renderRecipeSteps();
}

function selectedSavedRecipeId() {
  const select = el("savedRecipeSelect");
  return select ? select.value : "";
}

function selectedSavedRecipe() {
  const recipeId = selectedSavedRecipeId();
  return state.savedRecipes.find((recipe) => recipe.id === recipeId) || null;
}

function formatSavedRecipeOption(recipe) {
  const stepLabel = recipe.step_count === 1 ? "1 step" : `${recipe.step_count} steps`;
  const updated = recipe.updated_at ? new Date(recipe.updated_at).toLocaleString() : "--";
  return `${recipe.name} | ${stepLabel} | ${updated}`;
}

function recipeStepSummary(step) {
  if (step.type === "ramp_temperature") {
    return `Ramp ${step.loop} to ${formatNumber(step.target_K, 3)} K at ${formatNumber(step.rate_K_per_min, 3)} K/min, wait ±${formatNumber(step.tolerance_K, 3)} K`;
  }
  if (step.type === "set_temperature_target") {
    return `Set ${step.loop} target ${formatNumber(step.target_K, 3)} K`;
  }
  if (step.type === "ramp_field") {
    return `Ramp B to ${formatNumber(step.target_T, 4)} T at ${formatNumber(step.rate_T_per_min, 4)} T/min, wait IPS`;
  }
  if (step.type === "ramp_to_zero") {
    return `Ramp B to zero at ${formatNumber(step.rate_T_per_min, 4)} T/min, wait IPS`;
  }
  if (step.type === "wait") {
    return `Wait ${formatNumber(step.duration_s, 0)} s`;
  }
  if (step.type === "signal" || step.type === "notice") {
    return `Wait signal ${step.signal || "manual"}: ${step.message || "Continue when ready"}`;
  }
  return formatText(step.type);
}

function renderSavedRecipes() {
  const select = el("savedRecipeSelect");
  if (!select) {
    return;
  }
  const previousValue = select.value;
  select.replaceChildren();
  if (!state.savedRecipes.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No saved recipes";
    select.appendChild(option);
    select.disabled = true;
    updateRecipeControlState();
    return;
  }
  select.disabled = false;
  state.savedRecipes.forEach((recipe) => {
    const option = document.createElement("option");
    option.value = recipe.id;
    option.textContent = formatSavedRecipeOption(recipe);
    select.appendChild(option);
  });
  select.value = state.savedRecipes.some((recipe) => recipe.id === previousValue)
    ? previousValue
    : state.savedRecipes[0].id;
  updateRecipeControlState();
}

async function loadSavedRecipes(selectedRecipeId = null) {
  const payload = await fetchJson("/recipes");
  state.savedRecipes = Array.isArray(payload.recipes) ? payload.recipes : [];
  renderSavedRecipes();
  if (selectedRecipeId && state.savedRecipes.some((recipe) => recipe.id === selectedRecipeId)) {
    el("savedRecipeSelect").value = selectedRecipeId;
    updateRecipeControlState();
  }
}

function renderRecipeSteps() {
  const list = el("recipeSteps");
  list.replaceChildren();
  if (!state.recipeSteps.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No recipe steps.";
    list.appendChild(item);
    updateRecipeControlState();
    return;
  }
  state.recipeSteps.forEach((step, index) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = recipeStepSummary(step);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.recipeSteps.splice(index, 1);
      renderRecipeSteps();
    });
    item.append(label, remove);
    list.appendChild(item);
  });
  updateRecipeControlState();
}

function syncRecipeStepInputs() {
  const type = el("recipeStepType").value;
  document.querySelectorAll(".recipe-temperature-input")
    .forEach((node) => node.classList.toggle("hidden", !["ramp_temperature", "set_temperature_target"].includes(type)));
  document.querySelectorAll(".recipe-temperature-ramp-input")
    .forEach((node) => node.classList.toggle("hidden", type !== "ramp_temperature"));
  document.querySelectorAll(".recipe-field-input")
    .forEach((node) => node.classList.toggle("hidden", type !== "ramp_field"));
  document.querySelectorAll(".recipe-field-rate-input")
    .forEach((node) => node.classList.toggle("hidden", !["ramp_field", "ramp_to_zero"].includes(type)));
  document.querySelectorAll(".recipe-ramp-wait-input")
    .forEach((node) => node.classList.toggle("hidden", !["ramp_temperature", "ramp_field", "ramp_to_zero"].includes(type)));
  document.querySelectorAll(".recipe-wait-input")
    .forEach((node) => node.classList.toggle("hidden", type !== "wait"));
  document.querySelectorAll(".recipe-signal-input")
    .forEach((node) => node.classList.toggle("hidden", type !== "signal"));
}

function updateRecipeControlState(status = null) {
  const readOnly = Boolean(state.config && state.config.read_only);
  const activeStatus = status || (el("recipeStatus") ? el("recipeStatus").textContent : "idle");
  const running = ["running", "waiting_signal"].includes(activeStatus);
  const waitingNotice = activeStatus === "waiting_signal";
  const hasSavedRecipe = Boolean(selectedSavedRecipeId());
  const startButton = el("startRecipeButton");
  const saveButton = el("saveRecipeButton");
  const loadButton = el("loadRecipeButton");
  const renameButton = el("renameRecipeButton");
  const duplicateButton = el("duplicateRecipeButton");
  const deleteButton = el("deleteRecipeButton");
  const ackButton = el("ackRecipeButton");
  const abortButton = el("abortRecipeButton");
  if (startButton) {
    startButton.disabled = readOnly || running || !state.recipeSteps.length;
  }
  if (saveButton) {
    saveButton.disabled = readOnly || !state.recipeSteps.length;
  }
  if (loadButton) {
    loadButton.disabled = !hasSavedRecipe;
  }
  if (renameButton) {
    renameButton.disabled = readOnly || !hasSavedRecipe;
  }
  if (duplicateButton) {
    duplicateButton.disabled = readOnly || !hasSavedRecipe;
  }
  if (deleteButton) {
    deleteButton.disabled = readOnly || !hasSavedRecipe;
  }
  if (ackButton) {
    ackButton.disabled = readOnly || !waitingNotice;
  }
  if (abortButton) {
    abortButton.disabled = readOnly || !running;
  }
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
  el("shutdownButton").addEventListener("click", async () => {
    if (!window.confirm("Stop this Teslatron service instance and free its port?")) {
      return;
    }
    const button = el("shutdownButton");
    button.disabled = true;
    try {
      await shutdownService();
      const message = el("commandMessage");
      message.className = "message";
      message.textContent = "Shutdown requested";
    } catch (error) {
      button.disabled = false;
      const message = el("commandMessage");
      message.className = "message error";
      message.textContent = error.message;
      addEvent("Shutdown failed");
    }
  });
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
  bindRecipes();
}

function bindRecipes() {
  syncRecipeStepInputs();
  renderRecipeSteps();
  renderSavedRecipes();
  el("savedRecipeSelect").addEventListener("change", () => updateRecipeControlState());
  el("recipeStepType").addEventListener("change", syncRecipeStepInputs);
  el("addRecipeStepButton").addEventListener("click", () => {
    state.recipeSteps.push(currentRecipeStepFromForm());
    renderRecipeSteps();
  });
  el("clearRecipeButton").addEventListener("click", () => {
    state.recipeSteps = [];
    renderRecipeSteps();
  });
  el("recipeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get("name") || "Recipe";
    await runRecipeCommand(() => postJson("/recipes/start", {
      name,
      steps: state.recipeSteps,
    }), "Recipe start");
  });
  el("saveRecipeButton").addEventListener("click", async () => {
    const nameInput = recipeFormNameInput();
    const name = nameInput ? nameInput.value : "Recipe";
    await runRecipeCommand(async () => {
      const saved = await postJson("/recipes/save", {
        name,
        steps: state.recipeSteps,
      });
      await loadSavedRecipes(saved.id);
      if (saved.id) {
        el("savedRecipeSelect").value = saved.id;
      }
    }, "Recipe save");
  });
  el("loadRecipeButton").addEventListener("click", async () => {
    const recipeId = el("savedRecipeSelect").value;
    await runRecipeCommand(async () => {
      const recipe = await fetchJson(`/recipes/${encodeURIComponent(recipeId)}`);
      applyRecipeDefinition(recipe);
    }, "Recipe load");
  });
  el("renameRecipeButton").addEventListener("click", async () => {
    const recipe = selectedSavedRecipe();
    if (!recipe) {
      return;
    }
    const nextName = window.prompt("Rename saved recipe", recipe.name);
    if (!nextName || nextName === recipe.name) {
      return;
    }
    await runRecipeCommand(async () => {
      const renamed = await postJson(`/recipes/${encodeURIComponent(recipe.id)}/rename`, {
        new_name: nextName,
      });
      await loadSavedRecipes(renamed.id);
      applyRecipeDefinition(await fetchJson(`/recipes/${encodeURIComponent(renamed.id)}`));
    }, "Recipe rename");
  });
  el("duplicateRecipeButton").addEventListener("click", async () => {
    const recipe = selectedSavedRecipe();
    if (!recipe) {
      return;
    }
    const suggestedName = `${recipe.name} copy`;
    const nextName = window.prompt("Duplicate saved recipe as", suggestedName);
    if (!nextName) {
      return;
    }
    await runRecipeCommand(async () => {
      const duplicated = await postJson(`/recipes/${encodeURIComponent(recipe.id)}/duplicate`, {
        new_name: nextName,
      });
      await loadSavedRecipes(duplicated.id);
    }, "Recipe duplicate");
  });
  el("deleteRecipeButton").addEventListener("click", async () => {
    const recipe = selectedSavedRecipe();
    if (!recipe || !window.confirm(`Delete saved recipe "${recipe.name}"?`)) {
      return;
    }
    await runRecipeCommand(async () => {
      await deleteJson(`/recipes/${encodeURIComponent(recipe.id)}`);
      await loadSavedRecipes();
    }, "Recipe delete");
  });
  el("ackRecipeButton").addEventListener("click", () => {
    runRecipeCommand(() => postJson("/recipes/acknowledge"), "Recipe continue");
  });
  el("abortRecipeButton").addEventListener("click", () => {
    runRecipeCommand(() => postJson("/recipes/abort"), "Recipe abort");
  });
}

async function runRecipeCommand(action, label) {
  const message = el("recipeMessage");
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
    initializePlotSeriesSelection();
    renderPlotLegendControls();
    return loadSavedRecipes();
  })
  .then(() => {
    startPolling();
    connectWebSocket();
  })
  .catch((error) => {
    setBadge("connectionBadge", "Config error", "error");
    addEvent(error.message);
  });

function renderCharts() {
  renderPlotLegendControls();
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
    selectedPlotSeries("temperature"),
    "Temperature (K)",
  );
  drawTimeSeries(
    el(`${prefix}MagneticsChart`),
    points,
    selectedPlotSeries("magnetics"),
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
  if (!series.length) {
    drawNoData(ctx, plot, scale, "Select at least one series");
    return;
  }
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

function drawNoData(ctx, plot, scale, message = "Waiting for data") {
  ctx.fillStyle = "#93a2af";
  ctx.font = `${13 * scale}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillText(message, plot.x + plot.w / 2, plot.y + plot.h / 2);
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
