const state = {
  meetings: [],
  current: null,
  allActions: [],
  tab: "overview",
  health: null,
  audioSettings: null,
  speakers: [],
  recording: false,
  recorder: null,
  stream: null,
  micStream: null,
  deviceStream: null,
  audioContext: null,
  mixDestination: null,
  mixBus: null,
  sourceNodes: {},
  audioSources: { mic: true, device: false },
  chunks: [],
  recordStarted: 0,
  pausedStarted: 0,
  totalPaused: 0,
  recordTimer: null,
  meterFrame: null,
  analyser: null,
  cancelled: false,
  micDeviceId: "",
  captureReturnHash: "",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

function formatTimestamp(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function formatDate(value, style = "short") {
  if (!value) return "Today";
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const options = style === "long"
    ? { weekday: "short", day: "numeric", month: "short", year: "numeric" }
    : { day: "numeric", month: "short" };
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function initials(name) {
  return String(name || "?").split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
}

function copyIcon() {
  return `<svg class="button-icon" viewBox="0 0 20 20" aria-hidden="true"><rect x="6.5" y="6.5" width="9" height="9" rx="2"></rect><path d="M13 6.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h1.5"></path></svg>`;
}

function downloadIcon() {
  return `<svg class="button-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v9"></path><path d="m6.5 8.5 3.5 3.5 3.5-3.5"></path><path d="M4 15.5h12"></path></svg>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload;
  try { payload = await response.json(); } catch { payload = { error: `Request failed (${response.status})` }; }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 3200);
}

async function refreshMeetings() {
  const value = await api("/api/meetings");
  state.meetings = value.meetings || [];
  renderMeetingList();
  renderLibraryCards();
}

function renderMeetingList() {
  const list = $("#meeting-list");
  list.innerHTML = state.meetings.length ? state.meetings.map((meeting) => `
    <button class="meeting-link ${state.current?.id === meeting.id ? "active" : ""}" data-action="open-meeting" data-id="${escapeHtml(meeting.id)}">
      <span class="meeting-dot"></span>
      <span><strong>${escapeHtml(meeting.title)}</strong><small>${formatDate(meeting.created_at)} · ${formatDuration(meeting.duration)}</small></span>
    </button>`).join("") : `<div class="empty-state">Your meetings will appear here.</div>`;
  const openActions = state.meetings.reduce((sum, meeting) => sum + Number(meeting.action_count || 0), 0);
  $("#action-count").textContent = String(openActions);
}

function setActiveNav(view) {
  $$("[data-action='show-library'], [data-action='show-actions']").forEach((item) => {
    item.classList.toggle("active", item.dataset.action === view);
  });
}

function showWelcome() {
  hideCaptureWorkspace();
  state.current = null;
  state.tab = "overview";
  if (location.hash) history.replaceState(null, "", location.pathname);
  $("#global-search-wrap").hidden = true;
  setActiveNav("home");
  renderMeetingList();
  $("#app").innerHTML = `
    <section class="welcome">
      <div class="welcome-inner">
        <div class="welcome-copy">
          <h1>Talk it through.<br><em>Leave aligned.</em></h1>
          <p>Meeter turns every conversation into crisp decisions, searchable context, and action items—without your data leaving this laptop.</p>
        </div>
        <div class="welcome-buttons">
          <button class="primary-button" data-action="new-meeting"><span class="button-orb">●</span> Start a meeting</button>
          <button class="secondary-button" data-action="create-demo">Explore a sample <span>→</span></button>
        </div>
        <div class="welcome-visual" aria-hidden="true">
          <div class="visual-glow"></div>
          <div class="preview-card preview-card-main">
            <div class="preview-top"><span><i></i> Live conversation</span><b>08:42</b></div>
            <div class="preview-wave">${Array.from({ length: 29 }, () => "<i></i>").join("")}</div>
            <div class="preview-speaker"><span>MC</span><p><b>Maya</b><small>“Let’s lock the rollout plan today…”</small></p></div>
          </div>
          <div class="preview-card preview-card-action"><span class="preview-check">✓</span><p><b>Action captured</b><small>Publish revised technical plan</small></p></div>
          <div class="preview-card preview-card-summary"><span>✦</span><p><b>Clear by default</b><small>Decisions, owners, and next steps</small></p></div>
        </div>
        <div class="welcome-trust"><span>100% local</span><span>No meeting bots</span><span>Built for real conversations</span></div>
      </div>
    </section>`;
  updateRecordingEntryPoints();
}

function libraryCard(meeting) {
  const people = meeting.participants || [];
  return `
    <button class="library-card" data-action="open-meeting" data-id="${escapeHtml(meeting.id)}">
      <div class="library-card-top">
        <span class="library-date">${formatDate(meeting.created_at, "long")}</span>
        <span class="library-open" aria-hidden="true">↗</span>
      </div>
      <div>
        <h2>${escapeHtml(meeting.title)}</h2>
        <p>${formatDuration(meeting.duration)} · ${people.length} speaker${people.length === 1 ? "" : "s"}</p>
      </div>
      <div class="library-card-footer">
        <span class="library-people">${people.slice(0, 4).map((person) => `<i title="${escapeHtml(person.name)}">${escapeHtml(initials(person.name))}</i>`).join("") || "<i>?</i>"}</span>
        <span class="library-actions">${Number(meeting.action_count || 0)} action${Number(meeting.action_count || 0) === 1 ? "" : "s"}</span>
      </div>
    </button>`;
}

function renderLibraryCards() {
  const grid = $("#library-grid");
  if (!grid) return;
  const query = ($("#library-search")?.value || "").trim().toLowerCase();
  const sort = $("#library-sort")?.value || "newest";
  const meetings = state.meetings.filter((meeting) => {
    const haystack = [meeting.title, ...(meeting.participants || []).map((person) => person.name)].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  meetings.sort((a, b) => {
    if (sort === "oldest") return new Date(a.created_at) - new Date(b.created_at);
    if (sort === "title") return a.title.localeCompare(b.title);
    if (sort === "duration") return Number(b.duration || 0) - Number(a.duration || 0);
    return new Date(b.created_at) - new Date(a.created_at);
  });
  grid.innerHTML = meetings.length
    ? meetings.map(libraryCard).join("")
    : `<div class="library-empty"><span>⌕</span><h2>No meetings found</h2><p>Try another title or speaker.</p></div>`;
  const count = $("#library-result-count");
  if (count) count.textContent = `${meetings.length} meeting${meetings.length === 1 ? "" : "s"}`;
}

function showLibrary() {
  hideCaptureWorkspace();
  state.current = null;
  history.replaceState(null, "", "#library");
  $("#global-search-wrap").hidden = true;
  setActiveNav("show-library");
  renderMeetingList();
  $("#app").innerHTML = `
    <section class="library-page">
      <header class="library-header">
        <div><span class="library-kicker">Your conversations</span><h1>Meeting library</h1><p>Everything discussed, decided, and assigned—ready when you need it.</p></div>
        <button class="primary-button library-new" data-action="new-meeting"><span class="button-orb">●</span> New meeting</button>
      </header>
      <div class="library-toolbar">
        <label class="library-search"><span>⌕</span><input id="library-search" type="search" placeholder="Search by meeting or speaker…" autocomplete="off"></label>
        <span id="library-result-count" class="library-result-count"></span>
        <label class="library-sort-label"><span>Sort</span><select id="library-sort"><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="title">Title A–Z</option><option value="duration">Longest first</option></select></label>
      </div>
      <div id="library-grid" class="library-grid"></div>
    </section>`;
  renderLibraryCards();
  updateRecordingEntryPoints();
}

async function openMeeting(id) {
  hideCaptureWorkspace();
  try {
    state.current = await api(`/api/meetings/${encodeURIComponent(id)}`);
    state.tab = "overview";
    history.replaceState(null, "", `#${id}`);
    $("#global-search-wrap").hidden = false;
    setActiveNav("show-library");
    renderMeeting();
    renderMeetingList();
    updateRecordingEntryPoints();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) { toast(error.message, "error"); }
}

function participantAvatars(participants = []) {
  return participants.slice(0, 5).map((person) => `<span class="participant-avatar ${person.known === false ? "unknown" : ""}" title="${escapeHtml(person.name)}">${escapeHtml(initials(person.name))}</span>`).join("");
}

const meetingPlayback = {
  snippetAudio: null,
  snippetButton: null,
  snippetEnd: null,
};

function resetSnippetPlayback() {
  if (meetingPlayback.snippetAudio) {
    meetingPlayback.snippetAudio.pause();
  }
  if (meetingPlayback.snippetButton) {
    meetingPlayback.snippetButton.classList.remove("active");
    meetingPlayback.snippetButton.setAttribute("aria-pressed", "false");
    meetingPlayback.snippetButton.textContent = "Listen";
  }
  meetingPlayback.snippetAudio = null;
  meetingPlayback.snippetButton = null;
  meetingPlayback.snippetEnd = null;
}

function resetMeetingPlayback() {
  const fullAudio = $("#meeting-audio");
  if (fullAudio) fullAudio.pause();
  resetSnippetPlayback();
}

function renderMeeting() {
  const meeting = state.current;
  if (!meeting) return showWelcome();
  resetMeetingPlayback();
  const unknownCount = (meeting.participants || []).filter((person) => person.known === false).length;
  $("#app").innerHTML = `
    <section class="meeting-page">
      <header class="meeting-header">
        <div>
          <div class="meeting-kicker">Complete · processed locally</div>
          <div class="meeting-title-row"><h1>${escapeHtml(meeting.title)}</h1><button class="title-edit-button" data-action="open-rename-meeting" aria-label="Rename ${escapeHtml(meeting.title)}">Edit name</button></div>
          <div class="meeting-meta">
            <span>▦ ${formatDate(meeting.created_at, "long")}</span>
            <span>◷ ${formatDuration(meeting.duration)}</span>
            <span>◉ ${escapeHtml(meeting.language?.toUpperCase() || "AUTO")}</span>
            <span>▣ ${escapeHtml(meeting.source_name || "Recording")}</span>
          </div>
          <div class="participant-row">
            <div class="participant-stack">${participantAvatars(meeting.participants)}</div>
            <span class="participant-label">${(meeting.participants || []).length} speakers${unknownCount ? ` · ${unknownCount} to identify` : ""}</span>
          </div>
        </div>
        <div class="meeting-controls">
          <button class="more-button feedback-button" data-action="export-markdown" title="Download Markdown" aria-label="Download meeting as Markdown">${downloadIcon()}</button>
          <button class="share-button feedback-button" data-action="copy-summary">${copyIcon()}<span>Copy summary</span></button>
        </div>
      </header>
      <nav class="tabs" aria-label="Meeting view">
        ${["overview", "transcript", "details"].map((tab) => `<button class="tab ${state.tab === tab ? "active" : ""}" data-action="tab" data-tab="${tab}">${tab[0].toUpperCase() + tab.slice(1)}</button>`).join("")}
      </nav>
      <div class="tab-panel">${renderTab()}</div>
    </section>`;
  if (state.tab === "transcript") setupTranscriptPlayback();
}

function renderTab() {
  if (state.tab === "transcript") return renderTranscript();
  if (state.tab === "details") return renderDetails();
  return renderOverview();
}

function renderOverview() {
  const meeting = state.current;
  const decisions = meeting.decisions || [];
  const discussion = meeting.discussion || [];
  const risks = meeting.risks || [];
  return `
    <div class="overview-grid">
      <div class="column">
        <article class="card summary-card">
          <div class="card-label"><span class="sparkle">✦</span> In a nutshell</div>
          <p>${escapeHtml(meeting.summary)}</p>
        </article>
        <article class="card card-pad">
          <div class="section-head"><h2>Decisions made <span class="count-badge">${decisions.length}</span></h2></div>
          ${decisions.length ? `<ul class="decision-list">${decisions.map((item) => `<li><span class="decision-check">✓</span><span>${escapeHtml(item)}</span></li>`).join("")}</ul>` : `<div class="empty-state">No explicit decisions detected.</div>`}
        </article>
        <article class="card card-pad">
          <div class="section-head"><h2>Discussion notes</h2></div>
          ${discussion.length ? `<div class="discussion-list">${discussion.map((item) => `<div class="discussion-item"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p></div>`).join("")}</div>` : `<div class="empty-state">No discussion themes detected.</div>`}
        </article>
      </div>
      <aside class="column">
        <article class="card card-pad action-section">
          <div class="section-head"><h2>Action items <span class="count-badge">${(meeting.actions || []).length}</span></h2>${(meeting.actions || []).length ? `<button class="copy-all-button feedback-button" data-action="copy-all-actions">${copyIcon()}<span>Copy all</span></button>` : ""}</div>
          ${renderActions(meeting.actions || [])}
        </article>
        ${risks.length ? `<article class="card card-pad"><div class="section-head"><h2>Watch-outs</h2></div>${risks.map((risk) => `<div class="risk-item"><span class="risk-icon">△</span><span>${escapeHtml(risk)}</span></div>`).join("")}</article>` : ""}
      </aside>
    </div>`;
}

function renderActions(actions) {
  if (!actions.length) return `<div class="empty-state">No explicit commitments found.</div>`;
  return `<div class="actions-list">${actions.map((action) => `
    <div class="action-card ${action.completed ? "completed" : ""}">
      <div class="action-main">
        <input class="action-check" type="checkbox" ${action.completed ? "checked" : ""} data-action="toggle-action" data-id="${escapeHtml(action.id)}" aria-label="Mark action complete">
        <div><p class="action-title">${escapeHtml(action.text)}</p>${action.context ? `<p class="action-context">${escapeHtml(action.context)}</p>` : ""}</div>
      </div>
      <div class="action-meta">
        <span class="priority ${escapeHtml(action.priority)}"></span>
        <span class="owner-pill">${escapeHtml(action.owner || "Unassigned")}</span>
        ${action.due ? `<span class="due-pill">Due ${formatDate(action.due)}</span>` : ""}
      </div>
      <div class="action-buttons">
        <button class="mini-action feedback-button" data-action="copy-action" data-id="${escapeHtml(action.id)}">${copyIcon()}<span>Copy</span></button>
        <button class="mini-action feedback-button" data-action="calendar-action" data-id="${escapeHtml(action.id)}"><span>＋ Calendar</span></button>
      </div>
    </div>`).join("")}</div>`;
}

function renderTranscript() {
  const participants = state.current.participants || [];
  const hasAudio = Boolean(state.current.audio);
  return `
    <div class="transcript-player ${hasAudio ? "" : "unavailable"}">
      ${hasAudio ? `
        <div class="transcript-player-heading"><strong>Meeting audio</strong><span>Click any timestamp to jump to that moment.</span></div>
        <audio id="meeting-audio" controls preload="metadata" src="/api/meetings/${encodeURIComponent(state.current.id)}/audio"></audio>
        <p class="audio-playback-error" id="audio-playback-error" role="status" hidden>Audio could not be played in this browser.</p>`
        : `<strong>Audio unavailable</strong><span>This meeting was saved without its original recording.</span>`}
    </div>
    <div class="transcript-toolbar">
      <input class="transcript-search" id="transcript-search" type="search" placeholder="Search what was said…" autocomplete="off">
      <select class="speaker-filter" id="speaker-filter" aria-label="Filter by speaker">
        <option value="">All speakers</option>
        ${participants.map((person) => `<option value="${escapeHtml(person.name)}">${escapeHtml(person.name)}</option>`).join("")}
      </select>
    </div>
    <article class="card transcript-card">
      ${(state.current.transcript || []).map((turn) => `
        <div class="transcript-turn" data-speaker="${escapeHtml(turn.speaker)}" data-text="${escapeHtml(turn.text.toLowerCase())}">
          <button class="timestamp" data-action="seek-transcript" data-start="${Number(turn.start) || 0}" aria-label="Play from ${formatTimestamp(turn.start)}" ${hasAudio ? "" : "disabled"}>${formatTimestamp(turn.start)}</button>
          <span class="speaker-name ${turn.speaker.toLowerCase().includes("unknown") ? "unknown" : ""}"><i class="speaker-color"></i>${escapeHtml(turn.speaker)}</span>
          <p class="transcript-text">${escapeHtml(turn.text)}</p>
        </div>`).join("") || `<div class="empty-state">No transcript available.</div>`}
    </article>`;
}

function setupTranscriptPlayback() {
  const audio = $("#meeting-audio");
  if (!audio) return;
  const rows = $$(".transcript-turn");
  const updateActiveTurn = () => {
    const time = audio.currentTime;
    rows.forEach((row, index) => {
      const turn = state.current?.transcript?.[index];
      const active = Boolean(turn && time >= Number(turn.start) && time < Number(turn.end));
      row.classList.toggle("active-audio", active);
      if (active) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
  };
  audio.addEventListener("timeupdate", updateActiveTurn);
  audio.addEventListener("play", resetSnippetPlayback);
  audio.addEventListener("seeked", updateActiveTurn);
  audio.addEventListener("ended", updateActiveTurn);
  audio.addEventListener("error", () => {
    const message = $("#audio-playback-error");
    if (message) message.hidden = false;
  });
}

async function seekTranscript(start) {
  const audio = $("#meeting-audio");
  if (!audio) return;
  try {
    audio.currentTime = Math.max(0, Number(start) || 0);
    await audio.play();
  } catch {
    const message = $("#audio-playback-error");
    if (message) message.hidden = false;
  }
}

async function toggleVoiceSnippet(button, start, end) {
  const sameSnippet = meetingPlayback.snippetButton === button && meetingPlayback.snippetAudio;
  if (sameSnippet && !meetingPlayback.snippetAudio.paused) {
    meetingPlayback.snippetAudio.pause();
    button.classList.remove("active");
    button.setAttribute("aria-pressed", "false");
    button.textContent = "Listen";
    return;
  }
  if (!sameSnippet) {
    resetMeetingPlayback();
    const audio = new Audio(`/api/meetings/${encodeURIComponent(state.current.id)}/audio`);
    meetingPlayback.snippetAudio = audio;
    meetingPlayback.snippetButton = button;
    meetingPlayback.snippetEnd = end;
    audio.preload = "metadata";
    audio.addEventListener("timeupdate", () => {
      if (audio === meetingPlayback.snippetAudio && audio.currentTime >= meetingPlayback.snippetEnd) resetMeetingPlayback();
    });
    audio.addEventListener("ended", () => {
      if (audio === meetingPlayback.snippetAudio) resetMeetingPlayback();
    });
    audio.addEventListener("error", () => {
      if (audio !== meetingPlayback.snippetAudio) return;
      resetMeetingPlayback();
      toast("Voice snippet could not be played", "error");
    });
    audio.currentTime = start;
  }
  try {
    button.classList.add("active");
    button.setAttribute("aria-pressed", "true");
    button.textContent = "Pause";
    await meetingPlayback.snippetAudio.play();
  } catch {
    resetMeetingPlayback();
    toast("Voice snippet could not be played", "error");
  }
}

function renderDetails() {
  const meeting = state.current;
  return `<div class="details-grid">
    <article class="card card-pad">
      <div class="section-head"><h2>Meeting details</h2></div>
      <div class="details-row"><span>Source</span><strong>${escapeHtml(meeting.source_name)}</strong></div>
      <div class="details-row"><span>Duration</span><strong>${formatDuration(meeting.duration)}</strong></div>
      <div class="details-row"><span>Language</span><strong>${escapeHtml(meeting.language?.toUpperCase() || "AUTO")}</strong></div>
      <div class="details-row"><span>Intelligence</span><strong>${escapeHtml(meeting.model_note)}</strong></div>
      <div class="details-row"><span>Storage</span><strong>On this device</strong></div>
    </article>
    <article class="card card-pad">
      <div class="section-head"><h2>Speakers</h2></div>
      ${(meeting.participants || []).map((person) => `
        <div class="participant-manage">
          <span class="participant-avatar ${person.known === false ? "unknown" : ""}">${escapeHtml(initials(person.name))}</span>
          <span><strong>${escapeHtml(person.name)}</strong><small>${person.known === false ? "Unknown voice · only in this meeting" : "Recognized local profile"}</small></span>
          ${meeting.audio && person.voice_snippet ? `<button class="listen-button" data-action="play-voice-snippet" data-start="${Number(person.voice_snippet.start_seconds)}" data-end="${Number(person.voice_snippet.end_seconds)}" aria-pressed="false" aria-label="Listen to ${escapeHtml(person.name)} voice snippet">Listen</button>` : ""}
          <button class="rename-button" data-action="rename-speaker" data-name="${escapeHtml(person.name)}">Rename</button>
        </div>`).join("")}
    </article>
    <article class="card card-pad danger-zone">
      <div><h2>Delete meeting</h2><p>Permanently remove this transcript, notes, snippets, and retained audio.</p></div>
      <button class="discard-button" data-action="open-delete-meeting">Delete meeting</button>
    </article>
  </div>`;
}

async function showActions() {
  hideCaptureWorkspace();
  const full = await Promise.all(state.meetings.map((item) => api(`/api/meetings/${encodeURIComponent(item.id)}`)));
  const actions = full.flatMap((meeting) => (meeting.actions || []).map((action) => ({ ...action, meetingTitle: meeting.title, meetingId: meeting.id })));
  state.allActions = actions;
  state.current = null;
  history.replaceState(null, "", "#actions");
  $("#global-search-wrap").hidden = true;
  setActiveNav("show-actions");
  $("#app").innerHTML = `<section class="actions-page"><header class="meeting-header"><div><div class="meeting-kicker">Across all meetings</div><h1>Action hub</h1><div class="meeting-meta"><span>${actions.filter((item) => !item.completed).length} open commitments</span><span>${actions.length} total</span></div></div>${actions.length ? `<button class="share-button feedback-button" data-action="copy-all-actions" data-scope="all">${copyIcon()}<span>Copy all actions</span></button>` : ""}</header><div class="tabs"></div><div class="tab-panel"><article class="card card-pad action-section"><div class="actions-list">${actions.length ? actions.map((action) => `<div class="action-card ${action.completed ? "completed" : ""}"><div class="action-main"><span class="decision-check">${action.completed ? "✓" : "→"}</span><div><p class="action-title">${escapeHtml(action.text)}</p><p class="action-context">${escapeHtml(action.meetingTitle)} · ${escapeHtml(action.owner || "Unassigned")}${action.due ? ` · Due ${formatDate(action.due)}` : ""}</p></div></div><div class="action-buttons"><button class="mini-action" data-action="open-meeting" data-id="${escapeHtml(action.meetingId)}">Open meeting →</button></div></div>`).join("") : `<div class="empty-state">No actions yet.</div>`}</div></article></div></section>`;
  renderMeetingList();
  updateRecordingEntryPoints();
}

async function createDemo() {
  try {
    const meeting = await api("/api/meetings/demo", { method: "POST" });
    await refreshMeetings();
    state.current = meeting;
    renderMeeting();
    renderMeetingList();
    toast("Demo meeting created locally");
  } catch (error) { toast(error.message, "error"); }
}

function setModal(id, visible) {
  $(id).hidden = !visible;
  if (id === "#capture-modal") document.body.classList.toggle("capture-open", visible);
}

function openRenameMeeting() {
  const input = $("#meeting-title-input");
  $("#meeting-title-error").hidden = true;
  input.value = state.current.title;
  setModal("#rename-meeting-modal", true);
  requestAnimationFrame(() => { input.focus(); input.select(); });
}

function closeRenameMeeting() {
  setModal("#rename-meeting-modal", false);
  $("[data-action='open-rename-meeting']")?.focus();
}

function openDeleteMeeting() {
  $("#delete-meeting-copy").textContent = `“${state.current.title}” and its transcript, notes, snippets, and audio will be permanently deleted.`;
  setModal("#delete-meeting-modal", true);
  requestAnimationFrame(() => $("#delete-meeting-submit").focus());
}

function closeDeleteMeeting() {
  setModal("#delete-meeting-modal", false);
  $("[data-action='open-delete-meeting']")?.focus();
}

async function renameMeeting() {
  const input = $("#meeting-title-input");
  const submit = $("#rename-meeting-submit");
  const errorNode = $("#meeting-title-error");
  submit.disabled = true;
  submit.textContent = "Saving…";
  errorNode.hidden = true;
  try {
    state.current = await api(`/api/meetings/${encodeURIComponent(state.current.id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: input.value }),
    });
    state.allActions = [];
    await refreshMeetings();
    setModal("#rename-meeting-modal", false);
    renderMeeting();
    toast("Meeting renamed");
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.hidden = false;
    input.focus();
  } finally {
    submit.disabled = false;
    submit.textContent = "Save name";
  }
}

async function deleteMeeting() {
  const submit = $("#delete-meeting-submit");
  const meetingId = state.current.id;
  resetMeetingPlayback();
  submit.disabled = true;
  submit.textContent = "Deleting…";
  try {
    await api(`/api/meetings/${encodeURIComponent(meetingId)}`, { method: "DELETE" });
    state.current = null;
    state.allActions = [];
    setModal("#delete-meeting-modal", false);
    await refreshMeetings();
    showLibrary();
    toast("Meeting deleted");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    submit.disabled = false;
    submit.textContent = "Delete permanently";
  }
}

function hideCaptureWorkspace() {
  if (!$("#capture-modal")?.hidden) setModal("#capture-modal", false);
}

function updateRecordingEntryPoints() {
  const elapsed = formatTimestamp(recordingElapsed());
  $$("[data-action='new-meeting']").forEach((button) => {
    if (state.recording) {
      if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
      button.classList.add("recording-entry");
      button.setAttribute("aria-label", `Return to recording, ${elapsed} elapsed`);
      button.innerHTML = `
        <span class="live-record-icon" aria-hidden="true"><i></i></span>
        <span class="recording-entry-copy"><strong>Recording</strong><time datetime="PT${Math.floor(recordingElapsed())}S">${elapsed}</time></span>`;
    } else if (button.dataset.idleHtml) {
      button.innerHTML = button.dataset.idleHtml;
      delete button.dataset.idleHtml;
      button.classList.remove("recording-entry");
      button.removeAttribute("aria-label");
    }
  });
}

async function openCapture() {
  if (location.hash !== "#record") state.captureReturnHash = location.hash;
  history.replaceState(null, "", "#record");
  setModal("#capture-modal", true);
  updateRecordingUI();
  await refreshMicrophoneDevices();
}

function closeCapture() {
  setModal("#capture-modal", false);
  history.replaceState(null, "", state.captureReturnHash || location.pathname);
}

function updateAudioSourceUI() {
  for (const source of ["mic", "device"]) {
    const enabled = state.audioSources[source];
    const button = $(`[data-action="toggle-audio-source"][data-source="${source}"]`);
    if (button) {
      button.classList.toggle("active", enabled);
      button.setAttribute("aria-checked", String(enabled));
      button.setAttribute("aria-label", `Turn ${source === "mic" ? "microphone" : "device audio"} ${enabled ? "off" : "on"}`);
    }
    $(`#${source}-source-status`).textContent = enabled ? "On" : "Off";
  }
  if (!state.recording) {
    const sources = [
      state.audioSources.mic ? "Microphone on" : "Microphone off",
      state.audioSources.device ? "Device audio on" : "Device audio off",
    ];
    $("#record-subtitle").textContent = sources.join(" · ");
  }
}

async function refreshMicrophoneDevices() {
  const select = $("#microphone-select");
  if (!select || !navigator.mediaDevices?.enumerateDevices) return;
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput");
    const current = state.micDeviceId || select.value;
    select.innerHTML = `<option value="">Default microphone</option>${devices.map((device, index) => (
      `<option value="${escapeHtml(device.deviceId)}">${escapeHtml(device.label || `Microphone ${index + 1}`)}</option>`
    )).join("")}`;
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  } catch {}
}

function recordingElapsed() {
  if (!state.recordStarted) return 0;
  const endpoint = state.pausedStarted || Date.now();
  return Math.max(0, (endpoint - state.recordStarted - state.totalPaused) / 1000);
}

function updateRecordingUI() {
  const paused = state.recorder?.state === "paused";
  const active = state.recording;
  const status = $("#recording-status");
  status.hidden = !active;
  status?.classList.toggle("is-recording", active && !paused);
  status?.classList.toggle("is-paused", Boolean(paused));
  $("#recording-status-label").textContent = paused ? "Recording paused" : active ? "Recording" : "Ready to record";
  $("#recording-time").textContent = formatTimestamp(recordingElapsed());
  $("#recording-time").setAttribute("datetime", `PT${Math.floor(recordingElapsed())}S`);
  $(".pause-control").disabled = !active;
  $(".pause-control").classList.toggle("is-paused", Boolean(paused));
  $(".pause-control").setAttribute("aria-label", paused ? "Resume recording" : "Pause recording");
  $(".cancel-control").disabled = !active;
  $(".finish-control").classList.toggle("is-recording", active);
  $(".finish-control").setAttribute("aria-label", active ? "Finish recording" : "Start recording");
  $("#record-label").textContent = active ? "Finish" : "Start recording";
  $("#audio-detection").classList.toggle("is-paused", Boolean(paused));
  updateRecordingEntryPoints();
}

function stopAudioMeter() {
  if (state.meterFrame) cancelAnimationFrame(state.meterFrame);
  state.meterFrame = null;
  state.analyser = null;
  $$("#live-waveform i").forEach((bar) => { bar.style.height = ""; });
}

function startAudioMeter() {
  if (!state.audioContext || !state.mixBus) return;
  state.analyser = state.audioContext.createAnalyser();
  state.analyser.fftSize = 256;
  state.analyser.smoothingTimeConstant = .72;
  state.mixBus.connect(state.analyser);
  const values = new Uint8Array(state.analyser.frequencyBinCount);
  const bars = $$("#live-waveform i");
  const draw = () => {
    if (!state.analyser || !state.recording) return;
    state.analyser.getByteFrequencyData(values);
    const level = values.reduce((sum, value) => sum + value, 0) / values.length;
    const hearingAudio = level > 5 && state.recorder?.state !== "paused";
    $("#audio-detection").classList.toggle("has-audio", hearingAudio);
    bars.forEach((bar, index) => {
      const sample = values[Math.floor(index * values.length / bars.length)] || 0;
      bar.style.height = `${Math.max(8, Math.min(100, sample * .72))}%`;
    });
    state.meterFrame = requestAnimationFrame(draw);
  };
  draw();
}

function setStreamAudioEnabled(stream, enabled) {
  stream?.getAudioTracks().forEach((track) => { track.enabled = enabled; });
}

function connectAudioStream(source, stream) {
  const track = stream?.getAudioTracks()[0];
  if (!track || !state.audioContext || !state.mixBus) return;
  state.sourceNodes[source]?.disconnect();
  const node = state.audioContext.createMediaStreamSource(new MediaStream([track]));
  node.connect(state.mixBus);
  state.sourceNodes[source] = node;
}

async function acquireMicrophone() {
  if (!state.micStream?.getAudioTracks().some((track) => track.readyState === "live")) {
    state.micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: state.micDeviceId ? { exact: state.micDeviceId } : undefined,
        echoCancellation: true,
        noiseSuppression: true,
      },
      video: false,
    });
    if (state.audioContext) connectAudioStream("mic", state.micStream);
    await refreshMicrophoneDevices();
  }
  setStreamAudioEnabled(state.micStream, state.audioSources.mic);
}

async function changeMicrophone(deviceId) {
  state.micDeviceId = deviceId;
  if (!state.recording || !state.audioSources.mic) return;
  const oldStream = state.micStream;
  state.micStream = null;
  try {
    await acquireMicrophone();
    oldStream?.getTracks().forEach((track) => track.stop());
    toast("Microphone changed");
  } catch (error) {
    state.micStream = oldStream;
    if (oldStream) connectAudioStream("mic", oldStream);
    toast(error.name === "NotAllowedError" ? "Microphone access was not allowed" : error.message, "error");
  }
}

async function acquireDeviceAudio() {
  if (!navigator.mediaDevices.getDisplayMedia) {
    throw new Error("Device audio capture is not supported by this browser");
  }
  if (!state.deviceStream?.getAudioTracks().some((track) => track.readyState === "live")) {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: true,
      preferCurrentTab: false,
      selfBrowserSurface: "exclude",
      systemAudio: "include",
    });
    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("The selected source did not share audio. Enable “Share audio” and try again.");
    }
    state.deviceStream = stream;
    const audioTrack = stream.getAudioTracks()[0];
    audioTrack.addEventListener("ended", () => {
      if (state.deviceStream !== stream) return;
      stream.getTracks().forEach((track) => { if (track.readyState === "live") track.stop(); });
      state.audioSources.device = false;
      state.deviceStream = null;
      updateAudioSourceUI();
      if (state.recording) toast("Device audio sharing ended");
    }, { once: true });
    if (state.audioContext) connectAudioStream("device", stream);
  }
  setStreamAudioEnabled(state.deviceStream, state.audioSources.device);
}

