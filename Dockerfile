# Multi-stage. The builder compiles wheels; the runtime image carries neither
# the compiler nor pip's cache, which is most of the size difference.

# --- builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# --- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root. A container that never needs to write to its own filesystem should
# not be able to.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

USER appuser

EXPOSE 8000

# Render injects $PORT and it is not always 8000, so it is read at runtime
# rather than baked in.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
