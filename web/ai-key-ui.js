/* ⚙️ 설정 화면의 "AI 연결" 아래에 ChatGPT·Claude·Gemini 키 등록 + 모델 선택 칸을 붙입니다.
 * 보드 코드는 빈 자리(<div id="srvKeyBox">)만 두고, 그 안은 이 파일이 채웁니다.
 * 키는 서버로만 보내고(암호화 저장), 화면에는 끝 4자리만 다시 보여 줍니다.
 * 사용법: 키만 붙여 넣으면 → 그 키로 쓸 수 있는 모델 목록을 서비스에서 받아 와 → 목록에서 골라 씁니다. */
(function () {
  "use strict";

  const ORDER = ["openai", "anthropic", "gemini"];
  const OPEN = new Set();        // 펼쳐 둔 카드(다시 그려도 유지)
  const REKEY = new Set();       // '키 바꾸기'를 누른 카드
  const MODELS = {};             // 서비스별 모델 목록 {models, model_default, model_quick}

  async function call(path, method, json) {
    const res = await fetch(path, {
      method, credentials: "same-origin",
      headers: json ? { "Content-Type": "application/json" } : {},
      body: json ? JSON.stringify(json) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
    if (!res.ok) {
      const err = new Error((data && typeof data.detail === "string" && data.detail) || "요청을 처리하지 못했어요.");
      err.code = data && data.code;
      throw err;
    }
    return data;
  }

  function el(tag, props, ...kids) {
    const e = document.createElement(tag);
    Object.assign(e, props || {});
    kids.forEach((k) => e.append(k));
    return e;
  }
  const note = (text, extra) => el("p", Object.assign({ className: "note", textContent: text }, extra || {}));
  const chip = (text, tone) => {
    const c = el("span", { textContent: text });
    c.style.cssText = "font-size:12px;padding:2px 9px;border-radius:999px;white-space:nowrap;" + (tone === "ok"
      ? "background:var(--ok-soft);color:var(--ok);font-weight:600;"
      : tone === "on" ? "background:var(--accent-soft);color:var(--accent);font-weight:600;"
      : "border:1px solid var(--line);color:var(--muted);");
    return c;
  };

  async function render(box) {
    box.replaceChildren(note("🔑 내 AI 키를 불러오는 중…"));
    let st;
    try { st = await call("/api/ai/status", "GET"); }
    catch (e) { box.replaceChildren(note("⚠️ AI 상태를 불러오지 못했어요: " + e.message)); return; }

    const wrap = el("div", { className: "row" });
    wrap.append(el("div", { className: "top" }, el("b", { textContent: "🔑 내 AI 키 · ChatGPT / Claude / Gemini" })));

    if (st.demo) {
      wrap.append(note("데모 계정에서는 AI가 꺼져 있어요. 위에서 'AI 연결'을 '복사·붙여넣기'로 바꾸면 어떤 AI로든 직접 써 볼 수 있어요."));
      box.replaceChildren(wrap);
      return;
    }
    if (!st.server_ready) {
      wrap.append(note("⚠️ 이 서버에는 키를 안전하게 저장할 설정(APP_ENCRYPTION_KEY)이 아직 없어요. 서버 관리자에게 알려 주세요. 그동안은 'AI 연결'을 '복사·붙여넣기'로 쓸 수 있어요."));
      box.replaceChildren(wrap);
      return;
    }

    const act = st.active && st.providers[st.active];
    wrap.append(note(act
      ? `✅ 지금 ${act.label}(${act.model_default || "모델 미정"})로 답해요 · 오늘 ${st.today.used}/${st.today.limit}회 사용 (한국 시간 0시에 초기화)`
      : "아직 연결된 AI가 없어요. 아래 세 곳 중 쓰고 싶은 곳에 키만 붙여 넣으면 바로 쓸 수 있어요. 모델은 키를 넣은 뒤 목록에서 고르면 돼요."));

    ORDER.forEach((p) => wrap.append(card(box, st, p)));

    wrap.append(
      note("🔒 키는 암호화해서 이 서버에만 저장하고, 화면에는 끝 4자리만 보여요. 사용 요금은 내 ChatGPT·Claude·Gemini 계정에서 나가니, 각 사이트에서 월 사용 한도를 걸어 두는 걸 권해요."),
      note("📋 '복사·붙여넣기'는 키 없이 쓰는 방법이에요. 붙여 넣는 글에는 개인정보가 들어갈 수 있으니 쓰는 AI의 정책을 확인하세요."),
      note("🎙 '말로 답하기'는 브라우저(Chrome·Edge)의 음성 인식을 써요. 음성은 브라우저 제공사의 서버에서 글자로 바뀌어요."),
    );
    box.replaceChildren(wrap);
  }

  // ---------------------------------------------------------------- 서비스 카드
  function card(box, st, p) {
    const info = st.providers[p];
    const isActive = st.active === p;
    const d = el("details");
    d.style.cssText = "border:1px solid var(--line);border-radius:12px;padding:0 12px;margin:8px 0;background:var(--surface);";
    d.open = OPEN.has(p);

    const sum = el("summary");
    sum.style.cssText = "cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 0;";
    sum.append(el("b", { textContent: info.label }), el("span", { className: "note", textContent: info.vendor }));
    const sp = el("span"); sp.style.flex = "1";
    sum.append(sp);
    if (info.configured) {
      if (isActive) sum.append(chip("✔ 사용 중", "on"));
      sum.append(chip("연결됨 · …" + info.last4, "ok"));
    } else sum.append(chip("미연결"));
    d.append(sum);

    const body = el("div");
    body.style.cssText = "display:grid;gap:10px;padding:2px 0 12px;";
    const msg = note("", { id: "srvKeyMsg-" + p });
    msg.style.margin = "0";
    const fail = (e) => { msg.textContent = "⚠️ " + e.message; };

    if (!info.configured || REKEY.has(p)) {
      body.append(note(info.configured
        ? "새 키를 붙여 넣으면 이전 키는 바뀌어요."
        : `${info.label} API 키를 붙여 넣어 주세요. 키가 확인되면 쓸 수 있는 모델 목록을 자동으로 불러와요.`));
      const input = el("input", { type: "password", className: "ap-in", placeholder: info.hint, autocomplete: "off", spellcheck: false });
      input.style.cssText = "width:100%;max-width:340px;";
      const save = el("button", { type: "button", className: "mk-btn pri", textContent: "키 등록" });
      const go = async () => {
        const v = input.value.trim();
        if (!v) { msg.textContent = "키를 붙여 넣어 주세요."; return; }
        save.disabled = true; msg.textContent = "키를 확인하는 중…";
        try {
          await call(`/api/ai/providers/${p}/key`, "PUT", { api_key: v });
          input.value = ""; REKEY.delete(p); OPEN.add(p); delete MODELS[p];
          await render(box);
        } catch (e) { fail(e); save.disabled = false; }
      };
      save.onclick = go;
      input.onfocus = () => input.select(); // 틀린 키가 남아 있어도 새로 붙여 넣으면 통째로 바뀌게
      input.onkeydown = (ev) => { if (ev.key === "Enter") go(); };
      const row = el("div", { className: "links" }, input, save);
      if (info.configured) {
        const cancel = el("button", { type: "button", className: "ghost", textContent: "취소" });
        cancel.onclick = () => { REKEY.delete(p); render(box); };
        row.append(cancel);
      }
      body.append(row);
      body.append(el("div", { className: "note" }, "👉 ", el("a", { href: info.console, target: "_blank", rel: "noopener noreferrer", textContent: `키 만드는 곳(${info.vendor})` })));
    } else {
      // 사용할 서비스
      if (isActive) body.append(note("✔ 지금 이 AI로 답하고 있어요."));
      else {
        const use = el("button", { type: "button", className: "mk-btn pri", textContent: `${info.label}로 답하기` });
        use.onclick = async () => {
          use.disabled = true;
          try { await call("/api/ai/active", "PUT", { provider: p }); await render(box); } catch (e) { fail(e); use.disabled = false; }
        };
        body.append(el("div", { className: "links" }, use));
      }

      // 모델 선택
      const selDef = el("select"), selQuick = el("select");
      [selDef, selQuick].forEach((s) => { s.style.cssText = "font:inherit;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink);max-width:100%;"; });
      const lab = (t, s, hint) => {
        const w = el("label"); w.style.cssText = "display:grid;gap:4px;";
        w.append(el("span", { textContent: t, style: "font-weight:600;font-size:13px;" }), s);
        if (hint) w.append(el("span", { className: "note", textContent: hint }));
        return w;
      };
      const fill = (m) => {
        const opts = (cur, extra) => {
          const ids = m.models.slice();
          if (cur && !ids.includes(cur)) ids.unshift(cur);
          return [...(extra ? [extra] : []), ...ids.map((id) => [id, id + (m.models.includes(id) ? "" : " (목록에 없음)")])];
        };
        selDef.replaceChildren(...opts(m.model_default).map(([v, t]) => el("option", { value: v, textContent: t })));
        selQuick.replaceChildren(...opts(m.model_quick, ["", "기본 모델과 같게"]).map(([v, t]) => el("option", { value: v, textContent: t })));
        selDef.value = m.model_default; selQuick.value = m.model_quick || "";
        selDef.disabled = selQuick.disabled = false;
      };
      const load = async (refresh) => {
        selDef.disabled = selQuick.disabled = true;
        msg.textContent = refresh ? "모델 목록을 새로 받아 오는 중…" : "모델 목록을 불러오는 중…";
        selDef.replaceChildren(el("option", { textContent: info.model_default || "불러오는 중…" }));
        selQuick.replaceChildren(el("option", { textContent: info.model_quick || "불러오는 중…" }));
        try {
          MODELS[p] = await call(`/api/ai/providers/${p}/models` + (refresh ? "?refresh=true" : ""), "GET");
          fill(MODELS[p]);
          msg.textContent = `모델 ${MODELS[p].models.length}개를 불러왔어요.`;
        } catch (e) {
          msg.textContent = "⚠️ " + e.message + (e.code === "invalid_key" ? " ('키 바꾸기'로 다시 등록해 주세요)" : "");
        }
      };
      const saveModels = async () => {
        selDef.disabled = selQuick.disabled = true;
        try {
          await call(`/api/ai/providers/${p}/models`, "PUT", { model_default: selDef.value, model_quick: selQuick.value });
          if (MODELS[p]) { MODELS[p].model_default = selDef.value; MODELS[p].model_quick = selQuick.value; }
          msg.textContent = "✅ 모델을 저장했어요.";
        } catch (e) { fail(e); }
        selDef.disabled = selQuick.disabled = false;
      };
      selDef.onchange = selQuick.onchange = saveModels;
      body.append(
        lab("기본 모델", selDef, "상담사·서류 첨삭·모의면접처럼 깊이 생각해야 하는 일에 써요."),
        lab("빠른 모델", selQuick, "가벼운 분류·요약처럼 빨리 끝나는 일에 써요. 보통 더 싸요."),
      );
      if (MODELS[p]) fill(MODELS[p]);
      d.addEventListener("toggle", () => { if (d.open && !MODELS[p] && !d.dataset.loading) { d.dataset.loading = "1"; load(false); } });
      if (d.open && !MODELS[p]) load(false);

      const refresh = el("button", { type: "button", className: "ghost", textContent: "🔄 모델 목록 새로고침" });
      refresh.onclick = () => load(true);
      const rekey = el("button", { type: "button", className: "ghost", textContent: "키 바꾸기" });
      rekey.onclick = () => { REKEY.add(p); OPEN.add(p); render(box); };
      const del = el("button", { type: "button", className: "ghost", textContent: "키 지우기" });
      del.onclick = async () => {
        if (!confirm(`${info.label} 키를 지울까요? 서버에 저장된 키가 삭제돼요.`)) return;
        del.disabled = true;
        try { await call(`/api/ai/providers/${p}/key`, "DELETE"); delete MODELS[p]; OPEN.delete(p); await render(box); }
        catch (e) { fail(e); del.disabled = false; }
      };
      body.append(el("div", { className: "links" }, refresh, rekey, del));
    }

    body.append(msg);
    d.append(body);
    d.addEventListener("toggle", () => { if (d.open) OPEN.add(p); else OPEN.delete(p); });
    return d;
  }

  // 설정 화면은 열 때마다 다시 그려지므로, 빈 자리가 새로 나타날 때마다 채웁니다.
  const fill = () => {
    const box = document.getElementById("srvKeyBox");
    if (box && !box.dataset.ready) { box.dataset.ready = "1"; render(box); }
  };
  new MutationObserver(fill).observe(document.body, { childList: true, subtree: true });
  fill();
})();
