# Single stage on purpose. Every dependency installs from a prebuilt wheel, so
# there is nothing to compile and nothing a builder stage would leave behind.
FROM python:3.14-slim

# Unbuffered so logs reach the platform as they happen rather than when a buffer
# fills, which is the difference between watching a deploy and guessing at it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first, so a code change does not reinstall the world. The
# --only-binary flag is the same guarantee the README makes: if this succeeds,
# no compiler was needed.
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY src/ ./src/
COPY web/ ./web/

# The database lives on a mounted volume, not in the image layer, or every
# deploy would silently reset every runner's history and profile.
ENV DATABASE_URL=sqlite:////data/runcoach.db

# Runs as nobody. The application writes only to the volume, which the platform
# mounts writable, so nothing else needs to be.
RUN useradd --create-home --uid 10001 runcoach && mkdir -p /data && chown runcoach /data
USER runcoach

EXPOSE 8080

# One worker, deliberately. Two would poll Telegram twice, which Telegram
# answers with 409 Conflict, and would run two reminder sweeps, which is two
# messages for one reminder. Concurrency here is asyncio inside one process,
# which is what the voice proxy and the sweep are written for.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