async function setAudioSource(source, enabled) {
  if (!["mic", "device"].includes(source)) return;
  const previous = state.audioSources[source];
  state.audioSources[source] = enabled;
  updateAudioSourceUI();
  if (!state.recording) return;
  try {
    if (source === "mic") {
      if (enabled) await acquireMicrophone();
      setStreamAudioEnabled(state.micStream, enabled);
    } else {
      if (enabled) await acquireDeviceAudio();
      setStreamAudioEnabled(state.deviceStream, enabled);
    }
  } catch (error) {
    state.audioSources[source] = previous;
    updateAudioSourceUI();
    const denied = error.name === "NotAllowedError" ? `${source === "mic" ? "Microphone" : "Device audio"} access was not allowed` : error.message;
    toast(denied, "error");
  }
}

async function cleanupRecordingAudio() {
  stopAudioMeter();
  for (const node of Object.values(state.sourceNodes)) {
    try { node.disconnect(); } catch {}
  }
  state.sourceNodes = {};
  for (const stream of [state.micStream, state.deviceStream, state.stream]) {
    stream?.getTracks().forEach((track) => track.stop());
  }
  state.micStream = null;
  state.deviceStream = null;
  state.stream = null;
  state.mixDestination = null;
  state.mixBus = null;
  if (state.audioContext && state.audioContext.state !== "closed") await state.audioContext.close();
  state.audioContext = null;
}

