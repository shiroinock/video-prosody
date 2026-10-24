const { $, esc } = Review;
Review.mount("moras");
const video = $("player");
Review.watchMedia(video);
let vid = new URLSearchParams(location.search).get("id");
let data,
  unit,
  dirty = false,
  saving = false,
  clip = false,
  looping = false,
  raf = null,
  from = 0,
  to = 3,
  lastPlaying = null,
  boundaryMode = null;
const LOCKED =
  "#label,#reason,#start,#end,#verified,#save,#confirmUnit,#setStart,#setEnd,[data-id],[data-boundary],#repeatStart,#repeatEnd,#boundaryWindow";
// A pause longer than this starts a new line in the mora strip.
const LINE_GAP = 0.4;

function effective() {
  return {
    ...unit,
    label: $("label").value,
    start: Number($("start").value),
    end: Number($("end").value),
  };
}
function renderList() {
  $("count").textContent = `${data.moras.length}拍`;
  let html = "",
    lastWord = null,
    lastEnd = null;
  for (const m of data.moras) {
    const newLine = lastEnd === null || m.start - lastEnd > LINE_GAP;
    if (m.word_id !== lastWord || newLine) {
      if (lastWord !== null) html += "</div></div>";
      if (newLine) {
        if (lastEnd !== null) html += "</div>";
        html += `<div class="utterance"><span class="uttTime num">${Review.clock(m.start).slice(0, -2)}</span>`;
      }
      html += `<div class="wordGroup"><small>${esc(m.word_text)}</small><div>`;
      lastWord = m.word_id;
    }
    lastEnd = m.end;
    html += `<button class="chip ${m.verified ? "verified" : ""} ${m.review_flags.length ? "flagged" : ""} ${m.id === unit?.id ? "selected" : ""}" data-id="${m.id}" aria-label="拍${m.id} ${esc(m.label)}${m.verified ? " 確認済み" : ""}" title="${m.start.toFixed(3)}–${m.end.toFixed(3)}秒">${esc(m.label)}</button>`;
  }
  if (lastWord !== null) html += "</div></div></div>";
  $("units").innerHTML = html;
  document.querySelectorAll("[data-id]").forEach(
    (b) =>
      (b.onclick = () => {
        video.pause();
        select(Number(b.dataset.id));
      }),
  );
  Review.reveal(document.querySelector(".chip.selected"), $("units").parentElement);
}
function select(id, auto = false) {
  if (dirty && !confirm("未保存のモーラの訂正を破棄しますか？")) return false;
  unit = data.moras.find((m) => m.id === id);
  dirty = false;
  if (!auto) {
    looping = false;
    boundaryMode = null;
    boundaryStatus();
  }
  $("selected").innerHTML = `${esc(unit.label)}<small>${esc(unit.word_text)}</small>`;
  $("label").value = unit.label;
  $("start").value = unit.start.toFixed(3);
  $("end").value = unit.end.toFixed(3);
  $("reason").value = "";
  $("verified").checked = unit.verified;
  $("editStatus").textContent = "";
  $("estimate").innerHTML = `<span>元の推定<b>${esc(unit.original.label)} ${unit.original.start.toFixed(3)}–${unit.original.end.toFixed(3)}秒</b></span>
<span>音素<b>${esc(unit.phonemes.join(" "))}</b></span>
<span>CTC一致度<b>${unit.ctc_score.toFixed(3)}</b></span>`;
  const notices = [];
  if (unit.review_flags.length)
    notices.push(`要確認：${unit.review_flags.join("・")}`);
  if (unit.source_changed)
    notices.push(
      "単語の表記・発音が推定後に更新されています。対応を確認してください。",
    );
  $("notice").hidden = !notices.length;
  $("notice").textContent = notices.join(" ");
  $("context").innerHTML = data.moras
    .filter((m) => m.word_id === unit.word_id)
    .map(
      (m) =>
        `<span class="${m.id === id ? "current" : ""}" data-live="${m.id}">${esc(m.label)}</span>`,
    )
    .join(" ");
  from = Math.max(0, unit.start - 0.55);
  to = Math.min(data.duration, Math.max(unit.end + 0.55, from + 1.4));
  if (auto) {
    document
      .querySelectorAll(".chip.selected")
      .forEach((b) => b.classList.remove("selected"));
    document.querySelector(`[data-id="${id}"]`)?.classList.add("selected");
  } else renderList();
  info();
  graph();
  return true;
}
function info() {
  const m = effective(),
    i = data.moras.indexOf(unit),
    before = i ? m.start - data.moras[i - 1].end : null,
    after = i + 1 < data.moras.length ? data.moras[i + 1].start - m.end : null;
  const stat = (label, value, note = "", warn = false) =>
    `<div class="${warn ? "warn" : ""}"><dt>${label}</dt><dd>${value}${note ? `<small>${note}</small>` : ""}</dd></div>`;
  $("info").innerHTML = [
    stat("長さ", `${(m.end - m.start).toFixed(3)}秒`),
    stat(
      "前の拍との間",
      before === null ? "なし" : `${before.toFixed(3)}秒`,
      before < 0 ? "区間が重なっています" : "",
      before < 0,
    ),
    stat(
      "後の拍との間",
      after === null ? "なし" : `${after.toFixed(3)}秒`,
      after < 0 ? "区間が重なっています" : "",
      after < 0,
    ),
  ].join("");
}
const X0 = 54,
  X1 = 784;
