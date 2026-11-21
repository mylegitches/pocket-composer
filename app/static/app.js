// flp2score web UI: plain JS, no build step.
const $ = (sel, el = document) => el.querySelector(sel);
const state = { instruments: [], byId: {}, project: null, current: null, favorites: new Set(), ai: {}, presets: [] };
const KIND_ICON = { flp: "🎹", scan: "📄", score: "🎼", tab: "🎸" };
const PART_NAMES = ["Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Trumpet", "Trombone", "Bass Trombone",
  "Horn", "Tuba", "Flute", "Clarinet", "Vibraphone", "Piano", "Guitar", "Bass", "Drums", "Aux. Percussion", "Vocal"];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, error = false) {
  const t = document.createElement("div");
  t.className = "toast" + (error ? " error" : "");
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), error ? 6000 : 2500);
}

// ---------------------------------------------------------------- instrument previews

const player = new Audio();
let playingBtn = null;

function setPlayState(btn, mode) {
  if (!btn) return;
  btn.classList.toggle("loading", mode === "loading");
  btn.classList.toggle("playing", mode === "playing");
  btn.textContent = mode === "playing" ? "■" : mode === "loading" ? "" : "▶";
}

function stopPreview() {
  player.pause();
  setPlayState(playingBtn, "idle");
  playingBtn = null;
}

// Play (or stop) the MuseScore sample of an instrument. The first play renders it on the server.
function playPreview(id, btn) {
  if (playingBtn === btn) { stopPreview(); return; }
  stopPreview();
  playingBtn = btn;
  setPlayState(btn, "loading");
  player.src = `/api/instruments/${encodeURIComponent(id)}/preview`;
  player.play().catch(() => {});
}
player.addEventListener("playing", () => setPlayState(playingBtn, "playing"));
player.addEventListener("ended", stopPreview);
player.addEventListener("error", () => {
  if (playingBtn) toast("Could not render a preview for this instrument", true);
  stopPreview();
});

function playButton(id) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "play";
  b.title = "Hear this instrument";
  b.textContent = "▶";
  b.onclick = (e) => { e.stopPropagation(); playPreview(id, b); };
  return b;
}

// ---------------------------------------------------------------- favorites

async function toggleFavorite(id) {
  const favs = new Set(state.favorites);
  favs.has(id) ? favs.delete(id) : favs.add(id);
  state.favorites = favs; // show it right away; the server's answer follows
  refreshStars();
  try {
    const s = await api("/api/settings", { method: "PUT", body: JSON.stringify({ favorites: [...favs] }) });
    state.favorites = new Set(s.favorites);
    state.ai = s.ai;
  } catch (e) { toast(e.message, true); }
  refreshStars();
}

// Re-draw everything that shows favorites.
function refreshStars() {
  document.querySelectorAll(".browser").forEach((b) => b.redraw?.());
  document.querySelectorAll("#rows tr").forEach((tr) => {
    const ins = state.byId[tr.dataset.ins];
    if (ins) $(".ins-group", tr).textContent = ins.group + (state.favorites.has(ins.id) ? " · ★" : "");
  });
  if (!$("#settings").hidden) renderFavChips();
}

// ---------------------------------------------------------------- instrument browser

// A searchable list: Favorites first, then one collapsible section per MuseScore group.
// Groups start collapsed (Favorites starts open); while searching, every group with a match opens.
function instrumentBrowser(container, { selected = null, onPick = null } = {}) {
  container.innerHTML = "";
  const box = document.createElement("div");
  box.className = "browser";
  const search = document.createElement("input");
  search.type = "search";
  search.className = "browser-search";
  search.placeholder = `Search ${state.instruments.length} instruments…`;
  const list = document.createElement("div");
  list.className = "browser-list";
  box.append(search, list);
  container.append(box);
  const open = new Set(["★ Favorites"]);

  const item = (ins) => {
    const row = document.createElement("div");
    row.className = "ins-item" + (ins.id === selected ? " selected" : "");
    const fav = state.favorites.has(ins.id);
    const star = document.createElement("button");
    star.type = "button";
    star.className = "star" + (fav ? " on" : "");
    star.textContent = fav ? "★" : "☆";
    star.title = fav ? "Remove from favorites" : "Add to favorites";
    star.onclick = (e) => { e.stopPropagation(); toggleFavorite(ins.id); };
    const name = document.createElement(onPick ? "button" : "div");
    if (onPick) {
      name.type = "button";
      name.onclick = () => onPick(ins.id);
    }
    name.className = "ins-label";
    name.innerHTML = `<span></span>${ins.drums ? ' <small class="tag">drums</small>' : ""}`;
    name.firstChild.textContent = ins.name;
    if (ins.description) name.title = ins.description;
    row.append(star, name, playButton(ins.id));
    return row;
  };

  const section = (label, items, query) => {
    if (!items.length) return null;
    const d = document.createElement("details");
    d.className = "group";
    d.open = query ? true : open.has(label);
    const sum = document.createElement("summary");
    sum.innerHTML = `<span></span><small>${items.length}</small>`;
    sum.firstChild.textContent = label;
    d.append(sum);
    // Closed groups only cost a summary line; their rows are made on first open.
    const fill = () => {
      if (d.open && !d.dataset.filled) { d.dataset.filled = "1"; items.forEach((i) => d.append(item(i))); }
    };
    d.addEventListener("toggle", () => {
      if (!query) d.open ? open.add(label) : open.delete(label);
      fill();
    });
    fill();
    return d;
  };

  const redraw = () => {
    const q = search.value.trim().toLowerCase();
    const match = (i) => !q || i.name.toLowerCase().includes(q) || i.group.toLowerCase().includes(q) || i.id.includes(q);
    const byName = (a, b) => a.name.localeCompare(b.name);
    const scroll = list.scrollTop;
    list.innerHTML = "";
    const favs = [...state.favorites].map((id) => state.byId[id]).filter((i) => i && match(i)).sort(byName);
    const groups = {};
    for (const i of state.instruments) if (match(i)) (groups[i.group] ||= []).push(i);
    const sections = [section("★ Favorites", favs, q)];
    for (const g of Object.keys(groups).sort()) sections.push(section(g, groups[g].sort(byName), q));
    const shown = sections.filter(Boolean);
    list.append(...shown);
    if (!state.favorites.size && !q) {
      const hint = document.createElement("p");
      hint.className = "muted small";
      hint.textContent = "No favorites yet: tap ☆ next to an instrument to add it.";
      list.prepend(hint);
    }
    if (!shown.length) {
      const none = document.createElement("p");
      none.className = "muted small";
      none.textContent = `No instrument matches “${search.value}”.`;
      list.append(none);
    }
    list.scrollTop = scroll;
  };
  search.oninput = redraw;
  box.redraw = redraw;
  redraw();
  return { focus: () => search.focus() };
}

