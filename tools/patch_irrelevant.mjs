// 관련 없는 공고 숨김 패치: 공고의 '관련도'가 ×(AI 제목 판정)이면 목록·추천·대시보드에서 자동으로 숨기고, 기존 '🚫 숨긴 공고' 보기에서 모아 봅니다.
// 지우지 않으므로 언제든 되돌릴 수 있어요(서버에서 관련도를 고치거나 judge_existing --redo). 지우려면 scripts.cleanup_excluded --x.
// 사용: node tools/patch_irrelevant.mjs web/board.js   (다른 patch 뒤에 실행. 여러 번 실행해도 안전)
import fs from 'node:fs';
const file = process.argv[2] || 'web/board.js';
let s = fs.readFileSync(file, 'utf8');
if (s.includes('function isIrrel(')) { console.log('이미 적용됨 — 건너뜀'); process.exit(0); }
const bad = [], cnt = (x) => s.split(x).length - 1;
const rep = (name, from, to, times = 1) => {
  if (cnt(from) !== times) { bad.push(`${name}: 일치 ${cnt(from)}곳(필요 ${times}곳)`); return; }
  s = s.split(from).join(to);
};
rep('숨김 판정', "const isHid = j => HID.has(hidKey(j)) || isHidCo(j.company);",
  "function isIrrel(j) { return !!(j && j.r && j.r['관련도'] === '×'); }   // AI 제목 판정이 '관련 없음'\nconst isHid = j => HID.has(hidKey(j)) || isHidCo(j.company) || isIrrel(j);");
rep('숨김 안내', "<b>🚫 숨긴 회사·제외한 공고만 보고 있어요</b><span class=\"note\">카드를 열어",
  "<b>🚫 숨긴 회사·제외한 공고만 보고 있어요</b><span class=\"note\">관련 없다고 AI가 판정한 공고(제목 판정 ×)도 여기에 모여 있어요. 카드를 열어");
if (bad.length) { console.error('✗ ' + bad.join('\n✗ ') + '\n적용하지 않음'); process.exit(1); }
fs.writeFileSync(file, s); console.log('✓ 관련 없는 공고(관련도 ×) 자동 숨김 적용');