const x = (t) => X0 + ((t - from) / (to - from)) * (X1 - X0);
const y = (db) => 210 - ((Math.max(-60, db) + 60) / 60) * 150;
function graph() {
  const m = effective();
  let line = "";
  for (const f of data.frames.filter((f) => f.time >= from && f.time <= to))
    line += `${line ? "L" : "M"}${x(f.time).toFixed(1)},${y(f.rms_dbfs).toFixed(1)} `;
  const bands = data.moras
    .filter((a) => a.end >= from && a.start <= to)
    .map((a) => {
      const b = a.id === m.id ? m : a,
        l = Math.max(from, b.start),
        r = Math.min(to, b.end);
      return `<rect class="band ${a.id === m.id ? "selected" : ""}" x="${x(l)}" y="24" width="${Math.max(0, x(r) - x(l))}" height="190"/><text class="label" x="${x((l + r) / 2)}" y="17" text-anchor="middle">${esc(b.label)}</text>`;
    })
    .join("");
  const axis = [
    [y(0), "0dB"],
    [y(-30), "−30"],
    [y(-60), "−60dB"],
  ]
    .map(
      ([yy, label]) =>
        `<line class="grid" x1="${X0}" x2="${X1}" y1="${yy}" y2="${yy}"/><text x="${X0 - 6}" y="${yy + 4}" text-anchor="end">${label}</text>`,
    )
    .join("");
  const ticks = [0, 1, 2, 3, 4]
    .map((i) => {
      const t = from + ((to - from) * i) / 4;
      return `<text x="${x(t)}" y="232" text-anchor="middle">${t.toFixed(2)}秒</text>`;
    })
    .join("");
  $("chart").innerHTML =
    bands +
    axis +
    `<path class="volume" d="${line}"/>` +
    `<line class="edge" x1="${x(m.start)}" x2="${x(m.start)}" y1="24" y2="214"/><line class="edge" x1="${x(m.end)}" x2="${x(m.end)}" y1="24" y2="214"/>` +
    `<line id="cursor" class="cursor" x1="${x(video.currentTime)}" x2="${x(video.currentTime)}" y1="24" y2="214"/>` +
    ticks;
}
$("chart").onclick = (e) => {
  const r = $("chart").getBoundingClientRect(),
    px = ((e.clientX - r.left) * 800) / r.width;
  video.currentTime = Math.max(
    0,
    Math.min(data.duration, from + ((px - X0) / (X1 - X0)) * (to - from)),
  );
};
for (const id of ["label", "reason", "start", "end", "verified"])
  $(id).oninput = () => {
    dirty = true;
    $("editStatus").textContent = "未保存";
    $("editStatus").classList.remove("error");
    info();
    graph();
  };
