async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.clone().json();
  } catch (err) {
    data = {};
  }
  if (!response.ok || data.error) {
    const message = data.error || `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

const api = {
  status: () => fetchJson("/dellfans/api/status"),
  mode: (body) =>
    fetchJson("/dellfans/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  pwm: (value) =>
    fetchJson("/dellfans/api/pwm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }),
  limits: (min_pwm, max_pwm) =>
    fetchJson("/dellfans/api/limits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_pwm, max_pwm }),
    }),
  pollInterval: (seconds) =>
    fetchJson("/dellfans/api/poll_interval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds }),
    }),
};

const ui = {
  slider: document.getElementById("pwm-slider"),
  sliderDisplay: document.getElementById("pwm-display"),
  pwmInput: document.getElementById("pwm-input"),
  btnAuto: document.getElementById("btn-auto"),
  btnManual: document.getElementById("btn-manual"),
  applyPwm: document.getElementById("apply-pwm"),
  applyLimits: document.getElementById("apply-limits"),
  minInput: document.getElementById("min-pwm"),
  maxInput: document.getElementById("max-pwm"),
  pollInput: document.getElementById("poll-interval"),
  applyInterval: document.getElementById("apply-interval"),
  tempCards: document.getElementById("temp-cards"),
  fanTable: document.getElementById("fan-table"),
  statusList: document.getElementById("status-list"),
  errorLog: document.getElementById("error-log"),
  lastRefresh: document.getElementById("last-refresh"),
};

function showError(message) {
  ui.errorLog.textContent = message;
}

function clearError() {
  ui.errorLog.textContent = "";
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function setSliderValue(value) {
  const min = parseInt(ui.slider.min, 10);
  const max = parseInt(ui.slider.max, 10);
  let numeric = parseInt(value, 10);
  if (Number.isNaN(numeric)) {
    numeric = min;
  }
  const clamped = clamp(numeric, min, max);
  ui.slider.value = clamped;
  ui.sliderDisplay.textContent = clamped;
  ui.pwmInput.value = clamped;
}

function previewPwmInput(value) {
  if (value === "") {
    ui.sliderDisplay.textContent = "--";
    return;
  }
  const numeric = parseInt(value, 10);
  if (Number.isNaN(numeric)) {
    ui.sliderDisplay.textContent = value;
    return;
  }
  const min = parseInt(ui.slider.min, 10);
  const max = parseInt(ui.slider.max, 10);
  if (numeric < min || numeric > max) {
    ui.sliderDisplay.textContent = numeric;
    return;
  }
  setSliderValue(numeric);
}

function isAdjustingPwm() {
  const active = document.activeElement;
  return active === ui.slider || active === ui.pwmInput;
}

function updateTemps(telemetry) {
  const cpu1 = telemetry.cpu.cpu1?.value ?? "--";
  const cpu2 = telemetry.cpu.cpu2?.value ?? "--";
  const inlet = telemetry.temps.inlet?.value ?? "--";
  const exhaust = telemetry.temps.exhaust?.value ?? "--";

  ui.tempCards.querySelectorAll(".card .value")[0].textContent = `${cpu1} °C`;
  ui.tempCards.querySelectorAll(".card .value")[1].textContent = `${cpu2} °C`;
  ui.tempCards.querySelectorAll(".card .value")[2].textContent = `${inlet} °C`;
  ui.tempCards.querySelectorAll(".card .value")[3].textContent = `${exhaust} °C`;
}

function updateFans(list) {
  ui.fanTable.innerHTML = "";
  list.forEach((fan) => {
    const row = document.createElement("tr");
    const value =
      typeof fan.value === "number" ? fan.value.toFixed(0) : fan.value;
    row.innerHTML = `<td>${fan.name}</td><td>${value} ${fan.unit}</td>`;
    ui.fanTable.appendChild(row);
  });
}

function updateStatus(status) {
  ui.statusList.innerHTML = "";
  const modeItem = document.createElement("li");
  modeItem.textContent = `当前模式: ${status.controller.mode}`;
  ui.statusList.appendChild(modeItem);
  const pwmItem = document.createElement("li");
  pwmItem.textContent = `目标转速: ${status.controller.target_pwm}%`;
  ui.statusList.appendChild(pwmItem);

  ui.errorLog.innerHTML = (status.telemetry.errors || [])
    .map((err) => `<div>${err}</div>`)
    .join("");

  const ts = status.telemetry.timestamp
    ? new Date(status.telemetry.timestamp * 1000).toLocaleString()
    : "--";
  ui.lastRefresh.textContent = ts;
}

async function refresh() {
  try {
    const status = await api.status();
    clearError();
    ui.slider.min = status.controller.min_pwm;
    ui.slider.max = status.controller.max_pwm;
    ui.pwmInput.min = status.controller.min_pwm;
    ui.pwmInput.max = status.controller.max_pwm;
    if (!isAdjustingPwm()) {
      setSliderValue(status.controller.target_pwm);
    }
    ui.minInput.value = status.controller.min_pwm;
    ui.maxInput.value = status.controller.max_pwm;
    ui.pollInput.value = status.config.poll_interval_seconds;
    updateTemps(status.telemetry);
    updateFans(status.telemetry.fans || []);
    updateStatus(status);
  } catch (err) {
    showError(`刷新失败: ${err.message || err}`);
  }
}

ui.slider.addEventListener("input", (e) => setSliderValue(e.target.value));
ui.pwmInput.addEventListener("input", (e) => previewPwmInput(e.target.value));
ui.pwmInput.addEventListener("blur", () => setSliderValue(ui.pwmInput.value));

ui.btnAuto.addEventListener("click", async () => {
  ui.btnAuto.disabled = true;
  try {
    await api.mode({ mode: "auto" });
    await refresh();
  } catch (err) {
    showError(`切换自动失败: ${err.message || err}`);
  } finally {
    ui.btnAuto.disabled = false;
  }
});

ui.btnManual.addEventListener("click", async () => {
  ui.btnManual.disabled = true;
  try {
    await api.mode({ mode: "manual", target_pwm: parseInt(ui.slider.value, 10) });
    await refresh();
  } catch (err) {
    showError(`切换手动失败: ${err.message || err}`);
  } finally {
    ui.btnManual.disabled = false;
  }
});

ui.applyPwm.addEventListener("click", async () => {
  ui.applyPwm.disabled = true;
  try {
    await api.pwm(parseInt(ui.slider.value, 10));
    await refresh();
  } catch (err) {
    showError(`设置转速失败: ${err.message || err}`);
  } finally {
    ui.applyPwm.disabled = false;
  }
});

ui.applyLimits.addEventListener("click", async () => {
  const minVal = parseInt(ui.minInput.value, 10);
  const maxVal = parseInt(ui.maxInput.value, 10);
  ui.applyLimits.disabled = true;
  try {
    await api.limits(minVal, maxVal);
    await refresh();
  } catch (err) {
    showError(`保存上下限失败: ${err.message || err}`);
  } finally {
    ui.applyLimits.disabled = false;
  }
});

ui.applyInterval.addEventListener("click", async () => {
  const seconds = parseFloat(ui.pollInput.value);
  ui.applyInterval.disabled = true;
  try {
    await api.pollInterval(seconds);
    await refresh();
  } catch (err) {
    showError(`保存刷新周期失败: ${err.message || err}`);
  } finally {
    ui.applyInterval.disabled = false;
  }
});

refresh();
setInterval(refresh, 3500);
