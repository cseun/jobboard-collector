// 설정 화면에 '📒 수집 기록'(최근 수집기 실행 표)을 넣습니다. 관리자 계정에만 보입니다(서버가 관리자만 허용).
// 사용: node tools/patch_collectlog.mjs web/board.js   (patch_newrule.mjs 뒤. 여러 번 실행해도 안전)
import fs from 'node:fs';
const file = process.argv[2] || 'web/board.js';
let s = fs.readFileSync(file, 'utf8');
if (s.includes('function loadColLog(')) { console.log('이미 적용됨 — 건너뜀'); process.exit(0); }
const bad = [], cnt = (x) => s.split(x).length - 1;
const rep = (name, from, to) => { if (cnt(from) !== 1) { bad.push(`${name}: 일치 ${cnt(from)}곳(필요 1곳)`); return; } s = s.split(from).join(to); };

const fn = String.raw`// 수집 기록: 관리자만 받아 올 수 있어요(일반 계정은 403이라 칸을 숨김)
async function loadColLog() {
  const box = document.getElementById('colLog'); if (!box) return;
  let items;
  try { const r = await fetch('/api/admin/collect-runs?limit=15', { credentials: 'same-origin' }); if (!r.ok) return; items = (await r.json()).items || []; } catch { return; }
  box.hidden = false;
  const n = v => (v ?? 0).toLocaleString('ko-KR'), md = v => String(v || '').slice(5).replace('-', '/');
  const done = items.filter(r => r.mode !== '미리보기');
  const sum = k => done.slice(0, 7).reduce((a, r) => a + (r[k] || 0), 0);
  const stBad = r => r.status !== '완료';
  box.innerHTML = '<div class="top"><b>📒 수집 기록</b><span class="note">최근 ' + items.length + '번 · 실행(미리보기 제외) 최근 7번 합계: 새 공고 ' + n(sum('saved_new')) + ' · AI ' + n(sum('ai_reads')) + '건 · 토큰 ' + n(sum('input_tokens')) + '/' + n(sum('output_tokens')) + ' · $' + sum('cost_usd').toFixed(2) + '</span></div>' +
    (items.length ? '<div style="overflow-x:auto"><table class="note" style="border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap"><thead><tr style="text-align:left"><th>날짜</th><th>모드·상태</th><th>요청</th><th>목록</th><th>새 공고</th><th>건너뜀</th><th>저장</th><th>상세</th><th>이미지·AI</th><th>모델</th><th>토큰 입/출</th><th>비용</th></tr></thead><tbody>' +
      items.map(r => '<tr style="border-top:1px solid var(--line,#ddd)' + (stBad(r) ? ';color:var(--hot)' : '') + '"><td>' + esc(md(r.day)) + '</td><td>' + esc(r.mode) + '·' + esc(r.status) + (r.blocked ? ' ⛔' : '') + '</td><td>' + n(r.requests) + '</td><td>' + n(r.listed) + '</td><td>' + n(r.new_found) + '</td><td>' + n(r.skipped) + '</td><td>' + n(r.saved_new) + '/' + n(r.updated) + '</td><td>' + n(r.detail_ok) + '/' + n(r.detail_requests) + '</td><td>' + n(r.image_postings) + '·' + n(r.ai_reads) + '</td><td>' + esc(r.ai_model || '—') + '</td><td>' + n(r.input_tokens) + '/' + n(r.output_tokens) + '</td><td>' + (r.cost_usd ? '$' + r.cost_usd.toFixed(3) : '—') + '</td></tr>').join('') +
      '</tbody></table></div><p class="note" style="margin:0">저장 칸은 새 공고/갱신, 상세 칸은 성공/요청 수예요. 붉은 줄은 차단·오류로 멈춘 실행이에요. 자세한 내용(키워드별 결과)은 서버에서 <code>--report</code>로 봐요.</p>'
      : '<p class="note" style="margin:0">아직 수집 기록이 없어요. 서버에서 <code>python -m scripts.collect</code>를 실행하면 쌓여요.</p>');
}
`;
rep('함수 넣기', "function vSet() {\n", fn + "function vSet() {\n");
rep('칸 넣기', "에서 확인해요.</p></section>`;\n  document.querySelectorAll('input[name=aiMode]').forEach(",
    "에서 확인해요.</p></section>\n  <section class=\"row\" id=\"colLog\" hidden></section>`;\n  loadColLog();\n  document.querySelectorAll('input[name=aiMode]').forEach(");
if (bad.length) { console.error('✗ ' + bad.join('\n✗ ') + '\n적용하지 않음'); process.exit(1); }
fs.writeFileSync(file, s); console.log('✓ 설정 화면에 수집 기록 표 추가');