async function beginRecording() {
  if (state.recording) {
    if (state.recorder?.state !== "inactive") state.recorder.stop();
    return;
  }
  if (!state.audioSources.mic && !state.audioSources.device) {
    toast("Turn on the microphone or device audio before recording", "error");
    return;
  }
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass || !window.MediaRecorder) throw new Error("Audio recording is not supported by this browser");
    state.audioContext = new AudioContextClass();
    state.mixDestination = state.audioContext.createMediaStreamDestination();
    state.mixBus = state.audioContext.createDynamicsCompressor();
    state.mixBus.connect(state.mixDestination);
    // Screen capture must be requested while the Record click still has transient
    // user activation, so acquire it before awaiting microphone permission.
    if (state.audioSources.device) await acquireDeviceAudio();
    if (state.audioSources.mic) await acquireMicrophone();
    await state.audioContext.resume();
    state.stream = state.mixDestination.stream;
    const preferred = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((type) => window.MediaRecorder?.isTypeSupported(type));
    state.recorder = new MediaRecorder(state.stream, preferred ? { mimeType: preferred } : undefined);
    state.chunks = [];
    state.cancelled = false;
    state.recorder.ondataavailable = (event) => { if (event.data.size) state.chunks.push(event.data); };
    state.recorder.onstop = async () => {
      clearInterval(state.recordTimer);
      const blob = new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" });
      const cancelled = state.cancelled;
      state.recording = false;
      setModal("#cancel-confirm-modal", false);
      await cleanupRecordingAudio();
      state.recorder = null;
      state.recordStarted = 0;
      state.pausedStarted = 0;
      state.totalPaused = 0;
      updateRecordingUI();
      updateAudioSourceUI();
      if (cancelled) {
        state.chunks = [];
        closeCapture();
        toast("Recording discarded");
        return;
      }
      setModal("#capture-modal", false);
      await uploadRecording(blob, `Meeting-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.${blob.type.includes("mp4") ? "m4a" : "webm"}`);
    };
    state.recorder.start(1000);
    state.recording = true;
    state.recordStarted = Date.now();
    state.pausedStarted = 0;
    state.totalPaused = 0;
    startAudioMeter();
    updateRecordingUI();
    state.recordTimer = setInterval(() => {
      updateRecordingUI();
    }, 250);
  } catch (error) {
    await cleanupRecordingAudio();
    const deniedSource = state.audioSources.device ? "Audio capture" : "Microphone";
    toast(error.name === "NotAllowedError" ? `${deniedSource} access was not allowed` : error.message, "error");
    updateAudioSourceUI();
  }
}

