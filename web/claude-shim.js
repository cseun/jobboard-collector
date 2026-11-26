/* 기존 보드가 쓰던 저장·파일·사용자 호출(window.claude.use(...))을 이 서버의 API로 이어 주는 호환 계층.
 * 보드 코드는 거의 그대로 두고 이 파일만 서버 쪽을 바라보게 합니다.
 * 지원: db(문서 저장소) · user · assets(파일) · sample(AI, 서버가 내 API 키로 대신 호출) · dictation(브라우저 마이크 받아쓰기).
 * 읽어 주기(tts)는 보드가 브라우저 음성(speechSynthesis)으로 알아서 대신하므로 연결하지 않습니다. */
(function () {
  "use strict";
  const ME = window.__ME__ || {};

  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
    if (opts.json !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
    if (opts.form) init.body = opts.form;
    const res = await fetch(path, init);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
    if (!res.ok) {
      const err = new Error((data && typeof data.detail === "string" && data.detail) || "요청을 처리하지 못했습니다.");
      err.status = res.status;
      err.code = (data && data.code) || (res.status === 401 ? "unauthenticated" : res.status === 403 ? "permission-denied" : "error");
      if (res.status === 401) location.replace("/");
      throw err;
    }
    return data;
  }

  const enc = encodeURIComponent;
  const clean = (v) => JSON.parse(JSON.stringify(v === undefined ? null : v)); // undefined 제거
  const newId = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 20) : String(Date.now()) + Math.random().toString(36).slice(2, 8));
  const META = { fromCache: false, hasPendingWrites: false };

  function docSnap(id, exists, data) {
    return { id, exists: !!exists, data: () => (exists ? data : undefined), metadata: META };
  }
  function colSnap(items) {
    const docs = items.map((d) => ({ id: d.id, exists: true, data: () => d.data, metadata: META }));
    return { docs, size: docs.length, empty: docs.length === 0, metadata: META, forEach: (fn) => docs.forEach(fn) };
  }

  // 구독: 처음 한 번 읽고, 탭이 다시 보일 때 서버 값이 바뀌었으면 다시 알려 줍니다(실시간 연결은 쓰지 않음).
  function subscribe(read, onNext, onError) {
    let alive = true, last = null;
    const run = async () => {
      try {
        const { sig, snap } = await read();
        if (!alive || sig === last) return;
        last = sig; onNext(snap);
      } catch (e) { if (alive && onError) onError(e); }
    };
    const onVis = () => { if (document.visibilityState === "visible") run(); };
    document.addEventListener("visibilitychange", onVis);
    run();
    return () => { alive = false; document.removeEventListener("visibilitychange", onVis); };
  }

  function docRef(coll, id) {
    const q = () => `c=${enc(coll)}&id=${enc(id)}`;
    const read = async () => {
      const r = await api(`/api/docs/get?${q()}`);
      return { sig: r.exists ? String(r.updated_at) : "none", snap: docSnap(id, r.exists, r.data) };
    };
    return {
      id,
      get: async () => (await read()).snap,
      set: async (data, opts) => { await api(`/api/docs/set?${q()}`, { method: "PUT", json: { data: clean(data), merge: !!(opts && opts.merge) } }); },
      delete: async () => { await api(`/api/docs/delete?${q()}`, { method: "DELETE" }); },
      onSnapshot: (next, error) => subscribe(read, next, error),
    };
  }

  function collRef(coll) {
    const read = async () => {
      const r = await api(`/api/docs/list?c=${enc(coll)}`);
      const sig = r.docs.map((d) => d.id + "@" + d.updated_at).join("|");
      return { sig, snap: colSnap(r.docs) };
    };
    return {
      doc: (id) => docRef(coll, id || newId()),
      add: async (data) => { const r = await api(`/api/docs/add?c=${enc(coll)}`, { method: "POST", json: { data: clean(data) } }); return docRef(coll, r.id); },
      get: async () => (await read()).snap,
      onSnapshot: (next, error) => subscribe(read, next, error),
    };
  }

  const db = { collection: (path) => collRef(String(path).replace(/^\/+|\/+$/g, "")) };

  const user = {
    id: async () => String(ME.id),
    isOwner: () => !!ME.is_admin,
    canEdit: () => !!ME.is_admin,
    can: async () => true,
    profiles: async () => ({}),
  };

  const assets = {
    upload: async (file, opts) => {
      const form = new FormData();
      form.append("file", file, file.name);
      if (opts && opts.type) form.append("type", opts.type);
      const r = await api("/api/files", { method: "POST", form });
      return { id: r.id, contentType: r.contentType, sizeBytes: r.sizeBytes, name: r.name };
    },
    delete: async (id) => { await api(`/api/files/${enc(id)}`, { method: "DELETE" }); },
  };

  // ---- AI: 브라우저는 Anthropic에 직접 가지 않고 서버(/api/ai/sample)에 부탁합니다. 키는 서버에만 있습니다.
  class AiText extends String { get text() { return this.toString(); } } // text()는 문자열이자 {text}처럼 쓰여서 둘 다 되게 함
  const toB64 = (blob) => new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || "");
    r.onerror = () => fail(r.error);
    r.readAsDataURL(blob);
  });
  async function imgPart(im) {
    if (im instanceof Blob) return { media_type: im.type || "image/png", data: await toB64(im) };
    throw Object.assign(new Error("이미지를 읽지 못했어요."), { code: "bad_image" });
  }
  async function ask(kind, prompt, opts) {
    opts = opts || {};
    const body = { kind, prompt, model_tier: opts.modelTier === "quick" ? "quick" : "default" };
    if (opts.maxTokens) body.max_tokens = opts.maxTokens;
    if (opts.images && opts.images.length) body.images = await Promise.all(opts.images.map(imgPart));
    return api("/api/ai/sample", { method: "POST", json: body });
  }
  const sample = {
    json: async (prompt, opts) => (await ask("json", prompt, opts)).json,
    text: async (prompt, opts) => new AiText((await ask("text", prompt, opts)).text),
    limits: async () => ({ images: { maxCount: 4 } }),
  };

  // ---- 말로 답하기: 브라우저의 음성 인식(Chrome·Edge). 지원하지 않는 브라우저에서는 연결하지 않아 보드가 키보드 안내로 대신합니다.
  function makeDictation() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return null;
    const h = { partial: [], final: [], state: [] };
    const emit = (k, v) => h[k].forEach((f) => { try { f(v); } catch (e) { /* 화면 쪽 오류는 무시 */ } });
    let rec = null, want = false;
    function create() {
      const r = new SR();
      r.lang = "ko-KR"; r.continuous = true; r.interimResults = true;
      r.onstart = () => emit("state", { state: "listening" });
      r.onresult = (ev) => {
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const t = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) emit("final", { text: t }); else interim += t;
        }
        emit("partial", { text: interim });
      };
      r.onerror = (ev) => { if (ev.error === "not-allowed" || ev.error === "service-not-allowed") want = false; };
      r.onend = () => {
        if (want && rec === r) setTimeout(() => { try { r.start(); } catch (e) { want = false; emit("state", { state: "idle" }); } }, 250); // 조용하면 브라우저가 끊으므로 다시 이어 붙임
        else emit("state", { state: "idle" });
      };
      return r;
    }
    return {
      onPartial: (f) => h.partial.push(f),
      onFinal: (f) => h.final.push(f),
      onState: (f) => h.state.push(f),
      start: async () => {
        if (want) return;
        want = true; rec = create();
        try { rec.start(); } catch (e) { want = false; throw e; }
      },
      stop: async () => { want = false; try { rec && rec.stop(); } catch (e) { /* 이미 멈춤 */ } },
    };
  }
  const dictation = makeDictation();

  const caps = { db, user, assets, sample };
  if (dictation) caps.dictation = dictation;
  window.claude = {
    use: async (name) => {
      if (caps[name]) return caps[name];
      throw Object.assign(new Error(`'${name}' 기능은 이 서버에 아직 연결되지 않았습니다.`), { code: "unsupported" });
    },
  };
})();
