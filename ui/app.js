// Mini Cursor 前端：只做三件事 —— 发消息、渲染回答、把工具调用摊开给人看
// 刻意不用框架：一个文件、零构建步骤，双击就能跑，打包也不会多出 node_modules

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const traceList = $("traceList");
const inputEl = $("input");
const sendBtn = $("sendBtn");
const stopBtn = $("stopBtn");

let ws = null;
let busy = false;
let cards = new Map();      // tool_call 序号 -> DOM 卡片
let cardSeq = 0;
let lastAssistantText = ""; // 用来判断 done 时是否还需要补一条回答

// ---------------------------------------------------------------- 连接
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => setStatus("busy", "握手中");
  ws.onclose = () => {
    setStatus("offline", "已断开");
    setTimeout(connect, 1200); // 后端重启时自动接回来，不用手动刷新
  };
  ws.onerror = () => setStatus("offline", "连接异常");
  ws.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    handle(data);
  };
}

function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMsg("error", "还没连上后端，稍等一下再发（正在自动重连）。");
    return;
  }
  ws.send(JSON.stringify(obj));
}

// ---------------------------------------------------------------- 事件处理
function handle(ev) {
  switch (ev.type) {
    case "ready":
      $("modelPill").textContent = `模型 ${ev.model}`;
      $("workspacePath").textContent = `工作目录：${ev.workspace}`;
      $("workspacePath").title = ev.workspace;
      $("modelPill").title = `工具：${(ev.tools || []).join(" / ")}`;
      setStatus("online", "就绪");
      break;

    case "assistant_message":
      if (ev.content) {
        lastAssistantText = ev.content;
        addMsg("assistant", ev.content);
      }
      break;

    case "tool_start":
      addToolCard(ev.name, ev.arguments);
      break;

    case "tool_end":
      finishToolCard(ev);
      break;

    case "done":
      setBusy(false);
      if (ev.error) {
        addMsg("error", ev.error);
      } else if (ev.stop_reason === "cancelled") {
        addMsg("system", "已中断（不是假停：循环在下一个安全点退出了）");
      } else if (ev.answer && ev.answer !== lastAssistantText) {
        addMsg("assistant", ev.answer);
      }
      break;

    case "notice":
      addMsg("system", ev.message);
      break;

    case "error":
      addMsg("error", ev.message);
      setBusy(false);
      break;

    case "reset_done":
      messagesEl.innerHTML = "";
      traceList.innerHTML = '<div class="trace-empty">还没有工具调用</div>';
      cards.clear();
      lastAssistantText = "";
      addMsg("system", "已开始新会话，上下文已清空。");
      break;
  }
}

// ---------------------------------------------------------------- 渲染：聊天
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 极简 markdown：只认 ``` 代码块 和 `行内代码`，够用且不会被 XSS
function render(text) {
  const parts = escapeHtml(text).split(/```/);
  return parts
    .map((chunk, i) => {
      if (i % 2 === 1) {
        const body = chunk.replace(/^[a-zA-Z0-9_+-]*\n/, "");
        return `<pre>${body}</pre>`;
      }
      return chunk.replace(/`([^`]+)`/g, "<code>$1</code>");
    })
    .join("");
}

function addMsg(role, text) {
  const welcome = $("welcome");
  if (welcome) welcome.remove();

  const row = document.createElement("div");
  row.className = `msg ${role}`;
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = { user: "你", assistant: "AI", error: "!", system: "i" }[role] || "·";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = role === "system" ? escapeHtml(text) : render(text);

  row.append(who, bubble);
  messagesEl.appendChild(row);
  scrollToEnd();
  return row;
}

function scrollToEnd() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ---------------------------------------------------------------- 渲染：trace
function addToolCard(name, argsRaw) {
  const empty = traceList.querySelector(".trace-empty");
  if (empty) empty.remove();

  let argsText = argsRaw;
  try {
    const parsed = JSON.parse(argsRaw);
    argsText = Object.entries(parsed)
      .map(([k, v]) => `${k}=${typeof v === "string" && v.length > 60 ? v.slice(0, 60) + "…" : v}`)
      .join("  ");
  } catch { /* 参数不是 JSON 就原样显示 */ }

  const id = ++cardSeq;
  const card = document.createElement("div");
  card.className = "card running";
  card.innerHTML = `
    <div class="card-top">
      <div class="spinner"></div>
      <span class="card-name">${escapeHtml(name)}</span>
      <span class="card-time">执行中…</span>
    </div>
    <div class="card-args">${escapeHtml(argsText || "")}</div>
  `;
  traceList.appendChild(card);
  cards.set(id, card);
  traceList.scrollTop = traceList.scrollHeight;
  card.dataset.seq = id;
  return id;
}

function finishToolCard(ev) {
  // tool_end 不带 id，就取最近一张还在 running 的卡片
  let card = null;
  for (const [, c] of cards) {
    if (c.classList.contains("running")) card = c;
  }
  if (!card) {
    addToolCard(ev.name, ev.arguments);
    card = [...cards.values()].pop();
  }
  if (!card) return;

  card.classList.remove("running");
  card.classList.add(ev.ok ? "ok" : "fail");

  const top = card.querySelector(".card-top");
  const spinner = top.querySelector(".spinner");
  if (spinner) spinner.remove();

  const badge = document.createElement("span");
  badge.className = `badge ${ev.ok ? "ok" : "fail"}`;
  badge.textContent = ev.ok ? "成功" : "失败";
  top.insertBefore(badge, top.querySelector(".card-time"));

  top.querySelector(".card-time").textContent = `${ev.elapsed.toFixed(2)}s`;

  const body = document.createElement("div");
  body.className = "card-body";
  const text = ev.content || "（无输出）";
  body.textContent = text.length > 1200 ? text.slice(0, 1200) + "\n…（更长的内容已折叠）" : text;
  card.appendChild(body);
  traceList.scrollTop = traceList.scrollHeight;
}

// ---------------------------------------------------------------- 状态
function setStatus(kind, text) {
  const pill = $("statusPill");
  pill.className = `pill status ${kind}`;
  pill.innerHTML = `<i></i>${escapeHtml(text)}`;
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  stopBtn.disabled = !value;
  inputEl.disabled = false;
  const hint = $("hint");
  hint.textContent = value ? "正在执行…可以点停止中断" : "Enter 发送 · Shift+Enter 换行";
  setStatus(value ? "busy" : "online", value ? "执行中" : "就绪");
}

// ---------------------------------------------------------------- 交互
function submit() {
  const text = inputEl.value.trim();
  if (!text || busy) return;
  addMsg("user", text);
  lastAssistantText = "";
  inputEl.value = "";
  setBusy(true);
  send({ type: "chat", content: text });
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  submit();
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submit();
  }
});

stopBtn.addEventListener("click", () => {
  send({ type: "cancel" });
  stopBtn.disabled = true;
  $("hint").textContent = "正在中断…";
});

$("resetBtn").addEventListener("click", () => send({ type: "reset" }));

$("clearTrace").addEventListener("click", () => {
  traceList.innerHTML = '<div class="trace-empty">还没有工具调用</div>';
  cards.clear();
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    inputEl.value = chip.dataset.prompt;
    inputEl.focus();
  });
});

connect();
