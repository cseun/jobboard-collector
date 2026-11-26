"""3단계: API 키 암호화 저장, ChatGPT·Claude·Gemini 키 등록과 모델 선택, AI 중계, 데모 계정."""
import base64
import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config, crypto
from app.db import SessionLocal
from app.models import DemoUser, Document, User, UserAiKey, UserApiKey, utcnow
from app.routers import ai as ai_mod
from tests.conftest import new_client
from tests.test_api import make_invite, register
from tests.test_docs_files import PNG, two_users

MASTER = base64.urlsafe_b64encode(b"m" * 32).decode()
GOOD_KEY = "sk-ant-api03-" + "A" * 40 + "WXYZ"          # Claude
OPENAI_KEY = "sk-proj-" + "B" * 40 + "OPQR"              # ChatGPT
GEMINI_KEY = "AIza" + "C" * 35 + "GHIJ"                   # Gemini
KEYS = {"anthropic": GOOD_KEY, "openai": OPENAI_KEY, "gemini": GEMINI_KEY}

LISTS = {
    "anthropic": {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-5-5"}]},
    "openai": {"data": [{"id": "gpt-5.1"}, {"id": "gpt-5.1-mini"}, {"id": "gpt-4o"}, {"id": "o3"}, {"id": "text-embedding-3-large"},
                        {"id": "whisper-1"}, {"id": "dall-e-3"}, {"id": "gpt-4o-2024-08-06"}, {"id": "gpt-4o-mini-tts"}]},
    "gemini": {"models": [
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4-argon", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]},
}


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


def ok_anthropic(text, stop="end_turn"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop, "usage": {"input_tokens": 11, "output_tokens": 7}}


def ok_openai(text, finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


def ok_gemini(text, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}}


def _provider_of(url):
    return "openai" if "openai.com" in url else "gemini" if "googleapis" in url else "anthropic"


class FakeAI:
    """httpx.get/post를 대신해 세 서비스처럼 답합니다."""

    def __init__(self):
        self.calls = []
        self.list_status = {"anthropic": 200, "openai": 200, "gemini": 200}
        self.nexts = {"anthropic": FakeResp(200, ok_anthropic("안녕하세요")),
                      "openai": FakeResp(200, ok_openai("안녕하세요(GPT)")),
                      "gemini": FakeResp(200, ok_gemini("안녕하세요(Gemini)"))}

    # 예전 테스트와 같은 이름(Claude 기준)
    @property
    def next(self):
        return self.nexts["anthropic"]

    @next.setter
    def next(self, v):
        self.nexts["anthropic"] = v

    @property
    def validate_status(self):
        return self.list_status["anthropic"]

    @validate_status.setter
    def validate_status(self, v):
        self.list_status["anthropic"] = v

    @staticmethod
    def ok(text, stop="end_turn"):
        return ok_anthropic(text, stop)

    def get(self, url, params=None, headers=None, timeout=None):
        p = _provider_of(url)
        self.calls.append(("GET", url, headers, params))
        st = self.list_status[p]
        return FakeResp(st, LISTS[p] if st == 200 else {})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return self.nexts[_provider_of(url)]


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    ai_mod.reset_limits()
    f = FakeAI()
    monkeypatch.setattr(ai_mod.httpx, "get", f.get)
    monkeypatch.setattr(ai_mod.httpx, "post", f.post)
    return f


def sample(c, prompt="질문", kind="text", **kw):
    return c.post("/api/ai/sample", json={"kind": kind, "prompt": prompt, **kw})


def put_key(c, provider="anthropic", key=None):
    return c.put(f"/api/ai/providers/{provider}/key", json={"api_key": key or KEYS[provider]})


def user_with_key(admin, fake):
    u1, u2, id1, id2 = two_users(admin)
    assert put_key(u1).status_code == 200
    return u1, u2, id1, id2


# ---------------------------------------------------------------- 암호화
def test_crypto_roundtrip_and_binding(monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    t = crypto.encrypt(7, "sk-ant-secret")
    assert "sk-ant-secret" not in t
    assert crypto.decrypt(7, t) == "sk-ant-secret"
    with pytest.raises(crypto.CryptoError):  # 다른 사람 id로는 복호화 거부
        crypto.decrypt(8, t)


def test_crypto_key_rotation(monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    old = crypto.encrypt(1, "x")
    new_master = base64.urlsafe_b64encode(b"n" * 32).decode()
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", f"{new_master},{MASTER}")  # 새 키 + 옛 키
    assert crypto.decrypt(1, old) == "x"
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", new_master)  # 옛 키를 뺀 뒤엔 못 읽음
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(1, old)


def test_crypto_disabled_without_master(monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", "")
    assert not crypto.enabled()
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    assert not crypto.enabled()


# ---------------------------------------------------------------- 키 등록
def test_requires_login(client):
    assert client.get("/api/ai/status").status_code == 401
    assert client.put("/api/ai/providers/anthropic/key", json={"api_key": GOOD_KEY}).status_code == 401
    assert client.get("/api/ai/providers/openai/models").status_code == 401
    assert client.put("/api/ai/active", json={"provider": "gemini"}).status_code == 401
    assert client.post("/api/ai/sample", json={"prompt": "x"}).status_code == 401


def test_without_master_key_ai_is_off_with_clear_message(admin_client, monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", "")
    assert admin_client.get("/api/ai/status").json()["server_ready"] is False
    r = put_key(admin_client)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    assert sample(admin_client).json()["code"] == "not_configured"


def test_status_lists_all_three_services(admin_client, fake):
    st = admin_client.get("/api/ai/status").json()
    assert set(st["providers"]) == {"openai", "anthropic", "gemini"}
    assert [st["providers"][p]["label"] for p in ("openai", "anthropic", "gemini")] == ["ChatGPT", "Claude", "Gemini"]
    assert st["active"] is None and not st["configured"] and not any(v["configured"] for v in st["providers"].values())


def test_unknown_provider_is_rejected(admin_client, fake):
    assert admin_client.put("/api/ai/providers/grok/key", json={"api_key": "x" * 30}).json()["code"] == "unknown_provider"
    assert admin_client.get("/api/ai/providers/grok/models").json()["code"] == "unknown_provider"
    assert admin_client.delete("/api/ai/providers/grok/key").json()["code"] == "unknown_provider"


def test_register_key_validates_and_stores_encrypted(admin_client, fake):
    c = admin_client
    p = "/api/ai/providers/anthropic/key"
    r = c.put(p, json={"api_key": "hello"})
    assert r.status_code == 400 and r.json()["code"] == "bad_format"

    fake.validate_status = 401  # 서비스가 거절하면 저장하지 않음
    assert c.put(p, json={"api_key": GOOD_KEY}).json()["code"] == "invalid_key"
    assert c.get("/api/ai/status").json()["configured"] is False

    fake.validate_status = 503  # 서비스 쪽 문제는 '키 문제'로 오해하지 않게 구분
    assert c.put(p, json={"api_key": GOOD_KEY}).json()["code"] == "upstream_error"

    fake.validate_status = 200
    r = c.put(p, json={"api_key": GOOD_KEY})
    assert r.status_code == 200
    st = r.json()
    assert st["configured"] and st["last4"] == "WXYZ" and st["server_ready"] and st["active"] == "anthropic"
    assert GOOD_KEY not in json.dumps(st) and GOOD_KEY not in json.dumps(c.get("/api/ai/status").json())

    with SessionLocal() as db:  # DB에는 평문이 없다
        row = db.execute(select(UserAiKey)).scalar_one()
        assert GOOD_KEY not in row.ciphertext and "WXYZ" not in row.ciphertext.replace(row.last4, "")
        assert crypto.decrypt(row.user_id, row.ciphertext) == GOOD_KEY

    st = c.delete("/api/ai/providers/anthropic/key").json()
    assert st["configured"] is False and st["last4"] is None and st["active"] is None
    with SessionLocal() as db:
        assert db.execute(select(UserAiKey)).first() is None


def test_key_for_the_wrong_service_is_caught_by_format(admin_client, fake):
    c = admin_client
    assert put_key(c, "openai", GOOD_KEY).json()["code"] == "bad_format"      # Claude 키를 ChatGPT 칸에
    assert put_key(c, "anthropic", OPENAI_KEY).json()["code"] == "bad_format"  # ChatGPT 키를 Claude 칸에
    assert put_key(c, "gemini", OPENAI_KEY).json()["code"] == "bad_format"     # 글자가 다른 Gemini 칸
    assert fake.calls == []  # 형식이 틀리면 서비스에 보내지도 않음


def test_key_registration_is_rate_limited(admin_client, fake):
    fake.validate_status = 401  # 형식은 맞지만 서비스가 거절하는 키를 계속 시도하면 막힘
    for _ in range(10):
        assert put_key(admin_client).json()["code"] == "invalid_key"
    assert put_key(admin_client).status_code == 429
    n = len(fake.calls)
    assert put_key(admin_client).status_code == 429 and len(fake.calls) == n  # 막힌 동안은 서비스에 보내지도 않음


def test_typos_do_not_use_up_the_attempts(admin_client, fake):
    for _ in range(30):  # 형식이 틀린 입력은 서비스에 보내지 않으므로 횟수에 안 셈
        assert admin_client.put("/api/ai/providers/anthropic/key", json={"api_key": "bad"}).json()["code"] == "bad_format"
    assert put_key(admin_client).status_code == 200


@pytest.mark.parametrize("provider,expect_default,expect_quick", [
    ("anthropic", "claude-sonnet-5-5", "claude-haiku-5-5"),
    ("openai", "gpt-5.1", "gpt-5.1-mini"),
    ("gemini", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
])
def test_register_picks_default_models_from_the_live_list(admin_client, fake, provider, expect_default, expect_quick):
    st = put_key(admin_client, provider).json()
    p = st["providers"][provider]
    assert p["configured"] and p["model_default"] == expect_default and p["model_quick"] == expect_quick
    method, url, headers, _ = fake.calls[-1]
    assert method == "GET" and "/models" in url  # 모델 목록 조회로 키를 확인(토큰을 쓰지 않음)


def test_model_list_hides_non_chat_models(admin_client, fake):
    put_key(admin_client, "openai"); put_key(admin_client, "gemini")
    oa = admin_client.get("/api/ai/providers/openai/models").json()["models"]
    assert oa == ["gpt-5.1-mini", "gpt-5.1", "gpt-4o", "o3"] or set(oa) == {"gpt-5.1", "gpt-5.1-mini", "gpt-4o", "o3"}
    assert not any(x in oa for x in ("text-embedding-3-large", "whisper-1", "dall-e-3", "gpt-4o-2024-08-06", "gpt-4o-mini-tts"))
    assert oa.index("gpt-5.1") < oa.index("gpt-4o")  # 새 버전이 앞
    gm = admin_client.get("/api/ai/providers/gemini/models").json()["models"]
    assert gm == ["gemini-3.5-flash-lite"]   # 허용 목록 밖(Pro·Flash·이미지·Gemini 4·임베딩)은 모두 숨김


def test_gemini_key_goes_in_header_not_url(admin_client, fake):
    put_key(admin_client, "gemini")
    method, url, headers, params = fake.calls[-1]
    assert GEMINI_KEY not in url and GEMINI_KEY not in json.dumps(params or {}) and headers["x-goog-api-key"] == GEMINI_KEY


def test_first_key_becomes_active_and_user_can_switch(admin_client, fake):
    c = admin_client
    assert put_key(c, "openai").json()["active"] == "openai"
    assert put_key(c, "gemini").json()["active"] == "openai"  # 두 번째 키는 쓰던 서비스를 바꾸지 않음
    assert sample(c).json()["provider"] == "openai"
    assert c.put("/api/ai/active", json={"provider": "gemini"}).json()["active"] == "gemini"
    assert sample(c).json()["provider"] == "gemini"
    r = c.put("/api/ai/active", json={"provider": "anthropic"})  # 키가 없는 서비스로는 못 바꿈
    assert r.status_code == 409 and r.json()["code"] == "no_api_key"
    assert c.put("/api/ai/active", json={"provider": "grok"}).status_code == 422


def test_deleting_the_active_service_falls_back_to_another(admin_client, fake):
    c = admin_client
    put_key(c, "openai"); put_key(c, "gemini")
    st = c.delete("/api/ai/providers/openai/key").json()
    assert st["active"] == "gemini" and sample(c).json()["provider"] == "gemini"
    assert c.delete("/api/ai/providers/gemini/key").json()["active"] is None
    assert sample(c).json()["code"] == "no_api_key"


def test_choose_models_from_the_list(admin_client, fake):
    c = admin_client
    put_key(c, "openai")
    r = c.put("/api/ai/providers/openai/models", json={"model_default": "gpt-4o", "model_quick": "gpt-5.1-mini"})
    assert r.status_code == 200 and r.json()["providers"]["openai"]["model_default"] == "gpt-4o"
    sample(c)
    assert fake.calls[-1][3]["model"] == "gpt-4o"
    sample(c, model_tier="quick")
    assert fake.calls[-1][3]["model"] == "gpt-5.1-mini"

    # 빠른 모델을 비우면 기본 모델을 그대로 씀
    c.put("/api/ai/providers/openai/models", json={"model_default": "o3", "model_quick": ""})
    sample(c, model_tier="quick")
    assert fake.calls[-1][3]["model"] == "o3"

    # 목록에 없는 모델·이상한 이름은 거절
    for bad in ("gpt-99", "../../etc/passwd", "gpt 5", "text-embedding-3-large"):
        r = c.put("/api/ai/providers/openai/models", json={"model_default": bad, "model_quick": ""})
        assert r.status_code == 400 and r.json()["code"] == "bad_model", bad
    assert c.get("/api/ai/providers/openai/models").json()["model_default"] == "o3"


def test_models_need_a_registered_key(admin_client, fake):
    assert admin_client.get("/api/ai/providers/openai/models").json()["code"] == "no_api_key"
    r = admin_client.put("/api/ai/providers/openai/models", json={"model_default": "gpt-5.1"})
    assert r.status_code == 409


def test_model_list_is_cached_and_can_be_refreshed(admin_client, fake):
    put_key(admin_client, "openai")
    n = len(fake.calls)
    admin_client.get("/api/ai/providers/openai/models"); admin_client.get("/api/ai/providers/openai/models")
    assert len(fake.calls) == n  # 등록할 때 받은 목록을 쓰므로 다시 묻지 않음
    admin_client.get("/api/ai/providers/openai/models?refresh=true")
    assert len(fake.calls) == n + 1


def test_model_list_errors_are_mapped(admin_client, fake):
    put_key(admin_client, "openai")
    fake.list_status["openai"] = 401
    r = admin_client.get("/api/ai/providers/openai/models?refresh=true")
    assert r.status_code == 424 and r.json()["code"] == "invalid_key"


def test_old_claude_only_key_is_carried_over(admin_client, fake):
    """이전 버전(Claude 키만 저장)에서 올린 계정도 키를 다시 넣지 않고 그대로 씁니다."""
    u1, _, id1, _ = two_users(admin_client)
    with SessionLocal() as db:
        db.add(UserApiKey(user_id=id1, ciphertext=crypto.encrypt(id1, GOOD_KEY), last4="WXYZ"))
        db.commit()
    st = u1.get("/api/ai/status").json()
    assert st["active"] == "anthropic" and st["providers"]["anthropic"]["last4"] == "WXYZ"
    assert st["providers"]["anthropic"]["model_default"] == config.AI_MODEL_DEFAULT
    assert sample(u1).status_code == 200 and fake.calls[-1][2]["x-api-key"] == GOOD_KEY
    with SessionLocal() as db:
        assert db.get(UserApiKey, id1) is None and db.get(UserAiKey, (id1, "anthropic")) is not None


def test_no_plaintext_keys_in_database(admin_client, fake):
    for p in KEYS:
        put_key(admin_client, p)
    with SessionLocal() as db:
        for row in db.execute(select(UserAiKey)).scalars():
            assert KEYS[row.provider] not in row.ciphertext
            assert crypto.decrypt(row.user_id, row.ciphertext) == KEYS[row.provider]


# ---------------------------------------------------------------- AI 호출
def test_sample_without_key_points_to_settings(admin_client, fake):
    r = sample(admin_client)
    assert r.status_code == 409 and r.json()["code"] == "no_api_key" and "설정" in r.json()["detail"]
    assert fake.calls == []


def test_sample_text_and_usage(admin_client, fake):
    u1, _, _, _ = user_with_key(admin_client, fake)
    r = sample(u1, "안녕")
    assert r.status_code == 200 and r.json()["text"] == "안녕하세요" and r.json()["json"] is None and r.json()["provider"] == "anthropic"

    method, url, headers, payload = fake.calls[-1]
    assert url.endswith("/v1/messages") and headers["x-api-key"] == GOOD_KEY  # 서버가 복호화한 키로 호출
    assert payload["model"] == "claude-sonnet-5-5" and payload["messages"] == [{"role": "user", "content": "안녕"}]

    sample(u1, "빠른 질문", model_tier="quick")
    assert fake.calls[-1][3]["model"] == "claude-haiku-5-5"
    st = u1.get("/api/ai/status").json()
    assert st["today"]["used"] == 2


def test_sample_openai_format(admin_client, fake):
    c = admin_client
    put_key(c, "openai")
    r = sample(c, [{"role": "user", "content": "안녕"}], max_tokens=100)
    assert r.status_code == 200 and r.json()["text"] == "안녕하세요(GPT)" and r.json()["model"] == "gpt-5.1"
    method, url, headers, body = fake.calls[-1]
    assert url == "https://api.openai.com/v1/chat/completions" and headers["authorization"] == f"Bearer {OPENAI_KEY}"
    assert body["messages"] == [{"role": "user", "content": "안녕"}]
    assert body["max_completion_tokens"] == 4096 and "max_tokens" not in body  # 추론형 모델을 위한 여유
    assert c.get("/api/ai/status").json()["today"]["used"] == 1
    fake.nexts["openai"] = FakeResp(200, ok_openai('{"a":1}'))
    assert sample(c, kind="json").json()["json"] == {"a": 1}
    fake.nexts["openai"] = FakeResp(200, ok_openai("잘린 {", finish="length"))
    assert sample(c, kind="json").json()["code"] == "truncated"


def test_sample_gemini_format(admin_client, fake):
    c = admin_client
    put_key(c, "gemini")
    r = sample(c, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}])
    assert r.status_code == 200 and r.json()["text"] == "안녕하세요(Gemini)" and r.json()["model"] == "gemini-3.5-flash-lite"
    method, url, headers, body = fake.calls[-1]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert headers["x-goog-api-key"] == GEMINI_KEY and GEMINI_KEY not in url
    assert [c_["role"] for c_ in body["contents"]] == ["user", "model", "user"]  # assistant → model
    assert body["contents"][2]["parts"] == [{"text": "c"}] and body["generationConfig"]["maxOutputTokens"] >= 4096
    sample(c, model_tier="quick")
    assert fake.calls[-1][1].endswith("/gemini-3.5-flash-lite:generateContent")
    fake.nexts["gemini"] = FakeResp(200, ok_gemini("답", finish="MAX_TOKENS"))
    assert sample(c).json()["truncated"] is True
    fake.nexts["gemini"] = FakeResp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})
    r = sample(c)
    assert r.status_code == 422 and r.json()["code"] == "blocked"
    fake.nexts["gemini"] = FakeResp(200, {"candidates": []})
    assert sample(c).json()["code"] == "upstream_error"


def test_usage_counts_tokens_for_every_service(admin_client, fake):
    c = admin_client
    for p in ("openai", "gemini", "anthropic"):
        put_key(c, p)
        c.put("/api/ai/active", json={"provider": p})
        assert sample(c).status_code == 200
    from app.models import AiUsage
    with SessionLocal() as db:
        u = db.execute(select(AiUsage)).scalar_one()
        assert (u.calls, u.input_tokens, u.output_tokens) == (3, 5 + 4 + 11, 3 + 2 + 7)


def test_sample_json_parsing(admin_client, fake):
    u1, _, _, _ = user_with_key(admin_client, fake)
    fake.next = FakeResp(200, fake.ok('설명입니다\n```json\n{"reply":"안녕","updates":[]}\n```'))
    r = sample(u1, "q", kind="json")
    assert r.status_code == 200 and r.json()["json"] == {"reply": "안녕", "updates": []}

    fake.next = FakeResp(200, fake.ok("JSON 아님"))
    r = sample(u1, "q", kind="json")
    assert r.status_code == 422 and r.json()["code"] == "bad_json"

    fake.next = FakeResp(200, fake.ok('{"a": [1, 2', stop="max_tokens"))
    assert sample(u1, "q", kind="json").json()["code"] == "truncated"


def test_messages_are_normalized(admin_client, fake):
    u1, _, _, _ = user_with_key(admin_client, fake)
    # 보드가 보내는 형태: user로 시작, 역할 교대. 연속된 같은 역할은 합치고, assistant로 끝나면 user를 덧붙임
    sample(u1, [{"role": "assistant", "content": "먼저"}, {"role": "user", "content": "a"}, "b", {"role": "assistant", "content": "c"}])
    msgs = fake.calls[-1][3]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[2]["content"] == "a\n\nb"
    assert sample(u1, "   ").json()["code"] == "empty_prompt"


def test_images(admin_client, fake):
    u1, _, _, _ = user_with_key(admin_client, fake)
    b64 = base64.b64encode(PNG).decode()
    r = sample(u1, "글자 읽기", images=[{"media_type": "image/png", "data": b64}])
    assert r.status_code == 200
    content = fake.calls[-1][3]["messages"][-1]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["data"] == b64 and content[-1] == {"type": "text", "text": "글자 읽기"}

    assert sample(u1, "x", images=[{"media_type": "image/svg+xml", "data": b64}]).json()["code"] == "bad_image"
    assert sample(u1, "x", images=[{"media_type": "image/png", "data": "!!!"}]).json()["code"] == "bad_image"
    assert sample(u1, "x", images=[{"media_type": "image/png", "data": b64}] * 5).json()["code"] == "too_many_images"
    big = base64.b64encode(b"\x00" * (config.AI_MAX_IMAGE_BYTES + 1)).decode()
    assert sample(u1, "x", images=[{"media_type": "image/png", "data": big}]).json()["code"] == "too_large"


def test_images_for_chatgpt_and_gemini(admin_client, fake):
    c = admin_client
    b64 = base64.b64encode(PNG).decode()
    img = [{"media_type": "image/png", "data": b64}]
    put_key(c, "openai"); put_key(c, "gemini")
    assert sample(c, "읽기", images=img).status_code == 200
    content = fake.calls[-1][3]["messages"][-1]["content"]
    assert content[0] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}} and content[-1] == {"type": "text", "text": "읽기"}
    c.put("/api/ai/active", json={"provider": "gemini"})
    assert sample(c, "읽기", images=img).status_code == 200
    parts = fake.calls[-1][3]["contents"][-1]["parts"]
    assert parts[0] == {"inline_data": {"mime_type": "image/png", "data": b64}} and parts[-1] == {"text": "읽기"}


def test_prompt_size_limit(admin_client, fake, monkeypatch):
    u1, _, _, _ = user_with_key(admin_client, fake)
    monkeypatch.setattr(config, "AI_MAX_PROMPT_CHARS", 100)
    assert sample(u1, "가" * 101).json()["code"] == "too_large"


@pytest.mark.parametrize("status,body,http,code", [
    (401, {"error": {"message": "invalid x-api-key"}}, 424, "invalid_key"),   # 401을 그대로 내리면 화면이 로그인 화면으로 튕김
    (403, {}, 424, "invalid_key"),
    (429, {}, 429, "rate_limited"),
    (400, {"error": {"message": "Your credit balance is too low to access the API"}}, 402, "no_credit"),
    (404, {"error": {"message": "model: claude-x"}}, 400, "bad_model"),
    (529, {}, 502, "upstream_error"),
    (500, {}, 502, "upstream_error"),
])
def test_upstream_errors_are_mapped(admin_client, fake, status, body, http, code):
    u1, _, _, _ = user_with_key(admin_client, fake)
    fake.next = FakeResp(status, body)
    r = sample(u1)
    assert r.status_code == http and r.json()["code"] == code
    assert GOOD_KEY not in r.text
    assert u1.get("/api/ai/status").json()["today"]["used"] == 0  # 실패는 하루 한도에 세지 않음


@pytest.mark.parametrize("provider,status,body,http,code", [
    ("openai", 401, {"error": {"message": "Incorrect API key", "code": "invalid_api_key"}}, 424, "invalid_key"),
    ("openai", 429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota", "type": "insufficient_quota"}}, 402, "no_credit"),
    ("openai", 429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}, 429, "rate_limited"),
    ("openai", 404, {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}}, 400, "bad_model"),
    ("gemini", 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}, 424, "invalid_key"),
    ("gemini", 403, {"error": {"message": "permission denied"}}, 424, "invalid_key"),
    ("gemini", 429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}, 429, "rate_limited"),
    ("gemini", 404, {"error": {"message": "models/x is not found"}}, 400, "bad_model"),
    ("gemini", 503, {"error": {"message": "overloaded"}}, 502, "upstream_error"),
])
def test_chatgpt_and_gemini_errors_are_mapped(admin_client, fake, provider, status, body, http, code):
    put_key(admin_client, provider)
    fake.nexts[provider] = FakeResp(status, body)
    r = sample(admin_client)
    assert r.status_code == http and r.json()["code"] == code and KEYS[provider] not in r.text
    assert admin_client.get("/api/ai/status").json()["today"]["used"] == 0


