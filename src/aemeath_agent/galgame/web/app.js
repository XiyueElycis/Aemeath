/* ============================================================
   爱弥斯 · 心动模式 —— 前端行为
   ============================================================ */

const $ = (id) => document.getElementById(id);

const els = {
  cgLayer: $("cgLayer"),
  chatInner: $("chatInner"),
  input: $("input"), sendBtn: $("sendBtn"),
  choices: $("choices"),
  typing: $("typing"), typingSec: $("typingSec"),
  eventBanner: $("eventBanner"),
  eventEmoji: $("eventEmoji"), eventTitle: $("eventTitle"),
  eventTier: $("eventTier"), eventR16: $("eventR16"),
  chapterTitle: $("chapterTitle"), chapterSubtitle: $("chapterSubtitle"),
  turnDots: $("turnDots"),
  worldClock: $("worldClock"), worldMeta: $("worldMeta"),
  connDot: $("connDot"),
  affinityFill: $("affinityFill"), affinityNum: $("affinityNum"),
  affinityLevel: $("affinityLevel"), affinityBox: $("affinityBox"),
  hlChip: $("hlChip"),
  endingOverlay: $("endingOverlay"), endingTitle: $("endingTitle"),
  endingEpilogue: $("endingEpilogue"),
  logBtn: $("logBtn"), logBadge: $("logBadge"),
  memoryDrawer: $("memoryDrawer"), drawerScrim: $("drawerScrim"),
  drawerClose: $("drawerClose"), drawerBody: $("drawerBody"),
  drawerFilters: $("drawerFilters"), drawerSearch: $("drawerSearch"),
  tabRecall: $("tabRecall"), tabNotes: $("tabNotes"), tabPast: $("tabPast"),
  recallCount: $("recallCount"), notesCount: $("notesCount"),
  pastCount: $("pastCount"),
  continueBtn: $("continueBtn"),
};

/* 女友头像（本地静态资源） */
const AVATAR_URL = "assets/avatar.jpg";
/* 台词与动作的分段：（）或() 内为心理活动/动作，「」内为台词 */
const SEGMENT = /(（[^）]*）|\([^)]*\)|「[^」]*」)/g;
/* 相邻两条消息间隔超过这么久，就在中间插一条时间轴分隔线 */
const SEP_GAP_MS = 30 * 60 * 1000;
/* 她主动搭话后给出的快捷回应（纯前端，不需要后端参与） */
const PROACTIVE_REPLIES = ["我回来啦", "刚才在忙，没顾上回你", "想我了吗？"];

let state = null;          // 最近一次 public_state
let bannerTimer = null;
let typingTimer = null;
let sending = false;
/* 玩家最后一次「有动作」的时刻：用于判断页面闲置，触发她主动搭话 */
let lastLocalInputAt = Date.now();
/* 时间轴分隔线的上一个时间点 */
let lastStamp = null;
/* 抽屉状态：当前分页、记忆类型筛选、搜索词、往期周目缓存 */
let drawerTab = "recall";
let drawerKind = "all";
let drawerQuery = "";
let archives = [];
/* CG 清单（启动时取一次）与当前画面 key；失败的 URL 不重试，避免每轮都卡一次加载 */
let artCache = {};
let currentCG = "";
const failedCG = new Set();

function markInput() { lastLocalInputAt = Date.now(); }