function pauseRecording() {
  if (!state.recording || !state.recorder) return;
  if (state.recorder.state === "recording") {
    state.recorder.pause();
    state.pausedStarted = Date.now();
    state.audioContext?.suspend();
  } else if (state.recorder.state === "paused") {
    state.totalPaused += Date.now() - state.pausedStarted;
    state.pausedStarted = 0;
    state.recorder.resume();
    state.audioContext?.resume();
  }
  updateRecordingUI();
}

function cancelRecording() {
  if (!state.recording || !state.recorder) return;
  setModal("#cancel-confirm-modal", true);
  $("[data-action='dismiss-cancel']")?.focus();
}

function confirmCancelRecording() {
  if (!state.recording || !state.recorder) {
    setModal("#cancel-confirm-modal", false);
    return;
  }
  setModal("#cancel-confirm-modal", false);
  state.cancelled = true;
  if (state.recorder.state !== "inactive") state.recorder.stop();
}

async function uploadRecording(blob, filename) {
  setModal("#process-modal", true);
  updateProgress("Saving recording locally", 4);
  try {
    const job = await api("/api/meetings/process", { method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream", "X-Filename": encodeURIComponent(filename) }, body: blob });
    await pollJob(job.id);
  } catch (error) {
    setModal("#process-modal", false);
    toast(error.message, "error");
  }
}

function updateProgress(step, progress) {
  $("#process-step").textContent = step;
  $("#process-progress").style.width = `${progress}%`;
  $("#process-percent").textContent = `${progress}%`;
}

async function pollJob(id) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    updateProgress(job.step, job.progress || 0);
    if (job.state === "complete") {
      setModal("#process-modal", false);
      await refreshMeetings();
      await openMeeting(job.meeting_id);
      toast("Meeting processed entirely on this device");
      return;
    }
    if (job.state === "error") {
      setModal("#process-modal", false);
      toast(job.error || "Processing failed", "error");
      if (job.code === "MODEL_NOT_READY") showSettings();
      return;
    }
  }
}

