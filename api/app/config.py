import os


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")
# HTTPS 뒤에서는 true(기본). http로 로컬 테스트할 때만 false로 바꿉니다.
COOKIE_SECURE = _bool("COOKIE_SECURE", True)
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "14"))
# 쉼표로 구분. 비워 두면 요청 Host와 같은 Origin만 허용합니다.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
ENABLE_DOCS = _bool("ENABLE_DOCS", True)
MAX_KV_BYTES = int(os.environ.get("MAX_KV_BYTES", str(1_000_000)))
MIN_PASSWORD_LEN = 10
LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOGIN_WINDOW_SEC = int(os.environ.get("LOGIN_WINDOW_SEC", "900"))
SEED_DIR = os.environ.get("SEED_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed"))

# ---- 2단계: 파일·문서·비공개 코드 ----
FILES_DIR = os.environ.get("FILES_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "files"))
PRIVATE_DIR = os.environ.get("PRIVATE_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "private"))
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(20 * 1024 * 1024)))
USER_QUOTA_BYTES = int(os.environ.get("USER_QUOTA_BYTES", str(300 * 1024 * 1024)))
MAX_DOCS_PER_USER = int(os.environ.get("MAX_DOCS_PER_USER", "5000"))
# 개발용: 값이 있으면 그 폴더를 정적 파일로 같이 서빙합니다(운영에서는 Caddy가 서빙하므로 비워 둠).
WEB_DIR = os.environ.get("WEB_DIR", "")

# 시드의 공용 데이터 중 본인 맞춤 내용이 들어 있어 관리자에게만 보여 줄 키
ADMIN_ONLY_SHARED = {"docs"}

# ---- 3단계: AI(계정별 API 키) · 데모 ----
# 사용자가 등록한 API 키를 암호화할 때 쓰는 마스터 키(Fernet). 쉼표로 여러 개를 주면 앞의 것으로 암호화하고 모두로 복호화(키 교체용).
# 비어 있으면 AI 기능은 꺼진 채로 안내만 나갑니다. 생성: python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
APP_ENCRYPTION_KEY = os.environ.get("APP_ENCRYPTION_KEY", "").strip()
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
# Claude는 모델 목록에서 고르기 전에도 동작하도록 기본값을 둡니다. ChatGPT·Gemini는 키 등록 때 받아 온 모델 목록에서 자동으로 고릅니다.
AI_MODEL_QUICK = os.environ.get("AI_MODEL_QUICK", "claude-haiku-5-5")
AI_MODEL_DEFAULT = os.environ.get("AI_MODEL_DEFAULT", "claude-sonnet-5-5")
# Gemini는 여기에 적은 모델만 목록·선택·호출에 씁니다(쉼표로 여러 개). 임베딩·이미지 생성·유료 전용 모델은 쓰지 않습니다.
AI_GEMINI_ALLOWED = tuple(m.strip() for m in os.environ.get("AI_GEMINI_ALLOWED", "gemini-3.5-flash-lite").split(",") if m.strip())
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "300"))       # 계정당 하루 호출 수(한국 시간 0시 기준)
AI_PER_MIN_LIMIT = int(os.environ.get("AI_PER_MIN_LIMIT", "20"))    # 계정당 분당 호출 수
AI_TIMEOUT_SEC = int(os.environ.get("AI_TIMEOUT_SEC", "120"))
AI_MAX_PROMPT_CHARS = int(os.environ.get("AI_MAX_PROMPT_CHARS", "200000"))
AI_MAX_TOKENS_CAP = 8192
AI_MAX_IMAGES = 4
AI_MAX_IMAGE_BYTES = 4 * 1024 * 1024

# 데모 계정(채용 담당자가 가입 없이 둘러보기). 기본은 꺼짐.
DEMO_ENABLED = _bool("DEMO_ENABLED", False)
DEMO_HOURS = int(os.environ.get("DEMO_HOURS", "6"))                  # 데모 계정 수명
DEMO_PER_HOUR_PER_IP = int(os.environ.get("DEMO_PER_HOUR_PER_IP", "6"))
DEMO_MAX_DOCS = 300
