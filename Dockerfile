FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    google-genai>=2.0.0 \
    PyGithub>=2.1.0 \
    pydantic>=2.7.0 \
    pytest>=8.0.0

COPY src /app/src
COPY README.md /app/

ENTRYPOINT ["python", "-m", "src.main"]