$("setStart").onclick = () => {
  $("start").value = video.currentTime.toFixed(3);
  $("start").oninput();
};
$("setEnd").onclick = () => {
  $("end").value = video.currentTime.toFixed(3);
  $("end").oninput();
};
function boundaryStatus() {
  $("boundaryStatus").textContent = boundaryMode
    ? `${boundaryMode === "start" ? "開始" : "終了"} ${effective()[boundaryMode].toFixed(3)}秒の前後を繰り返し${video.paused ? "（一時停止中）" : "再生中"}。もう一度押すと止まります。`
    : "「前後を聴く」で境界の前後を繰り返し再生します。";
  $("repeatStart").setAttribute("aria-pressed", String(boundaryMode === "start"));
  $("repeatEnd").setAttribute("aria-pressed", String(boundaryMode === "end"));
}
function playbackRange() {
  const w = effective();
  if (boundaryMode) {
    const pad = Number($("boundaryWindow").value),
      point = w[boundaryMode];
    return {
      start: Math.max(0, point - pad),
      end: Math.min(data.duration, point + pad),
    };
  }
  return {
    start: Math.max(0, w.start - 0.1),
    end: Math.min(data.duration, w.end + 0.1),
  };
}
function stopBoundary() {
  looping = false;
  boundaryMode = null;
  video.pause();
  boundaryStatus();
}
function beginBoundary(mode) {
  const w = effective();
  if (
    !Number.isFinite(w.start) ||
    !Number.isFinite(w.end) ||
    w.start < 0 ||
    w.end > data.duration ||
    w.end <= w.start
  ) {
    $("boundaryStatus").textContent = "開始・終了の時刻を確認してください。";
    return;
  }
  boundaryMode = mode;
  looping = true;
  clip = true;
  boundaryStatus();
  video.currentTime = playbackRange().start;
  video.play().catch((e) => Review.setStatus(e.message, true));
}
$("repeatStart").onclick = () =>
  boundaryMode === "start" ? stopBoundary() : beginBoundary("start");
$("repeatEnd").onclick = () =>
  boundaryMode === "end" ? stopBoundary() : beginBoundary("end");
$("boundaryWindow").onchange = () => {
  if (boundaryMode) beginBoundary(boundaryMode);
};
document.querySelectorAll("[data-boundary]").forEach(
  (b) =>
    (b.onclick = () => {
      if (saving) return;
      const id = b.dataset.boundary,
        w = effective(),
        value = Number((w[id] + Number(b.dataset.delta)).toFixed(3));
      if (
        value < 0 ||
        value > data.duration ||
        (id === "start" ? value >= w.end : value <= w.start)
      ) {
        $("boundaryStatus").textContent =
          "開始は終了より前の、録音内の時刻にしてください。";
        return;
      }
      $(id).value = value.toFixed(3);
      $(id).oninput();
      if (boundaryMode) beginBoundary(boundaryMode);
    }),
);
for (const id of ["start", "end"])
  $(id).addEventListener("change", () => {
    if (boundaryMode) beginBoundary(boundaryMode);
  });
