import { h, render } from "preact";
import { useState, useEffect, useRef, useCallback } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

// ---------- api helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const getPath = (obj, key) => key.split(".").reduce((o, k) => (o ? o[k] : undefined), obj);
function setPath(obj, key, value) {
  const parts = key.split(".");
  const copy = structuredClone(obj);
  let cur = copy;
  for (const p of parts.slice(0, -1)) cur = cur[p];
  cur[parts.at(-1)] = value;
  return copy;
}
const fmtDur = (s) => (s == null ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const fmtSize = (b) => (b == null ? "—" : `${(b / 1e6).toFixed(1)} MB`);
const fmtDate = (s) => (s || "").replace("T", " ").slice(0, 16);

// ---------- toast ----------
let toastFn = () => {};
function Toast() {
  const [msg, setMsg] = useState(null);
  toastFn = (m) => { setMsg(m); setTimeout(() => setMsg(null), 3500); };
  return msg ? html`<div class="toast">${msg}</div>` : null;
}

// ---------- SSE ----------
function useEvents(handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    let es;
    const connect = () => {
      es = new EventSource("/api/events");
      es.onmessage = (e) => { try { ref.current(JSON.parse(e.data)); } catch {} };
      es.onerror = () => { es.close(); setTimeout(connect, 2000); ref.current({ event: "reconnect" }); };
    };
    connect();
    return () => es && es.close();
  }, []);
}

// ---------- router ----------
function useRoute() {
  const [route, setRoute] = useState(location.hash.slice(1) || "/");
  useEffect(() => {
    const fn = () => setRoute(location.hash.slice(1) || "/");
    addEventListener("hashchange", fn);
    return () => removeEventListener("hashchange", fn);
  }, []);
  return route;
}

// ---------- nav ----------
function Nav({ route }) {
  const [stats, setStats] = useState(null);
  useEffect(() => { api("/api/stats").then(setStats).catch(() => {}); }, [route]);
  return html`
    <nav>
      <a href="#/" class="logo">TikTok<span>Studio</span></a>
      <a href="#/" class=${"navlink" + (route === "/" ? " active" : "")}>Dashboard</a>
      <a href="#/history" class=${"navlink" + (route === "/history" ? " active" : "")}>History</a>
      <a href="#/presets" class=${"navlink" + (route === "/presets" ? " active" : "")}>Presets</a>
      ${stats && html`<span class="stats">${stats.video_count} videos · ${stats.data_size_gb} GB used · ${stats.disk_free_gb} GB free</span>`}
    </nav>`;
}

// ---------- dropzone ----------
function Dropzone({ onDone }) {
  const [state, setState] = useState({ drag: false, uploading: false, pct: 0 });
  const inputRef = useRef();

  const upload = (file) => {
    if (!file) return;
    setState({ drag: false, uploading: true, pct: 0 });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/videos");
    xhr.setRequestHeader("X-Filename", file.name);
    xhr.upload.onprogress = (e) => e.lengthComputable &&
      setState((s) => ({ ...s, pct: Math.round((e.loaded / e.total) * 100) }));
    xhr.onload = () => {
      setState({ drag: false, uploading: false, pct: 0 });
      if (xhr.status >= 200 && xhr.status < 300) {
        const res = JSON.parse(xhr.responseText);
        toastFn(res.duplicate ? "Already ingested — opening it" : "Upload complete — editing started");
        location.hash = `#/v/${res.video_id}`;
        onDone && onDone();
      } else {
        let msg = xhr.statusText;
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch {}
        toastFn(`Upload failed: ${msg}`);
      }
    };
    xhr.onerror = () => { setState({ drag: false, uploading: false, pct: 0 }); toastFn("Upload failed"); };
    xhr.send(file);
  };

  return html`
    <div class=${"dropzone" + (state.drag ? " over" : "")}
      onClick=${() => !state.uploading && inputRef.current.click()}
      onDragOver=${(e) => { e.preventDefault(); setState((s) => ({ ...s, drag: true })); }}
      onDragLeave=${() => setState((s) => ({ ...s, drag: false }))}
      onDrop=${(e) => { e.preventDefault(); upload(e.dataTransfer.files[0]); }}>
      <input type="file" ref=${inputRef} accept="video/*"
        onChange=${(e) => upload(e.target.files[0])} />
      ${state.uploading
        ? html`<div class="big">Uploading… ${state.pct}%</div><div>hang tight</div>`
        : html`<div class="big">Drop a raw video here</div>
               <div>or click to browse — it comes back fully edited with 3 hook variants</div>`}
    </div>`;
}

