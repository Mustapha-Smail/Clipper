# Clipper backend: one image, run as the API or as the worker.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The Claude Code CLI, for AI tiers that run on a Claude subscription instead of the API (claude_cli.py).
# Nothing starts it unless Settings pick "Claude plan"; it then needs CLAUDE_CODE_OAUTH_TOKEN in .env.
# Anthropic's installer checks the download's checksum; the one binary is kept, its updater is not.
ARG CLAUDE_CLI_VERSION=2.1.322
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://claude.ai/install.sh -o /tmp/install.sh \
    && bash /tmp/install.sh "$CLAUDE_CLI_VERSION" \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && chmod 755 /usr/local/bin/claude \
    && rm -rf /tmp/install.sh /root/.local /root/.claude /root/.claude.json \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && claude --version

# uv installs the locked dependencies; deno lets yt-dlp solve YouTube's JS challenges.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY clipper ./clipper
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    CLIPPER_IN_DOCKER=1 \
    CLIPPER_DATA_DIR=/data \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/cache/huggingface \
    DENO_DIR=/data/cache/deno \
    PYTHONUNBUFFERED=1

# Load the OCR engine once so its models are inside the image, not fetched at run time.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

# Run unprivileged. On a Linux host whose user is not uid 1000, set `user:` in docker-compose.yml.
RUN useradd --uid 1000 --create-home clipper \
    && mkdir -p /data /output /videos && chown clipper:clipper /data /output
USER clipper

EXPOSE 8000
CMD ["clipper", "api", "--host", "0.0.0.0", "--port", "8000"]
