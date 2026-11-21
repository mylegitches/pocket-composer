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

let listTimer = 0;
const lastStates = {};

function jobBadge(p) {
  const j = p.job;
  if (j?.state === "running") {
    const what = j.kind === "detect" ? "finding parts" : "working";
    return { text: j.progress ? `${what} ${Math.round(j.progress * 100)}%` : `${what}…`, cls: "busy", spin: true };
  }
  if (j?.state === "queued") return { text: "waiting", cls: "busy", spin: false };
  if (j?.state === "failed") return { text: j.error === "Cancelled" ? "cancelled" : "⚠ failed", cls: "bad", title: j.error };
  if (p.converted) return { text: "✓ scored", cls: "done" };
  return { text: "", cls: "" };
}

const KIND_LABEL = { flp: "FL Studio", scan: "Sheet music", score: "MusicXML / MIDI", tab: "Tab" };
const hashFor = (name, view) => "#" + encodeURIComponent(name) + (view === "play" ? "/play" : "");

function pieceCard(p) {
  const li = document.createElement("li");
  li.className = "piece";
  li.dataset.name = p.name;
  const b = jobBadge(p);
  li.innerHTML = `<a class="piece-main"><span class="piece-kind" aria-hidden="true"></span><span class="piece-text">
      <span class="piece-name"></span><span class="piece-meta"></span></span></a>
    <div class="piece-foot"><span class="badge ${b.cls}">${b.spin ? '<span class="spinner"></span>' : ""}<span></span></span>
      <span class="piece-actions"><a class="button primary small-btn play-btn">▶ Play</a>
      <a class="button secondary small-btn edit-btn">✎ Edit</a></span></div>`;
  li.querySelector(".badge span:last-child").textContent = b.text;
  if (b.title) li.title = b.title;
  li.querySelector(".piece-kind").textContent = KIND_ICON[p.kind] || "♪";
  li.querySelector(".piece-name").textContent = p.name;
  li.querySelector(".piece-meta").textContent =
    `${KIND_LABEL[p.kind] || p.kind} · ${new Date(p.updated * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
  li.querySelector(".piece-main").href = hashFor(p.name, p.audio ? "play" : "edit");
  li.querySelector(".play-btn").href = hashFor(p.name, "play");
  li.querySelector(".play-btn").hidden = !p.audio;
  li.querySelector(".edit-btn").href = hashFor(p.name, "edit");
  li.querySelector(".piece-main").ondblclick = (e) => { e.preventDefault(); renameProject(p.name); };
  return li;
}

async function loadProjects() {
  clearTimeout(listTimer);
  let list;
  try { list = await api("/api/projects"); } catch (e) { listTimer = setTimeout(loadProjects, 5000); return; }
  state.list = list;
  const ul = $("#pieces");
  ul.replaceChildren(...list.map(pieceCard));
  if (!list.length) {
    ul.innerHTML = `<li class="pieces-empty card"><p><b>No pieces yet.</b> Add a photo, a PDF, a link, a MusicXML or MIDI
      file, or an FL Studio project, and PocketComposer turns it into a MuseScore score you can play along with.</p>
      <a class="button primary" href="#:new">＋ Add your first piece</a></li>`;
  }
  // A piece's job finished while it's open (started on another device, or after a restart): show the result.
  for (const p of list) {
    const st = p.job?.state;
    const was = lastStates[p.name];
    lastStates[p.name] = st;
    if (p.name === state.current && was && was !== st && (st === "done" || st === "failed") && p.job.id !== shownFinished) {
      api(`/api/jobs/${p.job.id}`).then(jobFinished).catch(() => {});
    }
  }
  if (list.some((p) => p.job && (p.job.state === "running" || p.job.state === "queued"))) {
    listTimer = setTimeout(loadProjects, 2500);
  }
}

// Page titles follow the house style "Section · Product".
function setTitle(section) {
  document.title = section ? `${section} · PocketComposer` : "PocketComposer";
}

function showPage(which) {
  if (which === "settings") setTitle("Settings");
  else if (which === "home") setTitle("Pieces");
  else if (which === "new") setTitle("New piece");
  for (const id of ["home", "new", "project", "settings"]) $("#" + id).hidden = id !== which;
  $("#nav-home").classList.toggle("active", which === "home" || which === "project");
  $("#nav-new").classList.toggle("active", which === "new");
  $("#nav-settings").classList.toggle("active", which === "settings");
  if (which !== "project") leavePlay();
}

// The two tabs of a piece: Edit (instruments, parts, convert, downloads) and Play.
function setView(view) {
  state.view = view;
  const name = state.current;
  $("#edit-view").hidden = view !== "edit";
  $("#play-view").hidden = view !== "play";
  for (const [id, v] of [["#tab-edit", "edit"], ["#tab-play", "play"]]) {
    $(id).href = hashFor(name, v);
    $(id).classList.toggle("active", v === view);
    $(id).setAttribute("aria-current", v === view ? "page" : "false");
  }
  document.body.classList.toggle("playing-view", view === "play");
  setTitle(view === "play" ? `${name} · Play` : name);
  if (view === "play") openPlay(); else leavePlay();
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
  state.project = null;
  showPage("settings");
  renderFavChips();
  renderAiSettings();
  instrumentBrowser($("#settings-browser"));
}

async function openProject(name, view = "edit", force = false) {
  const same = state.current === name && state.project?.name === name;
  state.current = name;
  showPage("project");
  $("#pname").textContent = name;
  if (same && !force) { setView(view); return; }
  state.view = view;
  try {
    render(await api(`/api/projects/${encodeURIComponent(name)}`));
  } catch (e) {
    toast(e.message, true);
  }
}

function render(p) {
  state.project = p;
  showPage("project");
  $("#pname").textContent = p.name;
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
  // Show the job card while it runs, or if the last one failed; a finished job's card isn't needed.
  showJob(p.job && p.job.state !== "done" ? p.job : null);
  setView(state.view || "edit");
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
    loadProjects();
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
  const p = state.project;
  const what = job.kind === "playback" ? "Getting it ready to play"
    : job.kind === "detect" ? "Finding the parts"
    : p?.kind === "scan" && p.read_as === "tab" ? "Reading the tab with the AI and writing the score"
    : p?.kind === "scan" ? "Reading the notes and writing the score" : "Writing the score";
  $("#job-title").textContent = what + (job.state === "queued" ? " (waiting its turn)" : job.state === "failed"
    ? (job.error === "Cancelled" ? " — cancelled" : " — failed") : job.state === "done" ? " — done" : "…");
  $("#job-cancel").hidden = job.state !== "queued";
  $("#job-cancel").onclick = async () => {
    try { showJob(await api(`/api/jobs/${job.id}`, { method: "DELETE" })); loadProjects(); } catch (e) { toast(e.message, true); }
  };
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
      } catch (e) { jobTimer = setTimeout(() => showJob(job), 4000); } // server restarting: keep trying
    }, 1500);
  }
}

let shownFinished = null;

async function jobFinished(job) {
  if (shownFinished === job.id) return;
  shownFinished = job.id;
  const view = state.view || "edit";
  await openProject(job.project, view, true);
  showJob(job);
  if (job.result && job.kind === "convert") renderOutputs(state.project, job.result);
  loadProjects();
  if (job.state === "failed") toast(job.error || "The job failed", true);
  else if (job.kind === "convert" && state.view === "edit") $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
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

async function renameProject(name = state.current) {
  if (!name) return;
  const next = prompt("New name for this piece:", name);
  if (next === null || !next.trim() || next.trim() === name) return;
  try {
    const p = await api(`/api/projects/${encodeURIComponent(name)}/rename`, {
      method: "POST", body: JSON.stringify({ name: next.trim() }),
    });
    delete lastStates[name];
    toast(`Renamed to ${p.name}`);
    if (state.current === name) {
      state.current = p.name;
      state.project = null;
      location.hash = hashFor(p.name, state.view);
    } else loadProjects();
  } catch (e) { toast(e.message, true); }
}

async function deleteProject() {
  const name = state.current;
  if (!confirm(`Delete “${name}” and everything converted from it? This can't be undone.`)) return;
  try {
    await api(`/api/projects/${encodeURIComponent(name)}`, { method: "DELETE" });
    state.current = null;
    state.project = null;
    location.hash = "";
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

  const mp3 = files.find((f) => f.kind === "mp3" && !f.part);
  $("#go-play").hidden = !mp3;
  $("#go-play").href = hashFor(p.name, "play");

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
  for (const f of files.filter((f) => !f.part && !["svg", "spos", "mpos"].includes(f.kind))) dl.append(link(f, "." + f.kind));
  const partFiles = files.filter((f) => f.part);
  $("#part-files").hidden = !partFiles.length;
  $("#part-downloads").innerHTML = "";
  for (const f of partFiles) $("#part-downloads").append(link(f, f.file));
}

// ---------------------------------------------------------------- Play screen
//
// The score follows the audio, laid out as one continuous strip (MuseScore's own one-line layout of the
// whole piece) or as pages. On the strip, the start of the line (instrument names, clefs, key and time
// signatures) stays pinned on the left, and each instrument's name lights up while it sounds.
// The badges above are a mixer: click to mute or unmute an instrument, right-click to solo it, All to reset.
// Every instrument has its own audio (rendered by MuseScore), played in sync with Web Audio.

const play = { name: null, data: null, raf: 0, sys: [], cursor: null, last: null, userScroll: 0,
  lights: [], badges: [], off: new Set(), strip: null };
const LAYOUT_KEY = "pocketcomposer.layout";
const STRIP_GAP = 10;

// ---- the mixer: one AudioBuffer per instrument (or the full mix while those aren't ready)

const mixer = {
  ctx: null, buffers: [], gains: [], sources: [], startAt: 0, offset: 0, playing: false, duration: 0,
  stems: false, loadId: 0, ready: false,

  async load(urls, stems) {
    const id = ++this.loadId;
    this.stop();
    this.ready = false;
    this.offset = 0;
    this.ctx ||= new (window.AudioContext || window.webkitAudioContext)();
    const bufs = await Promise.all(urls.map(async (u) => {
      const r = await fetch(u);
      if (!r.ok) throw new Error(`couldn't load the audio (${r.status})`);
      return this.ctx.decodeAudioData(await r.arrayBuffer());
    }));
    if (id !== this.loadId) return false;
    this.buffers = bufs;
    this.stems = stems;
    this.duration = Math.max(...bufs.map((b) => b.duration));
    this.gains = bufs.map(() => {
      const g = this.ctx.createGain();
      g.connect(this.ctx.destination);
      return g;
    });
    this.ready = true;
    this.applyMutes(true);
    return true;
  },
  time() {
    if (!this.playing) return this.offset;
    return Math.min(this.duration, this.offset + this.ctx.currentTime - this.startAt);
  },
  play() {
    if (!this.ready || this.playing) return;
    this.ctx.resume();
    if (this.offset >= this.duration - 0.05) this.offset = 0;
    const at = this.ctx.currentTime + 0.05;
    this.sources = this.buffers.map((b, i) => {
      const s = this.ctx.createBufferSource();
      s.buffer = b;
      s.connect(this.gains[i]);
      s.start(at, this.offset);
      return s;
    });
    const longest = this.sources[this.buffers.findIndex((b) => b.duration === this.duration)];
    longest.onended = () => { if (this.playing && this.time() >= this.duration - 0.1) this.ended(); };
    this.startAt = at;
    this.playing = true;
    onTransport();
  },
  stop() {
    for (const s of this.sources) { s.onended = null; try { s.stop(); } catch { /* not started */ } }
    this.sources = [];
  },
  pause() {
    if (!this.playing) return;
    this.offset = this.time();
    this.stop();
    this.playing = false;
    onTransport();
  },
  ended() {
    this.stop();
    this.playing = false;
    this.offset = 0;
    play.last = null;
    onTransport();
  },
  seek(t) {
    const was = this.playing;
    if (was) { this.stop(); this.playing = false; }
    this.offset = Math.max(0, Math.min(this.duration, t));
    if (was) this.play(); else onTransport();
  },
  toggle() { this.playing ? this.pause() : this.play(); },
  applyMutes(now = false) {
    if (!this.ready) return;
    this.gains.forEach((g, i) => {
      const v = this.stems && play.off.has(i) ? 0 : 1;
      if (now) g.gain.value = v; else g.gain.setTargetAtTime(v, this.ctx.currentTime, 0.015);
    });
  },
};

function fmtTime(t) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Play/pause button, time and the seek bar.
function onTransport() {
  const btn = $("#pp");
  btn.textContent = mixer.playing ? "❚❚" : "▶";
  btn.setAttribute("aria-label", mixer.playing ? "Pause" : "Play");
  btn.disabled = !mixer.ready;
  $("#seek").max = String(mixer.duration || 0);
  updateClock();
  cancelAnimationFrame(play.raf);
  if (mixer.playing) { stopPreview(); followLoop(); } else drawCursor();
}

function updateClock() {
  const t = mixer.time();
  $("#clock").textContent = `${fmtTime(t)} / ${fmtTime(mixer.duration || 0)}`;
  if (!play.seeking) $("#seek").value = String(t);
}

function getLayout() {
  try { return localStorage.getItem(LAYOUT_KEY) || "strip"; } catch { return "strip"; }
}

function setLayout(v) {
  try { localStorage.setItem(LAYOUT_KEY, v); } catch { /* private window: just don't remember it */ }
  renderScore();
}

async function openPlay() {
  const name = state.current;
  let d;
  try { d = await api(`/api/projects/${encodeURIComponent(name)}/playback`); } catch (e) { toast(e.message, true); return; }
  if (state.current !== name || state.view !== "play") return;
  $("#play-prep").hidden = d.ready;
  $("#play-ui").hidden = !d.ready;
  const busy = d.job && (d.job.state === "queued" || d.job.state === "running");
  if (!d.ready) {
    play.data = null;
    mixer.pause();
    $("#play-prep-text").textContent = busy
      ? (d.job.state === "queued"
        ? "Getting it ready to play: waiting for the jobs ahead of it. It opens here by itself when it's done."
        : "Getting it ready to play: MuseScore is laying out the score. It opens here by itself when it's done.")
      : d.can_prepare ? "Getting it ready to play…" : "There's nothing to play yet. Convert this piece on the Edit tab first.";
    $("#play-prep-go").hidden = true;
    $("#play-prep-edit").hidden = d.can_prepare;
    $("#play-prep-edit").href = hashFor(name, "edit");
    if (busy) showJob(d.job);
    return;
  }
  // Ready, maybe with the one-line layout and per-instrument audio still being made in the background.
  if (busy) showJob(d.job); else if (state.project?.job?.state !== "failed") $("#job").hidden = true;
  const sameAudio = play.name === name && play.data?.audio === d.audio &&
    JSON.stringify(play.data?.instruments?.map((i) => i.stem)) === JSON.stringify(d.instruments.map((i) => i.stem));
  if (play.name !== name) play.off = new Set();
  play.name = name;
  play.data = d;
  renderBadges();
  renderScore();
  if (!sameAudio) {
    const stems = d.instruments.length && d.instruments.every((i) => i.stem);
    $("#clock").textContent = stems ? "loading instruments…" : "loading…";
    mixer.duration = 0;
    onTransport();
    try {
      await mixer.load(stems ? d.instruments.map((i) => i.stem) : [d.audio], stems);
    } catch (e) {
      toast(e.message, true);
      return;
    }
  }
  onTransport();
  $("#mixer-note").textContent = d.strip && d.instruments.every((i) => i.stem) ? ""
    : busy ? "Muting instruments and the one-line strip are being made in the background; they switch on by themselves." : "";
}

function leavePlay() {
  mixer.pause();
  cancelAnimationFrame(play.raf);
}

async function preparePlay() {
  try {
    showJob(await api(`/api/projects/${encodeURIComponent(state.current)}/playback`, { method: "POST" }));
    loadProjects();
  } catch (e) { toast(e.message, true); }
}

// ---- badges: a mixer, not a meter (the names on the staves light up instead)

function renderBadges() {
  const box = $("#lights");
  box.innerHTML = "";
  const all = document.createElement("button");
  all.type = "button";
  all.className = "badge-all";
  all.textContent = "All";
  all.title = "Hear and show every instrument";
  all.onclick = () => { play.off.clear(); applyFilter(); };
  box.append(all);
  play.badges = (play.data.instruments || []).map((ins, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mix";
    b.innerHTML = `<span class="state" aria-hidden="true"></span><span></span>`;
    b.lastChild.textContent = ins.name;
    b.title = `${ins.name}: click to mute or unmute, right-click to hear only this one`;
    const solo = () => {
      play.off = new Set(play.data.instruments.map((_, k) => k).filter((k) => k !== i));
      applyFilter();
    };
    let pressTimer = 0, longPressed = false;
    b.onclick = () => {
      if (longPressed) { longPressed = false; return; } // the long-press already soloed it
      play.off.has(i) ? play.off.delete(i) : play.off.add(i);
      applyFilter();
    };
    b.oncontextmenu = (e) => { e.preventDefault(); if (!longPressed) solo(); };
    // Phones: a long press solos too (iPhones never send a right-click).
    b.onpointerdown = (e) => {
      if (e.pointerType !== "touch") return;
      longPressed = false;
      pressTimer = setTimeout(() => { longPressed = true; solo(); navigator.vibrate?.(15); }, 550);
    };
    b.onpointerup = b.onpointercancel = b.onpointerleave = () => clearTimeout(pressTimer);
    box.append(b);
    return { el: b, ins, on: false, hit: -1 };
  });
  box.hidden = !play.badges.length;
  applyFilter();
}

function applyFilter() {
  play.badges.forEach((b, i) => {
    const off = play.off.has(i);
    b.el.classList.toggle("off", off);
    b.el.setAttribute("aria-pressed", String(!off));
    b.el.querySelector(".state").textContent = off ? "🔇" : "🔊";
  });
  document.querySelectorAll(".mute-band").forEach((el) => { el.hidden = !play.off.has(Number(el.dataset.ins)); });
  document.querySelectorAll(".name-light").forEach((el) => el.classList.toggle("muted", play.off.has(Number(el.dataset.ins))));
  if (play.off.size && mixer.ready && !mixer.stems) toast("Muting needs each instrument's own audio, which is still being made. It works as soon as that's done.");
  mixer.applyMutes();
}

// Index of the last item whose start (item[0], or the item itself) is at or before ms; -1 if none.
function lastAtOrBefore(arr, ms, key = (x) => x) {
  let lo = 0, hi = arr.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (key(arr[mid]) <= ms) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

// Light up what's sounding: the instrument names on the strip, or the badges in the other layouts.
function updateLights(ms, running) {
  const names = play.lights.length ? play.lights : play.badges;
  for (const L of names) {
    const ins = L.ins;
    const i = lastAtOrBefore(ins.spans, ms, (s) => s[0]);
    const on = running || ms > 0 ? i >= 0 && ms < ins.spans[i][1] : false;
    if (on !== L.on) { L.on = on; L.el.classList.toggle("on", on); }
    const h = lastAtOrBefore(ins.hits, ms);
    if (h !== L.hit) {
      const fresh = running && h >= 0 && ms - ins.hits[h] < 200;
      L.hit = h;
      if (fresh) { L.el.classList.remove("hit"); void L.el.offsetWidth; L.el.classList.add("hit"); }
    }
  }
}

// ---- the score

function renderScore() {
  const d = play.data;
  if (!d) return;
  const layout = getLayout();
  for (const [id, v] of [["#lay-strip", "strip"], ["#lay-pages", "pages"]]) {
    $(id).classList.toggle("on", v === layout);
    $(id).setAttribute("aria-pressed", String(v === layout));
  }
  const box = $("#play-score");
  box.innerHTML = "";
  play.sys = [];
  play.cursor = null;
  play.strip = null;
  play.last = null;
  play.lights = [];
  play.badges.forEach((b) => { b.on = false; b.el.classList.remove("on"); });
  if (layout === "strip" && d.strip?.staves?.length) buildLine(box);
  else if (layout === "strip" && d.systems?.length) buildStrip(box);
  else buildPages(box);
  applyFilter();
  drawCursor();
}

function buildPages(box) {
  box.className = "pages seekable";
  play.data.pages.forEach((url, i) => {
    const page = document.createElement("div");
    page.className = "page";
    page.dataset.page = i;
    const img = document.createElement("img");
    img.src = url;
    img.alt = `page ${i + 1}`;
    img.loading = "lazy";
    page.append(img);
    page.onclick = (e) => {
      const tl = play.data.timeline;
      const r = page.getBoundingClientRect();
      seekTo(tl, i, (e.clientX - r.left) / r.width * tl.page_w, (e.clientY - r.top) / r.height * tl.page_h);
    };
    box.append(page);
  });
}

// Which staves belong to which instrument: each staff goes to the name nearest to it (a piano's name sits
// between its two staves). Returns, per instrument, the [top, bottom] of its band on the page.
function instrumentBands(staves, names, count) {
  const gap = (staves[0][1] - staves[0][0]) / 4 || 80;
  const owner = staves.map(([a, b]) => {
    const c = (a + b) / 2;
    let best = 0, bestD = Infinity;
    names.forEach((n, k) => { const d = Math.abs((n[1] + n[3]) / 2 - c); if (d < bestD) { bestD = d; best = k; } });
    return best;
  });
  const bands = [];
  for (let k = 0; k < count; k++) {
    const mine = staves.filter((_, s) => owner[s] === k);
    if (!mine.length) { bands.push(null); continue; }
    bands.push([Math.min(...mine.map((s) => s[0])), Math.max(...mine.map((s) => s[1]))]);
  }
  // Grow each band halfway to its neighbours, so a fade also covers notes above and below the staff.
  return bands.map((b, k) => {
    if (!b) return null;
    const prev = bands.slice(0, k).reverse().find(Boolean);
    const next = bands.slice(k + 1).find(Boolean);
    return [prev ? (prev[1] + b[0]) / 2 : b[0] - gap * 5, next ? (b[1] + next[0]) / 2 : b[1] + gap * 5];
  });
}

// The whole piece as MuseScore laid it out on one line. The start of the line (names, clefs, key and time
// signatures) is pinned on the left; the music scrolls past it.
function buildLine(box) {
  box.className = "strip";
  const d = play.data, st = d.strip, tl = st.timeline;
  const W = tl.page_w;
  const sys = st.systems?.[0] || { x: 0, y: st.staves[0][0], w: W, h: st.staves.at(-1)[1] - st.staves[0][0] };
  const gap = (st.staves[0][1] - st.staves[0][0]) / 4 || 80;
  const firstX = Math.min(...tl.events.map((e) => e[2]));
  const headX0 = Math.max(0, Math.min(sys.x, ...st.names.map((n) => n[0])) - gap * 2);
  const headX1 = firstX - gap * 1.2;
  const bodyX1 = sys.x + sys.w + gap;
  const top = Math.max(0, Math.min(sys.y, ...st.names.map((n) => n[1])) - gap * 7);
  const bottom = Math.max(sys.y + sys.h, st.staves.at(-1)[1]) + gap * 7;
  const H = bottom - top;
  const boxTop = box.getBoundingClientRect().top;
  const avail = Math.max(180, Math.min(innerHeight * 0.78, innerHeight - boxTop - 48));
  const scale = Math.min(avail / H, 1.6 * box.clientWidth / (bodyX1 - headX0));
  const row = document.createElement("div");
  row.className = "strip-row line";
  row.style.height = `${H * scale}px`;

  const slice = (cls, x0, x1) => {
    const el = document.createElement("div");
    el.className = cls;
    el.style.width = `${(x1 - x0) * scale}px`;
    el.style.height = `${H * scale}px`;
    const img = document.createElement("img");
    img.src = st.page;
    img.alt = "";
    img.draggable = false;
    img.style.width = `${W * scale}px`;
    img.style.left = `${-x0 * scale}px`;
    img.style.top = `${-top * scale}px`;
    el.append(img);
    return el;
  };
  const head = slice("line-head", headX0, headX1);
  const body = slice("line-body", headX1, bodyX1);
  body.querySelector("img").alt = "score";

  // Mute fades and name lights.
  const bands = instrumentBands(st.staves, st.names, d.instruments.length);
  const band = (parent, k, y0, y1) => {
    const el = document.createElement("div");
    el.className = "mute-band";
    el.dataset.ins = k;
    el.style.top = `${(y0 - top) * scale}px`;
    el.style.height = `${(y1 - y0) * scale}px`;
    el.hidden = true;
    parent.append(el);
  };
  bands.forEach((b, k) => { if (b) { band(body, k, b[0], b[1]); band(head, k, b[0], b[1]); } });
  play.lights = d.instruments.map((ins, k) => {
    const n = st.names[k];
    if (!n) return null;
    const el = document.createElement("div");
    el.className = "name-light";
    el.dataset.ins = k;
    const padX = gap * 0.8, padY = gap * 0.6;
    el.style.left = `${(n[0] - padX - headX0) * scale}px`;
    el.style.top = `${(n[1] - padY - top) * scale}px`;
    el.style.width = `${(n[2] - n[0] + 2 * padX) * scale}px`;
    el.style.height = `${(n[3] - n[1] + 2 * padY) * scale}px`;
    head.append(el);
    return { el, ins, on: false, hit: -1 };
  }).filter(Boolean);

  const cur = document.createElement("div");
  cur.className = "cursor strip-cursor";
  cur.hidden = true;
  body.append(cur);
  body.onclick = (e) => {
    const r = body.getBoundingClientRect();
    seekTo(tl, 0, headX1 + (e.clientX - r.left) / scale, top + (e.clientY - r.top) / scale);
  };
  row.append(head, body);
  box.append(row);
  play.cursor = cur;
  play.strip = { tl, scale, x0: headX1, top, sys, headW: (headX1 - headX0) * scale };
}

// Fallback while the one-line layout is being made: cut each system out of the pages and lay them side by side.
function buildStrip(box) {
  box.className = "strip";
  const d = play.data;
  const { page_w: W, page_h: H } = d.timeline;
  const sys = d.systems;
  const pads = sys.map((s, i) => {
    const prev = sys[i - 1]?.page === s.page ? sys[i - 1] : null;
    const next = sys[i + 1]?.page === s.page ? sys[i + 1] : null;
    const room = Math.max(s.h * 0.14, W * 0.012);
    return {
      top: Math.max(0, Math.min(room, prev ? (s.y - prev.y - prev.h) / 2 : s.y)),
      bot: Math.max(0, Math.min(room, next ? (next.y - s.y - s.h) / 2 : H - s.y - s.h)),
    };
  });
  const above = Math.max(...pads.map((p) => p.top));
  const below = Math.max(...sys.map((s, i) => s.h + pads[i].bot));
  const stripH = above + below;
  const avail = Math.max(180, Math.min(innerHeight * 0.75, innerHeight - box.getBoundingClientRect().top - 56));
  const scale = Math.min(avail / stripH, 2.2 * box.clientWidth / W);
  const padX = W * 0.006;
  const row = document.createElement("div");
  row.className = "strip-row";
  row.style.height = `${stripH * scale}px`;
  let left = 0;
  sys.forEach((s, i) => {
    const w = (s.w + 2 * padX) * scale;
    const el = document.createElement("div");
    el.className = "sys";
    el.style.width = `${w}px`;
    const clip = document.createElement("div");
    clip.className = "clip";
    clip.style.top = `${(above - pads[i].top) * scale}px`;
    clip.style.height = `${(s.h + pads[i].top + pads[i].bot) * scale}px`;
    const img = document.createElement("img");
    img.src = d.pages[s.page];
    img.alt = i ? "" : "score";
    img.draggable = false;
    img.style.width = `${W * scale}px`;
    img.style.left = `${-(s.x - padX) * scale}px`;
    img.style.top = `${-(s.y - pads[i].top) * scale}px`;
    clip.append(img);
    el.append(clip);
    const info = { ...s, left, x0: s.x - padX, scale, above };
    el.onclick = (e) => {
      const r = el.getBoundingClientRect();
      seekTo(d.timeline, s.page, info.x0 + (e.clientX - r.left) / scale, s.y - above + (e.clientY - r.top) / scale);
    };
    play.sys.push(info);
    row.append(el);
    left += w + STRIP_GAP;
  });
  const cur = document.createElement("div");
  cur.className = "cursor strip-cursor";
  cur.hidden = true;
  row.append(cur);
  play.cursor = cur;
  box.append(row);
}

function cursorAt(tl, ms) {
  const ev = tl?.events;
  if (!ev?.length || ms < ev[0][0]) return null;
  const lo = lastAtOrBefore(ev, ms, (e) => e[0]);
  const [t, page, x, y, w, h] = ev[lo];
  const next = ev[lo + 1];
  // Glide toward the next note when it is on the same system; otherwise toward this note's right edge.
  const sameLine = next && next[1] === page && next[3] === y;
  const span = next ? next[0] - t : 500;
  const frac = Math.min(1, Math.max(0, (ms - t) / (span || 1)));
  const x2 = sameLine ? next[2] : x + w;
  return { page, x: x + (x2 - x) * frac, y, h };
}

function systemFor(pos) {
  let best = null, bestD = Infinity;
  for (const s of play.sys) {
    if (s.page !== pos.page) continue;
    const d = pos.y < s.y ? s.y - pos.y : pos.y > s.y + s.h ? pos.y - s.y - s.h : 0;
    if (d < bestD) { bestD = d; best = s; }
  }
  return best;
}

function drawCursor() {
  if (!play.data || $("#play-view").hidden) return;
  updateClock();
  const ms = mixer.time() * 1000;
  const running = mixer.playing;
  updateLights(ms, running);
  const idle = !running && mixer.time() === 0;
  const box = $("#play-score");
  const follow = running && Date.now() > play.userScroll;
  if (play.strip) { // the one-line layout
    const S = play.strip;
    const pos = idle ? null : cursorAt(S.tl, ms);
    play.cursor.hidden = !pos;
    if (!pos) return;
    const x = (pos.x - S.x0) * S.scale;
    play.cursor.style.left = `${x}px`;
    play.cursor.style.top = `${(pos.y - S.top) * S.scale}px`;
    play.cursor.style.height = `${pos.h * S.scale}px`;
    // The music glides past a fixed reading point, just right of the pinned names and clefs.
    if (follow) box.scrollLeft = x - (box.clientWidth - S.headW) * 0.3;
    return;
  }
  const pos = idle ? null : cursorAt(play.data.timeline, ms);
  if (play.cursor) { // stitched pages
    const s = pos && systemFor(pos);
    play.cursor.hidden = !s;
    if (!s) return;
    const x = s.left + (pos.x - s.x0) * s.scale;
    play.cursor.style.left = `${x}px`;
    play.cursor.style.top = `${(s.above + pos.y - s.y) * s.scale}px`;
    play.cursor.style.height = `${pos.h * s.scale}px`;
    if (follow) box.scrollLeft = x - box.clientWidth * 0.3;
    return;
  }
  box.querySelectorAll(".cursor").forEach((c) => c.remove());
  if (!pos) return;
  const page = box.querySelector(`.page[data-page="${pos.page}"]`);
  if (!page) return;
  const { page_w: W, page_h: H } = play.data.timeline;
  const c = document.createElement("div");
  c.className = "cursor";
  c.style.left = `${pos.x / W * 100}%`;
  c.style.top = `${pos.y / H * 100}%`;
  c.style.height = `${pos.h / H * 100}%`;
  page.append(c);
  const key = pos.page + ":" + pos.y;
  const r = c.getBoundingClientRect();
  const offscreen = r.bottom < 60 || r.top > innerHeight - 20;
  if (follow && (key !== play.last || offscreen)) {
    play.last = key;
    c.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function followLoop() {
  drawCursor();
  if (mixer.playing) play.raf = requestAnimationFrame(followLoop);
}

// Jump to the note column nearest a point on a page of the given layout.
function seekTo(tl, pageNo, px, py) {
  if (!tl || !mixer.ready) return;
  let best = null, bestD = Infinity;
  for (const ev of tl.events) {
    if (ev[1] !== pageNo) continue;
    const dy = py < ev[3] ? ev[3] - py : py > ev[3] + ev[5] ? py - ev[3] - ev[5] : 0;
    const d = dy * 4 + Math.abs(px - ev[2]);
    if (d < bestD) { bestD = d; best = ev; }
  }
  if (!best) return;
  play.last = null;
  play.userScroll = 0;
  mixer.seek(best[0] / 1000);
  if (!mixer.playing) mixer.play();
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
    showJob(p.job);
    loadProjects();
    $("#job").scrollIntoView({ behavior: "smooth", block: "start" });
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
  for (const id of ["#upload", "#pick-photos", "#pick-camera"]) $(id).value = "";
}

// Images on the clipboard (a screenshot, "Copy image" on a phone) become pages. With a sheet-music
// project open you can add them to it; otherwise they start a new project.
function clipboardImages(e) {
  const out = [];
  for (const item of e.clipboardData?.items || []) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const f = item.getAsFile();
      if (f) out.push(f);
    }
  }
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
  return out.map((f, i) => new File([f], `pasted-${stamp}${out.length > 1 ? "-" + (i + 1) : ""}.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type }));
}

async function onPaste(e) {
  const imgs = clipboardImages(e);
  if (!imgs.length) return; // ordinary text paste: leave it alone
  e.preventDefault();
  const p = state.project;
  if (p && p.kind === "scan" && !$("#project").hidden &&
      confirm(`Add the pasted image as page ${p.pages + 1} of “${p.name}”?\n\nCancel starts a new project instead.`)) {
    return addPages(imgs);
  }
  return upload(imgs);
}

async function addPages(fileList) {
  const files = [...(fileList || [])];
  $("#add-pages").value = "";
  if (!files.length || !state.current) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  toast(`Adding ${files.length} page${files.length > 1 ? "s" : ""}…`);
  try {
    render(await api(`/api/projects/${encodeURIComponent(state.current)}/pages`, { method: "POST", body: fd }));
    toast("Pages added. Find the parts again before converting.");
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- studio library (server folder of FL projects)

const ICONS = { ".flp": "🎛️", ".mid": "🎹", ".midi": "🎹", ".pdf": "📄", ".mxl": "🎼", ".musicxml": "🎼" };
let studioSearchTimer = 0;
let studioSeq = 0;

function ago(t) {
  const d = new Date(t * 1000);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function studioRow(e, isDir, sub) {
  const li = document.createElement("li");
  const ext = isDir ? "" : (e.name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
  li.innerHTML = `<span>${isDir ? "📁" : ICONS[ext] || "📎"}</span><span class="nm"><span></span><span class="sub"></span></span><span class="when"></span>`;
  li.querySelector(".nm span").textContent = e.name;
  li.querySelector(".sub").textContent = sub || "";
  li.querySelector(".when").textContent = ago(e.mtime);
  li.onclick = () => (isDir ? studioOpen(e.path) : studioImport(e));
  return li;
}

function studioNote(text) {
  const li = document.createElement("li");
  li.className = "note";
  li.textContent = text;
  return li;
}

async function studioOpen(path = "") {
  const seq = ++studioSeq;
  const ul = $("#studio-list");
  ul.replaceChildren(studioNote("Loading…"));
  try {
    const r = await api(`/api/library?path=${encodeURIComponent(path)}`);
    if (seq !== studioSeq) return;
    $("#studio").dataset.path = r.path;
    const crumbs = $("#studio-crumbs");
    crumbs.replaceChildren();
    const parts = r.path ? r.path.split("/") : [];
    [["Projects", ""], ...parts.map((p, i) => [p, parts.slice(0, i + 1).join("/")])].forEach(([label, to], i, all) => {
      if (i) crumbs.append(" / ");
      const a = document.createElement(i === all.length - 1 ? "span" : "a");
      a.textContent = label;
      if (i < all.length - 1) a.onclick = () => studioOpen(to);
      crumbs.append(a);
    });
    ul.replaceChildren(...r.files.map((f) => studioRow(f, false)), ...r.dirs.map((d) => studioRow(d, true)));
    if (!r.files.length && !r.dirs.length) ul.append(studioNote("Nothing to import in this folder."));
    ul.scrollTop = 0;
  } catch (e) { ul.replaceChildren(studioNote(e.message)); }
}

async function studioSearch(q) {
  if (!q.trim()) return studioOpen("");
  const seq = ++studioSeq;
  const ul = $("#studio-list");
  ul.replaceChildren(studioNote("Searching…"));
  try {
    const r = await api(`/api/library/search?q=${encodeURIComponent(q)}`);
    if (seq !== studioSeq) return;
    $("#studio-crumbs").textContent = `${r.files.length}${r.more ? "+" : ""} match${r.files.length === 1 ? "" : "es"}`;
    ul.replaceChildren(...r.files.map((f) => studioRow(f, false, f.path.split("/").slice(0, -1).join(" / "))));
    if (!r.files.length) ul.append(studioNote("No projects match."));
  } catch (e) { ul.replaceChildren(studioNote(e.message)); }
}

async function studioImport(e) {
  $("#studio").close();
  toast(`Importing ${e.name}…`);
  try {
    const p = await api("/api/projects/from_library", { method: "POST", body: JSON.stringify({ path: e.path }) });
    await loadProjects();
    const h = "#" + encodeURIComponent(p.name);
    if (location.hash === h) openProject(p.name); else location.hash = h;
  } catch (err) { toast(err.message, true); }
}

async function initStudio() {
  try {
    const r = await api("/api/library");
    if (!r.enabled) return;
  } catch { return; }
  $("#pick-studio").hidden = false;
  $("#pick-studio").onclick = () => {
    $("#studio").showModal();
    if (matchMedia("(pointer: fine)").matches) $("#studio-search").focus(); // not on phones: no keyboard pop-up
    const q = $("#studio-search").value;
    q.trim() ? studioSearch(q) : studioOpen($("#studio").dataset.path || "");
  };
  $("#studio-close").onclick = () => $("#studio").close();
  $("#studio").onclick = (e) => { if (e.target === $("#studio")) $("#studio").close(); }; // backdrop
  $("#studio-search").oninput = (e) => {
    clearTimeout(studioSearchTimer);
    studioSearchTimer = setTimeout(() => studioSearch(e.target.value), 300);
  };
}

// ---------------------------------------------------------------- startup

async function init() {
  $("#save").onclick = save;
  $("#reset").onclick = reset;
  $("#convert").onclick = convert;
  for (const id of ["#upload", "#pick-photos", "#pick-camera"]) $(id).onchange = (e) => upload(e.target.files);
  $("#add-pages").onchange = (e) => addPages(e.target.files);
  document.addEventListener("paste", onPaste);
  initStudio();
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
  $("#rename").onclick = () => renameProject();
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

  api("/api/health").then((h) => { // only worth saying when something's wrong
    const el = $("#health");
    el.textContent = h.musescore ? "" : "⚠ MuseScore not found";
    el.classList.toggle("bad", !h.musescore);
  });
  $("#pp").onclick = () => mixer.toggle();
  const seek = $("#seek");
  seek.oninput = () => { play.seeking = true; $("#clock").textContent = `${fmtTime(Number(seek.value))} / ${fmtTime(mixer.duration)}`; };
  seek.onchange = () => { play.seeking = false; play.last = null; play.userScroll = 0; mixer.seek(Number(seek.value)); };
  $("#lay-strip").onclick = () => setLayout("strip");
  $("#lay-pages").onclick = () => setLayout("pages");
  $("#play-prep-go").onclick = preparePlay;
  // Scrolling the score yourself pauses the follow for a few seconds.
  for (const ev of ["wheel", "touchstart"]) {
    $("#play-score").addEventListener(ev, () => { play.userScroll = Date.now() + 4000; }, { passive: true });
  }
  let resizeTimer = 0;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (!$("#play-view").hidden && getLayout() === "strip") renderScore(); }, 200);
  });
  // Space plays and pauses on the Play tab.
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || $("#play-view").hidden || $("#play-ui").hidden) return;
    if (e.target.closest("input, textarea, select, button, audio, [contenteditable]")) return;
    e.preventDefault();
    mixer.toggle();
  });
  player.addEventListener("play", () => mixer.pause());
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
  // #  Pieces · #:new · #:settings · #<piece> (Edit) · #<piece>/play
  const fromHash = () => {
    const raw = location.hash.slice(1);
    stopPreview();
    if (raw === ":settings") openSettings();
    else if (raw === ":new") { state.current = null; showPage("new"); }
    else if (!raw) { state.current = null; showPage("home"); loadProjects(); }
    else {
      const isPlay = raw.endsWith("/play");
      openProject(decodeURIComponent(isPlay ? raw.slice(0, -5) : raw), isPlay ? "play" : "edit");
    }
  };
  window.addEventListener("hashchange", fromHash);
  fromHash();
}

init();
