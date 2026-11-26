// 확인: node tools/test_patch_irrelevant.mjs   — 패치가 적용되고, 두 번 실행해도 같고, isIrrel 동작이 맞는지
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path'; import { execFileSync } from 'node:child_process';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'irr-')); const f = path.join(dir, 'board.js');
fs.writeFileSync(f, "const isHid = j => HID.has(hidKey(j)) || isHidCo(j.company);\nX('<b>🚫 숨긴 회사·제외한 공고만 보고 있어요</b><span class=\"note\">카드를 열어 ...');\n");
const run = () => execFileSync('node', ['tools/patch_irrelevant.mjs', f]).toString();
if (!run().includes('✓')) throw new Error('1차 적용 실패');
if (!run().includes('이미 적용됨')) throw new Error('2차는 건너뛰어야 함');
const src = fs.readFileSync(f, 'utf8');
const isIrrel = new Function(src.match(/function isIrrel\(j\) \{[^}]*\}/)[0] + '; return isIrrel;')();
const ok = isIrrel({ r: { '관련도': '×' } }) && !isIrrel({ r: { '관련도': '○' } }) && !isIrrel({ r: {} }) && !isIrrel({}) && !isIrrel(null);
if (!ok) throw new Error('isIrrel 동작 오류');
if (!src.includes('|| isIrrel(j)') || !src.includes('제목 판정 ×')) throw new Error('치환 누락');
console.log('✓ patch_irrelevant 확인 통과');