/* ---------------- 接口 ---------------- */
async function api(path, options = {}) {
  const resp = await fetch(`api/${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  setConnection(true);
  return resp.json();
}

function setConnection(online) {
  els.connDot.classList.toggle("online", online);
  els.connDot.classList.toggle("offline", !online);
  els.connDot.title = online ? "后端已连接" : "后端未连接：请确认服务已启动";
}

/* ---------------- 初始化 ---------------- */
async function boot() {
  try {
    // CG 清单与往期周目：各只拉一次，失败不影响主流程
    api("art").then((art) => { artCache = art || {}; renderCG(state); })
      .catch(() => {});
    loadArchives().catch(() => {});

    let data = await api("state");
    if (!data.save.started && !data.ended) {
      data = await api("start", { method: "POST" });
    }
    state = data;
    renderAll(data, { keepHistory: false });
    // 她不会永远等着你先开口：隔了很久再进来，让她先打个招呼
    setTimeout(() => { maybeProactive("resume"); }, 800);
  } catch (ex) {
    setConnection(false);
    appendNarration(
      `（无法连接后端：${ex.message}。请先运行 Aemeath 启动服务后再打开本页面）`,
      { warn: true });
  }
}

async function loadArchives() {
  archives = await api("archives") || [];
  renderDrawer();
}

/* ---------------- 渲染 ---------------- */
function renderAll(data, { keepHistory }) {
  state = data;
  renderChapter(data);
  renderAffinity(data.save.affinity, data.affinity_level);
  renderWorld(data.world);
  renderHeartlink(data);
  renderCG(data);
  if (!keepHistory) {
    els.chatInner.innerHTML = "";
    lastStamp = null;
    (data.save.history || []).forEach(renderHistoryItem);
    scrollBottom();
  }
  renderMemoryLog(data);
  if (data.ended) showEnding(data);
}

/* ---------------- CG 画面切换 ---------------- */
/* 后端用 save.cg 告诉我们此刻该看哪张图（chapter:xx / event:xx / ending:xx）。
   远程生成图有失败的可能，所以：加载成功才淡入，失败就保留上一张（最底层是
   本地 background.jpeg 兜底，永远不会黑屏），并且失败的 URL 不再重试。 */
function renderCG(data) {
  if (!data || !data.save) return;
  const key = data.save.cg || "";
  if (!key || key === currentCG) return;
  const url = artCache[key];
  if (!url || failedCG.has(url)) return;

  const layer = document.createElement("div");
  layer.className = "cg-static cg-fade";
  layer.style.backgroundImage = `url("${url}")`;

  const probe = new Image();
  let settled = false;
  probe.onload = () => {
    if (settled) return;
    settled = true;
    currentCG = key;
    els.cgLayer.appendChild(layer);
    requestAnimationFrame(() => layer.classList.add("on"));
    // 淡入完成后清掉旧层，避免 DOM 里堆一摞背景
    setTimeout(() => {
      els.cgLayer.querySelectorAll(".cg-static.cg-fade").forEach((node) => {
        if (node !== layer) node.remove();
      });
    }, 1000);
  };
  probe.onerror = () => {
    if (settled) return;
    settled = true;
    failedCG.add(url);
  };
  probe.src = url;
}

function renderHistoryItem(item) {
  // 老存档没有 at，跳过分隔线（不报错、不补假时间）
  if (item.at) {
    const stamp = new Date(item.at);
    if (!Number.isNaN(stamp.getTime())) maybeTimelineSeparator(stamp);
  }
  if (item.role === "user") appendBubble("user", item.content);
  else if (item.role === "aemeath") appendBubble("aemeath", item.content);
  else if (item.role === "narration") appendNarration(item.content);
  else if (item.role === "event") appendEventLine(item.content);
}

/* ---------------- 时间轴分隔线 ---------------- */
function formatStamp(date) {
  const now = new Date();
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  if (sameDay(date, now)) return `今天 ${hh}:${mm}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return `昨天 ${hh}:${mm}`;
  const md = `${date.getMonth() + 1}月${date.getDate()}日`;
  return date.getFullYear() === now.getFullYear()
    ? `${md} ${hh}:${mm}`
    : `${date.getFullYear()}年${md} ${hh}:${mm}`;
}

/* 间隔够久（跨时段或跨天）才画分隔线，否则一段连续对话会被切碎 */
function maybeTimelineSeparator(date) {
  const t = date.getTime();
  if (Number.isNaN(t)) return;
  if (lastStamp !== null && t - lastStamp < SEP_GAP_MS) {
    lastStamp = t;
    return;
  }
  const sep = document.createElement("div");
  sep.className = "timeline-sep";
  const mkLine = () => {
    const s = document.createElement("span");
    s.className = "timeline-line";
    return s;
  };
  const label = document.createElement("span");
  label.className = "timeline-label";
  label.textContent = formatStamp(date);
  sep.append(mkLine(), label, mkLine());
  els.chatInner.appendChild(sep);
  lastStamp = t;
}

function renderChapter(data) {
  els.chapterTitle.textContent = data.chapter.title;
  els.chapterSubtitle.textContent = data.chapter.subtitle;
  const total = data.chapter.turn_target;
  const done = data.save.chapter_turns;
  els.turnDots.innerHTML = "";
  for (let i = 0; i < total; i++) {
    const dot = document.createElement("i");
    if (i < done) dot.className = "on";
    els.turnDots.appendChild(dot);
  }
}

function renderAffinity(value, level) {
  els.affinityFill.style.width = `${value}%`;
  els.affinityNum.textContent = value;
  const lv = level || (state ? state.affinity_level : "");
  els.affinityLevel.textContent = lv;
  if (state && state.growth_cap) {
    els.affinityBox.title =
      `好感 ${value}/100 · ${lv} · 单轮上涨上限 +${state.growth_cap}（越亲近涨得越慢）`;
  }
}

/* 现实世界 HUD：时钟按本机时间滚动，天气/地点用后端采集值 */
function renderWorld(world) {
  renderClock();
  if (!world) return;
  const bits = [];
  bits.push(`<em>${world.time_period}</em>`);
  if (world.weather) {
    bits.push(`${world.weather}${world.temperature ? " " + world.temperature : ""}`);
  } else {
    bits.push("天气未知");
  }
  const place = world.place_text || world.city;
  if (place) bits.push(place);
  els.worldMeta.innerHTML = bits.join(" · ");
}

function renderClock() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  els.worldClock.textContent = `${hh}:${mm}`;
}