// ---------- job card ----------
function JobCard({ job, onCancel }) {
  return html`
    <div class="job">
      <span class="stage">${job.stage || job.status}</span>
      <div class="bar"><div style=${`width:${Math.round((job.progress || 0) * 100)}%`}></div></div>
      <span class="msg">${job.message || ""}</span>
      <a href=${`#/v/${job.video_id}`} class="btn small">open</a>
      <button class="danger" onClick=${() => onCancel(job.id)}>cancel</button>
    </div>`;
}

// ---------- dashboard ----------
function Dashboard() {
  const [videos, setVideos] = useState([]);
  const [jobs, setJobs] = useState([]);

  const refresh = useCallback(() => {
    api("/api/videos?limit=24").then((d) => setVideos(d.videos));
    api("/api/jobs?status=queued,running").then((d) => setJobs(d.jobs));
  }, []);
  useEffect(refresh, []);
  useEvents((ev) => {
    if (ev.event === "job_update" || ev.event === "render_done" ||
        ev.event === "video_update" || ev.event === "reconnect") refresh();
  });

  const cancel = (id) => post(`/api/jobs/${id}/cancel`).then(refresh).catch((e) => toastFn(e.message));

  return html`
    <${Dropzone} onDone=${refresh} />
    ${jobs.length > 0 && html`
      <div class="section" style="margin-top:0">
        <h2>Active jobs</h2>
        <div class="jobs">${jobs.map((j) => html`<${JobCard} job=${j} onCancel=${cancel} />`)}</div>
      </div>`}
    <div class="section" style="margin-top:0">
      <h2>Recent videos</h2>
      ${videos.length === 0
        ? html`<div class="empty">Nothing yet — drop your first video above, or copy one into ~/TikTokStudio/inbox</div>`
        : html`<div class="grid">${videos.map((v) => html`<${VideoCard} v=${v} />`)}</div>`}
    </div>`;
}

function VideoCard({ v }) {
  const done = v.renders?.[0];
  const poster = done ? `/api/renders/${done.id}/poster` : null;
  return html`
    <a class="card" href=${`#/v/${v.id}`}>
      <div class="poster" style=${poster ? `background-image:url(${poster})` : ""}>
        ${!poster && (v.active_job ? `${v.active_job.stage || "queued"}…` : "no renders")}
      </div>
      <div class="meta">
        <div class="title">${v.filename}</div>
        <div class="sub">${fmtDur(v.duration_s)} · ${v.renders?.length || 0} renders · ${fmtDate(v.created_at)}</div>
      </div>
    </a>`;
}

// ---------- settings panel ----------
const GROUP_LABELS = {
  captions: "Captions", cuts: "Cuts & pacing", zoom: "Zoom", audio: "Loudness",
  music: "Music", sfx: "Sound effects", color: "Color grade",
  overlays: "Banner & CTA", hook: "Hook", output: "Output",
};

function Control({ entry, value, onChange }) {
  const val = value === undefined ? entry.default : value;
  if (entry.type === "bool") return html`
    <div class="ctl">
      <label>${entry.label}</label>
      <input type="checkbox" checked=${!!val} onChange=${(e) => onChange(e.target.checked)} />
    </div>`;
  if (entry.type === "number") return html`
    <div class="ctl-slider">
      <div class="row">
        <label>${entry.label}</label>
        <span class="val">${val}</span>
      </div>
      <input type="range" min=${entry.min} max=${entry.max} step=${entry.step || 1}
        value=${val} onInput=${(e) => onChange(Number(e.target.value))} />
    </div>`;
  if (entry.type === "color") return html`
    <div class="ctl">
      <label>${entry.label}</label>
      <input type="color" value=${val} onChange=${(e) => onChange(e.target.value)} />
    </div>`;
  if (entry.type === "enum") return html`
    <div class="ctl">
      <label>${entry.label}</label>
      <select value=${val === null ? "__none__" : val}
        onChange=${(e) => onChange(e.target.value === "__none__" ? null : e.target.value)}>
        ${entry.options.map((o) => html`
          <option value=${o === null ? "__none__" : o}>${o === null ? "none" : o}</option>`)}
      </select>
    </div>`;
  return html`
    <div class="ctl">
      <label>${entry.label}</label>
      <input type="text" value=${val ?? ""} placeholder="auto"
        onChange=${(e) => onChange(e.target.value === "" ? (entry.key === "hook.text_override" ? null : "") : e.target.value)} />
    </div>`;
}

function SettingsPanel({ schema, settings, baseline, onChange, footer }) {
  const [open, setOpen] = useState({ captions: true });
  const groups = [...new Set(schema.map((e) => e.group))];
  return html`
    <div class="settings">
      <div class="settings-head"><h2>Edit settings</h2>${footer?.head || null}</div>
      ${groups.map((g) => {
        const entries = schema.filter((e) => e.group === g);
        const dirty = baseline && entries.some((e) =>
          JSON.stringify(getPath(settings, e.key)) !== JSON.stringify(getPath(baseline, e.key)));
        return html`
          <div class="group">
            <div class="group-head" onClick=${() => setOpen((o) => ({ ...o, [g]: !o[g] }))}>
              ${GROUP_LABELS[g] || g}${dirty && html`<span class="dirty-dot" />`}
              <span class="chev">${open[g] ? "▲" : "▼"}</span>
            </div>
            ${open[g] && html`<div class="group-body">
              ${entries.map((e) => html`
                <${Control} entry=${e} value=${getPath(settings, e.key)}
                  onChange=${(v) => onChange(setPath(settings, e.key, v))} />`)}
            </div>`}
          </div>`;
      })}
      ${footer?.bar || null}
    </div>`;
}

// ---------- video detail ----------
function VideoView({ id }) {
  const [video, setVideo] = useState(null);
  const [meta, setMeta] = useState(null);
  const [settings, setSettings] = useState(null);
  const [variant, setVariant] = useState("hook_a");
  const [selVariants, setSelVariants] = useState({ hook_a: true, hook_b: true, hook_c: true });
  const [presets, setPresets] = useState([]);

  const refresh = useCallback(() =>
    api(`/api/videos/${id}`).then(setVideo).catch((e) => toastFn(e.message)), [id]);

  useEffect(() => {
    refresh();
    api("/api/settings/schema").then((m) => {
      setMeta(m);
      setSettings((s) => s || m.last_settings || m.defaults);
    });
    api("/api/presets").then((d) => setPresets(d.presets));
  }, [id]);

  useEvents((ev) => {
    if (ev.video_id === id || ev.event === "reconnect") refresh();
  });

  if (!video || !meta || !settings) return html`<div class="empty">Loading…</div>`;

  const rendersByVariant = {};
  for (const r of video.renders) {
    if (r.status.startsWith("done") && !rendersByVariant[r.variant]) rendersByVariant[r.variant] = r;
  }
  const current = rendersByVariant[variant];
  const baseline = current ? current.settings : null;
  const dirty = baseline && JSON.stringify(settings) !== JSON.stringify(baseline);
  const activeJob = video.jobs.find((j) => j.status === "queued" || j.status === "running");
  const brainStatus = video.brain_status;

  const doRender = () => {
    const variants = Object.keys(selVariants).filter((k) => selVariants[k]);
    post(`/api/videos/${id}/render`, { settings, variants })
      .then(() => { toastFn("Re-render queued (analysis cached — no re-transcription)"); refresh(); })
      .catch((e) => toastFn(e.message));
  };
  const regenBrain = () =>
    post(`/api/videos/${id}/analyze`).then(() => {
      toastFn("Re-analysis + AI edit decisions queued");
      refresh();
    }).catch((e) => toastFn(e.message));
  const savePreset = () => {
    const name = prompt("Preset name:");
    if (name) post("/api/presets", { name, settings }).then(() => toastFn(`Preset “${name}” saved`));
  };

  const qc = current?.qc;
  return html`
    <h1>${video.filename}
      ${brainStatus === "claude" && html` <span class="badge claude">AI edit · Claude</span>`}
      ${brainStatus === "fallback" && html` <span class="badge fallback">heuristic edit</span>`}
    </h1>
    ${activeJob && html`<div class="jobs"><${JobCard} job=${{ ...activeJob, video_id: id }}
        onCancel=${(jid) => post(`/api/jobs/${jid}/cancel`).then(refresh)} /></div>`}
    <div class="detail">
      <div class="player-panel">
        <div class="tabs">
          ${Object.keys(meta.variants).map((v) => html`
            <div class=${"tab" + (variant === v ? " active" : "")} onClick=${() => setVariant(v)}>
              ${meta.variants[v]}${rendersByVariant[v]?.status === "done_with_warnings" ? html` <span class="qcwarn">⚠</span>` : ""}
            </div>`)}
        </div>
        ${current
          ? html`<video class="player" controls playsinline key=${current.id}
                   src=${`/api/renders/${current.id}/file`} poster=${`/api/renders/${current.id}/poster`} />`
          : html`<div class="empty">No ${meta.variants[variant]} render yet${activeJob ? " — rendering…" : ""}</div>`}
        <div class="player-actions">
          ${current && html`
            <a class="btn" href=${`/api/renders/${current.id}/file`} download>Download</a>
            <span class="muted small" style="align-self:center">
              ${fmtDur(current.duration_s)} · ${fmtSize(current.size_bytes)}</span>`}
          <button onClick=${regenBrain}>↻ Regenerate AI edit</button>
        </div>
        ${qc && html`
          <div class="qc-box">
            <b>QC</b> — transcript match ${(qc.match_ratio * 100).toFixed(1)}%
            ${qc.pass ? html` <span class="badge claude">pass</span>` : html` <span class="badge warn">warnings</span>`}
            ${qc.missing?.length > 0 && html`
              <div class="muted small" style="margin-top:6px">
                possibly missing: ${qc.missing.map((m) => m.words.join(" ")).join(" · ")}</div>`}
            <div class="stills">
              ${[0, 1, 2, 3, 4, 5].map((n) => html`<img src=${`/api/renders/${current.id}/still/${n}`} loading="lazy" />`)}
            </div>
          </div>`}
      </div>
      <div>
        <${SettingsPanel} schema=${meta.schema} settings=${settings} baseline=${baseline}
          onChange=${setSettings}
          footer=${{
            head: html`
              <select onChange=${(e) => {
                const p = presets.find((x) => x.id === e.target.value);
                if (p) { setSettings(p.settings); toastFn(`Applied “${p.name}”`); }
              }}>
                <option value="">preset…</option>
                ${presets.map((p) => html`<option value=${p.id}>${p.name}</option>`)}
              </select>
              <button onClick=${savePreset}>save as</button>`,
            bar: html`
              <div class="render-bar">
                <div class="variants">
                  ${Object.keys(meta.variants).map((v) => html`
                    <label><input type="checkbox" checked=${selVariants[v]}
                      onChange=${(e) => setSelVariants((s) => ({ ...s, [v]: e.target.checked }))} />
                      ${meta.variants[v]}</label>`)}
                </div>
                <button class="primary" style="margin-left:auto" onClick=${doRender}
                  disabled=${!!activeJob}>
                  ${dirty ? "Re-render with changes" : "Re-render"}</button>
              </div>`,
          }} />
        <div class="section">
          <h2>Render history</h2>
          <table class="list">
            <tr><th>when</th><th>variant</th><th>len</th><th>status</th><th></th></tr>
            ${video.renders.map((r) => html`
              <tr>
                <td class="muted">${fmtDate(r.created_at)}</td>
                <td>${meta.variants[r.variant] || r.variant}</td>
                <td>${fmtDur(r.duration_s)}</td>
                <td>${r.status.startsWith("done")
                  ? html`<span class=${r.status === "done" ? "" : "muted"}>${r.status === "done" ? "✓" : "✓ warnings"}</span>`
                  : r.status}</td>
                <td><button class="small" onClick=${() => { setSettings(r.settings); toastFn("Settings restored from this render"); }}>
                  use settings</button></td>
              </tr>`)}
          </table>
        </div>
      </div>
    </div>`;
}

// ---------- history ----------
function History() {
  const [videos, setVideos] = useState([]);
  const [q, setQ] = useState("");
  const refresh = useCallback(() =>
    api(`/api/videos?limit=200&q=${encodeURIComponent(q)}`).then((d) => setVideos(d.videos)), [q]);
  useEffect(refresh, [q]);

  const del = (v) => {
    if (confirm(`Delete “${v.filename}” and all its renders?`))
      api(`/api/videos/${v.id}`, { method: "DELETE" }).then(refresh);
  };

  return html`
    <h1>History</h1>
    <input type="text" placeholder="search…" value=${q} onInput=${(e) => setQ(e.target.value)}
      style="margin-bottom:16px;width:280px" />
    <table class="list">
      <tr><th>video</th><th>uploaded</th><th>length</th><th>renders</th><th>status</th><th></th></tr>
      ${videos.map((v) => html`
        <tr>
          <td><a href=${`#/v/${v.id}`}><b>${v.filename}</b></a></td>
          <td class="muted">${fmtDate(v.created_at)}</td>
          <td>${fmtDur(v.duration_s)}</td>
          <td>${v.renders?.length || 0}</td>
          <td class="muted">${v.active_job ? (v.active_job.stage || "queued") + "…" : v.status}</td>
          <td><button class="danger" onClick=${() => del(v)}>delete</button></td>
        </tr>`)}
    </table>
    ${videos.length === 0 && html`<div class="empty">No videos yet</div>`}`;
}

// ---------- presets ----------
function Presets() {
  const [data, setData] = useState({ presets: [], default_preset: null });
  const refresh = useCallback(() => api("/api/presets").then(setData), []);
  useEffect(refresh, []);

  const makeDefault = (p) =>
    post("/api/presets", { name: p.name, settings: p.settings, make_default: true }).then(refresh);
  const del = (p) => confirm(`Delete preset “${p.name}”?`) &&
    api(`/api/presets/${p.id}`, { method: "DELETE" }).then(refresh);

  return html`
    <h1>Presets</h1>
    <p class="muted small" style="margin-bottom:16px">
      Save presets from any video's settings panel. The default preset applies to new uploads.</p>
    ${data.presets.length === 0 && html`<div class="empty">No presets saved yet</div>`}
    ${data.presets.map((p) => html`
      <div class="preset-row">
        <b style="min-width:160px">${p.name}</b>
        ${data.default_preset === p.id
          ? html`<span class="badge claude">default</span>`
          : html`<button onClick=${() => makeDefault(p)}>make default</button>`}
        <span class="muted small">updated ${fmtDate(p.updated_at)}</span>
        <button class="danger" style="margin-left:auto" onClick=${() => del(p)}>delete</button>
      </div>`)}`;
}

// ---------- app ----------
function App() {
  const route = useRoute();
  let view;
  if (route.startsWith("/v/")) view = html`<${VideoView} id=${route.slice(3)} key=${route} />`;
  else if (route === "/history") view = html`<${History} />`;
  else if (route === "/presets") view = html`<${Presets} />`;
  else view = html`<${Dashboard} />`;
  return html`
    <div class="shell">
      <${Nav} route=${route} />
      ${view}
      <${Toast} />
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
