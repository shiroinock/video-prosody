// Shared chrome for the three review steps: app bar (recording picker, step
// navigation, progress, export), the bottom transport bar, keyboard shortcuts
// and small helpers. Each page loads this before its own script.
const Review = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s).replace(
      /[&<>"']/g,
      (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
          c
        ],
    );
  const clock = (s) => {
    if (!Number.isFinite(s)) return "0:00.000";
    const m = Math.floor(s / 60);
    return `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}`;
  };
  async function api(url, options) {
    const r = await fetch(url, options);
    const d = await r.json();
    if (!r.ok) throw Error(d.error || "読み込みに失敗しました");
    return d;
  }
  function download(name, data) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const STEPS = [
    { key: "transcript", path: "/", label: "文字起こし" },
    { key: "prosody", path: "/prosody", label: "単語と声" },
    { key: "moras", path: "/moras", label: "モーラ" },
  ];

  // Inserts the app bar, status line, transport bar and shortcut dialog.
  function mount(step, { exportLabel = "JSONを書き出す" } = {}) {
    const bar = document.createElement("header");
    bar.className = "appbar";
    bar.innerHTML = `<select id="recording" class="recording" aria-label="録音"></select>
<nav aria-label="確認の手順"><ol class="steps">${STEPS.map(
      (s, i) =>
        `<li><a href="${s.path}" data-step="${s.key}" ${s.key === step ? 'aria-current="page"' : ""}><span class="stepNo">${i + 1}</span>${s.label}</a></li>`,
    ).join("")}</ol></nav>
<div class="spacer"></div>
<div class="meter" role="status"><div class="bar"><div id="meterBar"></div></div><span id="meterText" class="num"></span></div>
<button id="export">${exportLabel}</button>`;
    const status = document.createElement("p");
    status.id = "status";
    status.className = "statusLine";
    status.setAttribute("role", "status");
    status.textContent = "読み込み中…";
    document.body.prepend(bar, status);

    const transport = document.createElement("footer");
    transport.className = "transport";
    transport.setAttribute("aria-label", "再生");
    transport.innerHTML = `<button id="tBack" class="skip" title="2秒戻る">−2秒</button>
<button id="tPlay" class="play" aria-label="再生" title="再生 / 停止（Space）">▶</button>
<button id="tForward" class="skip" title="2秒進む">＋2秒</button>
<span class="clock num"><span id="tTime">0:00.000</span> / <span id="tDuration">0:00.000</span></span>
<input id="tSeek" type="range" min="0" max="1" step="0.01" value="0" aria-label="再生位置">
<select id="tSpeed" aria-label="再生速度"><option value="0.5">0.5倍</option><option value="0.75">0.75倍</option><option value="1" selected>1倍</option><option value="1.25">1.25倍</option></select>
<div class="opts"><label class="check"><input id="tLoop" type="checkbox"> 選択を繰り返す</label><label class="check"><input id="tFollow" type="checkbox" checked> 再生位置を追う</label></div>
<button id="tHelp" class="quiet help" title="キーボード操作（?）">キー操作</button>`;
    document.body.append(transport);

    const dialog = document.createElement("dialog");
    dialog.id = "keyHelp";
    dialog.innerHTML = `<h2 class="sectionTitle">キーボード操作</h2><table><tbody>
<tr><td><kbd>Space</kbd></td><td>再生 / 停止</td></tr>
<tr><td><kbd>R</kbd></td><td>選択中の${step === "transcript" ? "区間" : step === "prosody" ? "単語" : "拍"}を再生</td></tr>
<tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>前 / 次の${step === "transcript" ? "区間" : step === "prosody" ? "単語" : "拍"}</td></tr>
<tr><td><kbd>Ctrl</kbd>+<kbd>Enter</kbd></td><td>確認済みにして保存し、次へ</td></tr>
<tr><td><kbd>Ctrl</kbd>+<kbd>S</kbd></td><td>保存（確認状態は変えない）</td></tr>
<tr><td><kbd>Esc</kbd></td><td>入力欄から抜ける</td></tr>
</tbody></table><form method="dialog"><button>閉じる</button></form>`;
    document.body.append(dialog);
    $("tHelp").onclick = () => dialog.showModal();
  }

  function setStatus(message, warn = false) {
    $("status").textContent = message;
    $("status").classList.toggle("warn", warn);
  }

  function meter(done, total, unit) {
    $("meterText").textContent = `確認済み ${done} / ${total}${unit}`;
    $("meterBar").style.width = total ? `${(100 * done) / total}%` : "0";
  }

  // Fills the recording picker and points the step links at the recording.
  // `onChange(id)` decides how to switch; it returns false to keep the choice.
  async function recordings(current, onChange) {
    const videos = await api("/api/catalog");
    const picker = $("recording");
    for (const v of videos) {
      const option = new Option(v.title, v.video_id);
      option.selected = v.video_id === current;
      picker.add(option);
    }
    picker.dataset.current = current ?? "";
    picker.onchange = () => {
      if (onChange(picker.value) === false)
        picker.value = picker.dataset.current;
      else picker.dataset.current = picker.value;
    };
    return videos;
  }
  function linkSteps(id, videos) {
    $("recording").value = id;
    $("recording").dataset.current = id;
    const v = videos.find((v) => v.video_id === id) || {};
    for (const a of document.querySelectorAll("[data-step]")) {
      const step = STEPS.find((s) => s.key === a.dataset.step);
      a.href = `${step.path}?id=${encodeURIComponent(id)}`;
      const missing =
        (step.key === "prosody" && !v.has_prosody) ||
        (step.key === "moras" && !v.has_moras);
      if (missing && a.getAttribute("aria-current") !== "page") {
        a.setAttribute("aria-disabled", "true");
        a.title = "この録音にはまだ計測結果がありません";
      } else {
        a.removeAttribute("aria-disabled");
        a.removeAttribute("title");
      }
    }
  }

  // Shows the native player only when the media actually has a picture.
  function watchMedia(media) {
    const update = () =>
      document.body.classList.toggle(
        "audioOnly",
        !(media.videoWidth > 0 && media.videoHeight > 0),
      );
    document.body.classList.add("audioOnly");
    media.addEventListener("loadedmetadata", update);
    media.addEventListener("emptied", update);
  }

  // Bottom transport bar. `player()` returns the current media-like object;
  // `play()` starts continuous playback the way the page wants.
  function transport({ player, play, duration }) {
    const seek = $("tSeek");
    let dragging = false;
    const total = () => {
      const d = player().duration;
      return Number.isFinite(d) && d > 0 ? d : duration() || 0;
    };
    $("tPlay").onclick = toggle;
    $("tBack").onclick = () =>
      (player().currentTime = Math.max(0, player().currentTime - 2));
    $("tForward").onclick = () =>
      (player().currentTime = Math.min(total(), player().currentTime + 2));
    $("tSpeed").onchange = () =>
      (player().playbackRate = Number($("tSpeed").value));
    seek.addEventListener("pointerdown", () => (dragging = true));
    seek.addEventListener("pointerup", () => (dragging = false));
    seek.oninput = () => (player().currentTime = Number(seek.value));
    let last = "";
    function frame() {
      const p = player();
      if (p) {
        const t = p.currentTime || 0,
          d = total(),
          key = `${t}|${d}|${p.paused}`;
        if (key !== last) {
          last = key;
          $("tTime").textContent = clock(t);
          $("tDuration").textContent = clock(d);
          seek.max = d || 1;
          if (!dragging) seek.value = t;
          $("tPlay").textContent = p.paused ? "▶" : "❚❚";
          $("tPlay").setAttribute("aria-label", p.paused ? "再生" : "停止");
        }
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    function toggle() {
      const p = player();
      if (p.paused) play();
      else p.pause();
    }
    return { toggle, applyRate: () => (player().playbackRate = Number($("tSpeed").value)) };
  }
  const loop = () => $("tLoop").checked;
  const follow = () => $("tFollow").checked;

  // Keyboard shortcuts. Handlers: toggle, replay, prev, next, confirmNext, save.
  function keys(h) {
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      const typing =
        t.matches?.("textarea, select, input") &&
        !t.matches('input[type="checkbox"], input[type="range"]');
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        h.confirmNext?.();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        h.save?.();
        return;
      }
      if (typing) {
        if (e.key === "Escape") t.blur();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector("dialog[open]"))
        return;
      const action = {
        " ": h.toggle,
        r: h.replay,
        R: h.replay,
        ArrowLeft: h.prev,
        ArrowRight: h.next,
        "?": () => $("keyHelp").showModal(),
      }[e.key];
      if (action) {
        e.preventDefault();
        action();
      }
    });
  }

  // Keeps a selected chip visible inside its scrolling strip.
  function reveal(el, box) {
    if (!el || !box) return;
    const top = el.offsetTop - box.offsetTop,
      bottom = top + el.offsetHeight;
    if (top < box.scrollTop || bottom > box.scrollTop + box.clientHeight)
      box.scrollTop = top - box.clientHeight / 2;
  }

  return {
    $,
    esc,
    clock,
    api,
    download,
    mount,
    setStatus,
    meter,
    recordings,
    linkSteps,
    watchMedia,
    transport,
    loop,
    follow,
    keys,
    reveal,
  };
})();
