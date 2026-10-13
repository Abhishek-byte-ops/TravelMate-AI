const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");
const themeBtn = document.getElementById("theme");

const SUGGESTIONS = [
  "Plan a 5 day trip from India to Nepal with flights and hotels",
  "3 days in Japan from Delhi",
  "Weekend getaway from Mumbai to Dubai",
];

// The four graph nodes, in the order backend.py runs them.
const STEPS = [
  ["✈️", "Finding flights"],
  ["🏨", "Searching hotels"],
  ["🗺️", "Building the itinerary"],
  ["📋", "Writing up your plan"],
];

/* ---------- tiny storage helpers (private mode can throw) ---------- */

function store(key, value) {
  try {
    value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
  } catch (e) {
    /* ignore - storage is a convenience, never required */
  }
}

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

let threadId = read("travelmate_thread");

/* ---------- theme ---------- */

const savedTheme = read("travelmate_theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;

themeBtn.addEventListener("click", () => {
  const root = document.documentElement;
  const isDark =
    root.dataset.theme === "dark" ||
    (!root.dataset.theme &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  const next = isDark ? "light" : "dark";
  root.dataset.theme = next;
  store("travelmate_theme", next);
});

/* ---------- rendering ---------- */

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function addMessage(role, html) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "You" : "✈️";
  avatar.setAttribute("aria-hidden", "true");

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = html;

  wrap.append(avatar, bubble);
  chat.appendChild(wrap);
  scrollToEnd();
  return bubble;
}

function scrollToEnd() {
  chat.scrollTop = chat.scrollHeight;
}

function welcome() {
  const bubble = addMessage(
    "assistant",
    `<p>Tell me where you want to go and I'll plan the whole trip —
      flights, hotels and a day-by-day itinerary.</p>
     <p class="hint">Try one of these:</p>`
  );

  const chips = document.createElement("div");
  chips.className = "chips";

  SUGGESTIONS.forEach((text) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      input.value = text;
      input.focus();
      autosize();
    });
    chips.appendChild(chip);
  });

  bubble.appendChild(chips);
}

/* ---------- download / copy ---------- */

function timestamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(
    d.getHours()
  )}${p(d.getMinutes())}`;
}

function downloadText(text, filename, mime) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoke late so Safari has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function flash(button, label) {
  const original = button.dataset.label;
  button.textContent = label;
  button.classList.add("done");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("done");
  }, 1600);
}

function addActions(bubble, markdown) {
  const bar = document.createElement("div");
  bar.className = "actions";

  const md = document.createElement("button");
  md.className = "action";
  md.type = "button";
  md.dataset.label = "⬇ Markdown";
  md.textContent = md.dataset.label;
  md.addEventListener("click", () => {
    downloadText(markdown, `travelmate-plan-${timestamp()}.md`, "text/markdown");
    flash(md, "✓ Saved");
  });

  const txt = document.createElement("button");
  txt.className = "action";
  txt.type = "button";
  txt.dataset.label = "⬇ Text";
  txt.textContent = txt.dataset.label;
  txt.addEventListener("click", () => {
    downloadText(markdown, `travelmate-plan-${timestamp()}.txt`, "text/plain");
    flash(txt, "✓ Saved");
  });

  const copy = document.createElement("button");
  copy.className = "action";
  copy.type = "button";
  copy.dataset.label = "⧉ Copy";
  copy.textContent = copy.dataset.label;
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      flash(copy, "✓ Copied");
    } catch (e) {
      flash(copy, "✗ Blocked");
    }
  });

  const print = document.createElement("button");
  print.className = "action";
  print.type = "button";
  print.dataset.label = "⎙ Print / PDF";
  print.textContent = print.dataset.label;
  print.addEventListener("click", () => window.print());

  bar.append(md, txt, copy, print);
  bubble.appendChild(bar);
}

/* ---------- review (human in the loop) ---------- */

// Renders the itinerary with Approve / Request changes controls. The graph
// is parked at review_agent in Postgres until one of these posts back.
function renderReview(bubble, data) {
  bubble.innerHTML = "";

  const head = document.createElement("p");
  head.className = "hint";
  head.innerHTML =
    data.revision_round > 0
      ? `Revision ${data.revision_round} — ${escapeHtml(data.question || "")}`
      : escapeHtml(data.question || "Review this itinerary.");
  bubble.appendChild(head);

  const draft = document.createElement("div");
  draft.className = "draft";
  draft.innerHTML = marked.parse(data.itinerary || "");
  bubble.appendChild(draft);

  const bar = document.createElement("div");
  bar.className = "actions review-bar";

  const approve = document.createElement("button");
  approve.className = "action primary";
  approve.type = "button";
  approve.textContent = "✓ Approve";

  const revise = document.createElement("button");
  revise.className = "action";
  revise.type = "button";
  revise.textContent = "✎ Request changes";

  const note = document.createElement("textarea");
  note.className = "feedback";
  note.rows = 2;
  note.placeholder = "What should change? e.g. slower pace, more temples, cheaper hotels";
  note.hidden = true;

  revise.addEventListener("click", () => {
    if (note.hidden) {
      note.hidden = false;
      note.focus();
      revise.textContent = "✎ Send changes";
      return;
    }
    const text = note.value.trim();
    if (!text) {
      note.focus();
      return;
    }
    sendReview(bubble, data.thread_id, "revise", text);
  });

  approve.addEventListener("click", () =>
    sendReview(bubble, data.thread_id, "approve", "")
  );

  bar.append(approve, revise);
  bubble.append(bar, note);
  scrollToEnd();
}

async function sendReview(bubble, threadId, action, feedback) {
  bubble.innerHTML =
    action === "approve"
      ? '<p class="hint"><span class="plane">✈️</span> Writing up your final plan<span class="dots"></span></p>'
      : '<p class="hint"><span class="plane">✈️</span> Reworking the itinerary<span class="dots"></span></p>';

  try {
    const res = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, action, feedback }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);

    handleResult(bubble, data);
  } catch (err) {
    bubble.innerHTML = `<p class="error"><span>⚠</span><span>${escapeHtml(
      err.message
    )}</span></p>`;
  } finally {
    scrollToEnd();
  }
}

// Both /api/chat and /api/review return the same envelope.
function handleResult(bubble, data) {
  threadId = data.thread_id;
  store("travelmate_thread", threadId);

  if (data.status === "awaiting_review") {
    renderReview(bubble, data);
    return;
  }

  bubble.innerHTML = marked.parse(data.reply || "");
  addActions(bubble, data.reply || "");
}

/* ---------- progress indicator ---------- */

function startProgress(bubble) {
  bubble.innerHTML = `
    <p class="hint">
      <span class="plane">✈️</span> Planning your trip<span class="dots"></span>
    </p>
    <div class="runway"></div>
    <ul class="steps">
      ${STEPS.map(
        ([icon, label], i) => `
        <li class="step${i === 0 ? " active" : ""}" data-i="${i}">
          <span class="ring"></span><span>${icon} ${label}</span>
        </li>`
      ).join("")}
    </ul>`;

  // The API returns one response at the end, so this advances on a timer
  // rather than on real node completions - it mirrors the fixed pipeline
  // order, not measured progress.
  let i = 0;
  const nodes = bubble.querySelectorAll(".step");

  const timer = setInterval(() => {
    if (i >= nodes.length - 1) return;
    nodes[i].classList.remove("active");
    nodes[i].classList.add("done");
    i += 1;
    nodes[i].classList.add("active");
    scrollToEnd();
  }, 9000);

  return timer;
}

/* ---------- composer behaviour ---------- */

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 170) + "px";
}

input.addEventListener("input", autosize);

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

resetBtn.addEventListener("click", () => {
  threadId = null;
  store("travelmate_thread", null);
  chat.innerHTML = "";
  welcome();
  input.focus();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const text = input.value.trim();
  if (!text || sendBtn.disabled) return;

  addMessage("user", `<p>${escapeHtml(text)}</p>`);
  input.value = "";
  autosize();

  sendBtn.disabled = true;
  input.disabled = true;

  const pending = addMessage("assistant", "");
  const timer = startProgress(pending);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, thread_id: threadId }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);

    handleResult(pending, data);
  } catch (err) {
    pending.innerHTML = `<p class="error"><span>⚠</span><span>${escapeHtml(
      err.message
    )}</span></p>`;
  } finally {
    clearInterval(timer);
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
    scrollToEnd();
  }
});

welcome();