// ---------------------------------------------------------------- instrument picker (mapping rows)

function openPicker(tr) {
  const dlg = $("#picker");
  $("#picker-for").textContent = "for " + $(".ch-name", tr).textContent;
  const b = instrumentBrowser($("#picker-browser"), {
    selected: tr.dataset.ins,
    onPick: (id) => { setRowInstrument(tr, id); dlg.close(); },
  });
  dlg.showModal();
  if (matchMedia("(pointer: fine)").matches) b.focus(); // don't pop up the keyboard on phones
}

function setRowInstrument(tr, id, drumValue) {
  const ins = state.byId[id] || state.byId.piano;
  tr.dataset.ins = ins.id;
  $(".ins-name", tr).textContent = ins.name;
  $(".ins-group", tr).textContent = ins.group + (state.favorites.has(ins.id) ? " · ★" : "");
  const play = $(".play", tr);
  play.onclick = () => playPreview(tr.dataset.ins, play);
  syncRow(tr, drumValue);
}

// ---------------------------------------------------------------- projects

async function loadProjects() {
  const list = await api("/api/projects");
  const ul = $("#projects");
  ul.innerHTML = "";
  for (const p of list) {
    const li = document.createElement("li");
    li.dataset.name = p.name;
    const badge = p.busy ? "working…" : p.converted ? "✓ scored" : "";
    li.innerHTML = `<span class="pl-name"><span class="kind"></span><span></span></span><span class="badge ${p.converted ? "done" : ""}">${badge}</span>`;
    li.querySelector(".kind").textContent = KIND_ICON[p.kind] || "";
    li.querySelector(".pl-name span:last-child").textContent = p.name;
    li.onclick = () => { location.hash = encodeURIComponent(p.name); };
    if (p.name === state.current) li.classList.add("active");
    ul.append(li);
  }
  if (!list.length) ul.innerHTML = `<li class="badge">No projects yet</li>`;
}

function showPage(which) {
  for (const id of ["empty", "project", "settings"]) $("#" + id).hidden = id !== which;
  $("#open-settings").classList.toggle("active", which === "settings");
  if (which !== "project") document.querySelectorAll("#projects li").forEach((li) => li.classList.remove("active"));
}

function renderFavChips() {
  const box = $("#fav-chips");
  box.innerHTML = "";
  const favs = [...state.favorites].map((id) => state.byId[id]).filter(Boolean)
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const ins of favs) {
    const c = document.createElement("span");
    c.className = "chip fav-chip";
    const name = document.createElement("span");
    name.textContent = ins.name;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "chip-x";
    x.title = "Remove from favorites";
    x.textContent = "✕";
    x.onclick = () => toggleFavorite(ins.id);
    c.append(playButton(ins.id), name, x);
    box.append(c);
  }
  if (!favs.length) box.innerHTML = `<span class="muted small">No favorites yet.</span>`;
}

function openSettings() {
  state.current = null;
  showPage("settings");
  renderFavChips();
  renderAiSettings();
  instrumentBrowser($("#settings-browser"));
}

async function openProject(name) {
  state.current = name;
  document.querySelectorAll("#projects li").forEach((li) => li.classList.toggle("active", li.dataset.name === name));
  try {
    render(await api(`/api/projects/${encodeURIComponent(name)}`));
  } catch (e) {
    toast(e.message, true);
  }
}

