const { $, esc } = Review;
Review.mount("transcript");
const localPlayer = $("player");
let player = localPlayer;
const playbackExtension = window.createReviewPlayback?.({
  localPlayer,
  setPlayer(value) {
    player = value;
  },
  sync: syncPlayback,
  status(message) {
    $("playbackStatus").textContent = message;
  },
});
if (!playbackExtension) Review.watchMedia(localPlayer);
let doc = null,
  selected = null,
  active = null,
  loading = false,
  filter = "all",
  videos = [];
const PAD = 0.25;
const time = (s) =>
  `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, "0")}`;
const needsReview = (s) =>
  s.notes.some(
    (n) => n.requires_audio_review || n.action === "retain_pending_audio_review",
  );
// Punctuation-only differences are not worth showing next to the text.
const bare = (t) => t.replace(/[\s、。，．,.!?！？「」『』…・]/g, "");
const segment = (id) => doc.segments.find((s) => s.id === id);
const rowOf = (id) => document.querySelector(`.segment[data-id="${id}"]`);

function dirty() {
  return [...document.querySelectorAll(".segment")].some(
    (e) => e.dataset.dirty === "true",
  );
}
function status(s) {
  if (s.text === "" && s.verified) return ["発言なし", "ok"];
  if (s.verified) return ["確認済み", "ok"];
  if (needsReview(s)) return ["要確認", "warn"];
  return ["未確認", ""];
}
function visible(s) {
  if (filter === "pending") return !s.verified;
  if (filter === "review") return !s.verified && needsReview(s);
  return true;
}
function progress() {
  const done = doc.segments.filter((s) => s.verified).length;
  Review.meter(done, doc.segments.length, "区間");
  const counts = {
    all: doc.segments.length,
    pending: doc.segments.filter((s) => !s.verified).length,
    review: doc.segments.filter((s) => !s.verified && needsReview(s)).length,
  };
  for (const el of document.querySelectorAll("[data-count]"))
    el.textContent = counts[el.dataset.count];
  $("historyCount").textContent = doc.history.length;
}
function renderHistory() {
  $("history").innerHTML =
    doc.history
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="historyItem"><b>${esc(new Date(e.created_at_utc).toLocaleString("ja-JP"))}</b>（${time(e.start)}）\n${esc(e.before || "（発言なし）")}\n→ ${esc(e.after || "（発言なし）")}\n${e.verified ? "確認済み" : "未確認"}${e.reason ? `：${esc(e.reason)}` : ""}</div>`,
      )
      .join("") || '<p class="muted">手動の訂正はまだありません。</p>';
}
function fit(area) {
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight + 2}px`;
}
function rowHtml(s) {
  const [label, tone] = status(s);
  const flags = s.notes.filter(
    (n) => n.requires_audio_review || n.action === "retain_pending_audio_review",
  );
  const notes = s.notes.filter((n) => !flags.includes(n));
  return `<article class="segment ${s.verified ? "verified" : ""} ${needsReview(s) ? "needsReview" : ""}" data-id="${s.id}">
<div class="segMeta"><button class="segTime" data-seek aria-label="区間${s.id}を再生">▶ ${time(s.start)}</button><span class="pill ${tone}">${label}</span></div>
<div class="segBody">
<textarea rows="1" aria-label="区間${s.id}の文字起こし">${esc(s.text)}</textarea>
${bare(s.raw_text) !== bare(s.text) ? `<p class="raw"><b>認識原文</b>${esc(s.raw_text)}</p>` : ""}
${flags.map((n) => `<p class="flags">要確認：${esc(n.reason)}</p>`).join("")}
${notes.length ? `<details class="notes"><summary>自動の補正 ${notes.length}件</summary>${notes.map((n) => `<div>${n.before ? `${esc(n.before)} → ${esc(n.after)}：` : ""}${esc(n.reason)}</div>`).join("")}</details>` : ""}
</div>
<div class="segActions">
<label class="check"><input type="checkbox" class="verifiedCheck" ${s.verified ? "checked" : ""}> 聴いて確認済み</label>
<input class="reason" aria-label="区間${s.id}の訂正理由" placeholder="訂正理由・聞き取りメモ（任意）">
<button class="quiet noSpeech" aria-label="区間${s.id}を発言なしにする">発言なし</button>
<button class="quiet reset">提案に戻す</button>
<div class="push"><button class="save" title="Ctrl+S">保存</button><button class="primary confirm" title="Ctrl+Enter">確認済みにして次へ</button></div>
</div>
<p class="saveStatus" role="status"></p>
</article>`;
}
function render() {
  const shown = doc.segments.filter(visible);
  $("segments").innerHTML = shown.length
    ? shown.map(rowHtml).join("")
    : `<p class="emptyList">${filter === "all" ? "区間がありません。" : "この条件の区間はありません。すべて確認済みです。"}</p>`;
  for (const row of document.querySelectorAll(".segment")) {
    const s = segment(Number(row.dataset.id));
    const area = row.querySelector("textarea");
    fit(area);
    row.addEventListener("focusin", () => open(s.id));
    row.addEventListener("click", () => open(s.id));
    row.querySelector("[data-seek]").onclick = () => seek(s);
    for (const input of row.querySelectorAll("textarea,input"))
      input.oninput = () => {
        row.dataset.dirty = "true";
        row.querySelector(".saveStatus").textContent = "未保存";
        if (input === area) fit(area);
      };
    row.querySelector(".save").onclick = () => save(row, s);
    row.querySelector(".confirm").onclick = () => confirmNext(s.id);
    row.querySelector(".noSpeech").onclick = () => {
      area.value = "";
      fit(area);
      row.querySelector(".verifiedCheck").checked = true;
      const reason = row.querySelector(".reason");
      reason.value = reason.value.trim()
        ? `発言なし（誤認識を削除）：${reason.value.trim()}`
        : "発言なし（誤認識を削除）";
      row.dataset.dirty = "true";
      save(row, s);
    };
    row.querySelector(".reset").onclick = () => {
      area.value = s.suggested_text;
      fit(area);
      row.querySelector(".verifiedCheck").checked = false;
      row.dataset.dirty = "true";
      row.querySelector(".saveStatus").textContent =
        "提案に戻しました。保存すると確定します。";
    };
  }
  if (selected !== null) rowOf(selected)?.classList.add("open");
  if (active !== null) rowOf(active)?.classList.add("playing");
  progress();
  renderHistory();
}
function open(id) {
  if (selected === id) return;
  selected = id;
  for (const row of document.querySelectorAll(".segment.open"))
    row.classList.remove("open");
  rowOf(id)?.classList.add("open");
}
async function save(row, s) {
  const buttons = [...row.querySelectorAll("button")];
  buttons.forEach((b) => (b.disabled = true));
  const value = row.querySelector("textarea").value,
    verified = row.querySelector(".verifiedCheck").checked,
    reason = row.querySelector(".reason").value;
  try {
    const result = await Review.api("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: doc.video_id,
        segment_id: s.id,
        text: value,
        verified,
        reason,
        expected_revision: doc.revision,
      }),
    });
    doc.revision = result.revision;
    s.text = value;
    s.verified = verified;
    if (result.event) doc.history.push(result.event);
    const stillDirty =
      row.querySelector("textarea").value !== value ||
      row.querySelector(".verifiedCheck").checked !== verified ||
      row.querySelector(".reason").value !== reason;
    row.dataset.dirty = String(stillDirty);
    if (!stillDirty) row.querySelector(".reason").value = "";
    row.classList.toggle("verified", verified);
    const [label, tone] = status(s);
    row.querySelector(".pill").textContent = label;
    row.querySelector(".pill").className = `pill ${tone}`;
    row.querySelector(".saveStatus").textContent = stillDirty
      ? "保存しました。その後の変更は未保存です。"
      : value === ""
        ? "発言なしとして保存しました"
        : "保存しました";
    progress();
    renderHistory();
    return true;
  } catch (e) {
    row.querySelector(".saveStatus").textContent = e.message;
    return false;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}
