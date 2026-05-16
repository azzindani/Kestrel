# ── Stage 1: builder + test ────────────────────────────────────────────────────
# Installs all deps (including dev/test), runs the full test suite.
# The build fails here if any test fails — the runtime image is never produced.
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

# Run the full test suite. A non-zero exit aborts the build.
RUN python -m pytest --tb=short -q


# ── Stage 2: runtime ────────────────────────────────────────────────────────────
# Clean image with only runtime dependencies.  No test code, no pytest.
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source from the builder stage (tests passed, safe to use)
COPY --from=builder /app/src ./src
COPY --from=builder /app/params.json .
COPY --from=builder /app/pyproject.toml .
COPY --from=builder /app/docker-entrypoint.sh .
COPY --from=builder /app/scripts ./scripts

RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