function showButtonFeedback(button, label) {
  if (!button) return;
  clearTimeout(button.feedbackTimer);
  if (!button.dataset.feedbackHtml) button.dataset.feedbackHtml = button.innerHTML;
  button.classList.add("feedback-success");
  button.innerHTML = `<span class="feedback-check" aria-hidden="true">✓</span><span>${escapeHtml(label)}</span>`;
  button.feedbackTimer = setTimeout(() => {
    if (!button.isConnected) return;
    button.innerHTML = button.dataset.feedbackHtml;
    button.classList.remove("feedback-success");
  }, 1500);
}

async function copyText(text, button, label = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  showButtonFeedback(button, label);
}

function findAction(id) { return (state.current?.actions || []).find((item) => item.id === id); }

function actionText(action) {
  return `${action.text}\nOwner: ${action.owner || "Unassigned"}${action.due ? `\nDue: ${action.due}` : ""}${action.context ? `\nContext: ${action.context}` : ""}`;
}

function actionsText(actions) {
  return actions.map((action, index) => {
    const source = action.meetingTitle ? `\nMeeting: ${action.meetingTitle}` : "";
    return `${index + 1}. ${actionText(action)}${source}`;
  }).join("\n\n");
}

function downloadCalendar(action, button) {
  const due = (action.due || new Date().toISOString().slice(0, 10)).replaceAll("-", "");
  const escapeIcs = (value) => String(value || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/,/g, "\\,").replace(/;/g, "\\;");
  const nextDate = new Date(`${action.due || new Date().toISOString().slice(0, 10)}T12:00:00`);
  nextDate.setDate(nextDate.getDate() + 1);
  const end = nextDate.toISOString().slice(0, 10).replaceAll("-", "");
  const content = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Meeter//Local Actions//EN", "BEGIN:VEVENT", `UID:${action.id}@meeter.local`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "")}`, `DTSTART;VALUE=DATE:${due}`, `DTEND;VALUE=DATE:${end}`, `SUMMARY:${escapeIcs(action.text)}`, `DESCRIPTION:${escapeIcs(actionText(action))}`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type: "text/calendar" }));
  link.download = `${action.text.replace(/[^a-z0-9]+/gi, "-").slice(0, 48) || "meeting-action"}.ics`;
  link.click();
  URL.revokeObjectURL(link.href);
  showButtonFeedback(button, "Added");
}

