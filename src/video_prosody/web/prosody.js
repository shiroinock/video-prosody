const { $, esc } = Review;
Review.mount("prosody");
const video = $("audioVideo");
Review.watchMedia(video);
let vid = new URLSearchParams(location.search).get("id");
let data,
  word,
  dirty = false,
  rangeStart = 0,
  rangeEnd = 5,
  looping = false,
  saving = false,
  clipPlayback = false,
  boundaryMode = null;
const fmt = (n, d = 3) => (n == null ? "未推定" : Number(n).toFixed(d));
const FIELDS = [
  "text",
  "pronunciation",
  "pronunciationVerified",
  "start",
  "end",
  "pitch",
  "volume",
  "reason",
  "verified",
];
const LOCKED =
  "#text,#pronunciation,#pronunciationVerified,#start,#end,#pitch,#volume,#reason,#verified,#saveWord,#confirmWord,#setStart,#setEnd,[data-word],[data-boundary],#repeatStart,#repeatEnd,#boundaryWindow";

function effective() {
  return {
    ...word,
    start: Number($("start").value),
    end: Number($("end").value),
  };
}
function list() {
  $("count").textContent = `${data.words.length}語`;
  let html = "",
    segment = null;
  for (const w of data.words) {
    if (w.segment_id !== segment) {
      if (segment !== null) html += "</div>";
      html += `<div class="utterance"><span class="uttTime num">${Review.clock(w.start).slice(0, -2)}</span>`;
      segment = w.segment_id;
    }
    html += `<button class="chip ${w.verified ? "verified" : ""} ${w.dictionary?.needs_review ? "flagged" : ""} ${w.id === word?.id ? "selected" : ""}" data-word="${w.id}" aria-label="単語${w.id} ${esc(w.text)}${w.verified ? " 確認済み" : ""}">${esc(w.text)}</button>`;
  }
  $("wordList").innerHTML = html + (segment !== null ? "</div>" : "");
  document.querySelectorAll("[data-word]").forEach(
    (b) =>
      (b.onclick = () => {
        video.pause();
        select(Number(b.dataset.word));
      }),
  );
  Review.reveal(document.querySelector(".chip.selected"), $("wordList").parentElement);
}
function progress() {
  Review.meter(
    data.words.filter((w) => w.verified).length,
    data.words.length,
    "語",
  );
}
function select(id, following = false) {
  if (dirty && !confirm("未保存の単語の訂正を破棄しますか？")) return false;
  word = data.words.find((w) => w.id === id);
  dirty = false;
  if (!following) {
    looping = false;
    boundaryMode = null;
    boundaryStatus();
  }
  $("wordText").textContent = word.text;
  $("text").value = word.text;
  $("pronunciation").value = word.pronunciation_override ?? "";
  $("pronunciationVerified").checked = word.pronunciation_verified ?? false;
  $("start").value = word.start.toFixed(3);
  $("end").value = word.end.toFixed(3);
  $("pitch").value = word.pitch_override_hz ?? "";
  $("volume").value = word.volume_override_dbfs ?? "";
  $("overrides").open =
    word.pitch_override_hz != null || word.volume_override_dbfs != null;
  $("reason").value = "";
  $("verified").checked = word.verified;
  $("saveState").textContent = "";
  $("context").innerHTML = data.words
    .filter((w) => w.segment_id === word.segment_id)
    .map(
      (w) =>
        `<span class="${w.id === word.id ? "current" : ""}" data-live="${w.id}">${esc(w.text)}</span>`,
    )
    .join(" ");
  rangeStart = Math.max(0, word.start - 1.5);
  rangeEnd = Math.min(data.duration, Math.max(word.end + 1.5, rangeStart + 3));
  if (following) {
    document
      .querySelectorAll(".chip.selected")
      .forEach((b) => b.classList.remove("selected"));
    const chip = document.querySelector(`[data-word="${id}"]`);
    chip?.classList.add("selected");
  } else list();
  graph();
  metrics();
  moraInfo();
  return true;
}
function moraSplit(value) {
  const kana = value
    .normalize("NFKC")
    .replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60))
    .trim();
  if (!kana) return [];
  if (!/^[ぁ-ゖー]+$/.test(kana))
    throw Error("発音は仮名と長音符で入力してください。〜は時間で記録します。");
  const units = [];
  for (const c of kana) {
    if (
      /[ゃゅょぁぃぅぇぉゎ]/.test(c) &&
      units.length &&
      !["ん", "っ", "ー"].includes(units.at(-1))
    )
      units[units.length - 1] += c;
    else units.push(c);
  }
  return units;
}
function moraInfo() {
  if (!word) return;
  const d = word.dictionary || {},
    fallback = (d.notes || []).some((n) => n.includes("辞書外語")),
    override = $("pronunciation").value.trim(),
    changed = $("text").value !== d.text,
    pron = override || (changed ? null : d.pronunciation);
  const notices = [];
  if (changed && !override)
    notices.push("表記が変わったため、発音を入力してください。");
  if (d.needs_review) notices.push(`読みを要確認：${(d.notes || []).join("／")}`);
  try {
    const units = pron ? moraSplit(pron) : [],
      seconds = effective().end - effective().start;
    $("pronunciation").placeholder = d.pronunciation
      ? `空欄なら辞書の発音（${d.pronunciation}）`
      : "空欄なら辞書の発音";
    $("moraInfo").innerHTML = `<span>${fallback ? "仮名からの読み" : "辞書の読み"}<b>${esc(d.reading || "未取得")}</b></span>
<span>分析に使う発音<b>${esc(pron || "未入力")}</b>（${override ? "手動" : "自動"}）</span>
${units.length ? `<span><span class="moraUnits">${units.map((m) => `<span>${esc(m)}</span>`).join("")}</span> <b>${units.length}モーラ</b>、1拍あたり平均 ${(seconds / units.length).toFixed(3)}秒</span>` : ""}
<span>${$("pronunciationVerified").checked ? "発音は確認済み" : "発音は未確認"}</span>`;
  } catch (e) {
    notices.unshift(e.message);
  }
  $("notice").hidden = !notices.length;
  $("notice").textContent = notices.join(" ");
}
function stats(w) {
  const fs = data.frames.filter((f) => f.time >= w.start && f.time < w.end),
    ps = fs
      .filter((f) => f.pitch_hz !== null && f.voiced_probability >= 0.5)
      .map((f) => f.pitch_hz)
      .sort((a, b) => a - b);
  return {
    voicedFraction: fs.length ? ps.length / fs.length : 0,
    pitch: ps.length
      ? (ps[Math.floor((ps.length - 1) / 2)] + ps[Math.floor(ps.length / 2)]) /
        2
      : null,
    db: fs.length
      ? 10 *
        Math.log10(
          fs.reduce((a, f) => a + 10 ** (f.rms_dbfs / 10), 0) / fs.length,
        )
      : null,
  };
}
function metrics() {
  if (!word) return;
  const w = effective(),
    m = stats(w),
    i = data.words.indexOf(word),
    before = i ? w.start - data.words[i - 1].end : null,
    after = i + 1 < data.words.length ? data.words[i + 1].start - w.end : null,
    pitch = $("pitch").value,
    volume = $("volume").value;
  const stat = (label, value, note = "", warn = false) =>
    `<div class="${warn ? "warn" : ""}"><dt>${label}</dt><dd>${value}${note ? `<small>${note}</small>` : ""}</dd></div>`;
  $("metrics").innerHTML = [
    stat("長さ", `${fmt(w.end - w.start)}秒`),
    stat(
      "高さ（中央値）",
      `${pitch === "" ? fmt(m.pitch, 1) : fmt(Number(pitch), 1)}Hz`,
      pitch === "" ? "自動" : `手動、自動は${fmt(m.pitch, 1)}`,
    ),
    stat(
      "音量",
      `${volume === "" ? fmt(m.db, 1) : fmt(Number(volume), 1)}dBFS`,
      volume === "" ? "自動" : `手動、自動は${fmt(m.db, 1)}`,
    ),
    stat(
      "前の語との間",
      before === null ? "なし" : `${fmt(before)}秒`,
      before < 0 ? "区間が重なっています" : "",
      before < 0,
    ),
    stat(
      "後の語との間",
      after === null ? "なし" : `${fmt(after)}秒`,
      after < 0 ? "区間が重なっています" : "",
      after < 0,
    ),
    stat(
      "有声の割合",
      `${(m.voicedFraction * 100).toFixed(0)}%`,
      m.voicedFraction < 0.3 ? "高さの推定が少ない" : "",
      m.voicedFraction < 0.3,
    ),
  ].join("");
}
// Chart geometry: word labels on top, pitch (log scale) above volume.
const X0 = 54,
  X1 = 784;