def test_register_rejected_keys_per_service(admin_client, fake):
    fake.list_status["openai"] = 401
    assert put_key(admin_client, "openai").json()["code"] == "invalid_key"
    fake.list_status["gemini"] = 400  # Gemini는 잘못된 키에 400을 줄 수 있어, 확인 단계에서는 어떤 거절이든 '저장 안 함'
    assert put_key(admin_client, "gemini").status_code in (400, 502)
    assert not any(v["configured"] for v in admin_client.get("/api/ai/status").json()["providers"].values())


def test_timeout_and_network_errors(admin_client, fake, monkeypatch):
    u1, _, _, _ = user_with_key(admin_client, fake)

    def boom(*a, **k):
        raise ai_mod.httpx.ReadTimeout("slow")
    monkeypatch.setattr(ai_mod.httpx, "post", boom)
    assert sample(u1).json()["code"] == "timeout"

    def boom2(*a, **k):
        raise ai_mod.httpx.ConnectError("down")
    monkeypatch.setattr(ai_mod.httpx, "post", boom2)
    assert sample(u1).json()["code"] == "upstream_error"


def test_daily_and_per_minute_limits(admin_client, fake, monkeypatch):
    u1, _, _, _ = user_with_key(admin_client, fake)
    monkeypatch.setattr(config, "AI_DAILY_LIMIT", 2)
    assert sample(u1).status_code == 200 and sample(u1).status_code == 200
    r = sample(u1)
    assert r.status_code == 429 and r.json()["code"] == "daily_limit"

    monkeypatch.setattr(config, "AI_DAILY_LIMIT", 100)
    ai_mod.reset_limits()
    monkeypatch.setattr(config, "AI_PER_MIN_LIMIT", 1)
    assert sample(u1).status_code == 200
    assert sample(u1).json()["code"] == "rate_limited"