function markdownMeeting() {
  const meeting = state.current;
  return `# ${meeting.title}\n\n${meeting.summary}\n\n## Decisions\n${(meeting.decisions || []).map((item) => `- ${item}`).join("\n") || "- None captured"}\n\n## Actions\n${(meeting.actions || []).map((item) => `- [${item.completed ? "x" : " "}] ${item.text} — ${item.owner || "Unassigned"}${item.due ? ` (due ${item.due})` : ""}`).join("\n") || "- None captured"}\n\n## Transcript\n${(meeting.transcript || []).map((turn) => `**${formatTimestamp(turn.start)} · ${turn.speaker}:** ${turn.text}`).join("\n\n")}\n`;
}

function exportMarkdown(button) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([markdownMeeting()], { type: "text/markdown" }));
  link.download = `${state.current.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase().slice(0, 60)}.md`;
  link.click();
  URL.revokeObjectURL(link.href);
  showButtonFeedback(button, "Saved");
}

function filterTranscript() {
  const query = ($("#transcript-search")?.value || "").trim().toLowerCase();
  const speaker = $("#speaker-filter")?.value || "";
  $$(".transcript-turn").forEach((row) => {
    row.classList.toggle("filtered", Boolean((query && !row.dataset.text.includes(query)) || (speaker && row.dataset.speaker !== speaker)));
  });
}

