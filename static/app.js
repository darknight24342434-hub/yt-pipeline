const form = document.querySelector("#job-form");
const submitButton = form.querySelector("button[type='submit']");
const currentJob = document.querySelector("#current-job");
const results = document.querySelector("#results");
const history = document.querySelector("#history");
const refresh = document.querySelector("#refresh");
const jobIdLabel = document.querySelector("#job-id");

let activeJobId = null;
let pollTimer = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitButton.disabled = true;
  const payload = {
    url: document.querySelector("#url").value.trim(),
    use_captions: document.querySelector("#use_captions").checked,
    allow_auto_captions: document.querySelector("#allow_auto_captions").checked,
    transcribe_if_no_captions: document.querySelector("#transcribe_if_no_captions").checked,
    max_clips: Number(document.querySelector("#max_clips").value || 5),
    min_clip_seconds: Number(document.querySelector("#min_clip_seconds").value || 30),
    max_clip_seconds: Number(document.querySelector("#max_clip_seconds").value || 120)
  };

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "建立工作失敗");
    }
    const job = await response.json();
    activeJobId = job.id;
    renderJob(job);
    startPolling();
    await loadHistory();
  } catch (error) {
    currentJob.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  } finally {
    submitButton.disabled = false;
  }
});

refresh.addEventListener("click", async () => {
  if (activeJobId) {
    await loadJob(activeJobId);
  }
  await loadHistory();
});

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!activeJobId) return;
    const job = await loadJob(activeJobId);
    if (job && ["completed", "failed"].includes(job.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      await loadHistory();
    }
  }, 1800);
}

async function loadJob(id) {
  const response = await fetch(`/api/jobs/${id}`);
  if (!response.ok) return null;
  const job = await response.json();
  activeJobId = job.id;
  renderJob(job);
  return job;
}

async function loadHistory() {
  const response = await fetch("/api/jobs");
  if (!response.ok) return;
  const jobs = await response.json();
  history.innerHTML = jobs.length ? jobs.slice(0, 20).map(renderHistoryRow).join("") : `<div class="empty">尚無歷史工作。</div>`;
  history.querySelectorAll("[data-job]").forEach((row) => {
    row.addEventListener("click", () => loadJob(row.dataset.job));
  });
  if (!activeJobId && jobs.length) {
    const resumable = jobs.find((job) => ["running", "queued"].includes(job.status)) || jobs[0];
    await loadJob(resumable.id);
    if (["running", "queued"].includes(resumable.status)) {
      startPolling();
    }
  }
}

function renderJob(job) {
  jobIdLabel.textContent = job.id;
  const isBusy = ["running", "queued"].includes(job.status);
  submitButton.disabled = isBusy;
  submitButton.textContent = isBusy ? "處理中" : "開始處理";
  currentJob.className = "job-card";
  currentJob.innerHTML = `
    <div class="job-top">
      <div>
        <h3>${escapeHtml(job.result?.metadata?.title || job.request?.url || job.id)}</h3>
        <div class="muted">${escapeHtml(job.stage || "")}</div>
      </div>
      <span class="status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
    </div>
    <div class="progress"><div class="bar" style="width:${Number(job.progress || 0)}%"></div></div>
    <div class="muted">${Number(job.progress || 0)}%</div>
    ${job.status === "queued" ? `<p class="muted">這筆工作正在排隊，前一筆完成後會自動開始。</p>` : ""}
    ${job.error ? `<p class="status failed">${escapeHtml(job.error)}</p>` : ""}
    <div class="logs">${renderLogs(job.logs || [])}</div>
  `;
  renderResults(job);
}

function renderResults(job) {
  if (job.status !== "completed") {
    results.className = "empty";
    results.textContent = job.status === "failed" ? "工作失敗，請查看上方錯誤訊息。" : "處理中。";
    return;
  }
  results.className = "result-grid";
  const artifacts = (job.artifacts || []).map((item) => {
    const href = `/api/jobs/${job.id}/files/${encodeURIComponentPath(item.path)}`;
    return `<a href="${href}" target="_blank" rel="noreferrer">${escapeHtml(item.label)}</a>`;
  }).join("");
  const clips = (job.result?.clips || []).map((clip) => renderClip(job.id, clip)).join("");
  const warnings = (job.result?.warnings || []).map((warning) => `<div class="warning-line">${escapeHtml(warning)}</div>`).join("");
  results.innerHTML = `
    <div class="stack">
      ${warnings ? `<section><h3>提醒</h3><div class="notice">${warnings}</div></section>` : ""}
      <section>
        <h3>內容摘要</h3>
        <div class="markdown-block">${escapeHtml(job.result?.summary || "")}</div>
      </section>
      <section>
        <h3>深度分析</h3>
        <div class="markdown-block">${escapeHtml(job.result?.analysis || "")}</div>
      </section>
    </div>
    <aside class="stack">
      <section>
        <h3>檔案</h3>
        <div class="artifact-list">${artifacts || "<span class='muted'>沒有檔案</span>"}</div>
      </section>
      <section>
        <h3>精華片段</h3>
        <div class="clip-list">${clips || "<span class='muted'>沒有片段</span>"}</div>
      </section>
    </aside>
  `;
}

function renderClip(jobId, clip) {
  const file = clip.file ? `<a href="/api/jobs/${jobId}/files/${encodeURIComponentPath(clip.file)}" target="_blank" rel="noreferrer">開啟片段</a>` : `<span class="muted">未輸出檔案</span>`;
  return `
    <div class="clip">
      <h3>${escapeHtml(clip.index || "")}. ${escapeHtml(clip.title || "精華片段")}</h3>
      <div class="clip-meta">${secondsToTime(clip.start)} - ${secondsToTime(clip.end)}</div>
      <p>${escapeHtml(clip.reason || "")}</p>
      <p>${escapeHtml(clip.hook || "")}</p>
      ${file}
    </div>
  `;
}

function renderHistoryRow(job) {
  const title = job.result?.metadata?.title || job.request?.url || job.id;
  return `
    <div class="history-row" data-job="${escapeHtml(job.id)}">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <div class="muted">${escapeHtml(job.id)} · ${escapeHtml(job.stage || "")}</div>
      </div>
      <span class="status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
    </div>
  `;
}

function renderLogs(logs) {
  if (!logs.length) return `<div class="muted">尚無紀錄。</div>`;
  return logs.slice(-80).map((log) => `
    <div class="log ${escapeHtml(log.level || "")}">
      <span class="time">${new Date(log.at).toLocaleTimeString()}</span>
      <span>${escapeHtml(log.message || "")}</span>
    </div>
  `).join("");
}

function encodeURIComponentPath(path) {
  return String(path).split("/").map(encodeURIComponent).join("/");
}

function secondsToTime(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadHistory();
