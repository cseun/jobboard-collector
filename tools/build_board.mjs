// 사용법: node tools/build_board.mjs <원본 보드.html> [출력 루트(기본: 저장소 루트)]
//
// 원본 보드(HTML 한 파일)를 서버용으로 나눕니다.
//   web/board.html            화면 뼈대 + 부트스트랩 (개인정보 없음)
//   web/board.js              보드 코드. 지원자 본인 정보가 박힌 부분은 비어 있거나 일반 값으로 대체 (모든 로그인 사용자에게 내려감)
//   private/board_private.js  지원자 본인 정보(관리자에게만 내려감). 저장소에 올리지 마세요(.gitignore).
//
// 원본에 개인 정보가 있는 최상위 선언은 PRIVATE 목록에 적습니다. 새로 생기면 여기에 추가하세요.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import * as acorn from "acorn";

const here = path.dirname(fileURLToPath(import.meta.url));
const [, , srcPath, outArg] = process.argv;
if (!srcPath) { console.error("사용법: node tools/build_board.mjs <원본 보드.html> [출력 루트]"); process.exit(1); }
const root = outArg ? path.resolve(outArg) : path.resolve(here, "..");

// 이름 → 로그인하지 않은(=관리자가 아닌) 사용자에게 보여 줄 일반 값
const PRIVATE = {
  MOCK: "{ companies: {} }",
  PROFILE: "[]",
  SKILLS: "[]",
  TD_FOCUS: "new Proxy({}, { get: (_, k) => (typeof k === 'string' && /^\\d+$/.test(k) ? [['오늘의 포커스', '직접 정한 할 일을 적어 보세요']] : undefined) })",
  FINAL1124: "{ career: {}, oneLine: [], sojae: [], tech: [], files: [] }",
  TD_WEEKS: "[]",
  TD_READ: "{}",
  TD_SAT: "{}",
  PJ_RB: "[]",
  MYF_SLOTS: `[
  { k: '이력서', file: '이력서.pdf', note: '', re: /이력서/ },
  { k: '경력기술서', file: '경력기술서.pdf', note: '', re: /경력\\s*기술/ },
  { k: '포트폴리오', file: '포트폴리오.pdf', note: '', re: /포트폴리오|portfolio/i },
  { k: '수료증·커리큘럼', file: '수료증, 교육과정 커리큘럼', note: '과정마다 여러 개 가능', re: /수료|커리큘럼|교육과정|이수증/, multi: true },
  { k: '자격·어학 증빙', file: '자격증, 어학 성적표', note: '여러 개 가능', re: /자격|어학|오픽|opic|토익|toeic|sqld|정보처리/i, multi: true },
  { k: '성적증명서', file: '성적증명서.pdf', note: '', re: /성적/ },
  { k: '제출한 자소서', file: '회사별 파일', note: '회사별로 정리 — 여러 개 올려도 돼요', re: /자소서|자기소개|cover/i, multi: true }
]`,
};
// 초기값이 보드의 다른 도우미(함수)를 쓰는 경우: 비공개 파일에서는 그 도우미를 인자로 받는 함수로 보냅니다.
const DEPS = { TD_READ: ["CH"] };
// 코드 안의 문자열 조각 치환(선언 단위로 나누기 어려운 곳)
const TEXT_FIXES = [
  // 비공개 자료가 비어 있는 일반 사용자 화면에서 오류가 나지 않게 하는 보정
  ["if (day < 0 || t > TD_ANCHOR + 45 * 864e5) return { day, wk, dow, items };",
   "if (!TD_WEEKS.length || day < 0 || t > TD_ANCHOR + 45 * 864e5) return { day, wk, dow, items };"],
  ["function vMock0(){\n  if (MS.mode === 'scen') { vScen(); return; }",
   "function vMock0(){\n  if (!Object.keys(MOCK.companies).length) { $('#view').innerHTML = '<div class=\"card\" style=\"padding:16px\"><b>모의면접 자료가 아직 없어요.</b><p>이 계정에는 회사별 모의면접 자료가 준비되어 있지 않습니다.</p></div>'; return; }\n  if (MS.mode === 'scen') { vScen(); return; }"],
  // 3단계: 설정 화면의 'AI 연결' — 'Claude' 모드를 '내 API 키(ChatGPT·Claude·Gemini)' 방식으로 바꾸고, 사용 불가로 막아 둔 키 칸 자리에 키 등록 칸(ai-key-ui.js가 채움)을 둔다
  ["보드를 연 사람의 Claude 사용량으로 바로 답해요. 가장 편해요.", "내 ChatGPT·Claude·Gemini 키로 바로 답해요(아래에서 키를 등록하고 모델을 골라요). 가장 편해요."],
  ["'Claude (기본)'", "'내 API 키로 바로 답하기 (기본)'"],
  ["claude: 'Claude로 바꿨어요'", "claude: '내 API 키로 바꿨어요'"],
  ["AI를 Claude로 둔 상태에서만", "AI를 \\'내 API 키\\'로 둔 상태에서만"],
  [/\$\{\[\['Gemini', 'gemini-3\.5-flash-lite'\][\s\S]*?쓰는 AI의 정책을 확인하세요\.<\/p>/, '<div id="srvKeyBox"></div>'],
];
// 본인 이름·아이디가 들어간 치환은 비공개 폴더(private/text_fixes.json: [[원문, 대체], …])에서 읽습니다.
{
  const f = path.join(root, "private", "text_fixes.json");
  if (fs.existsSync(f)) TEXT_FIXES.push(...JSON.parse(fs.readFileSync(f, "utf8")));
}
// 빌드 결과에 이 단어가 남아 있으면 실패시킵니다(개인정보 누출 방지).
// 단어 목록 자체가 개인정보이므로 저장소 밖(private/forbidden.txt, 한 줄에 하나)에 둡니다.
const forbiddenFile = path.join(root, "private", "forbidden.txt");
const FORBIDDEN = fs.existsSync(forbiddenFile)
  ? fs.readFileSync(forbiddenFile, "utf8").split(/\r?\n/).map((x) => x.trim()).filter(Boolean)
  : [];
if (!FORBIDDEN.length) console.warn("⚠️ private/forbidden.txt 가 없어 개인정보 금지 단어 검사를 건너뜁니다.");

const html = fs.readFileSync(srcPath, "utf8");
const a = html.indexOf("<script>"), b = html.lastIndexOf("</script>");
if (a < 0 || b < 0) throw new Error("원본에서 <script>를 찾지 못했습니다.");
let markup = html.slice(0, a), js = html.slice(a + "<script>".length, b);
const tail = html.slice(b + "</script>".length);

// 1) DATA(공고 등 큰 데이터)는 서버에서 받아 오도록 교체
const dataStart = js.indexOf("const DATA = ");
if (dataStart < 0) throw new Error("const DATA 를 찾지 못했습니다.");
const dataEnd = js.indexOf("\n", dataStart);
js = js.slice(0, dataStart) + "const DATA = window.__BOARD_DATA__;" + js.slice(dataEnd);

// 2) 개인정보가 든 최상위 선언의 초기값을 비공개 파일로 옮김
const ast = acorn.parse(js, { ecmaVersion: "latest", sourceType: "script" });
const found = new Map(); // name -> {start,end,src}
for (const st of ast.body) {
  if (st.type !== "VariableDeclaration") continue;
  for (const d of st.declarations) {
    if (d.id.type === "Identifier" && PRIVATE[d.id.name] !== undefined && d.init) {
      found.set(d.id.name, { start: d.init.start, end: d.init.end, src: js.slice(d.init.start, d.init.end) });
    }
  }
}
const missing = Object.keys(PRIVATE).filter((k) => !found.has(k));
if (missing.length) console.warn("⚠️ 원본에 없는 비공개 대상(건너뜀):", missing.join(", "));

// 비공개 초기값이 다른 코드에 기대지 않고 단독으로 계산되는지 확인
const sandbox = vm.createContext({ CH: () => [] }); // 도우미는 빈 함수로 대신
for (const [name, f] of found) {
  try { vm.runInContext(`(${f.src})`, sandbox, { timeout: 2000 }); }
  catch (e) { throw new Error(`${name} 초기값이 단독으로 실행되지 않습니다: ${e.message}`); }
}

// 뒤에서부터 치환해야 앞쪽 위치가 어긋나지 않음
let pub = js;
for (const [name, f] of [...found].sort((x, y) => y[1].start - x[1].start)) {
  const call = DEPS[name] ? `?.(${DEPS[name].join(", ")})` : "";
  pub = pub.slice(0, f.start) + `window.__PRIVATE__?.${name}${call} ?? ${PRIVATE[name]}` + pub.slice(f.end);
}
for (const [from, to] of TEXT_FIXES) {
  if (from instanceof RegExp) {
    if (!from.test(pub)) throw new Error("치환할 문구를 찾지 못했습니다(원본이 바뀌었나요?): " + String(from).slice(0, 60));
    pub = pub.replace(from, () => to); // 함수로 넘겨 to 안의 $ 가 특수 문자로 해석되지 않게 함
  } else {
    if (!pub.includes(from)) throw new Error("치환할 문구를 찾지 못했습니다(원본이 바뀌었나요?): " + from.slice(0, 60));
    pub = pub.split(from).join(to);
  }
}

const priv = `// 지원자 본인 정보 — 관리자에게만 내려갑니다. 저장소에 올리지 마세요.\nwindow.__PRIVATE__ = {\n${[...found]
  .map(([name, f]) => DEPS[name] ? `${name}: (${DEPS[name].join(", ")}) => (${f.src})` : `${name}: ${f.src}`).join(",\n")}\n};\n`;

// 3) 화면 뼈대: 제목에서 이름 제거, 스크립트 대신 부트스트랩
markup = markup.replace(/<title>[^<]*<\/title>/, "<title>채용 보드</title>");
const board = markup + tail.replace("</body>", "") + `<script src="/bootstrap.js"></script>\n</body></html>\n`;

// 4) 개인정보 누출 검사(공개 산출물)
const leaks = [];
for (const [label, text] of [["board.html", board], ["board.js", pub]]) {
  for (const w of FORBIDDEN) if (text.includes(w)) leaks.push(`${label}: "${w}"`);
}
if (leaks.length) { console.error("❌ 공개 산출물에 개인정보가 남아 있습니다:\n  " + leaks.join("\n  ")); process.exit(2); }

fs.mkdirSync(path.join(root, "web"), { recursive: true });
fs.mkdirSync(path.join(root, "private"), { recursive: true });
fs.writeFileSync(path.join(root, "web", "board.html"), board);
fs.writeFileSync(path.join(root, "web", "board.js"), pub);
fs.writeFileSync(path.join(root, "private", "board_private.js"), priv);
const kb = (s) => (Buffer.byteLength(s) / 1024).toFixed(0) + "KB";
console.log(`✅ board.html ${kb(board)} · board.js ${kb(pub)} · board_private.js ${kb(priv)} → ${root}`);
console.log("비공개로 옮긴 선언:", [...found.keys()].join(", "));