/* 心链进度：已达门槛时高亮提示 */
function renderHeartlink(data) {
  if (!data || !data.heartlink_total) {
    els.hlChip.hidden = true;
    return;
  }
  els.hlChip.hidden = false;
  els.hlChip.textContent = `心链 ${data.heartlink_done}/${data.heartlink_total}`;
  els.hlChip.classList.toggle("ready", !!data.heartlink_ready);
  els.hlChip.title = data.heartlink_ready
    ? "心链事件已解锁：在合适的时机自然会触发"
    : "提升好感度与章节进度可解锁心链事件";
}

/* 好感度浮动数字 + 结算说明 */
function floatAffinity(delta, notes) {
  if (!delta) return;
  const f = document.createElement("span");
  f.className = `affinity-float ${delta > 0 ? "up" : "down"}`;
  f.textContent = `${delta > 0 ? "+" : ""}${delta}`;
  if (notes && notes.length) f.title = notes.join("；");
  els.affinityBox.style.position = "relative";
  els.affinityBox.appendChild(f);
  setTimeout(() => f.remove(), 1500);
}

/* ---------------- 富文本：台词 / 心理活动分色 ---------------- */
function appendRich(container, text) {
  String(text).split(SEGMENT).forEach((part) => {
    if (!part) return;
    let cls = "";
    if (/^（[\s\S]*）$/.test(part) || /^\([\s\S]*\)$/.test(part)) cls = "act";
    else if (/^「[\s\S]*」$/.test(part)) cls = "line";
    if (!cls) {
      container.appendChild(document.createTextNode(part));
      return;
    }
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = part;
    container.appendChild(span);
  });
}

