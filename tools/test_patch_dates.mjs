// patch_dates.mjs 확인: node tools/test_patch_dates.mjs   (web/board.js에 patch_newflag까지 적용된 상태에서 실행)
// 임시 복사본에 패치를 입혀 ①문법 ②두 번 적용해도 같음 ③신규 판정 ④정렬 묶음을 확인합니다. 원본은 건드리지 않습니다.
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path'; import { execFileSync } from 'node:child_process'; import vm from 'node:vm';
import * as acorn from 'acorn';
const src = process.argv[2] || 'web/board.js';
const tmp = path.join(os.tmpdir(), 'board_dates_test.js');
let ok = 0, fail = 0; const t = (n, c) => { c ? ok++ : (fail++, console.log('✗', n)); };
let base = fs.readFileSync(src, 'utf8');
if (base.includes('function dayOf(')) { base = null; }   // 이미 패치된 파일이면 그대로 검사
fs.writeFileSync(tmp, base ?? fs.readFileSync(src, 'utf8'));
if (base) execFileSync('node', ['tools/patch_dates.mjs', tmp]);
if (!fs.readFileSync(tmp, 'utf8').includes('NEW_REG_DAYS')) execFileSync('node', ['tools/patch_newrule.mjs', tmp]);
const once = fs.readFileSync(tmp, 'utf8');
t('두 번째 적용은 바뀌지 않음', (execFileSync('node', ['tools/patch_dates.mjs', tmp]), execFileSync('node', ['tools/patch_newrule.mjs', tmp]), fs.readFileSync(tmp, 'utf8') === once));
try { acorn.parse(once, { ecmaVersion: 'latest', sourceType: 'script' }); t('문법', true); } catch (e) { t('문법: ' + e.message, false); }

// 도우미 + newRecent만 꺼내서 실행
const a = once.indexOf('// \'YYYY-MM-DD\' 글자'), b = once.indexOf('\n}', once.indexOf('function newRecent(')) + 2;
const ctx = { nowKst: new Date(Date.UTC(2026, 10, 24)), today: Date.UTC(2026, 10, 24) };
vm.createContext(ctx);
vm.runInContext('const NEW_DAYS = 7;\n' + once.slice(a, b) + '\nthis.newRecent = newRecent; this.daysAgo = daysAgo; this.mdOf = mdOf; this.dayOf = dayOf;', ctx);
const N = r => ctx.newRecent(r);
t('등록일 오늘 → 신규', N({ '등록일': '2026-11-24' }));
t('등록일 3일 전 → 신규', N({ '등록일': '2026-11-21' }));
t('등록일 4일 전 → 신규 아님', !N({ '등록일': '2026-11-20' }));
t('등록일이 오래되면 최초 수집이 오늘이어도 신규 아님(밀린 공고)', !N({ '등록일': '2026-11-10', '최초 수집': '2026-11-24' }));
t('등록일 없고 정확한 최초 수집 3일 전 → 신규', N({ '최초 수집': '2026-11-21' }));
t('등록일 없고 정확한 최초 수집 4일 전 → 신규 아님', !N({ '최초 수집': '2026-11-20' }));
t('추정값이면 예전 규칙(구분 신규 + 최종 확인 7일)', N({ '최초 수집': '2026-11-24', '최초 수집 추정': true, '구분': '[신규·시트]', '최종 확인': '11/22' }));
t('추정값 + 구분 없음 → 신규 아님', !N({ '최초 수집': '2026-11-24', '최초 수집 추정': true, '구분': '', '최종 확인': '11/24' }));
t('날짜 칸이 아예 없으면 예전 규칙', N({ '구분': '신규', '최종 확인': '11/20' }) && !N({ '구분': '', '최종 확인': '11/20' }));
t('daysAgo', ctx.daysAgo('2026-11-20') === 4 && ctx.daysAgo('') === null && ctx.daysAgo('abc') === null);
t('mdOf', ctx.mdOf('2026-11-05') === '11/5' && ctx.mdOf('') === '');
t('정렬·묶음 코드가 들어감', /posted: \['📅 등록일순'/.test(once) && /seen: \['📥 최초 수집순'/.test(once) && /state\.sort === 'posted'/.test(once) && /state\.sort === 'seen'/.test(once));
t('신규 배지가 등록일을 보여 줌', /' 등록' : r\['최초 수집'\]/.test(once));
t('공고 정보에 두 칸', /<dt>등록일<\/dt>/.test(once) && /<dt>최초 수집<\/dt>/.test(once));
console.log(fail ? `✗ ${fail}개 실패 (통과 ${ok})` : `✓ 모두 통과 (${ok}개)`); process.exit(fail ? 1 : 0);
