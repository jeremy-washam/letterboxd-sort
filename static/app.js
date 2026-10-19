const $ = (id) => document.getElementById(id);

let username = null;
let state = { films: {}, ranked: [], skipped: [] };
let queue = [];
let candidate = null;
let lo = 0;
let hi = 0;
let comparisons = 0;
let totalToPlace = 0;
let placedThisSession = 0;
let undoStack = [];

const views = ["view-setup", "view-compare", "view-done", "view-list"];

function show(viewId) {
  views.forEach((v) => $(v).classList.toggle("hidden", v !== viewId));
  $("nav-rank").classList.toggle("active", viewId !== "view-list");
  $("nav-list").classList.toggle("active", viewId === "view-list");
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `request failed (${res.status})`);
  return body;
}

function saveState() {
  fetch(`/api/state?username=${encodeURIComponent(username)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  }).catch(() => {});
}

async function init(name) {
  username = name;
  localStorage.setItem("lbx-username", username);
  $("whoami").textContent = `@${username}`;
  $("nav").classList.remove("hidden");

  const [saved, feed] = await Promise.all([
    api(`/api/state?username=${encodeURIComponent(username)}`),
    api(`/api/films?username=${encodeURIComponent(username)}`),
  ]);
  state = { films: saved.films || {}, ranked: saved.ranked || [], skipped: saved.skipped || [] };
  for (const film of feed.films) {
    if (!state.films[film.key]) state.films[film.key] = film;
    else state.films[film.key] = { ...state.films[film.key], ...film };
  }

  const placed = new Set([...state.ranked, ...state.skipped]);
  queue = Object.values(state.films)
    .filter((f) => !placed.has(f.key))
    .sort((a, b) => (b.watched || "").localeCompare(a.watched || ""))
    .map((f) => f.key);

  totalToPlace = queue.length;
  placedThisSession = 0;
  undoStack = [];
  saveState();
  nextCandidate();
}

function nextCandidate() {
  candidate = queue.shift() || null;
  comparisons = 0;
  if (!candidate) {
    show(state.ranked.length ? "view-done" : "view-setup");
    if (state.ranked.length) renderList();
    return;
  }
  if (state.ranked.length === 0) {
    state.ranked.push(candidate);
    placedThisSession++;
    saveState();
    nextCandidate();
    return;
  }
  lo = 0;
  hi = state.ranked.length;
  renderComparison();
}

function renderComparison() {
  const cand = state.films[candidate];
  const mid = Math.floor((lo + hi) / 2);
  const pivot = state.films[state.ranked[mid]];

  show("view-compare");
  $("compare-question").textContent = `Is ${cand.title} better than ${pivot.title}?`;
  $("poster-candidate").src = cand.poster || "";
  $("poster-candidate").alt = cand.title;
  $("title-candidate").textContent = cand.title;
  $("year-candidate").textContent = cand.year;
  $("poster-pivot").src = pivot.poster || "";
  $("poster-pivot").alt = pivot.title;
  $("title-pivot").textContent = pivot.title;
  $("year-pivot").textContent = pivot.year;
  $("pivot-rank").textContent = `ranked #${mid + 1}`;

  const maxComparisons = Math.ceil(Math.log2(state.ranked.length + 1));
  $("progress").textContent =
    `Film ${placedThisSession + 1} of ${totalToPlace} · ` +
    `comparison ${comparisons + 1} of ≤${maxComparisons}`;
  $("btn-undo").classList.toggle("hidden", undoStack.length === 0);
}

function snapshot() {
  undoStack.push({
    ranked: [...state.ranked],
    skipped: [...state.skipped],
    queue: [...queue],
    candidate,
    lo,
    hi,
    comparisons,
    placedThisSession,
  });
  if (undoStack.length > 50) undoStack.shift();
}

function undo() {
  const prev = undoStack.pop();
  if (!prev) return;
  state.ranked = prev.ranked;
  state.skipped = prev.skipped;
  queue = prev.queue;
  candidate = prev.candidate;
  lo = prev.lo;
  hi = prev.hi;
  comparisons = prev.comparisons;
  placedThisSession = prev.placedThisSession;
  saveState();
  renderComparison();
}

function place(index) {
  state.ranked.splice(index, 0, candidate);
  placedThisSession++;
  saveState();
  nextCandidate();
}

function answer(result) {
  if (!candidate) return;
  snapshot();
  const mid = Math.floor((lo + hi) / 2);
  if (result === "tie") {
    place(mid);
    return;
  }
  if (result === "better") hi = mid;
  else lo = mid + 1;
  comparisons++;
  if (lo >= hi) place(lo);
  else renderComparison();
}

function skipFilm() {
  if (!candidate) return;
  snapshot();
  state.skipped.push(candidate);
  placedThisSession++;
  saveState();
  nextCandidate();
}

function score(index, total) {
  if (total === 1) return "10.0";
  return (10 - (9 * index) / (total - 1)).toFixed(1);
}

function scoreClass(value) {
  if (value >= 7) return "hi";
  if (value >= 4) return "mid";
  return "lo";
}

function renderList() {
  const ol = $("ranking");
  ol.innerHTML = "";
  const total = state.ranked.length;
  state.ranked.forEach((key, i) => {
    const film = state.films[key];
    if (!film) return;
    const s = score(i, total);
    const li = document.createElement("li");
    li.className = "rank-row";
    li.innerHTML = `
      <span class="rank-num">${i + 1}</span>
      <img src="${film.poster || ""}" alt="" loading="lazy">
      <span class="rank-info">
        <a href="${film.url}" target="_blank" rel="noopener">${film.title} <span class="film-year">${film.year}</span></a>
        <span class="rank-sub">${film.rating ? `★ ${film.rating} on letterboxd` : "unrated"}${film.watched ? ` · watched ${film.watched}` : ""}</span>
      </span>
      <span class="score ${scoreClass(parseFloat(s))}">${s}</span>
      <button class="link-btn" data-rerank="${key}">re-rank</button>`;
    ol.appendChild(li);
  });

  const skippedUl = $("skipped");
  skippedUl.innerHTML = "";
  $("skipped-section").classList.toggle("hidden", state.skipped.length === 0);
  state.skipped.forEach((key) => {
    const film = state.films[key];
    if (!film) return;
    const li = document.createElement("li");
    li.className = "skip-row";
    li.innerHTML = `<span>${film.title} (${film.year})</span>
      <button class="link-btn" data-rerank="${key}">rank it</button>`;
    skippedUl.appendChild(li);
  });
}

function rerank(key) {
  state.ranked = state.ranked.filter((k) => k !== key);
  state.skipped = state.skipped.filter((k) => k !== key);
  queue.unshift(key);
  totalToPlace = placedThisSession + queue.length;
  saveState();
  nextCandidate();
}

$("setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("username-input").value.trim().replace(/^@/, "");
  if (!name) return;
  $("setup-error").classList.add("hidden");
  try {
    await init(name);
  } catch (err) {
    $("setup-error").textContent = err.message;
    $("setup-error").classList.remove("hidden");
  }
});