function render(p) {
  state.project = p;
  showPage("project");
  const kind = p.kind;
  document.querySelectorAll(".flp-only").forEach((el) => { el.hidden = kind !== "flp"; });
  $("#suggest").hidden = kind !== "flp" || !p.ai?.configured;
  $("#flp-panel").hidden = kind !== "flp";
  $("#scan-panel").hidden = kind !== "scan";
  $("#tab-panel").hidden = kind !== "tab";
  $("#title").readOnly = kind === "score";
  if (kind === "flp") renderFlp(p);
  else if (kind === "scan") renderScan(p);
  else if (kind === "tab") renderTab(p);
  else {
    $("#title").value = p.name;
    $("#meta").textContent = `${p.file} · converted as-is by MuseScore`;
  }
  renderOutputs(p);
  showJob(p.job);
}

function renderFlp(p) {
  $("#title").value = p.mapping.title || p.name;
  $("#meta").textContent =
    `${p.file} · FL ${p.fl_version.split(".").slice(0, 2).join(".")} · ${p.tempo} BPM · ${p.time_sig} · ${p.bars} bars` +
    (p.mapping_saved ? " · mapping saved" : " · automatic guesses");

  const notice = $("#notice");
  const msgs = [];
  if (!p.channels.length) msgs.push("No piano-roll notes are placed on the playlist, so there is nothing to notate.");
  if (p.audio_clips) msgs.push(`${p.audio_clips} audio clip(s) on the playlist are ignored (audio can't be turned into notes).`);
  notice.hidden = !msgs.length;
  notice.textContent = msgs.join(" ");

  const byCh = Object.fromEntries(p.mapping.channels.map((c) => [c.channel, c]));
  const tbody = $("#rows");
  tbody.innerHTML = "";
  for (const ch of p.channels) {
    const m = byCh[ch.channel];
    const tr = $("#row-tpl").content.firstElementChild.cloneNode(true);
    tr.dataset.channel = ch.channel;
    $(".ch-name", tr).textContent = ch.name;
    $(".ch-info", tr).textContent =
      `${ch.notes} notes · ${ch.low}–${ch.high}` + (ch.patterns.length ? ` · ${ch.patterns.join(", ")}` : "");
    $(".inc", tr).checked = m.include;
    $(".part", tr).value = m.part;
    $(".staves", tr).value = m.staves;
    $(".octave", tr).value = String(m.transpose);
    $(".ins-btn", tr).onclick = () => openPicker(tr);
    setRowInstrument(tr, m.instrument, m.drum_sound);
    $(".inc", tr).onchange = () => tr.classList.toggle("off", !$(".inc", tr).checked);
    tr.classList.toggle("off", !m.include);
    tbody.append(tr);
  }
  $("#silent").textContent = p.silent_channels.length
    ? `Channels with no notes on the playlist (not in the score): ${p.silent_channels.join(", ")}` : "";
}

// ---------------------------------------------------------------- sheet music (PDF / photos)

const pageList = (pages) => {
  // [1,2,3,5] -> "1-3, 5"
  const out = [];
  for (let i = 0; i < pages.length; i++) {
    let j = i;
    while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j++;
    out.push(j > i ? `${pages[i]}-${pages[j]}` : `${pages[i]}`);
    i = j;
  }
  return out.join(", ");
};

const parsePages = (text, max) => {
  const out = new Set();
  for (const chunk of text.split(/[,\s]+/).filter(Boolean)) {
    const [a, b] = chunk.split("-").map(Number);
    if (!a) continue;
    for (let n = a; n <= (b || a); n++) if (n >= 1 && (!max || n <= max)) out.add(n);
  }
  return [...out].sort((x, y) => x - y);
};

function keyOptions(sel, value) {
  sel.innerHTML = "";
  sel.append(new Option("auto", ""));
  for (let f = -7; f <= 7; f++) {
    const label = f === 0 ? "no sharps/flats" : f > 0 ? `${f} sharp${f > 1 ? "s" : ""}` : `${-f} flat${f < -1 ? "s" : ""}`;
    sel.append(new Option(label, String(f)));
  }
  sel.value = value === null || value === undefined ? "" : String(value);
}

function planRow(part) {
  const tr = $("#plan-tpl").content.firstElementChild.cloneNode(true);
  $(".inc", tr).checked = !part.skip;
  $(".pname", tr).value = part.name;
  $(".ppages", tr).value = pageList(part.pages);
  $(".pclef", tr).value = part.clef || "";
  keyOptions($(".pkey", tr), part.fifths);
  $(".premove", tr).onclick = () => { tr.remove(); paintThumbs(); };
  $(".inc", tr).onchange = () => { tr.classList.toggle("off", !$(".inc", tr).checked); paintThumbs(); };
  $(".ppages", tr).onchange = paintThumbs;
  $(".pname", tr).onchange = paintThumbs;
  tr.classList.toggle("off", part.skip);
  return tr;
}

function renderTab(p) {
  $("#title").value = p.title || p.name;
  $("#title").readOnly = true;
  $("#meta").textContent = `${p.file} · ${p.source === "songsterr" ? "Songsterr tab page" : "Songsterr part"}` +
    (p.artist ? ` · ${p.artist}` : "");
  const box = $("#tab-tracks");
  box.innerHTML = "";
  for (const t of p.tracks) {
    const l = document.createElement("label");
    l.className = "check";
    l.innerHTML = `<input type="checkbox"> <span></span> <small class="muted"></small>`;
    l.querySelector("input").checked = t.selected;
    l.querySelector("input").value = t.partId;
    l.querySelector("span").textContent = t.title;
    l.querySelector("small").textContent = t.isDrums ? "drums" : t.instrument;
    box.append(l);
  }
}

