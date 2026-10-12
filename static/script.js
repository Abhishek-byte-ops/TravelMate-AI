const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");

// Persisted so a page reload continues the same LangGraph thread.
let threadId = null;
try {
  threadId = localStorage.getItem("travelmate_thread");
} catch (e) {
  /* private mode / blocked storage - fall back to in-memory only */
}

function addMessage(role, html) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = html;
  wrap.appendChild(bubble);
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Textarea grows with its content.
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});

// Enter sends; Shift+Enter makes a newline.
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

resetBtn.addEventListener("click", () => {
  threadId = null;
  try {
    localStorage.removeItem("travelmate_thread");
  } catch (e) {}
  chat.innerHTML = "";
  addMessage("assistant", "<p>Started a new trip. Where are we going?</p>");
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const text = input.value.trim();
  if (!text) return;

  addMessage("user", `<p>${escapeHtml(text)}</p>`);
  input.value = "";
  input.style.height = "auto";

  sendBtn.disabled = true;
  input.disabled = true;

  const pending = addMessage(
    "assistant",
    '<p class="hint">Planning your trip<span class="dots"></span><br>Usually 20-70 seconds — four agents run in sequence.</p>'
  );

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, thread_id: threadId }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `Request failed (${res.status})`);
    }

    threadId = data.thread_id;
    try {
      localStorage.setItem("travelmate_thread", threadId);
    } catch (e) {}

    pending.innerHTML = marked.parse(data.reply);
  } catch (err) {
    pending.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
    chat.scrollTop = chat.scrollHeight;
  }
});
