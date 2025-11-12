const api = {
  status: () => fetch("/dellfans/api/status").then((r) => r.json()),
  mode: (body) =>
    fetch("/dellfans/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  pwm: (value) =>
    fetch("/dellfans/api/pwm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }).then((r) => r.json()),
  limits: (min_pwm, max_pwm) =>
    fetch("/dellfans/api/limits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_pwm, max_pwm }),
    }).then((r) => r.json()),
  pollInterval: (seconds) =>
    fetch("/dellfans/api/poll_interval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds }),
    }).then((r) => r.json()),
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

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function setSliderValue(value) {
  const min = parseInt(ui.slider.min, 10);
  const max = parseInt(ui.slider.max, 10);
  const clamped = clamp(parseInt(value, 10) || min, min, max);
  ui.slider.value = clamped;
  ui.sliderDisplay.textContent = clamped;
  ui.pwmInput.value = clamped;
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
    ui.slider.min = status.controller.min_pwm;
    ui.slider.max = status.controller.max_pwm;
    ui.pwmInput.min = status.controller.min_pwm;
    ui.pwmInput.max = status.controller.max_pwm;
    setSliderValue(status.controller.target_pwm);
    ui.minInput.value = status.controller.min_pwm;
    ui.maxInput.value = status.controller.max_pwm;
    ui.pollInput.value = status.config.poll_interval_seconds;
    updateTemps(status.telemetry);
    updateFans(status.telemetry.fans || []);
    updateStatus(status);
  } catch (err) {
    ui.errorLog.textContent = `刷新失败: ${err}`;
  }
}

ui.slider.addEventListener("input", (e) => setSliderValue(e.target.value));
ui.pwmInput.addEventListener("input", (e) => setSliderValue(e.target.value));

ui.btnAuto.addEventListener("click", async () => {
  ui.btnAuto.disabled = true;
  await api.mode({ mode: "auto" });
  ui.btnAuto.disabled = false;
  refresh();
});

ui.btnManual.addEventListener("click", async () => {
  ui.btnManual.disabled = true;
  await api.mode({ mode: "manual", target_pwm: parseInt(ui.slider.value, 10) });
  ui.btnManual.disabled = false;
  refresh();
});

ui.applyPwm.addEventListener("click", async () => {
  ui.applyPwm.disabled = true;
  await api.pwm(parseInt(ui.slider.value, 10));
  ui.applyPwm.disabled = false;
  refresh();
});

ui.applyLimits.addEventListener("click", async () => {
  const minVal = parseInt(ui.minInput.value, 10);
  const maxVal = parseInt(ui.maxInput.value, 10);
  ui.applyLimits.disabled = true;
  await api.limits(minVal, maxVal);
  ui.applyLimits.disabled = false;
  refresh();
});

ui.applyInterval.addEventListener("click", async () => {
  const seconds = parseFloat(ui.pollInput.value);
  ui.applyInterval.disabled = true;
  await api.pollInterval(seconds);
  ui.applyInterval.disabled = false;
  refresh();
});

refresh();
setInterval(refresh, 3500);
