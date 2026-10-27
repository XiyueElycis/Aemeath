/* ============================================================
   AEMEATH AGENT · 启动动画编排
   七阶段：黑场 → Logo → PV → 认证 → 系统启动 → 故障 → 过曝洗白
   ============================================================ */

"use strict";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------
   1) 偏好：URL 参数（宿主注入）+ localStorage（独立浏览器兜底）
   ------------------------------------------------------------ */
const qs = new URLSearchParams(location.search);
const prefs = {
  mode: localStorage.getItem("aemeath.mode") || "always",
  sound: (qs.get("sound") ?? localStorage.getItem("aemeath.sound") ?? "1") === "1",
  volume: Number(qs.get("vol") ?? localStorage.getItem("aemeath.vol") ?? 60),
};

/* 是否处于 WPF WebView2 宿主中 */
const host = window.chrome && window.chrome.webview ? window.chrome.webview : null;
function postHost(obj) {
  try { host && host.postMessage(JSON.stringify(obj)); } catch (_) { /* 非宿主 */ }
}

/* ------------------------------------------------------------
   2) 音效：WebAudio 轻量合成（克制、低音量）
   ------------------------------------------------------------ */
const Sound = (() => {
  let ctx = null, master = null, ambientNodes = [];

  function ensure() {
    if (ctx) { if (ctx.state === "suspended") ctx.resume(); return; }
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain();
      master.gain.value = (prefs.volume / 100) * 0.5;
      master.connect(ctx.destination);
    } catch (_) { ctx = null; }
  }

  function beep(freq, dur = 0.08, type = "sine", gain = 0.08, when = 0) {
    if (!prefs.sound) return;
    ensure(); if (!ctx) return;
    const t = ctx.currentTime + when;
    const osc = ctx.createOscillator(), g = ctx.createGain();
    osc.type = type; osc.frequency.value = freq;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g); g.connect(master);
    osc.start(t); osc.stop(t + dur + 0.02);
  }

  function startAmbient() {
    if (!prefs.sound || ambientNodes.length) return;
    ensure(); if (!ctx) return;
    // 低频环境氛围：两个失谐三角波 + 慢 LFO，极弱
    [55, 55.4].forEach((f) => {
      const osc = ctx.createOscillator(), g = ctx.createGain();
      osc.type = "triangle"; osc.frequency.value = f; g.gain.value = 0.012;
      osc.connect(g); g.connect(master); osc.start();
      ambientNodes.push(osc, g);
    });
  }

  const sfx = {
    boot: () => beep(880, 0.1, "sine", 0.05),
    tick: () => beep(1240, 0.03, "square", 0.015),
    granted: () => { beep(523, .18, "sine", .06); beep(659, .2, "sine", .05, .1); beep(784, .28, "sine", .05, .2); },
    glitch: () => beep(140, .18, "sawtooth", .04),
    welcome: () => { beep(1046, .3, "sine", .04); beep(1318, .4, "sine", .03, .08); },
  };

  function setVolume(v) {
    prefs.volume = v;
    if (master) master.gain.value = (v / 100) * 0.5;
  }

  return { ensure, startAmbient, sfx, setVolume };
})();

/* ------------------------------------------------------------
   3) 全局状态
   ------------------------------------------------------------ */
let aborted = false;        // 用户跳过
let finished = false;       // 动画已完成
let hudTimer = null, seqTimer = null;

/* ------------------------------------------------------------
   4) 装饰生成：DNA 横档 + 神经网络节点 + 星空层
   ------------------------------------------------------------ */
/* 星空层：为三层 <i> 生成 box-shadow 星点串（60/35/15 颗）。
   层色：浅紫白 / 爱弥斯粉 / 冰蓝；闪烁与漂移由 style.css 动画驱动。 */
function generateStars() {
  const layers = [
    { count: 60, color: "237,233,247", alpha: 0.9 },
    { count: 35, color: "255,143,199", alpha: 0.75 },
    { count: 15, color: "74,138,240", alpha: 0.7 },
  ];
  const w = window.innerWidth || 1600;
  const h = window.innerHeight || 900;
  document.querySelectorAll(".starfield i").forEach((el, li) => {
    const cfg = layers[li] || layers[0];
    const points = [];
    for (let i = 0; i < cfg.count; i++) {
      const x = Math.round(Math.random() * w);
      const y = Math.round(Math.random() * h);
      const a = (cfg.alpha * (0.4 + Math.random() * 0.6)).toFixed(2);
      points.push(`${x}px ${y}px 0 rgba(${cfg.color},${a})`);
    }
    // 首点占位（box-shadow 串以元素自身位置为基准，元素透明）
    el.style.boxShadow = points.join(",");
  });
}

function buildDna() {
  const g = document.querySelector(".dna-rungs");
  for (let i = 0; i < 9; i++) {
    const y = 16 + i * 21;
    const xa = 40 + 28 * Math.sin(y / 14);
    const xb = 40 - 28 * Math.sin(y / 14);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", xa); line.setAttribute("y1", y);
    line.setAttribute("x2", xb); line.setAttribute("y2", y);
    line.style.animationDelay = `${0.25 + i * 0.07}s`;
    g.appendChild(line);
  }
}

