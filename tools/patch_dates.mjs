// 공고 날짜 패치: '등록일'(사이트에 올라온 날)·'최초 수집'(보드가 처음 받은 날)을 공고 정보에 보여 주고,
// 정렬에 '등록일순'·'최초 수집순'을 넣고, 신규 기준을 '최초 수집 7일 이내'로 바꿉니다.
// 사용: node tools/patch_dates.mjs web/board.js   (patch_newflag.mjs 뒤에 실행. 여러 번 실행해도 안전)
//
// 신규 기준
//  - '최초 수집'이 정확한 값이면: 그 날짜가 오늘부터 7일 이내면 신규 ('구분' 글자는 보지 않음)
//  - '최초 수집'이 추정값('최초 수집 추정')이거나 없으면: 예전 규칙 그대로('구분'에 신규 + 최종 확인 7일 이내)
import fs from 'node:fs';
const file = process.argv[2] || 'web/board.js';
let s = fs.readFileSync(file, 'utf8');
if (s.includes('function dayOf(')) { console.log('이미 적용됨 — 건너뜀'); process.exit(0); }
if (!s.includes('function newRecent(')) { console.error('✗ patch_newflag.mjs를 먼저 적용하세요\n적용하지 않음'); process.exit(1); }
const bad = [], cnt = (x) => s.split(x).length - 1;
const rep = (name, from, to, times = 1) => {
  if (cnt(from) !== times) { bad.push(`${name}: 일치 ${cnt(from)}곳(필요 ${times}곳)`); return; }
  s = s.split(from).join(to);
};

// 1) 도우미 + 신규 판정 교체
rep('신규 판정 도입부', "function newRecent(r) {\n  if (!/신규/.test(r['구분'] || '')) return false;",
String.raw`// 'YYYY-MM-DD' 글자 → 그날 0시(UTC 기준 숫자). 읽을 수 없으면 null
function dayOf(v) { const k = String(v ?? '').match(/(\d{4})-(\d{1,2})-(\d{1,2})/); return k ? Date.UTC(+k[1], +k[2] - 1, +k[3]) : null; }
// 오늘(한국)부터 며칠 전인지. 읽을 수 없으면 null
function daysAgo(v) { const t = dayOf(v); return t === null ? null : Math.round((today - t) / 864e5); }
const mdOf = v => { const t = dayOf(v); if (t === null) return ''; const d = new Date(t); return (d.getUTCMonth() + 1) + '/' + d.getUTCDate(); };
function newRecent(r) {
  const fa = daysAgo(r['최초 수집']);
  if (fa !== null && !r['최초 수집 추정']) return fa >= -1 && fa <= NEW_DAYS;   // 정확한 최초 수집일이 있으면 그것만 봄
  if (!/신규/.test(r['구분'] || '')) return false;`);

// 2) 공고 모델에 날짜 숫자 추가
rep('공고 모델', "isNew: newRecent(r), listOnly: false,", "isNew: newRecent(r), posted: dayOf(r['등록일']), seen: dayOf(r['최초 수집']), listOnly: false,");

// 3) 신규 배지: 정확한 최초 수집일이면 '수집' 날짜를 보여 줌
rep('신규 배지', "신규 · ${esc(r['최종 확인'])} 확인</span>` : ''}${j.listOnly",
    "신규 · ${r['최초 수집'] && !r['최초 수집 추정'] ? esc(mdOf(r['최초 수집'])) + ' 수집' : esc(r['최종 확인']) + ' 확인'}</span>` : ''}${j.listOnly");

// 4) 공고 정보에 두 칸 추가
rep('공고 정보', "      ${r['상세 주소'] ? `<dt>주소</dt><dd>${esc(plain(r['상세 주소']))}</dd>` : ''}\n    </dl></div></details>`;",
"      ${r['상세 주소'] ? `<dt>주소</dt><dd>${esc(plain(r['상세 주소']))}</dd>` : ''}\n" +
"      <dt>등록일</dt><dd>${r['등록일'] ? esc(r['등록일']) + (daysAgo(r['등록일']) !== null ? ` <span class=\"note\">(${daysAgo(r['등록일'])}일 전)</span>` : '') : '<span class=\"note\">미확인 · 사이트에서 읽어 오면 채워져요</span>'}</dd>\n" +
"      <dt>최초 수집</dt><dd>${r['최초 수집'] ? esc(r['최초 수집']) + (r['최초 수집 추정'] ? ' <span class=\"note\">(추정 · 최종 확인일 기준)</span>' : '') : '<span class=\"note\">미확인</span>'}</dd>\n" +
"    </dl></div></details>`;");

// 5) 정렬 2개 추가 (국내 공고)
const byDateDesc = (k) => `(a, b) => (b.${k} ?? -1) - (a.${k} ?? -1) || byDue(a, b)`;
rep('정렬 목록', "new: ['신규순', (a, b) => (b.isNew - a.isNew) || byDue(a, b)], sal:",
  `new: ['신규순', (a, b) => (b.isNew - a.isNew) || byDue(a, b)], posted: ['📅 등록일순', ${byDateDesc('posted')}], seen: ['📥 최초 수집순', ${byDateDesc('seen')}], sal:`);
const grp = (k, label) => `[['오늘 ${label}', j => j.${k} === today], ['최근 7일', j => j.${k} !== null && j.${k} < today && today - j.${k} <= 7 * 864e5], ['최근 30일', j => j.${k} !== null && today - j.${k} > 7 * 864e5 && today - j.${k} <= 30 * 864e5], ['30일 넘음', j => j.${k} !== null && today - j.${k} > 30 * 864e5], ['${label} 날짜 미확인', j => j.${k} === null]]`;
rep('정렬 묶음', "  else if (state.sort === 'new') groups = [['이번에 새로 찾은 공고', j => j.isNew], ['이전부터 있던 공고', j => !j.isNew]];",
  "  else if (state.sort === 'new') groups = [['이번에 새로 찾은 공고', j => j.isNew], ['이전부터 있던 공고', j => !j.isNew]];\n" +
  `  else if (state.sort === 'posted') groups = ${grp('posted', '등록')};\n  else if (state.sort === 'seen') groups = ${grp('seen', '수집')};`);

if (bad.length) { console.error('✗ ' + bad.join('\n✗ ') + '\n적용하지 않음'); process.exit(1); }
fs.writeFileSync(file, s); console.log('✓ 등록일·최초 수집 표시, 정렬 2개, 신규 기준(최초 수집 7일) 적용');