function renderScan(p) {
  const plan = p.plan;
  const tab = p.read_as === "tab";
  $("#read-as").value = p.read_as || "notation";
  $("#parts-card").hidden = tab;
  $("#read-as-hint").textContent = tab
    ? (p.ai?.configured ? `Convert sends the pages to ${p.ai.model}, which writes out every bar's frets and rhythm. `
      + "That becomes a Guitar Pro file and a score. Use a model that can read images. Long tabs can take a few minutes."
      : "Reading tab needs an AI endpoint: set one up on the Settings page.")
    : "The notes are read by homr on this server. The AI endpoint (or OCR) only finds the parts.";
  $("#title").value = p.title || p.name;
  $("#meta").textContent = `${p.files.length > 1 ? p.files.length + " photos" : p.file} · ${p.pages} page(s)` +
    (plan ? ` · ${plan.parts.length} part(s)` : " · parts not found yet");
  const aiOk = p.ai?.configured;
  $("#detect-ai").disabled = !aiOk;
  $("#detect-ai").title = aiOk ? `Read the pages with ${p.ai.model}` : "Set up an AI endpoint on the Settings page first";
  $("#plan-hint").textContent = plan
    ? "Check the part names and page ranges, then Convert. The name picks the instrument (clef, transposition, sound)."
      + (plan.score_pages?.length ? ` Full-score pages left out: ${pageList(plan.score_pages)}.` : "")
    : aiOk ? `Press “Find parts with AI” (${p.ai.model}), or just Convert: parts are found first.`
      : "Press “Find parts with OCR”, or set up an AI endpoint on the Settings page to read the pages with AI.";
  const tbody = $("#plan-rows");
  tbody.innerHTML = "";
  for (const part of plan?.parts || []) tbody.append(planRow(part));
  const thumbs = $("#thumbs");
  thumbs.innerHTML = "";
  for (let n = 1; n <= p.pages; n++) {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.dataset.page = n;
    fig.innerHTML = `<img loading="lazy" alt="page ${n}"><figcaption><b>${n}</b> <span></span></figcaption>`;
    fig.querySelector("img").src = `/api/projects/${encodeURIComponent(p.name)}/pages/${n}?size=400`;
    fig.onclick = () => window.open(`/api/projects/${encodeURIComponent(p.name)}/pages/${n}?size=2000`, "_blank");
    thumbs.append(fig);
  }
  paintThumbs();
}

// Label each page thumbnail with the part it belongs to.
function paintThumbs() {
  const owner = {};
  [...document.querySelectorAll("#plan-rows tr")].forEach((tr, i) => {
    for (const n of parsePages($(".ppages", tr).value)) {
      owner[n] = { name: $(".pname", tr).value, skip: !$(".inc", tr).checked, i };
    }
  });
  const scorePages = new Set(state.project?.plan?.score_pages || []);
  document.querySelectorAll("#thumbs .thumb").forEach((fig) => {
    const n = Number(fig.dataset.page);
    const o = owner[n];
    fig.querySelector("span").textContent = o ? o.name : scorePages.has(n) ? "full score" : "—";
    fig.className = "thumb" + (o ? ` part-${o.i % 6}` : "") + (!o || o.skip ? " unused" : "");
  });
}

function collectPlan() {
  const max = state.project?.pages || 0;
  return {
    title: $("#title").value.trim(),
    parts: [...document.querySelectorAll("#plan-rows tr")].map((tr) => ({
      name: $(".pname", tr).value.trim() || "Part",
      pages: parsePages($(".ppages", tr).value, max),
      clef: $(".pclef", tr).value || null,
      fifths: $(".pkey", tr).value === "" ? null : Number($(".pkey", tr).value),
      skip: !$(".inc", tr).checked,
    })).filter((x) => x.pages.length),
  };
}

async function savePlan(quiet = false) {
  try {
    render(await api(`/api/projects/${encodeURIComponent(state.current)}/plan`, {
      method: "PUT", body: JSON.stringify(collectPlan()),
    }));
    if (!quiet) toast("Parts saved");
  } catch (e) { toast(e.message, true); }
}