const x = (t) => X0 + ((t - rangeStart) / (rangeEnd - rangeStart)) * (X1 - X0);
const py = (p) => 36 + (1 - Math.log(p / 65) / Math.log(600 / 65)) * 84;
const dy = (d) => 210 - ((Math.max(-60, d) + 60) / 60) * 52;
function graph() {
  if (!word) return;
  const w = effective();
  let pitch = "",
    volume = "",
    open = false;
  for (const f of data.frames.filter(
    (f) => f.time >= rangeStart && f.time <= rangeEnd,
  )) {
    if (f.pitch_hz !== null && f.voiced_probability >= 0.5) {
      pitch += `${open ? "L" : "M"}${x(f.time).toFixed(1)},${py(Math.min(600, Math.max(65, f.pitch_hz))).toFixed(1)} `;
      open = true;
    } else open = false;
    volume += `${volume ? "L" : "M"}${x(f.time).toFixed(1)},${dy(f.rms_dbfs).toFixed(1)} `;
  }
  const bands = data.words
    .filter((a) => a.end >= rangeStart && a.start <= rangeEnd)
    .map((a) => {
      const b = a.id === word.id ? w : a,
        l = Math.max(rangeStart, b.start),
        r = Math.min(rangeEnd, b.end);
      return `<rect class="band ${a.id === word.id ? "selected" : ""}" x="${x(l)}" y="24" width="${Math.max(0, x(r) - x(l))}" height="190"/><text class="label" x="${x((l + r) / 2)}" y="17" text-anchor="middle">${esc(b.text)}</text>`;
    })
    .join("");
  const axis = [
    [py(600), "600Hz"],
    [py(200), "200"],
    [py(65), "65Hz"],
    [dy(0), "0dB"],
    [dy(-60), "−60dB"],
  ]
    .map(
      ([yy, label]) =>
        `<line class="grid" x1="${X0}" x2="${X1}" y1="${yy}" y2="${yy}"/><text x="${X0 - 6}" y="${yy + 4}" text-anchor="end">${label}</text>`,
    )
    .join("");
  const ticks = [0, 1, 2, 3, 4]
    .map((i) => {
      const t = rangeStart + ((rangeEnd - rangeStart) * i) / 4;
      return `<text x="${x(t)}" y="232" text-anchor="middle">${t.toFixed(2)}秒</text>`;
    })
    .join("");
  $("chart").innerHTML =
    bands +
    axis +
    `<path class="volume" d="${volume}"/><path class="pitch" d="${pitch}"/>` +
    `<line class="edge" x1="${x(w.start)}" x2="${x(w.start)}" y1="24" y2="214"/><line class="edge" x1="${x(w.end)}" x2="${x(w.end)}" y1="24" y2="214"/>` +
    `<line id="cursor" class="cursor" x1="${x(video.currentTime)}" x2="${x(video.currentTime)}" y1="24" y2="214"/>` +
    ticks;
}
$("chart").onclick = (e) => {
  const r = $("chart").getBoundingClientRect(),
    px = ((e.clientX - r.left) * 800) / r.width;
  video.currentTime = Math.max(
    0,
    Math.min(
      data.duration,
      rangeStart + ((px - X0) / (X1 - X0)) * (rangeEnd - rangeStart),
    ),
  );
};
for (const id of FIELDS)
  $(id).oninput = () => {
    dirty = true;
    $("saveState").textContent = "未保存";
    $("saveState").classList.remove("error");
    metrics();
    graph();
    moraInfo();
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
    ? `${boundaryMode === "start" ? "開始" : "終了"} ${fmt(effective()[boundaryMode])}秒の前後を繰り返し${video.paused ? "（一時停止中）" : "再生中"}。もう一度押すと止まります。`
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
    start: Math.max(0, w.start - 0.15),
    end: Math.min(data.duration, w.end + 0.15),
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
  clipPlayback = true;
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
function playWord() {
  const w = effective();
  if (!(w.end > w.start)) return;
  boundaryMode = null;
  boundaryStatus();
  looping = true;
  clipPlayback = true;
  video.currentTime = Math.max(0, w.start - 0.15);
  video.play().catch((e) => Review.setStatus(e.message, true));
}
$("wordPlay").onclick = playWord;
function step(delta) {
  if (!word) return;
  const next = data.words[data.words.indexOf(word) + delta];
  if (next && select(next.id)) playWord();
}
$("prevItem").onclick = () => step(-1);
$("nextItem").onclick = () => step(1);
let lastPlaying = null,
  animation = null;
function syncPlayback() {
  if (!data) return;
  const current = word ? effective() : null,
    a =
      current &&
      current.start <= video.currentTime &&
      video.currentTime < current.end
        ? current
        : data.words.find(
            (w) =>
              w.id !== word?.id &&
              w.start <= video.currentTime &&
              video.currentTime < w.end,
          );
  if (
    a &&
    Review.follow() &&
    !looping &&
    !clipPlayback &&
    !dirty &&
    !saving &&
    !document.activeElement.matches("input") &&
    a.id !== word?.id
  )
    select(a.id, true);
  const cursor = $("cursor"),
    cx = x(video.currentTime);
  if (cursor) {
    cursor.setAttribute("x1", cx);
    cursor.setAttribute("x2", cx);
  }
  document
    .querySelectorAll(".playing")
    .forEach((b) => b.classList.remove("playing"));
  if (a) {
    const chip = document.querySelector(`[data-word="${a.id}"]`);
    chip?.classList.add("playing");
    document.querySelector(`[data-live="${a.id}"]`)?.classList.add("playing");
    if (a.id !== lastPlaying && Review.follow())
      Review.reveal(chip, $("wordList").parentElement);
  }
  lastPlaying = a?.id ?? null;
  if (looping && word) {
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
  syncPlayback();
  animation = video.paused ? null : requestAnimationFrame(tick);
}
video.addEventListener("play", () => {
  boundaryStatus();
  if (!looping) clipPlayback = false;
  if (animation === null) animation = requestAnimationFrame(tick);
});
video.addEventListener("pause", () => {
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  syncPlayback();
  boundaryStatus();
});
video.addEventListener("timeupdate", syncPlayback);
video.addEventListener("seeked", syncPlayback);
const transport = Review.transport({
  player: () => video,
  duration: () => data?.duration,
  play: () => {
    looping = false;
    boundaryMode = null;
    boundaryStatus();
    clipPlayback = false;
    video.play().catch((e) => Review.setStatus(e.message, true));
  },
});

function audit() {
  $("auditCount").textContent = data.history.length;
  $("audit").innerHTML =
    data.history
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="historyItem">${esc(`${new Date(e.created_at_utc).toLocaleString("ja-JP")}　単語${e.word_id}\n${JSON.stringify(e.before)}\n→ ${JSON.stringify(e.after)}\n理由：${e.reason || "記入なし"}`)}</div>`,
      )
      .join("") || '<p class="muted">訂正はまだありません。</p>';
}
async function save() {
  if (saving || !word) return false;
  looping = false;
  boundaryMode = null;
  video.pause();
  boundaryStatus();
  saving = true;
  document.querySelectorAll(LOCKED).forEach((e) => (e.disabled = true));
  try {
    const id = word.id;
    await Review.api("/api/prosody/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: vid,
        word_id: id,
        expected_revision: data.revision,
        text: $("text").value,
        pronunciation_override: $("pronunciation").value || null,
        pronunciation_verified: $("pronunciationVerified").checked,
        start: Number($("start").value),
        end: Number($("end").value),
        pitch_override_hz:
          $("pitch").value === "" ? null : Number($("pitch").value),
        volume_override_dbfs:
          $("volume").value === "" ? null : Number($("volume").value),
        verified: $("verified").checked,
        reason: $("reason").value,
      }),
    });
    data = await Review.api(`/api/prosody?id=${vid}`);
    dirty = false;
    select(id);
    audit();
    progress();
    $("saveState").textContent = "保存しました。新しい時刻で計測し直しました。";
    return true;
  } catch (e) {
    $("saveState").textContent = e.message;
    $("saveState").classList.add("error");
    return false;
  } finally {
    saving = false;
    document.querySelectorAll(LOCKED).forEach((e) => (e.disabled = false));
  }
}
async function confirmNext() {
  if (!word) return;
  $("verified").checked = true;
  dirty = true;
  if (!(await save())) return;
  const next = data.words[data.words.findIndex((w) => w.id === word.id) + 1];
  if (next) {
    select(next.id);
    playWord();
  } else $("saveState").textContent = "最後の単語まで確認しました。";
}
$("saveWord").onclick = save;
$("confirmWord").onclick = confirmNext;
$("export").onclick = () => {
  if (dirty) {
    $("saveState").textContent = "書き出す前に、訂正を保存してください。";
    return;
  }
  Review.download(`${vid}-prosody-reviewed.json`, data);
};
Review.keys({
  toggle: transport.toggle,
  replay: playWord,
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
    const available = videos.filter((v) => v.has_prosody);
    vid = vid || available[0]?.video_id;
    if (vid) Review.linkSteps(vid, videos);
    if (!available.some((v) => v.video_id === vid))
      throw Error(
        "この録音には単語の計測結果がありません。先に計測を実行してください。",
      );
    return Review.api(`/api/prosody?id=${vid}`);
  })
  .then((d) => {
    data = d;
    video.src = data.audio_url || data.media_url;
    transport.applyRate();
    document.title = `${data.title} - 単語と声の確認`;
    Review.setStatus(
      data.transcript_changed
        ? "計測のあとで文字起こしが更新されています。単語との対応を確認してください。"
        : data.audio_url
          ? "元の音質を保った音声で再生しています。"
          : "単語の推定区間と、声の高さ・音量の計測結果です。",
      data.transcript_changed,
    );
    if (data.words.length) select(data.words[0].id);
    else list();
    audit();
    progress();
  })
  .catch((e) => Review.setStatus(e.message, true));