$("btn-better").addEventListener("click", () => answer("better"));
$("btn-worse").addEventListener("click", () => answer("worse"));
$("btn-cantrate").addEventListener("click", () => answer("tie"));
$("btn-skip").addEventListener("click", skipFilm);
$("btn-undo").addEventListener("click", undo);
$("poster-candidate").parentElement.addEventListener("click", () => answer("better"));
$("poster-pivot").parentElement.addEventListener("click", () => answer("worse"));

$("nav-rank").addEventListener("click", () => {
  if (candidate) renderComparison();
  else if (queue.length) nextCandidate();
  else show("view-done");
});
$("nav-list").addEventListener("click", () => {
  renderList();
  show("view-list");
});
$("switch-user").addEventListener("click", () => {
  localStorage.removeItem("lbx-username");
  candidate = null;
  $("nav").classList.add("hidden");
  show("view-setup");
});

for (const id of ["btn-refresh", "btn-refresh-2"]) {
  $(id).addEventListener("click", () => init(username));
}

document.addEventListener("click", (e) => {
  const key = e.target.dataset && e.target.dataset.rerank;
  if (key) rerank(key);
});

document.addEventListener("keydown", (e) => {
  if ($("view-compare").classList.contains("hidden")) return;
  if (e.key === "ArrowLeft" || e.key === "b") answer("better");
  else if (e.key === "ArrowRight" || e.key === "w") answer("worse");
  else if (e.key === "t") answer("tie");
  else if (e.key === "s") skipFilm();
  else if (e.key === "u") undo();
});

const savedName = localStorage.getItem("lbx-username");
$("username-input").value = savedName || "m10zeus";
if (savedName) init(savedName).catch(() => show("view-setup"));