async function detectParts(useAi) {
  if (document.querySelector("#plan-rows tr") &&
      !confirm("Find the parts again? This replaces the part list below.")) return;
  try {
    showJob(await api(`/api/projects/${encodeURIComponent(state.current)}/detect`, {
      method: "POST", body: JSON.stringify({ use_ai: useAi }),
    }));
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- background jobs (part finding, OMR)

let jobTimer = 0;

function showJob(job) {
  clearTimeout(jobTimer);
  const box = $("#job");
  if (!job) { box.hidden = true; return; }
  box.hidden = false;
  const running = job.state === "queued" || job.state === "running";
  $("#job-spin").hidden = !running;
  $("#job-title").textContent = {
    detect: "Finding the parts", convert: "Reading the notes and writing the score",
    tab: "Reading the tab with the AI and writing the score",
  }[job.kind] + (job.state === "queued" ? " (waiting for another job)" : job.state === "failed" ? " — failed" : job.state === "done" ? " — done" : "…");
  $("#job-pct").textContent = running && job.progress ? `${Math.round(job.progress * 100)}%` : "";
  $("#job-bar").style.width = `${Math.round((job.state === "done" ? 1 : job.progress) * 100)}%`;
  box.classList.toggle("failed", job.state === "failed");
  const log = $("#job-log");
  const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  log.textContent = job.log.join("\n");
  if (atEnd) log.scrollTop = log.scrollHeight;
  $("#convert").disabled = running;
  if (running) {
    jobTimer = setTimeout(async () => {
      try {
        const j = await api(`/api/jobs/${job.id}`);
        if (state.current !== job.project) return;
        if (j.state === "done" || j.state === "failed") return jobFinished(j);
        showJob(j);
      } catch (e) { toast(e.message, true); }
    }, 1500);
  }
}

async function jobFinished(job) {
  await openProject(job.project);
  showJob(job);
  loadProjects();
  if (job.state === "failed") toast(job.error || "The job failed", true);
  else if (job.kind === "convert") $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- AI suggestions for FL channels

async function suggest() {
  const btn = $("#suggest");
  btn.disabled = true;
  const label = btn.textContent;
  btn.innerHTML = `<span class="spinner"></span>Asking ${state.project.ai.model}…`;
  try {
    const r = await api(`/api/projects/${encodeURIComponent(state.current)}/suggest`, { method: "POST" });
    let changed = 0;
    for (const s of r.channels) {
      const tr = document.querySelector(`#rows tr[data-channel="${s.channel}"]`);
      if (!tr) continue;
      if (s.instrument && s.instrument !== tr.dataset.ins) { setRowInstrument(tr, s.instrument); changed++; }
      if (s.part) $(".part", tr).value = s.part;
      $(".inc", tr).checked = s.include;
      tr.classList.toggle("off", !s.include);
      tr.classList.add("suggested");
      setTimeout(() => tr.classList.remove("suggested"), 2500);
    }
    toast(`AI suggestions filled in (${changed} instrument change${changed === 1 ? "" : "s"}). Save or Convert to keep them.`);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = label; }
}

async function deleteProject() {
  const name = state.current;
  if (!confirm(`Delete “${name}” and everything converted from it? This can't be undone.`)) return;
  try {
    await api(`/api/projects/${encodeURIComponent(name)}`, { method: "DELETE" });
    state.current = null;
    location.hash = "";
    showPage("empty");
    loadProjects();
    toast(`Deleted ${name}`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- AI settings form

function aiForm() {
  const preset = state.presets.find((x) => x.id === $("#ai-preset").value) || {};
  return {
    preset: preset.id || "", provider: preset.provider || "", base_url: $("#ai-url").value.trim(),
    model: $("#ai-model").value.split(" · ")[0].trim(), api_key: $("#ai-key").value || null,
    use_for_parts: $("#ai-parts").checked,
    models: Object.fromEntries([...document.querySelectorAll("#ai-tasks .task-model")].map((i) => [i.dataset.task, i.value.split(" · ")[0].trim()])),
  };
}

function renderAiSettings() {
  const a = state.ai;
  const sel = $("#ai-preset");
  if (!sel.options.length) {
    sel.append(new Option("— not set up —", ""));
    for (const pr of state.presets) sel.append(new Option(pr.label, pr.id));
    sel.onchange = () => {
      const pr = state.presets.find((x) => x.id === sel.value);
      if (!pr) return;
      $("#ai-url").value = pr.base_url;
      $("#ai-model").value = pr.model || "";
      $("#ai-models").innerHTML = "";
      syncKeyField();
    };
  }
  sel.value = a.preset || "";
  $("#ai-url").value = a.base_url || "";
  $("#ai-model").value = a.model || "";
  $("#ai-key").value = "";
  $("#ai-parts").checked = a.use_for_parts !== false;
  renderTaskRows(a.models || {});
  syncKeyField();
  const st = $("#ai-status");
  st.className = "small " + (a.configured ? "ok" : "muted");
  st.textContent = a.configured ? `Active: ${a.model} at ${a.base_url}` : "No AI endpoint in use: parts are found with OCR.";
}

function syncKeyField() {
  const pr = state.presets.find((x) => x.id === $("#ai-preset").value);
  const needs = pr ? pr.key : true;
  $("#ai-key").placeholder = state.ai.has_key ? `saved (${state.ai.key_hint}) — type to replace`
    : needs ? "paste your API key" : "not needed for a local Ollama";
  $("#ai-clear").hidden = !state.ai.has_key;
}

async function aiSave() {
  try {
    const s = await api("/api/settings", { method: "PUT", body: JSON.stringify({ ai: aiForm() }) });
    state.ai = s.ai;
    renderAiSettings();
    toast("AI settings saved");
  } catch (e) { toast(e.message, true); }
}

async function aiClearKey() {
  if (!confirm("Remove the saved API key from this server?")) return;
  try {
    const s = await api("/api/settings", { method: "PUT", body: JSON.stringify({ ai: { ...aiForm(), api_key: null, clear_key: true } }) });
    state.ai = s.ai;
    renderAiSettings();
  } catch (e) { toast(e.message, true); }
}

async function aiTest() {
  const st = $("#ai-status");
  st.className = "small muted";
  st.innerHTML = `<span class="spinner"></span>Asking ${$("#ai-model").value || "the model"}…`;
  try {
    const r = await api("/api/ai/test", { method: "POST", body: JSON.stringify(aiForm()) });
    st.className = "small ok";
    st.textContent = `✓ Connected in ${r.seconds}s. The model replied: “${r.reply}”. Press Save to use it.`;
  } catch (e) {
    st.className = "small bad";
    st.textContent = "✗ " + e.message;
  }
}

function renderTaskRows(models) {
  const box = $("#ai-tasks");
  box.innerHTML = "";
  for (const [task, label] of Object.entries(state.aiTasks || {})) {
    const row = document.createElement("div");
    row.className = "task-row";
    const [what, need] = label.split(" — ");
    row.innerHTML = `<div class="task-label"><b></b><small class="muted"></small></div>
      <input class="task-model" list="ai-models" autocomplete="off">
      <button type="button" class="ghost task-test">Test</button>
      <div class="task-result small"></div>`;
    row.querySelector("b").textContent = what;
    row.querySelector("small").textContent = need || "";
    const input = row.querySelector("input");
    input.dataset.task = task;
    input.value = models[task] || "";
    input.placeholder = "same as main model";
    row.querySelector("button").onclick = () => testTask(task, row);
    box.append(row);
  }
}

async function testTask(task, row) {
  const out = row.querySelector(".task-result");
  const btn = row.querySelector("button");
  const model = row.querySelector("input").value.trim() || $("#ai-model").value.trim();
  btn.disabled = true;
  out.className = "task-result small muted";
  out.innerHTML = `<span class="spinner"></span>Testing ${model}… (a tab can take a few minutes)`;
  try {
    const r = await api("/api/ai/test_task", { method: "POST", body: JSON.stringify({ ...aiForm(), task }) });
    out.className = "task-result small " + (r.score >= 90 ? "ok" : r.score >= 60 ? "warn" : "bad");
    out.textContent = `${r.model}: ${r.score}% in ${r.seconds}s — ${r.summary}`;
  } catch (e) {
    out.className = "task-result small bad";
    out.textContent = `${model}: ✗ ${e.message}`;
  } finally { btn.disabled = false; }
}

async function aiListModels() {
  const btn = $("#ai-list");
  btn.disabled = true;
  try {
    const r = await api("/api/ai/models", { method: "POST", body: JSON.stringify(aiForm()) });
    const dl = $("#ai-models");
    dl.innerHTML = "";
    for (const m of r.models) dl.append(new Option(m, m.split(" · ")[0]));
    toast(`${r.models.length} models found: tap the Model box to pick one`);
    $("#ai-model").focus();
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}

// Show the options that make sense for the chosen instrument.
function syncRow(tr, drumValue) {
  const ins = state.byId[tr.dataset.ins];
  if (!ins) return;
  $(".staves-opt", tr).hidden = !(ins.staves > 1);
  const drumOpt = $(".drum-opt", tr);
  drumOpt.hidden = !ins.drums;
  if (ins.drums) {
    const sel = $(".drum", tr);
    const keep = drumValue ?? sel.value ?? "auto";
    sel.innerHTML = "";
    sel.append(new Option("auto (from name)", "auto"), new Option("keys as played (GM map)", "keys"));
    for (const d of ins.drums) sel.append(new Option(`${d.name} (${d.pitch})`, String(d.pitch)));
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "auto";
  }
}

function collectMapping() {
  const channels = [...document.querySelectorAll("#rows tr")].map((tr) => ({
    channel: Number(tr.dataset.channel),
    include: $(".inc", tr).checked,
    instrument: tr.dataset.ins,
    part: $(".part", tr).value.trim(),
    staves: $(".staves", tr).value,
    drum_sound: $(".drum-opt", tr).hidden ? "auto" : $(".drum", tr).value,
    transpose: Number($(".octave", tr).value),
  }));
  return { title: $("#title").value.trim(), channels };
}

function renderOutputs(p, result) {
  const box = $("#result");
  const files = p.outputs || [];
  box.hidden = !files.length && !result;
  const parts = $("#parts");
  parts.innerHTML = "";
  for (const part of result?.parts || []) {
    const c = document.createElement("span");
    c.className = "chip";
    c.innerHTML = `<b></b> · <span></span> · ${part.notes} notes`;
    c.querySelector("b").textContent = part.part;
    c.querySelector("span").textContent = part.instrument;
    parts.append(c);
  }
  if (result?.message) toast(result.message, true);
  if (result?.failed?.length) toast("MuseScore could not write: " + result.failed.join(", "), true);

  const audio = $("#score-audio");
  const mp3 = files.find((f) => f.kind === "mp3");
  audio.hidden = !mp3;
  if (mp3) {
    const src = `${mp3.url}?t=${Math.round(mp3.mtime)}`;
    if (audio.getAttribute("src") !== src) audio.src = src;
  } else audio.removeAttribute("src");

  const dl = $("#downloads");
  dl.innerHTML = "";
  const stamp = Date.now();
  const link = (f, label) => {
    const a = document.createElement("a");
    a.href = f.url;
    if (f.kind !== "pdf") a.download = f.file; else a.target = "_blank";
    a.innerHTML = `<span></span><small>${(f.size / 1024).toFixed(0)} KB</small>`;
    a.firstChild.textContent = label;
    return a;
  };
  for (const f of files.filter((f) => !f.part && f.kind !== "svg" && f.kind !== "spos")) dl.append(link(f, "." + f.kind));
  const partFiles = files.filter((f) => f.part);
  $("#part-files").hidden = !partFiles.length;
  $("#part-downloads").innerHTML = "";
  for (const f of partFiles) $("#part-downloads").append(link(f, f.file));
  const pages = $("#pages");
  pages.innerHTML = "";
  files.filter((f) => f.kind === "svg" && !f.part).forEach((f, i) => {
    const page = document.createElement("div");
    page.className = "page";
    page.dataset.page = i;
    const img = document.createElement("img");
    img.src = `${f.url}?t=${stamp}`;
    img.alt = f.file;
    img.loading = "lazy";
    page.append(img);
    page.onclick = (e) => seekToClick(page, e);
    pages.append(page);
  });
  follow.timeline = p.timeline || null;
  follow.last = null;
  pages.classList.toggle("seekable", !!follow.timeline);
  drawCursor();
}

// ---------------------------------------------------------------- playback line

// MuseScore's .spos export says where each note column is on the pages and when it plays.
// While the score audio plays, a line moves through those positions and the view follows it.
const follow = { timeline: null, last: null, raf: 0 };

function cursorAt(ms) {
  const ev = follow.timeline?.events;
  if (!ev?.length) return null;
  let lo = 0, hi = ev.length - 1;
  if (ms < ev[0][0]) return null;
  while (lo < hi) { // last event at or before ms
    const mid = (lo + hi + 1) >> 1;
    if (ev[mid][0] <= ms) lo = mid; else hi = mid - 1;
  }
  const [t, page, x, y, w, h] = ev[lo];
  const next = ev[lo + 1];
  // Glide toward the next note when it is on the same system; otherwise toward this note's right edge.
  const sameLine = next && next[1] === page && next[3] === y;
  const span = next ? next[0] - t : 500;
  const frac = Math.min(1, Math.max(0, (ms - t) / (span || 1)));
  const x2 = sameLine ? next[2] : x + w;
  return { page, x: x + (x2 - x) * frac, y, h };
}

function drawCursor() {
  const audio = $("#score-audio");
  document.querySelectorAll("#pages .cursor").forEach((c) => c.remove());
  if (!follow.timeline || audio.hidden || (audio.paused && audio.currentTime === 0)) return;
  const pos = cursorAt(audio.currentTime * 1000);
  if (!pos) return;
  const page = document.querySelector(`#pages .page[data-page="${pos.page}"]`);
  if (!page) return;
  const { page_w: W, page_h: H } = follow.timeline;
  const c = document.createElement("div");
  c.className = "cursor";
  c.style.left = (pos.x / W * 100) + "%";
  c.style.top = (pos.y / H * 100) + "%";
  c.style.height = (pos.h / H * 100) + "%";
  page.append(c);
  // Scroll when the line moves to another system (or the user scrolled it out of sight).
  const key = pos.page + ":" + pos.y;
  const r = c.getBoundingClientRect();
  const offscreen = r.bottom < 60 || r.top > innerHeight - 20;
  if (!audio.paused && (key !== follow.last || offscreen)) {
    follow.last = key;
    c.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function followLoop() {
  drawCursor();
  if (!$("#score-audio").paused) follow.raf = requestAnimationFrame(followLoop);
}

// Click on the score: jump the audio to the note column nearest the click.
function seekToClick(page, e) {
  const tl = follow.timeline;
  const audio = $("#score-audio");
  if (!tl || audio.hidden) return;
  const rect = page.getBoundingClientRect();
  const px = (e.clientX - rect.left) / rect.width * tl.page_w;
  const py = (e.clientY - rect.top) / rect.height * tl.page_h;
  const n = Number(page.dataset.page);
  let best = null, bestD = Infinity;
  for (const ev of tl.events) {
    if (ev[1] !== n) continue;
    const dy = py < ev[3] ? ev[3] - py : py > ev[3] + ev[5] ? py - ev[3] - ev[5] : 0;
    const d = dy * 4 + Math.abs(px - ev[2]);
    if (d < bestD) { bestD = d; best = ev; }
  }
  if (!best) return;
  audio.currentTime = best[0] / 1000;
  follow.last = null;
  if (audio.paused) audio.play().catch(() => {});
  drawCursor();
}

// ---------------------------------------------------------------- actions

async function save() {
  try {
    render(await api(`/api/projects/${encodeURIComponent(state.current)}/mapping`, {
      method: "PUT", body: JSON.stringify(collectMapping()),
    }));
    toast("Mapping saved");
  } catch (e) { toast(e.message, true); }
}

async function reset() {
  if (!confirm("Forget the saved choices for this project and go back to the automatic guesses?")) return;
  try {
    render(await api(`/api/projects/${encodeURIComponent(state.current)}/mapping`, { method: "DELETE" }));
  } catch (e) { toast(e.message, true); }
}

async function convert() {
  const btn = $("#convert");
  const formats = [...document.querySelectorAll(".formats input:checked")].map((i) => i.value);
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Converting…`;
  try {
    const kind = state.project.kind;
    const body = { formats };
    if (kind === "flp") body.mapping = collectMapping();
    if (kind === "scan" && state.project.read_as !== "tab" && document.querySelector("#plan-rows tr")) body.plan = collectPlan();
    if (kind === "tab") {
      body.tracks = [...document.querySelectorAll("#tab-tracks input:checked")].map((i) => Number(i.value));
      if (!body.tracks.length) throw new Error("Pick at least one track");
      if (!formats.includes("gp5")) body.formats = [...formats, "gp5"];
    }
    const p = await api(`/api/projects/${encodeURIComponent(state.current)}/convert`, {
      method: "POST", body: JSON.stringify(body),
    });
    render(p);
    loadProjects();
    if (p.job) { $("#job").scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    renderOutputs(p, p.result);
    $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.textContent = "Convert";
    btn.disabled = !$("#job").hidden && !$("#job-spin").hidden;
  }
}

async function upload(fileList) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const what = files.length > 1 ? `${files.length} files` : files[0].name;
  toast(`Uploading ${what}…`);
  try {
    const p = await api("/api/projects", { method: "POST", body: fd });
    await loadProjects();
    const h = "#" + encodeURIComponent(p.name);
    if (location.hash === h) openProject(p.name); else location.hash = h;
  } catch (e) { toast(e.message, true); }
  $("#upload").value = "";
}

// ---------------------------------------------------------------- startup

async function init() {
  $("#save").onclick = save;
  $("#reset").onclick = reset;
  $("#convert").onclick = convert;
  $("#upload").onchange = (e) => upload(e.target.files);
  $("#suggest").onclick = suggest;
  $("#url-form").onsubmit = async (e) => {
    e.preventDefault();
    const url = $("#url-input").value.trim();
    if (!url) return;
    const btn = $("#url-form button");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>`;
    try {
      const p = await api("/api/projects/from_url", { method: "POST", body: JSON.stringify({ url }) });
      $("#url-input").value = "";
      await loadProjects();
      const h = "#" + encodeURIComponent(p.name);
      if (location.hash === h) openProject(p.name); else location.hash = h;
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; btn.textContent = "Add"; }
  };
  $("#delete").onclick = deleteProject;
  $("#detect-ai").onclick = () => detectParts(true);
  $("#detect-ocr").onclick = () => detectParts(false);
  $("#save-plan").onclick = () => savePlan();
  $("#read-as").onchange = async (e) => {
    try {
      render(await api(`/api/projects/${encodeURIComponent(state.current)}/read_as`, {
        method: "PUT", body: JSON.stringify({ read_as: e.target.value }),
      }));
    } catch (err) { toast(err.message, true); }
  };
  $("#add-part").onclick = () => {
    $("#plan-rows").append(planRow({ name: "", pages: [], clef: null, fifths: null, skip: false }));
    paintThumbs();
  };
  $("#ai-save").onclick = aiSave;
  $("#ai-test").onclick = aiTest;
  $("#ai-list").onclick = aiListModels;
  $("#ai-clear").onclick = aiClearKey;
  for (const n of PART_NAMES) $("#part-names").append(new Option(n));
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); };

  api("/api/health").then((h) => {
    const el = $("#health");
    el.textContent = h.musescore ? "MuseScore ready" : "MuseScore not found";
    el.classList.toggle("bad", !h.musescore);
  });
  const scoreAudio = $("#score-audio");
  scoreAudio.addEventListener("play", () => { stopPreview(); cancelAnimationFrame(follow.raf); followLoop(); });
  scoreAudio.addEventListener("seeked", drawCursor);
  scoreAudio.addEventListener("pause", drawCursor);
  scoreAudio.addEventListener("ended", () => { follow.last = null; scoreAudio.currentTime = 0; drawCursor(); });
  player.addEventListener("play", () => scoreAudio.pause());
  $("#picker-close").onclick = () => $("#picker").close();
  $("#picker").addEventListener("close", stopPreview);
  $("#picker").addEventListener("click", (e) => { if (e.target.id === "picker") e.target.close(); }); // backdrop
  const [instruments, settings] = await Promise.all([api("/api/instruments"), api("/api/settings")]);
  state.instruments = instruments;
  state.favorites = new Set(settings.favorites);
  state.ai = settings.ai;
  state.presets = settings.ai_presets;
  state.aiTasks = settings.ai_tasks;
  state.byId = Object.fromEntries(state.instruments.map((i) => [i.id, i]));
  await loadProjects();
  const fromHash = () => {
    const name = decodeURIComponent(location.hash.slice(1));
    stopPreview();
    if (name === ":settings") openSettings();
    else if (name && name !== state.current) openProject(name);
    else if (!name) { state.current = null; showPage("empty"); }
  };
  window.addEventListener("hashchange", fromHash);
  fromHash();
}

init();