/* ---------------- 消息节点 ---------------- */
function appendBubble(role, text) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${role}`;
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = role === "user" ? "你" : "爱弥斯";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  appendRich(bubble, text);

  if (role === "aemeath") {
    const avatar = document.createElement("img");
    avatar.className = "avatar";
    avatar.src = AVATAR_URL;
    avatar.alt = "爱弥斯";
    avatar.loading = "lazy";
    const body = document.createElement("div");
    body.className = "msg-body";
    body.append(who, bubble);
    wrap.append(avatar, body);
  } else {
    wrap.append(who, bubble);
  }
  els.chatInner.appendChild(wrap);
}

function appendNarration(text, { warn = false } = {}) {
  const d = document.createElement("div");
  d.className = warn ? "narration warn" : "narration";
  d.textContent = text;
  els.chatInner.appendChild(d);
}

function appendEventLine(text, { heartlink = false } = {}) {
  const d = document.createElement("div");
  d.className = heartlink ? "event-line heartlink" : "event-line";
  d.textContent = text;
  els.chatInner.appendChild(d);
}

function scrollBottom() {
  requestAnimationFrame(() => {
    els.chatInner.scrollTop = els.chatInner.scrollHeight;
  });
}

/* ---------------- 事件横幅与选项 ---------------- */
function showEventBanner(event) {
  if (!event) return;
  const isHl = event.tier === "heartlink";
  els.eventEmoji.textContent = event.emoji;
  els.eventTitle.textContent = event.title;
  els.eventTier.textContent = isHl ? "心链事件" : "事件";
  els.eventR16.hidden = event.rating !== "r16";
  els.eventBanner.classList.toggle("heartlink", isHl);
  els.eventBanner.classList.add("show");
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(
    () => els.eventBanner.classList.remove("show"),
    isHl ? 5200 : 3200);
}

function renderChoices(event) {
  els.choices.innerHTML = "";
  if (!event || !event.choices || event.phase === "end") return;
  event.choices.forEach((text) => {
    const b = document.createElement("button");
    b.className = "choice-pill";
    b.textContent = text;
    b.onclick = () => {
      els.input.value = text;
      doSend();
    };
    els.choices.appendChild(b);
  });
}

/* 她主动搭话时给几个顺手的回应，省得玩家对着空输入框发愣 */
function renderQuickReplies(list) {
  els.choices.innerHTML = "";
  list.forEach((text) => {
    const b = document.createElement("button");
    b.className = "choice-pill quick";
    b.textContent = text;
    b.onclick = () => {
      els.input.value = text;
      doSend();
    };
    els.choices.appendChild(b);
  });
}

/* ---------------- 回忆录 & 信箱 ---------------- */
function renderMemoryLog(data) {
  if (!data) return;
  const log = data.memory_log || [];
  const notes = data.notes || [];

  els.logBadge.hidden = log.length === 0 && notes.length === 0;
  els.logBadge.textContent = notes.length ? String(notes.length) : String(log.length);
  els.logBadge.classList.toggle("alert", notes.length > 0);
  els.logBtn.title = notes.length
    ? `回忆录 · 有 ${notes.length} 条留言她还没读到`
    : `回忆录 · 她记得 ${log.length} 件事`;

  els.recallCount.textContent = log.length;
  els.notesCount.textContent = notes.length;
  els.pastCount.textContent = archives.length;
  els.tabNotes.classList.toggle("has-notes", notes.length > 0);
  renderDrawer();
}

function renderDrawer() {
  if (!state) return;
  const log = state.memory_log || [];
  const notes = state.notes || [];

  els.tabRecall.classList.toggle("on", drawerTab === "recall");
  els.tabNotes.classList.toggle("on", drawerTab === "notes");
  els.tabPast.classList.toggle("on", drawerTab === "past");
  els.drawerFilters.hidden = drawerTab !== "recall";
  els.drawerBody.innerHTML = "";

  if (drawerTab === "notes") {
    renderNotes(notes);
    return;
  }
  if (drawerTab === "past") {
    renderArchives();
    return;
  }
  renderRecall(log);
}

function renderNotes(notes) {
  if (!notes.length) {
    emptyState("信箱是空的。她没信号的时候，你说的话都会先存在这里。");
    return;
  }
  const hint = document.createElement("p");
  hint.className = "drawer-note-hint";
  hint.textContent = "这些是你留的话，等她重新上线时会被读到。";
  els.drawerBody.appendChild(hint);
  notes.forEach((text) => {
    const card = document.createElement("article");
    card.className = "memory-card";
    const body = document.createElement("p");
    body.className = "memory-text";
    body.textContent = text;
    const foot = document.createElement("div");
    foot.className = "memory-foot";
    const tag = document.createElement("span");
    tag.className = "memory-entity";
    tag.textContent = "未读";
    foot.appendChild(tag);
    card.append(body, foot);
    els.drawerBody.appendChild(card);
  });
}

function renderRecall(log) {
  let items = log;
  if (drawerKind === "star") items = log.filter((m) => m.star);
  else if (drawerKind !== "all") items = log.filter((m) => m.kind === drawerKind);
  if (drawerQuery) {
    const q = drawerQuery.toLowerCase();
    items = items.filter((m) => {
      const haystack = [m.text, m.label, ...(m.tags || []), ...(m.entities || [])]
        .join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }
  if (!items.length) {
    emptyState(log.length
      ? "没有符合条件的回忆。换个词或换个分类试试。"
      : "还没有回忆。聊得多了，爱弥斯会慢慢记住你们的这些事。");
    return;
  }
  // 收藏置顶，其余最近发生的排在前
  const sorted = items.slice().sort((a, b) => (b.star - a.star));
  sorted.reverse().forEach((m) => {
    els.drawerBody.appendChild(memoryCard(m));
  });
}

function renderArchives() {
  if (!archives.length) {
    emptyState("还没有往期周目。点「重新开始」时，这一局会被归档留在这里。");
    return;
  }
  const hint = document.createElement("p");
  hint.className = "drawer-note-hint";
  hint.textContent = "每一局结束都会归档，回忆不会被抹掉。";
  els.drawerBody.appendChild(hint);
  archives.forEach((row) => {
    const card = document.createElement("article");
    card.className = "memory-card";
    const head = document.createElement("div");
    head.className = "memory-head";
    const kind = document.createElement("span");
    kind.className = "memory-kind";
    kind.textContent = row.ended ? (row.ending || "已完结") : "未完结";
    const meta = document.createElement("span");
    meta.className = "memory-meta";
    meta.textContent = row.archived_at ? safeStamp(row.archived_at) : "";
    head.append(kind, meta);

    const body = document.createElement("p");
    body.className = "memory-text";
    body.textContent = `第 ${row.turns} 轮 · 好感 ${row.affinity} · 回忆 ${row.memory_count} 条`;

    const foot = document.createElement("div");
    foot.className = "memory-foot";
    if (row.last_memory) {
      const chip = document.createElement("span");
      chip.className = "memory-entity";
      chip.textContent = row.last_memory.slice(0, 18);
      foot.appendChild(chip);
    }
    card.append(head, body, foot);
    els.drawerBody.appendChild(card);
  });
}

function emptyState(text) {
  const p = document.createElement("p");
  p.className = "drawer-empty";
  p.textContent = text;
  els.drawerBody.appendChild(p);
}

function memoryCard(m) {
  const card = document.createElement("article");
  card.className = `memory-card kind-${m.kind}`;

  const head = document.createElement("div");
  head.className = "memory-head";
  const kind = document.createElement("span");
  kind.className = "memory-kind";
  kind.textContent = m.label || "回忆";
  const meta = document.createElement("span");
  meta.className = "memory-meta";
  const when = m.at ? safeStamp(m.at) : "";
  meta.textContent = [m.turn ? `第 ${m.turn} 轮` : "", when].filter(Boolean).join(" · ");
  head.append(kind, meta);

  const body = document.createElement("p");
  body.className = "memory-text";
  body.textContent = m.text;

  const foot = document.createElement("div");
  foot.className = "memory-foot";
  foot.appendChild(salienceDots(m.salience || 0));
  (m.entities || []).slice(0, 2).forEach((e) => {
    const chip = document.createElement("span");
    chip.className = "memory-entity";
    chip.textContent = e;
    foot.appendChild(chip);
  });
  foot.appendChild(starButton(m));

  card.append(head, body, foot);
  return card;
}

/* 收藏：标记后这条记忆在裁剪时优先保留，也会在列表里置顶 */
function starButton(m) {
  const b = document.createElement("button");
  b.className = m.star ? "memory-star on" : "memory-star";
  b.textContent = m.star ? "★ 已收藏" : "☆ 收藏";
  b.title = "收藏这条回忆（收藏的回忆不会被淘汰）";
  b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      state = await api("memory/star", {
        method: "POST",
        body: JSON.stringify({ mid: m.mid, star: !m.star }),
      });
      renderMemoryLog(state);
    } catch (ex) {
      b.disabled = false;
    }
  });
  return b;
}

/* 显著度：3 颗点亮 = 心链/结局级别 */
function salienceDots(salience) {
  const wrap = document.createElement("span");
  wrap.className = "memory-dots";
  wrap.title = "她记得有多牢";
  const lit = Math.max(1, Math.min(3, Math.round(salience * 3)));
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement("i");
    if (i < lit) dot.className = "on";
    wrap.appendChild(dot);
  }
  return wrap;
}

function safeStamp(text) {
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? "" : formatStamp(d);
}

function openDrawer() {
  els.memoryDrawer.classList.add("show");
  els.drawerScrim.classList.add("show");
  renderDrawer();
}

function closeDrawer() {
  els.memoryDrawer.classList.remove("show");
  els.drawerScrim.classList.remove("show");
}

/* ---------------- 发送 ---------------- */
function startTyping() {
  els.typing.hidden = false;
  const t0 = Date.now();
  els.typingSec.textContent = "";
  clearInterval(typingTimer);
  typingTimer = setInterval(() => {
    els.typingSec.textContent = ` ${Math.floor((Date.now() - t0) / 1000)}s`;
  }, 1000);
}

function stopTyping() {
  clearInterval(typingTimer);
  typingTimer = null;
  els.typing.hidden = true;
}

async function doSend() {
  const message = els.input.value.trim();
  if (!message || sending) return;
  sending = true;
  els.sendBtn.disabled = true;
  els.input.value = "";
  autoGrow();
  els.choices.innerHTML = "";

  maybeTimelineSeparator(new Date());   // 隔久了再开口时补一条时间轴
  appendBubble("user", message);
  scrollBottom();
  startTyping();

  try {
    const data = await api("chat", {
      method: "POST", body: JSON.stringify({ message }),
    });
    state = data;
    markInput();   // 刚刚有过互动，重置闲置计时

    if (data.narration) appendNarration(data.narration);
    const isHl = data.event && data.event.tier === "heartlink";
    if (data.event && data.event.phase === "start") {
      const tag = isHl ? "心链事件开启" : "事件开启";
      appendEventLine(`${data.event.emoji} ${tag} · ${data.event.title}`,
                      { heartlink: isHl });
    }
    if (data.offline) {
      // 调用层失败（后端离线 / 超时）：用警示样式，避免玩家误以为是剧情
      appendNarration(data.reply || "（信号波动，暂时没有回应）", { warn: true });
      if (data.note_queued) {
        appendNarration("（这句话已经放进她的信箱，等她重新上线会读到）");
      }
    } else if (data.reply) {
      appendBubble("aemeath", data.reply);
    }

    renderChapter(data);
    floatAffinity(data.affinity_delta, data.affinity_notes);
    renderAffinity(data.save.affinity, data.affinity_level);
    renderWorld(data.world);
    renderHeartlink(data);
    renderCG(data);
    renderMemoryLog(data);

    if (data.event) {
      showEventBanner(data.event);
      renderChoices(data.event);
    }
    scrollBottom();
    if (data.transition === "ending" && data.ending) showEnding(data);
  } catch (ex) {
    setConnection(false);
    appendNarration(
      `（连接中断：${ex.message}。请确认后端服务仍在运行——点「返回控制台」后重新进入即可恢复）`,
      { warn: true });
  } finally {
    stopTyping();
    els.sendBtn.disabled = false;
    sending = false;
    els.input.focus();
  }
}

/* ---------------- 主动陪伴 ---------------- */
/* 让爱弥斯主动开口。reason：resume=久别问候 / idle=闲置搭话。
   后端会做冷却与每日上限判断，不允许时返回 skipped，这里直接忽略即可。 */
async function maybeProactive(reason) {
  if (sending || !state || state.ended) return;
  try {
    const data = await api("proactive", {
      method: "POST", body: JSON.stringify({ reason }),
    });
    if (!data.proactive) return;
    state = data;

    maybeTimelineSeparator(new Date());   // 她隔了很久才开口：单独起一段
    if (data.narration) appendNarration(data.narration);
    const isHl = data.event && data.event.tier === "heartlink";
    if (data.event && data.event.phase === "start") {
      const tag = isHl ? "心链事件开启" : "事件开启";
      appendEventLine(`${data.event.emoji} ${tag} · ${data.event.title}`,
                      { heartlink: isHl });
    }
    if (data.offline) {
      appendNarration(data.reply || "（信号波动，暂时没有回应）", { warn: true });
    } else if (data.reply) {
      appendBubble("aemeath", data.reply);
    }

    renderChapter(data);
    renderAffinity(data.save.affinity, data.affinity_level);
    renderWorld(data.world);
    renderHeartlink(data);
    renderCG(data);
    renderMemoryLog(data);
    if (data.event) {
      showEventBanner(data.event);
      renderChoices(data.event);
    } else if (!data.offline) {
      // 她主动开口后给几个顺手的回应
      renderQuickReplies(PROACTIVE_REPLIES);
    }
    scrollBottom();
  } catch (ex) {
    // 主动陪伴失败就静默跳过，不往聊天流里刷错误提示
  }
  markInput();   // 防止紧接着再触发一次
}

/* ---------------- 结局 ---------------- */
function showEnding(data) {
  const ending = data.ending;
  if (!ending) return;
  els.endingTitle.textContent = ending.title;
  els.endingEpilogue.textContent = ending.epilogue;
  els.endingOverlay.hidden = false;
}

async function restart() {
  els.endingOverlay.hidden = true;
  try {
    const data = await api("reset", { method: "POST" });
    currentCG = "";
    els.cgLayer.querySelectorAll(".cg-static.cg-fade").forEach((n) => n.remove());
    renderAll(data, { keepHistory: false });
    if (data.archived) appendNarration("（上一局已经归档，在「回忆录 · 往期」里还能翻到）");
    loadArchives().catch(() => {});
  } catch (ex) {
    setConnection(false);
    appendNarration(`（重置失败：${ex.message}）`, { warn: true });
  }
}

/* ---------------- 返回 ---------------- */
function goBack() {
  // WPF WebView2 宿主监听该消息并关闭 Galgame 窗口
  try {
    if (window.chrome && window.chrome.webview) {
      window.chrome.webview.postMessage("back");
      return;
    }
  } catch (e) { /* 非宿主环境 */ }
  // 兜底：浏览器中尝试关闭，否则后退
  window.history.length > 1 ? window.history.back() : window.close();
}

/* ---------------- 输入框交互 ---------------- */
function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 110)}px`;
}