function neighbour(id, step) {
  const rows = [...document.querySelectorAll(".segment")];
  const i = rows.findIndex((r) => Number(r.dataset.id) === id);
  const row = i < 0 ? rows[0] : rows[i + step];
  return row ? segment(Number(row.dataset.id)) : null;
}
async function confirmNext(id = selected) {
  if (id === null) return;
  const row = rowOf(id);
  if (!row) return;
  row.querySelector(".verifiedCheck").checked = true;
  row.dataset.dirty = "true";
  if (!(await save(row, segment(id)))) return;
  const next = neighbour(id, 1);
  if (!next) {
    $("playbackStatus").textContent = "最後の区間まで確認しました。";
    return;
  }
  seek(next);
  rowOf(next.id)?.querySelector("textarea").focus({ preventScroll: true });
}
function seek(s) {
  open(s.id);
  player.currentTime = Math.max(0, s.start - PAD);
  player.play().catch((e) => ($("playbackStatus").textContent = e.message));
  rowOf(s.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function highlight(id) {
  if (active === id) return;
  active = id;
  document
    .querySelectorAll(".segment.playing")
    .forEach((e) => e.classList.remove("playing"));
  const row = rowOf(id);
  if (!row) return;
  row.classList.add("playing");
  if (Review.follow() && !document.activeElement.matches("textarea,input"))
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function syncPlayback() {
  if (!doc) return;
  if (Review.loop() && selected !== null) {
    const s = segment(selected);
    if (s && player.currentTime >= s.end + PAD)
      player.currentTime = Math.max(0, s.start - PAD);
  }
  const s = doc.segments.find(
    (s) => player.currentTime >= s.start && player.currentTime < s.end,
  );
  if (s) highlight(s.id);
}
localPlayer.addEventListener("timeupdate", () => {
  if (player === localPlayer) syncPlayback();
});
localPlayer.addEventListener("error", () => {
  $("playbackStatus").textContent =
    "ローカルのメディアを再生できません。ファイルがあるか確認してください。";
});
const transport = Review.transport({
  player: () => player,
  duration: () => doc?.duration,
  play: () =>
    player.play().catch((e) => ($("playbackStatus").textContent = e.message)),
});
async function setPlayback(reset = false) {
  const position = reset ? 0 : player.currentTime;
  player.pause();
  if (playbackExtension) return playbackExtension.load(doc, { position });
  player = localPlayer;
  player.src = doc.media_url || doc.audio_url;
  player.currentTime = position;
  transport.applyRate();
  $("playbackStatus").textContent = "";
}
async function load(id) {
  if (loading) return;
  loading = true;
  try {
    player.pause();
    doc = await Review.api(`/api/video?id=${encodeURIComponent(id)}`);
    selected = null;
    active = null;
    Review.linkSteps(id, videos);
    window.history.replaceState(null, "", `/?id=${encodeURIComponent(id)}`);
    document.title = `${doc.title} - 文字起こしの確認`;
    $("source").hidden = !doc.source_url;
    $("source").href = doc.source_url || "";
    render();
    Review.setStatus(
      "認識原文はそのまま残し、訂正は履歴付きでこのコンピューターに保存します。",
    );
    await setPlayback(true);
  } catch (e) {
    Review.setStatus(e.message, true);
  } finally {
    loading = false;
  }
}
for (const button of document.querySelectorAll("[data-filter]"))
  button.onclick = () => {
    if (dirty()) {
      Review.setStatus("表示を切り替える前に、訂正を保存してください。", true);
      return;
    }
    filter = button.dataset.filter;
    for (const b of document.querySelectorAll("[data-filter]"))
      b.setAttribute("aria-pressed", String(b === button));
    render();
  };
$("export").onclick = async () => {
  if (!doc) return;
  try {
    if (dirty()) throw Error("書き出す前に、訂正を保存してください。");
    const data = await Review.api(`/api/export?id=${doc.video_id}`);
    Review.download(`${doc.video_id}-reviewed.json`, data);
  } catch (e) {
    Review.setStatus(e.message, true);
  }
};
Review.keys({
  toggle: transport.toggle,
  replay: () => selected !== null && seek(segment(selected)),
  prev: () => {
    const s = neighbour(selected ?? -1, -1);
    if (s) seek(s);
  },
  next: () => {
    const s = neighbour(selected ?? -1, 1);
    if (s) seek(s);
  },
  confirmNext: () => confirmNext(),
  save: () => {
    const row = selected !== null && rowOf(selected);
    if (row) save(row, segment(selected));
  },
});
window.addEventListener("beforeunload", (e) => {
  if (dirty()) {
    e.preventDefault();
    e.returnValue = "";
  }
});
const initial = new URLSearchParams(location.search).get("id");
Review.recordings(initial, (id) => {
  if (dirty() && !confirm("未保存の訂正があります。破棄して録音を切り替えますか？"))
    return false;
  load(id);
})
  .then((list) => {
    videos = list;
    if (!videos.length) throw Error("確認できる録音がありません。");
    const id = videos.some((v) => v.video_id === initial)
      ? initial
      : videos[0].video_id;
    return load(id);
  })
  .catch((e) => Review.setStatus(e.message, true));
