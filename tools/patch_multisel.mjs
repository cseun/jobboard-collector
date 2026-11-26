// 국내 공고 필터의 '🏢 회사 분야'와 '🧩 직무'를 여러 개 선택할 수 있게 바꿉니다(체크박스 목록, 고른 것 중 하나라도 맞으면 보임).
// 저장 형식: 기존 문자열 그대로 '|'로 이어 붙임(예: 'AI·LLM·AX|로봇 SW') → 다른 화면의 단일 선택(state.role='...')과 호환.
// 직무 목록에 '필드엔지니어'(판정 '필' 공고를 묶는 직무)를 추가합니다.
// 사용: node tools/patch_multisel.mjs web/board.js   (여러 번 실행해도 안전)
import fs from 'node:fs';
const file = process.argv[2] || 'web/board.js';
let s = fs.readFileSync(file, 'utf8');
if (s.includes('const msHtml = ')) { console.log('이미 적용됨 — 건너뜀'); process.exit(0); }
const bad = [], cnt = (x) => s.split(x).length - 1;
const rep = (name, from, to, times = 1) => {
  if (cnt(from) !== times) { bad.push(`${name}: 일치 ${cnt(from)}곳(필요 ${times}곳)`); return; }
  s = s.split(from).join(to);
};
const helpers = String.raw`
  const selHas = (arr, sel) => !sel || String(sel).split('|').some(v => v && arr.includes(v));
  const selOn = (sel, v) => String(sel || '').split('|').includes(v);
  const msHtml = (id, label, opts, sel, cnt) => {
    if (!document.getElementById('msel-css')) document.head.insertAdjacentHTML('beforeend', '<style id="msel-css">.msel{position:relative;display:inline-block}.msel summary{cursor:pointer;list-style:none;font:inherit;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--ink);white-space:nowrap}.msel summary::-webkit-details-marker{display:none}.msel[open] summary{border-color:#3b82f6}.msel-box{position:absolute;z-index:40;top:calc(100% + 4px);left:0;min-width:240px;max-height:340px;overflow:auto;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:6px;box-shadow:0 6px 18px rgba(0,0,0,.18)}.msel-box label{display:flex;gap:8px;align-items:center;padding:5px 8px;border-radius:6px;cursor:pointer;white-space:nowrap}.msel-box label:hover{background:var(--bg)}.msel-x{margin:4px 6px 2px}</style>');
    const S = String(sel || '').split('|').filter(Boolean), open = (window.__msOpen || {})[id];
    return '<details class="msel" data-ms="' + id + '" ' + (open ? 'open' : '') + '><summary>' + label + (S.length ? ' <b class="f-n">' + S.length + '</b>' : ' 전체') + ' ▾</summary><div class="msel-box">'
      + opts.map(x => '<label><input type="checkbox" value="' + esc(x) + '" ' + (S.includes(x) ? 'checked' : '') + '> ' + esc(x) + ' <span class="note">(' + ((cnt && cnt[x]) || 0) + ')</span></label>').join('')
      + (S.length ? '<button type="button" class="chip-toggle msel-x" data-msx="' + id + '">✕ 선택 해제</button>' : '') + '</div></details>';
  };
  const msBind = (id, key) => {
    const d = document.querySelector('details[data-ms="' + id + '"]'); if (!d) return;
    window.__msOpen = window.__msOpen || {};
    d.addEventListener('toggle', () => { window.__msOpen[id] = d.open; });
    d.querySelectorAll('input[type=checkbox]').forEach(cb => cb.onchange = () => { state[key] = [...d.querySelectorAll('input:checked')].map(i => i.value).join('|'); store.set(key, state[key]); window.__msOpen[id] = true; vDomestic(); });
    const x = d.querySelector('[data-msx]'); if (x) x.onclick = () => { state[key] = ''; store.set(key, ''); window.__msOpen[id] = true; vDomestic(); };
  };`;
rep('도우미', "const catsOf = j => j.r['분야'] || ['미분류'], rolesOf = j => j.r['직무 분류'] || ['미분류'];", "const catsOf = j => j.r['분야'] || ['미분류'], rolesOf = j => j.r['직무 분류'] || ['미분류'];" + helpers);
rep('분야 개수', "base.filter(j => !state.role || rolesOf(j).includes(state.role))", "base.filter(j => selHas(rolesOf(j), state.role))");
rep('직무 개수', "base.filter(j => !state.cat || catsOf(j).includes(state.cat))", "base.filter(j => selHas(catsOf(j), state.cat))");
rep('목록 조건', "(!state.cat || catsOf(j).includes(state.cat)) && (!state.role || rolesOf(j).includes(state.role))", "selHas(catsOf(j), state.cat) && selHas(rolesOf(j), state.role)");
rep('목록 순서', "const cats = (DATA.catOrder || []).concat(['미분류']).filter(c => catN[c] || c === state.cat), roles = (DATA.roleOrder || []).concat(['미분류']).filter(c => roleN[c] || c === state.role);",
  "const cats = (DATA.catOrder || []).concat(['미분류']).filter(c => catN[c] || selOn(state.cat, c)), roles = (DATA.roleOrder || []).filter(c => c !== '필드엔지니어').concat(['필드엔지니어', '미분류']).filter(c => roleN[c] || selOn(state.role, c));");
rep('직무 선택칸', "<select id=\"role\" aria-label=\"직무\">${['', ...roles].map(x => `<option value=\"${esc(x)}\" ${x === state.role ? 'selected' : ''}>${x ? `${esc(x)} (${roleN[x] || 0})` : '🧩 직무 전체'}</option>`).join('')}</select>", "${msHtml('role', '🧩 직무', roles, state.role, roleN)}");
rep('분야 선택칸', "<select id=\"cat\" aria-label=\"회사 분야\">${['', ...cats].map(x => `<option value=\"${esc(x)}\" ${x === state.cat ? 'selected' : ''}>${x ? `${esc(x)} (${catN[x] || 0})` : '🏢 회사 분야 전체'}</option>`).join('')}</select>", "${msHtml('cat', '🏢 회사 분야', cats, state.cat, catN)}");
rep('분야 이벤트', "$('#cat').onchange = e => { state.cat = e.target.value; store.set('cat', state.cat); vDomestic(); };", "msBind('cat', 'cat');");
rep('직무 이벤트', "$('#role').onchange = e => { state.role = e.target.value; store.set('role', state.role); vDomestic(); };", "msBind('role', 'role');");
if (bad.length) { console.error('✗ ' + bad.join('\n✗ ') + '\n적용하지 않음'); process.exit(1); }
fs.writeFileSync(file, s); console.log('✓ 회사 분야·직무 다중 선택 적용');
