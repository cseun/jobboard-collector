// 확인: node tools/test_patch_multisel.mjs — 패치가 적용되고, 두 번 실행해도 같고, 다중 선택 동작이 맞는지
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path'; import { execFileSync } from 'node:child_process';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ms-')); const f = path.join(dir, 'board.js');
const stub = [
  "const catsOf = j => j.r['분야'] || ['미분류'], rolesOf = j => j.r['직무 분류'] || ['미분류'];",
  "const catN = {}, roleN = {}; base.filter(j => !state.role || rolesOf(j).includes(state.role)).forEach(1); base.filter(j => !state.cat || catsOf(j).includes(state.cat)).forEach(2);",
  "const cats = (DATA.catOrder || []).concat(['미분류']).filter(c => catN[c] || c === state.cat), roles = (DATA.roleOrder || []).concat(['미분류']).filter(c => roleN[c] || c === state.role);",
  "const list = base.filter(j => careerOk(j) && (!state.cat || catsOf(j).includes(state.cat)) && (!state.role || rolesOf(j).includes(state.role)));",
  "<select id=\"role\" aria-label=\"직무\">${['', ...roles].map(x => `<option value=\"${esc(x)}\" ${x === state.role ? 'selected' : ''}>${x ? `${esc(x)} (${roleN[x] || 0})` : '🧩 직무 전체'}</option>`).join('')}</select>",
  "<select id=\"cat\" aria-label=\"회사 분야\">${['', ...cats].map(x => `<option value=\"${esc(x)}\" ${x === state.cat ? 'selected' : ''}>${x ? `${esc(x)} (${catN[x] || 0})` : '🏢 회사 분야 전체'}</option>`).join('')}</select>",
  "$('#cat').onchange = e => { state.cat = e.target.value; store.set('cat', state.cat); vDomestic(); };",
  "$('#role').onchange = e => { state.role = e.target.value; store.set('role', state.role); vDomestic(); };", ""].join('\n');
fs.writeFileSync(f, stub);
const run = () => execFileSync('node', ['tools/patch_multisel.mjs', f]).toString();
if (!run().includes('✓')) throw new Error('1차 적용 실패');
if (!run().includes('이미 적용됨')) throw new Error('2차는 건너뛰어야 함');
const src = fs.readFileSync(f, 'utf8');
const a = src.indexOf('const selHas'), b = src.indexOf('\n  };', src.indexOf('const msBind')) + 5;
const store = { v: {}, set(k, v) { this.v[k] = v; } }, state = { cat: '', role: '' };
const checks = {};
const doc = { getElementById: () => ({}), head: { insertAdjacentHTML() {} }, querySelector: () => null };
const mk = new Function('document', 'window', 'esc', 'state', 'store', 'vDomestic', src.slice(a, b) + '; return { selHas, selOn, msHtml };');
const { selHas, selOn, msHtml } = mk(doc, {}, x => x, state, store, () => {});
const ok = selHas(['A', 'B'], '') && selHas(['A'], 'A|C') && selHas(['C'], 'A|C') && !selHas(['B'], 'A|C') && selOn('A|C', 'C') && !selOn('A|C', 'B');
if (!ok) throw new Error('selHas/selOn 동작 오류');
const html = msHtml('role', '🧩 직무', ['필드엔지니어', 'AI'], 'AI', { AI: 3 });
if (!html.includes('data-ms="role"') || !html.includes('value="AI" checked') || !html.includes('<b class="f-n">1</b>') || html.includes('value="필드엔지니어" checked')) throw new Error('msHtml 출력 오류');
console.log('✓ patch_multisel 확인 통과');