function buildNeural() {
  const layers = [
    { x: 34, n: 4 }, { x: 130, n: 5 }, { x: 226, n: 3 },
  ];
  const nodes = [];
  layers.forEach((L, li) => {
    for (let i = 0; i < L.n; i++) {
      const y = 22 + (140 - 44) * (i / (L.n - 1 || 1));
      nodes.push({ x: L.x, y, li });
    }
  });
  const edges = document.querySelector(".neuron-edges");
  const ns = "http://www.w3.org/2000/svg";
  // 全连接相邻层
  nodes.filter((n) => n.li === 0).forEach((a) => {
    nodes.filter((n) => n.li === 1).forEach((b, k) => {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      line.style.animationDelay = `${(k % 5) * 0.06}s`;
      edges.appendChild(line);
    });
  });
  nodes.filter((n) => n.li === 1).forEach((a, k) => {
    nodes.filter((n) => n.li === 2).forEach((b) => {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      line.style.animationDelay = `${0.2 + (k % 5) * 0.06}s`;
      edges.appendChild(line);
    });
  });
  const dots = document.querySelector(".neuron-nodes");
  nodes.forEach((n, k) => {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", n.x); c.setAttribute("cy", n.y); c.setAttribute("r", 3);
    c.style.animationDelay = `${0.3 + (k % 6) * 0.08}s`;
    dots.appendChild(c);
  });
}

/* ------------------------------------------------------------
   5) HUD 数据微变（1Hz 以内）
   ------------------------------------------------------------ */
function jitter(base, amp, digits) {
  return (base + (Math.random() - 0.5) * 2 * amp).toFixed(digits);
}
function tickHud() {
  $("hudTemp").textContent = jitter(36.7, 0.08, 2);
  $("hudOxy").textContent = jitter(98.4, 0.35, 1);
  $("hudNet").textContent = jitter(1.82, 0.25, 2);
  $("hudCell").textContent = jitter(99.98, 0.02, 2);
}
function tickSeq() {
  const hex = Math.floor(Math.random() * 0xffffffff)
    .toString(16).padStart(8, "0").toUpperCase();
  $("seqDigits").textContent = `0x${hex}`;
}

/* ------------------------------------------------------------
   6) 阶段切换工具
   ------------------------------------------------------------ */
async function show(id, extraClass = "") {
  const el = $(id);
  el.classList.add("show");
  if (extraClass) el.classList.add(extraClass);
}
function hide(id) { $(id).classList.remove("show"); }

/* ------------------------------------------------------------
   7) 七阶段编排
   ------------------------------------------------------------ */
async function phase1() {
  await show("ph1"); // 修复：阶段面板默认隐藏（.ph opacity:0），不 show 则打字内容不可见
  $("brandCorner").classList.add("on");
  $("seqCorner").classList.add("on");
  $("footCorner").classList.add("on");
  $("ringSight").classList.add("spin");
  $("scanline").classList.add("run");
  Sound.startAmbient();

  // 终端逐字打字
  const lines = ["AEMEATH", "AI CONSCIOUSNESS TERMINAL", "SECURE TERMINAL"];
  const out = $("bootLines");
  for (const line of lines) {
    for (const ch of line) {
      if (aborted) return;
      out.textContent += ch;
      await sleep(42);
    }
    out.textContent += "\n";
    Sound.sfx.tick();
  }
  out.innerHTML += '<span class="caret"></span>';
  Sound.sfx.boot();
  await sleep(500);
}

async function phase2() {
  hide("ph1");
  $("hudCorner").classList.add("on");
  await show("ph2", "ph2-draw");
  await sleep(1700); // 等待线框生成
  if (aborted) return;
  $("ph2").classList.add("ph2-glow");
  Sound.sfx.tick();
  await sleep(700);  // 粉色光晕
}

async function phase3() {
  hide("ph2"); $("ph2").classList.remove("ph2-draw", "ph2-glow");
  await show("ph3", "ph3-pv");
  Sound.sfx.tick();
  await sleep(2300);
  if (aborted) return;
}

/* 阶段 4：等待用户授权（点击/Enter/Space），4s 无操作自动推进 */
let authorized = false;
async function phase4() {
  hide("ph3"); $("ph3").classList.remove("ph3-pv");
  await show("ph4");
  const btn = $("authBtn");
  btn.focus();

  return new Promise((resolve) => {
    const done = async () => {
      if (authorized) return;
      authorized = true;
      btn.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
      clearTimeout(auto);
      await runAuthScan();
      resolve();
    };
    const onClick = () => { Sound.ensure(); Sound.sfx.granted(); done(); };
    const onKey = (e) => {
      if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
        Sound.ensure(); Sound.sfx.granted(); done();
      }
    };
    btn.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    // 用户离开时不卡死：4s 后自动授权
    const auto = setTimeout(() => { Sound.sfx.granted(); done(); }, 4000);
  });
}