function playUnit() {
  const m = effective();
  if (!(m.end > m.start)) return;
  boundaryMode = null;
  boundaryStatus();
  clip = true;
  looping = true;
  video.currentTime = Math.max(0, m.start - 0.1);
  video.play().catch((e) => Review.setStatus(e.message, true));
}
$("unitPlay").onclick = playUnit;
function step(delta) {
  if (!unit) return;
  const next = data.moras[data.moras.indexOf(unit) + delta];
  if (next && select(next.id)) playUnit();
}
$("prevItem").onclick = () => step(-1);
$("nextItem").onclick = () => step(1);
function sync() {
  if (!data) return;
  const current = unit ? effective() : null,
    m =
      current &&
      current.start <= video.currentTime &&
      video.currentTime < current.end
        ? current
        : data.moras.find(
            (a) =>
              a.id !== unit?.id &&
              a.start <= video.currentTime &&
              video.currentTime < a.end,
          );
  if (
    m &&
    Review.follow() &&
    !clip &&
    !dirty &&
    !saving &&
    !document.activeElement.matches("input") &&
    m.id !== unit?.id
  )
    select(m.id, true);
  const cx = x(video.currentTime),
    c = $("cursor");
  if (c) {
    c.setAttribute("x1", cx);
    c.setAttribute("x2", cx);
  }
  document
    .querySelectorAll(".playing")
    .forEach((e) => e.classList.remove("playing"));
  if (m) {
    const b = document.querySelector(`[data-id="${m.id}"]`);
    b?.classList.add("playing");
    document.querySelector(`[data-live="${m.id}"]`)?.classList.add("playing");
    if (lastPlaying !== m.id && Review.follow())
      Review.reveal(b, $("units").parentElement);
  }
  lastPlaying = m?.id ?? null;
  if (looping) {
    const r = playbackRange();
    if (video.currentTime >= r.end) {
      if (boundaryMode || Review.loop()) video.currentTime = r.start;
      else {
        looping = false;
        video.pause();
      }
    }
  }
}
function tick() {
  sync();
  raf = video.paused ? null : requestAnimationFrame(tick);
}
video.addEventListener("play", () => {
  boundaryStatus();
  if (!looping) clip = false;
  if (raf === null) raf = requestAnimationFrame(tick);
});
video.addEventListener("pause", () => {
  if (raf !== null) cancelAnimationFrame(raf);
  raf = null;
  sync();
  boundaryStatus();
});
video.addEventListener("timeupdate", sync);
video.addEventListener("seeked", sync);
const transport = Review.transport({
  player: () => video,
  duration: () => data?.duration,
  play: () => {
    clip = false;
    looping = false;
    boundaryMode = null;
    boundaryStatus();
    video.play().catch((e) => Review.setStatus(e.message, true));
  },
});
function audit() {
  $("historyCount").textContent = data.history.length;
  $("audit").innerHTML =
    data.history
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="historyItem">${esc(`${new Date(e.created_at_utc).toLocaleString("ja-JP")}　拍${e.mora_id}\n${JSON.stringify(e.before)}\n→ ${JSON.stringify(e.after)}\n理由：${e.reason || "記入なし"}`)}</div>`,
      )
      .join("") || '<p class="muted">訂正はまだありません。</p>';
  Review.meter(
    data.moras.filter((m) => m.verified).length,
    data.moras.length,
    "拍",
  );
}
async function save() {
  if (saving || !unit) return false;
  looping = false;
  boundaryMode = null;
  video.pause();
  boundaryStatus();
  saving = true;
  document.querySelectorAll(LOCKED).forEach((e) => (e.disabled = true));
  try {
    const m = effective(),
      id = m.id;
    await Review.api("/api/moras/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: vid,
        source_alignment: data.alignment_file,
        mora_id: id,
        label: m.label,
        start: m.start,
        end: m.end,
        verified: $("verified").checked,
        reason: $("reason").value,
        expected_revision: data.revision,
      }),
    });
    data = await Review.api(`/api/moras?id=${vid}`);
    dirty = false;
    select(id);
    audit();
    $("editStatus").textContent = "保存しました";
    return true;
  } catch (e) {
    $("editStatus").textContent = e.message;
    $("editStatus").classList.add("error");
    return false;
  } finally {
    saving = false;
    document.querySelectorAll(LOCKED).forEach((e) => (e.disabled = false));
  }
}
async function confirmNext() {
  if (!unit) return;
  $("verified").checked = true;
  dirty = true;
  if (!(await save())) return;
  const next = data.moras[data.moras.findIndex((m) => m.id === unit.id) + 1];
  if (next) {
    select(next.id);
    playUnit();
  } else $("editStatus").textContent = "最後の拍まで確認しました。";
}
$("save").onclick = save;
$("confirmUnit").onclick = confirmNext;
$("export").onclick = () => {
  if (dirty) {
    $("editStatus").textContent = "書き出す前に、訂正を保存してください。";
    return;
  }
  Review.download(`${vid}-moras-reviewed.json`, data);
};
Review.keys({
  toggle: transport.toggle,
  replay: playUnit,
  prev: () => step(-1),
  next: () => step(1),
  confirmNext,
  save,
});
window.onbeforeunload = (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
};
Review.recordings(vid, (id) => {
  if (dirty && !confirm("未保存の訂正を破棄しますか？")) return false;
  dirty = false;
  location.search = "?id=" + encodeURIComponent(id);
})
  .then((videos) => {
    const available = videos.filter((v) => v.has_moras);
    vid = vid || available[0]?.video_id;
    if (vid) Review.linkSteps(vid, videos);
    if (!available.some((v) => v.video_id === vid))
      throw Error(
        "この録音にはモーラの推定結果がありません。先に位置合わせを実行してください。",
      );
    return Review.api(`/api/moras?id=${vid}`);
  })
  .then((d) => {
    data = d;
    video.src = data.audio_url || data.media_url;
    transport.applyRate();
    document.title = `${data.title} - モーラの時刻確認`;
    const notes = [`${data.moras.length}拍を音声から推定しました。`];
    if (data.unaligned_segments.length)
      notes.push(`位置合わせできなかった区間が${data.unaligned_segments.length}つあります。`);
    if (data.source_word_revision_changed)
      notes.push("推定のあとで単語の計測・読みが更新されています。");
    Review.setStatus(
      notes.join(" "),
      data.source_word_revision_changed || data.unaligned_segments.length > 0,
    );
    if (data.moras.length) select(data.moras[0].id);
    else renderList();
    audit();
  })
  .catch((e) => Review.setStatus(e.message, true));