def test_keys_are_per_account(admin_client, fake):
    u1, u2, id1, id2 = user_with_key(admin_client, fake)
    assert u2.get("/api/ai/status").json()["configured"] is False
    r = sample(u2)  # 2번 계정은 1번 키를 쓸 수 없음
    assert r.json()["code"] == "no_api_key"
    assert u2.get("/api/ai/providers/anthropic/models").json()["code"] == "no_api_key"

    # DB가 변조돼 1번의 암호문이 2번 행으로 복사돼도 복호화가 거부됨
    with SessionLocal() as db:
        src = db.get(UserAiKey, (id1, "anthropic"))
        db.add(UserAiKey(user_id=id2, provider="anthropic", ciphertext=src.ciphertext, last4=src.last4,
                         model_default="claude-sonnet-5-5", active=True))
        db.commit()
    n = len(fake.calls)
    r = sample(u2)
    assert r.status_code == 424 and r.json()["code"] == "invalid_key" and len(fake.calls) == n


def test_master_key_change_asks_to_reregister(admin_client, fake, monkeypatch):
    u1, _, _, _ = user_with_key(admin_client, fake)
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"z" * 32).decode())
    r = sample(u1)
    assert r.status_code == 424 and "다시 등록" in r.json()["detail"]


# ---------------------------------------------------------------- 데모
@pytest.fixture()
def demo_on(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ENABLED", True)
    from app.routers import auth
    auth._demo_hits.clear()


def test_demo_is_off_by_default(client):
    assert client.post("/api/auth/demo").status_code == 404


def test_demo_session_is_limited(admin_client, fake, demo_on):
    d = new_client()
    r = d.post("/api/auth/demo")
    assert r.status_code == 201 and r.json()["demo"]
    me = d.get("/api/me").json()
    assert me["is_demo"] and not me["is_admin"] and me["email"].endswith("@demo.invalid")
    assert len(d.get("/api/board-data").json()["jobs"]) >= 0  # 공고는 볼 수 있음(시드 전이면 0건)

    assert d.put("/api/ai/providers/anthropic/key", json={"api_key": GOOD_KEY}).json()["code"] == "demo_ai_off"
    assert sample(d).status_code == 403 and sample(d).json()["code"] == "demo_ai_off"
    r = d.post("/api/files", files={"file": ("a.png", PNG)})
    assert r.status_code == 403 and r.json()["code"] == "demo_no_upload"
    assert d.get("/api/admin/users").status_code == 403

    # 관리자 사용자 목록에는 데모 계정이 안 보임
    emails = [u["email"] for u in admin_client.get("/api/admin/users").json()]
    assert not any(e.endswith("@demo.invalid") for e in emails)


def test_demo_users_are_isolated_from_each_other(client, demo_on):
    a, b = new_client(), new_client()
    a.post("/api/auth/demo"), b.post("/api/auth/demo")
    ida, idb = a.get("/api/me").json()["id"], b.get("/api/me").json()["id"]
    assert ida != idb
    assert a.put("/api/docs/set", params={"c": f"data/users/{ida}", "id": "x"}, json={"data": {"v": 1}}).status_code == 200
    assert b.get("/api/docs/get", params={"c": f"data/users/{ida}", "id": "x"}).status_code == 403


def test_demo_start_is_rate_limited_per_ip(client, demo_on, monkeypatch):
    monkeypatch.setattr(config, "DEMO_PER_HOUR_PER_IP", 2)
    assert new_client().post("/api/auth/demo").status_code == 201
    assert new_client().post("/api/auth/demo").status_code == 201
    assert new_client().post("/api/auth/demo").status_code == 429


def test_demo_doc_cap(client, demo_on, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MAX_DOCS", 2)
    d = new_client(); d.post("/api/auth/demo")
    uid = d.get("/api/me").json()["id"]
    for i in range(2):
        assert d.put("/api/docs/set", params={"c": f"data/users/{uid}", "id": f"d{i}"}, json={"data": {}}).status_code == 200
    assert d.put("/api/docs/set", params={"c": f"data/users/{uid}", "id": "d9"}, json={"data": {}}).status_code == 413


def test_expired_demo_accounts_are_removed_with_their_data(client, demo_on):
    from app.demo import cleanup_expired
    keep, gone = new_client(), new_client()
    keep.post("/api/auth/demo"), gone.post("/api/auth/demo")
    kid, gid = keep.get("/api/me").json()["id"], gone.get("/api/me").json()["id"]
    gone.put("/api/docs/set", params={"c": f"data/users/{gid}", "id": "x"}, json={"data": {"v": 1}})
    keep.put("/api/docs/set", params={"c": f"data/users/{kid}", "id": "x"}, json={"data": {"v": 1}})

    with SessionLocal() as db:
        db.get(DemoUser, gid).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        assert cleanup_expired(db, force=True) == 1
        assert db.get(User, gid) is None and db.get(User, kid) is not None
        assert db.execute(select(Document).where(Document.owner_id == gid)).first() is None
        assert db.execute(select(Document).where(Document.owner_id == kid)).first() is not None
    assert gone.get("/api/me").status_code == 401  # 세션도 함께 사라짐
    assert keep.get("/api/me").status_code == 200


def test_public_config_tells_login_page_about_demo(client, monkeypatch):
    assert client.get("/api/public-config").json() == {"demo": False}
    monkeypatch.setattr(config, "DEMO_ENABLED", True)
    assert client.get("/api/public-config").json() == {"demo": True}


def test_gemini_old_saved_model_falls_back_to_allowed(admin_client, fake):
    """예전에 저장된 모델(예: Pro)이 허용 목록에 없으면 호출 때 허용된 모델로 바꿔서 씁니다."""
    from app.db import SessionLocal
    from app.models import UserAiKey
    put_key(admin_client, "gemini")
    with SessionLocal() as db:
        row = db.query(UserAiKey).filter_by(provider="gemini").one()
        row.model_default, row.model_quick = "gemini-pro-latest", "gemini-3-flash"
        db.commit()
    assert sample(admin_client).json()["model"] == "gemini-3.5-flash-lite"
    assert sample(admin_client, model_tier="quick").json()["model"] == "gemini-3.5-flash-lite"