async function showSettings() {
  setModal("#settings-modal", true);
  try {
    const [health, speakers, audioSettings] = await Promise.all([api("/api/health"), api("/api/speakers"), api("/api/settings/audio")]);
    state.health = health;
    state.speakers = speakers.speakers || [];
    state.audioSettings = audioSettings;
    const components = health.readiness?.components || {};
    $("#model-status").innerHTML = Object.entries(components).map(([name, ready]) => `<div class="model-item ${ready ? "ready" : ""}"><span class="model-light"></span><span><strong>${escapeHtml(name)}</strong><small>${ready ? "Local path configured" : name === "summarization" ? "Using offline fallback" : "Setup required"}</small></span></div>`).join("");
    $("#speaker-library").innerHTML = state.speakers.length ? state.speakers.map((speaker) => `<span class="speaker-chip"><span>${escapeHtml(initials(speaker.name))}</span>${escapeHtml(speaker.name)}</span>`).join("") : `<span class="settings-copy">No voices enrolled yet.</span>`;
    const retentionToggle = $("#audio-retention-toggle");
    retentionToggle.checked = Boolean(audioSettings.retain_recordings);
    retentionToggle.disabled = Boolean(audioSettings.managed);
    $("#audio-retention-note").textContent = audioSettings.error
      ? audioSettings.error
      : audioSettings.managed
      ? "Managed by MEETER_KEEP_AUDIO. Change the environment setting and restart Meeter to update it."
      : "Store original recordings on this device for transcript playback.";
  } catch (error) { toast(error.message, "error"); }
}

async function updateAudioRetention(enabled) {
  const toggle = $("#audio-retention-toggle");
  try {
    state.audioSettings = await api("/api/settings/audio", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ retain_recordings: enabled }),
    });
    toggle.checked = Boolean(state.audioSettings.retain_recordings);
    toast(enabled ? "Meeting audio will be kept locally" : "New meeting audio will not be retained");
  } catch (error) {
    toggle.checked = Boolean(state.audioSettings?.retain_recordings);
    toast(error.message, "error");
  }
}

async function enrollVoice(file) {
  const name = $("#speaker-name").value.trim();
  if (name.length < 2) return toast("Enter the colleague's name first", "error");
  try {
    const profile = await api(`/api/speakers/enroll?name=${encodeURIComponent(name)}`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file });
    toast(`${profile.name}'s voice was enrolled locally`);
    $("#speaker-name").value = "";
    await showSettings();
  } catch (error) { toast(error.message, "error"); }
}

