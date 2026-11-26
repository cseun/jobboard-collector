"""APP_ENCRYPTION_KEY 용 무작위 키를 한 줄 출력합니다:  python -m scripts.gen_key"""
import base64
import os

print(base64.urlsafe_b64encode(os.urandom(32)).decode())
