// 신규 기준 변경: '등록일 3일 이내'가 신규입니다. (patch_dates.mjs 뒤에 실행. 여러 번 실행해도 안전)
//  1) '등록일'을 알면 등록일만 봄: 오늘부터 3일 이내 = 신규 (보드에 늦게 들어온 옛 공고가 신규로 뜨지 않음)
//  2) 등록일을 모르고 '최초 수집'이 정확한 값이면 그 날짜가 3일 이내일 때 신규
//  3) 그 외(추정값·날짜 없음)는 예전 규칙(구분에 신규 + 최종 확인 7일 이내)
// 신규 배지: 등록일을 알면 "신규 · 11/24 등록", 아니면 "신규 · 11/24 수집"(정확한 최초 수집일) 또는 "11/24 확인".
import fs from 'node:fs';
const file = process.argv[2] || 'web/board.js';
let s = fs.readFileSync(file, 'utf8');
if (s.includes('NEW_REG_DAYS')) { console.log('이미 적용됨 — 건너뜀'); process.exit(0); }
if (!s.includes('function dayOf(')) { console.error('✗ patch_dates.mjs를 먼저 적용하세요\n적용하지 않음'); process.exit(1); }
const bad = [], cnt = (x) => s.split(x).length - 1;
const rep = (name, from, to) => { if (cnt(from) !== 1) { bad.push(`${name}: 일치 ${cnt(from)}곳(필요 1곳)`); return; } s = s.split(from).join(to); };

rep('신규 판정', "function newRecent(r) {\n  const fa = daysAgo(r['최초 수집']);\n  if (fa !== null && !r['최초 수집 추정']) return fa >= -1 && fa <= NEW_DAYS;   // 정확한 최초 수집일이 있으면 그것만 봄",
"const NEW_REG_DAYS = 3;   // 등록일(또는 정확한 최초 수집일)이 오늘부터 이 일수 이내면 신규\nfunction newRecent(r) {\n  const pa = daysAgo(r['등록일']);\n  if (pa !== null) return pa >= -1 && pa <= NEW_REG_DAYS;   // 등록일을 알면 등록일만 봄\n  const fa = daysAgo(r['최초 수집']);\n  if (fa !== null && !r['최초 수집 추정']) return fa >= -1 && fa <= NEW_REG_DAYS;   // 등록일을 모르면 정확한 최초 수집일을 봄");
rep('신규 배지', "신규 · ${r['최초 수집'] && !r['최초 수집 추정'] ? esc(mdOf(r['최초 수집'])) + ' 수집' : esc(r['최종 확인']) + ' 확인'}",
"신규 · ${r['등록일'] ? esc(mdOf(r['등록일'])) + ' 등록' : r['최초 수집'] && !r['최초 수집 추정'] ? esc(mdOf(r['최초 수집'])) + ' 수집' : esc(r['최종 확인']) + ' 확인'}");
if (bad.length) { console.error('✗ ' + bad.join('\n✗ ') + '\n적용하지 않음'); process.exit(1); }
fs.writeFileSync(file, s); console.log('✓ 신규 기준: 등록일 3일 이내');
