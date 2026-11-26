/* board.html이 처음 실행하는 스크립트: 로그인 확인 → 호환 계층 → 공고 데이터 → (관리자면) 비공개 코드 → 보드 코드 */
(async function () {
  const load = (src, optional) => new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = ok;
    s.onerror = () => (optional ? ok() : fail(new Error(src + " 를 불러오지 못했습니다.")));
    document.body.appendChild(s);
  });
  const say = (msg) => { const d = document.createElement("div"); d.style.cssText = "padding:24px;font:14px system-ui"; d.textContent = msg; document.body.prepend(d); };

  try {
    const meRes = await fetch("/api/me", { credentials: "same-origin" });
    if (!meRes.ok) { location.replace("/"); return; }
    window.__ME__ = await meRes.json();

    const dataRes = await fetch("/api/board-data", { credentials: "same-origin" });
    if (!dataRes.ok) throw new Error("공고 데이터를 불러오지 못했습니다(" + dataRes.status + ").");
    window.__BOARD_DATA__ = await dataRes.json();

    await load("/claude-shim.js");
    if (window.__ME__.is_admin) await load("/api/private/board.js", true); // 없으면 건너뜀
    await load("/board.js");
    await load("/ai-key-ui.js", true); // 설정 화면의 키 등록 칸
    addBar(window.__ME__);
  } catch (e) {
    say("보드를 열지 못했습니다: " + e.message);
  }

  function addBar(me) {
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:99999;display:flex;gap:8px;align-items:center;" +
      "padding:6px 10px;border:1px solid #8886;border-radius:999px;background:#fffd;font:12px system-ui;color:#333;backdrop-filter:blur(4px)";
    const who = document.createElement("span"); who.style.opacity = ".7";
    who.textContent = me.is_demo ? "👀 데모 계정(저장한 내용은 곧 지워져요)" : me.email;
    bar.append(who);
    if (me.is_admin) {
      const a = document.createElement("a"); a.href = "/admin.html"; a.textContent = "관리"; a.style.color = "inherit";
      bar.append(a);
    }
    const out = document.createElement("button");
    out.textContent = "로그아웃"; out.style.cssText = "font:inherit;border:0;background:none;cursor:pointer;color:inherit;text-decoration:underline";
    out.onclick = async () => { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); location.replace("/"); };
    bar.append(out);
    document.body.append(bar);
  }
})();