async function renameSpeaker(oldName) {
  const newName = window.prompt(`Rename “${oldName}” in this meeting:`, oldName.toLowerCase().includes("unknown") ? "" : oldName);
  if (!newName || newName.trim() === oldName) return;
  try {
    state.current = await api(`/api/meetings/${encodeURIComponent(state.current.id)}/speaker-name`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ old_name: oldName, new_name: newName.trim() }) });
    renderMeeting();
    await refreshMeetings();
    toast("Speaker renamed for this meeting");
  } catch (error) { toast(error.message, "error"); }
}

document.addEventListener("click", async (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  if (trigger.tagName === "A") event.preventDefault();
  const action = trigger.dataset.action;
  if (action === "home") { showWelcome(); $("#sidebar").classList.remove("open"); }
  if (action === "show-library") { showLibrary(); $("#sidebar").classList.remove("open"); }
  if (action === "open-sidebar") $("#sidebar").classList.add("open");
  if (action === "close-sidebar") $("#sidebar").classList.remove("open");
  if (action === "new-meeting") openCapture();
  if (action === "close-capture") closeCapture();
  if (action === "choose-file") $("#file-input").click();
  if (action === "toggle-record") beginRecording();
  if (action === "pause-recording") pauseRecording();
  if (action === "cancel-recording") cancelRecording();
  if (action === "dismiss-cancel") setModal("#cancel-confirm-modal", false);
  if (action === "confirm-cancel") confirmCancelRecording();
  if (action === "toggle-mic-menu") {
    const menu = $("#mic-device-menu");
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
  }
  if (action === "toggle-audio-source") setAudioSource(trigger.dataset.source, !state.audioSources[trigger.dataset.source]);
  if (action === "create-demo") createDemo();
  if (action === "open-meeting") { openMeeting(trigger.dataset.id); $("#sidebar").classList.remove("open"); }
  if (action === "refresh") refreshMeetings().then(() => toast("Meetings refreshed"));
  if (action === "show-actions") { showActions(); $("#sidebar").classList.remove("open"); }
  if (action === "settings") { hideCaptureWorkspace(); showSettings(); }
  if (action === "close-settings") setModal("#settings-modal", false);
  if (action === "open-rename-meeting") openRenameMeeting();
  if (action === "close-rename-meeting") closeRenameMeeting();
  if (action === "open-delete-meeting") openDeleteMeeting();
  if (action === "close-delete-meeting") closeDeleteMeeting();
  if (action === "confirm-delete-meeting") deleteMeeting();
  if (action === "tab") { state.tab = trigger.dataset.tab; renderMeeting(); }
  if (action === "seek-transcript") seekTranscript(trigger.dataset.start);
  if (action === "play-voice-snippet") toggleVoiceSnippet(trigger, Number(trigger.dataset.start), Number(trigger.dataset.end));
  if (action === "copy-summary") copyText(`${state.current.title}\n\n${state.current.summary}\n\nDecisions:\n${(state.current.decisions || []).map((item) => `• ${item}`).join("\n")}`, trigger, "Copied");
  if (action === "export-markdown") exportMarkdown(trigger);
  if (action === "copy-action") { const item = findAction(trigger.dataset.id); if (item) copyText(actionText(item), trigger, "Copied"); }
  if (action === "copy-all-actions") {
    const items = trigger.dataset.scope === "all" ? state.allActions : (state.current?.actions || []);
    if (items.length) copyText(actionsText(items), trigger, "Copied");
  }
  if (action === "calendar-action") { const item = findAction(trigger.dataset.id); if (item) downloadCalendar(item, trigger); }
  if (action === "toggle-action") {
    const item = findAction(trigger.dataset.id);
    if (item) {
      item.completed = trigger.checked;
      trigger.closest(".action-card").classList.toggle("completed", item.completed);
      api(`/api/meetings/${encodeURIComponent(state.current.id)}/action-state`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_id: item.id, completed: item.completed }) }).then(refreshMeetings).catch((error) => toast(error.message, "error"));
    }
  }
  if (action === "rename-speaker") renameSpeaker(trigger.dataset.name);
  if (action === "choose-voice") $("#voice-file-input").click();
});

$("#file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) { setModal("#capture-modal", false); uploadRecording(file, file.name); }
  event.target.value = "";
});

$("#voice-file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) enrollVoice(file);
  event.target.value = "";
});

document.addEventListener("input", (event) => {
  if (event.target.matches("#library-search")) renderLibraryCards();
  if (event.target.matches("#transcript-search")) filterTranscript();
  if (event.target.matches("#global-search") && state.current) {
    state.tab = "transcript";
    renderMeeting();
    $("#transcript-search").value = event.target.value;
    filterTranscript();
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("#speaker-filter")) filterTranscript();
  if (event.target.matches("#audio-retention-toggle")) updateAudioRetention(event.target.checked);
  if (event.target.matches("#library-sort")) renderLibraryCards();
  if (event.target.matches("#microphone-select")) {
    changeMicrophone(event.target.value);
    $("#mic-device-menu").hidden = true;
    $("[data-action='toggle-mic-menu']").setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#global-search").focus(); }
  if (event.key === "Enter" && event.target.matches("#meeting-title-input") && !$("#rename-meeting-modal").hidden) {
    event.preventDefault();
    renameMeeting();
    return;
  }
  if (event.key === "Escape") {
    if (!$("#rename-meeting-modal").hidden) closeRenameMeeting();
    else if (!$("#delete-meeting-modal").hidden) closeDeleteMeeting();
    else if (!$("#cancel-confirm-modal").hidden) setModal("#cancel-confirm-modal", false);
    else if (!$("#capture-modal").hidden) closeCapture();
    setModal("#settings-modal", false);
  }
});

$("#rename-meeting-form").addEventListener("submit", (event) => {
  event.preventDefault();
  renameMeeting();
});

async function init() {
  const linkedView = location.hash.slice(1);
  $("#global-search-wrap").hidden = true;
  showWelcome();
  try {
    const [meetings, health] = await Promise.all([api("/api/meetings"), api("/api/health")]);
    state.meetings = meetings.meetings || [];
    state.health = health;
    renderMeetingList();
    if (/^meeting_[a-z0-9]+$/i.test(linkedView)) await openMeeting(linkedView);
    if (linkedView === "library") showLibrary();
    if (linkedView === "actions") await showActions();
    if (linkedView === "record") openCapture();
  } catch (error) { toast(`Local service unavailable: ${error.message}`, "error"); }
}

init();