els.input.addEventListener("input", () => { autoGrow(); markInput(); });
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    doSend();
  }
});
$("sendBtn").addEventListener("click", doSend);
$("backBtn").addEventListener("click", goBack);
$("endingBackBtn").addEventListener("click", goBack);
$("restartBtn").addEventListener("click", () => { restart().catch(console.error); });

/* 回忆录抽屉：手柄开、点关闭/遮罩/Esc 关 */
els.logBtn.addEventListener("click", openDrawer);
els.drawerClose.addEventListener("click", closeDrawer);
els.drawerScrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});
[els.tabRecall, els.tabNotes, els.tabPast].forEach((tab) => {
  tab.addEventListener("click", () => {
    drawerTab = tab.dataset.tab;
    renderDrawer();
  });
});
els.drawerSearch.addEventListener("input", () => {
  drawerQuery = els.drawerSearch.value.trim();
  renderDrawer();
});
els.drawerFilters.addEventListener("click", (e) => {
  const pill = e.target.closest(".filter-pill");
  if (!pill) return;
  drawerKind = pill.dataset.kind;
  [...els.drawerFilters.querySelectorAll(".filter-pill")].forEach((p) => {
    p.classList.toggle("on", p === pill);
  });
  renderDrawer();
});

/* 结局后不锁死：关掉覆盖层就能继续和她说话 */
els.continueBtn.addEventListener("click", () => {
  els.endingOverlay.hidden = true;
  els.input.focus();
});

/* 回到前台就当作「重新出现」：让她有机会先开口 */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    markInput();
    maybeProactive("resume");
  }
});

/* 页面开着但玩家一直没说话：每分钟检查一次，闲置够了就让她搭话 */
setInterval(() => {
  if (Date.now() - lastLocalInputAt >= 5 * 60 * 1000) {
    maybeProactive("idle");
  }
}, 60 * 1000);

/* 时钟每分钟自走一步，避免 HUD 停在加载时刻 */
renderClock();
setInterval(renderClock, 30000);

boot().catch((ex) => {
  setConnection(false);
  appendNarration(`（启动失败：${ex.message}）`, { warn: true });
});