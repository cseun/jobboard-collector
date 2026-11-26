// patch_collectlog.mjs 확인: node tools/test_patch_collectlog.mjs
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path'; import { execFileSync } from 'node:child_process'; import vm from 'node:vm';
import * as acorn from 'acorn';
const src = process.argv[2] || 'web/board.js';
const tmp = path.join(os.tmpdir(), 'board_collectlog_test.js');
let ok = 0, fail = 0; const t = (n, c) => { c ? ok++ : (fail++, console.log('✗', n)); };
fs.writeFileSync(tmp, fs.readFileSync(src, 'utf8'));
if (!fs.readFileSync(tmp, 'utf8').includes('function loadColLog(')) execFileSync('node', ['tools/patch_collectlog.mjs', tmp]);
const once = fs.readFileSync(tmp, 'utf8');
t('두 번째 적용은 바뀌지 않음', (execFileSync('node', ['tools/patch_collectlog.mjs', tmp]), fs.readFileSync(tmp, 'utf8') === once));
try { acorn.parse(once, { ecmaVersion: 'latest', sourceType: 'script' }); t('문법', true); } catch (e) { t('문법: ' + e.message, false); }
t('설정 화면에 칸과 호출', /id="colLog" hidden/.test(once) && /loadColLog\(\);\n\s+document\.querySelectorAll\('input\[name=aiMode\]'\)/.test(once));

const a = once.indexOf('async function loadColLog('), b = once.indexOf('\nfunction vSet() {');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const run = async (resp) => {
  const box = { hidden: true, innerHTML: '' };
  const ctx = { esc, document: { getElementById: id => id === 'colLog' ? box : null }, fetch: async () => resp };
  vm.createContext(ctx); vm.runInContext(once.slice(a, b) + '\nthis.loadColLog = loadColLog;', ctx); await ctx.loadColLog(); return box;
};
const item = (o) => ({ day: '2026-11-25', mode: '저장', status: '완료', blocked: null, requests: 150, listed: 900, new_found: 80, skipped: 12, saved_new: 80, updated: 3, detail_ok: 78, detail_requests: 80, image_postings: 6, ai_reads: 5, ai_model: 'claude-sonnet-5-5', input_tokens: 41000, output_tokens: 5200, cost_usd: 0.134, ...o });
const ok1 = await run({ ok: true, json: async () => ({ items: [item({}), item({ day: '2026-11-24', status: '차단으로 중단', blocked: 'HTTP 429', cost_usd: 0 }), item({ mode: '미리보기', ai_model: null })] }) });
t('관리자: 표가 보임', ok1.hidden === false && /수집 기록/.test(ok1.innerHTML) && /11\/25/.test(ok1.innerHTML) && /claude-sonnet-5-5/.test(ok1.innerHTML) && /41,000\/5,200/.test(ok1.innerHTML) && /\$0\.134/.test(ok1.innerHTML));
t('차단 실행은 ⛔ 와 붉은 줄', /⛔/.test(ok1.innerHTML) && /color:var\(--hot\)/.test(ok1.innerHTML));
t('실행 합계(미리보기 제외, 빈칸 채우기 포함)', /새 공고 160/.test(ok1.innerHTML));
const noRows = await run({ ok: true, json: async () => ({ items: [] }) });
t('기록이 없으면 안내 문구', noRows.hidden === false && /아직 수집 기록이 없어요/.test(noRows.innerHTML));
const forbidden = await run({ ok: false, status: 403 });
t('관리자가 아니면(403) 칸을 숨김', forbidden.hidden === true && forbidden.innerHTML === '');
const evil = await run({ ok: true, json: async () => ({ items: [item({ status: '<img onerror=x>' })] }) });
t('값에 들어 있는 HTML은 막음', !/<img onerror/.test(evil.innerHTML));
console.log(fail ? `✗ ${fail}개 실패 (통과 ${ok})` : `✓ 모두 통과 (${ok}개)`); process.exit(fail ? 1 : 0);
