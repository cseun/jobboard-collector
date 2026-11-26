FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# 이미지 공고 OCR 비교 시험용(scripts/image_compare.py). 한글 언어팩 포함
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY seed ./seed

RUN useradd --create-home --uid 10001 appuser && mkdir -p /srv/files /srv/private && chown -R appuser /srv
USER appuser

EXPOSE 8000
# 로그인 시도 제한을 프로세스 메모리에서 세므로 워커는 1개로 둡니다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