/* 认证扫描动画：扫描框 + 三行状态文字 */
async function runAuthScan() {
  hide("ph4");
  const frame = document.createElement("div");
  frame.className = "auth-scan";
  const msg = document.createElement("div");
  msg.className = "auth-scanmsg";
  document.body.append(frame, msg);
  frame.classList.add("run");

  for (const text of ["SCANNING...", "ANALYZING BIOMETRIC DATA...", "VERIFYING ACCESS..."]) {
    msg.textContent = text;
    Sound.sfx.tick();
    await sleep(380);
  }
  msg.textContent = "ACCESS GRANTED";
  msg.classList.add("granted");
  await sleep(420);
  frame.remove(); msg.remove();
}

async function phase5() {
  await show("ph5");
  const items = document.querySelectorAll("#moduleList li");
  // 模块依次点亮（生物=青绿、系统=冰蓝）
  for (const li of items) {
    if (aborted) return;
    const kind = li.dataset.kind;
    li.classList.add(kind === "bio" ? "lit-bio" : "lit-sys");
    Sound.sfx.tick();
    await sleep(200);
  }
  // 细线进度条 0 → 100%
  for (let p = 0; p <= 100; p += 4) {
    if (aborted) return;
    $("bpFill").style.width = p + "%";
    $("bpPct").textContent = p + "%";
    await sleep(70);
  }
  await sleep(300);
}

async function phase6() {
  hide("ph5");
  await show("ph6");
  $("chromatic").classList.add("run");
  Sound.sfx.glitch();
  await sleep(550);
  $("chromatic").classList.remove("run");
}

async function phase7() {
  hide("ph6");
  // 「洗入星海」：画面从深黑抬升到星海深靛（终点 = 主界面底色）
  $("washin").classList.add("run");
  await sleep(420);
  await show("ph7");
  Sound.sfx.welcome();
  // 洗入完成停留 ~0.9s，随后淡出进入主界面
  await sleep(950);
  if (aborted) return;
}

/* ------------------------------------------------------------
   8) 结束 / 跳过
   ------------------------------------------------------------ */
function finish() {
  if (finished) return;
  finished = true;
  clearInterval(hudTimer); clearInterval(seqTimer);
  // 淡出到主界面底色（暖白），宿主随后呈现主窗口
  const fade = document.createElement("div");
  fade.style.cssText =
    "position:fixed;inset:0;z-index:100;background:#f7f5ef;opacity:0;pointer-events:none;" +
    "transition:opacity .35s ease";
  document.body.appendChild(fade);
  requestAnimationFrame(() => { fade.style.opacity = "1"; });
  setTimeout(() => postHost({ type: "done" }), 380);
}

function skip() {
  if (aborted || finished) return;
  aborted = true;
  finish();
}

/* ------------------------------------------------------------
   9) 设置面板
   ------------------------------------------------------------ */
function syncSettingsUi() {
  document.querySelectorAll("#modeSeg button").forEach((b) =>
    b.classList.toggle("sel", b.dataset.mode === prefs.mode));
  const t = $("soundToggle");
  t.classList.toggle("on", prefs.sound);
  t.textContent = prefs.sound ? "ON" : "OFF";
  $("volumeSlider").value = prefs.volume;
  $("volNum").textContent = prefs.volume;
}

function savePrefs() {
  localStorage.setItem("aemeath.mode", prefs.mode);
  localStorage.setItem("aemeath.sound", prefs.sound ? "1" : "0");
  localStorage.setItem("aemeath.vol", String(prefs.volume));
  postHost({ type: "prefs", mode: prefs.mode, sound: prefs.sound, volume: prefs.volume });
}

function bindSettings() {
  $("settingsBtn").addEventListener("click", () => {
    const p = $("settingsPanel");
    p.hidden = !p.hidden;
    if (!p.hidden) syncSettingsUi();
  });
  $("spClose").addEventListener("click", () => { $("settingsPanel").hidden = true; });
  document.querySelectorAll("#modeSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      prefs.mode = b.dataset.mode; savePrefs(); syncSettingsUi();
    }));
  $("soundToggle").addEventListener("click", () => {
    prefs.sound = !prefs.sound;
    if (prefs.sound) { Sound.ensure(); Sound.startAmbient(); Sound.sfx.boot(); }
    savePrefs(); syncSettingsUi();
  });
  $("volumeSlider").addEventListener("input", (e) => {
    Sound.setVolume(Number(e.target.value));
    savePrefs(); syncSettingsUi();
  });
}

/* ------------------------------------------------------------
   10) 主流程
   ------------------------------------------------------------ */
async function main() {
  generateStars();
  buildDna();
  buildNeural();
  bindSettings();
  syncSettingsUi();

  hudTimer = setInterval(tickHud, 2000);
  seqTimer = setInterval(tickSeq, 900);
  tickHud(); tickSeq();

  $("skipBtn").addEventListener("click", skip);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") skip();
  });

  await phase1(); if (aborted) return finish();
  await phase2(); if (aborted) return finish();
  await phase3(); if (aborted) return finish();
  await phase4(); if (aborted) return finish();
  await phase5(); if (aborted) return finish();
  await phase6(); if (aborted) return finish();
  await phase7();
  finish();
}

main().catch((err) => {
  console.error(err);
  finish();
});
