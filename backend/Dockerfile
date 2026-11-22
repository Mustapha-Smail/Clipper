# Clipper backend runtime. Keep this target lean: API and migration containers use it.
FROM python:3.12-slim-bookworm AS runtime

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

# The Codex CLI, for AI tiers that run on a ChatGPT plan instead of the API (codex_cli.py): the release
# binaries from github.com/openai/codex, checked against the checksums GitHub publishes for them. The
# second binary runs the model's web searches. Sign in once with
# `docker compose exec worker codex login --device-auth`; the login is kept in the data folder.
ARG CODEX_CLI_VERSION=0.169.0
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x86_64; \
        cli=9af4d751fce183faedea629437d5252dbf458eeb04130cf4f97be844621ea985; \
        host=cbed6a8d25e8268792b757767fbc76d4c62e84bcf8ee4a63f97547f0de3aaceb ;; \
      arm64) arch=aarch64; \
        cli=97b97d87eea075c5cb7709c7f6156a451622a7667953ecda40e0276637196f3f; \
        host=059397e4d5beda7a7acc7441a6a56457d0f79e1001d129976ac0d26b0e6ee04c ;; \
      *) echo "no Codex CLI for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    apt-get update && apt-get install -y --no-install-recommends curl; \
    base=https://github.com/openai/codex/releases/download/rust-v$CODEX_CLI_VERSION; \
    cd /tmp; \
    for pair in "codex:$cli" "codex-code-mode-host:$host"; do \
      name=${pair%%:*}; file=$name-$arch-unknown-linux-musl; \
      curl -fsSL "$base/$file.tar.gz" -o "$file.tar.gz"; \
      echo "${pair##*:}  $file.tar.gz" | sha256sum -c -; \
      tar -xzf "$file.tar.gz"; \
      install -m 755 "$file" "/usr/local/bin/$name"; \
      rm -f "$file" "$file.tar.gz"; \
    done; \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
    codex --version

# uv installs the locked dependencies; deno lets yt-dlp solve YouTube's JS challenges.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# uv's download cache lives in a build cache mount, not in the image: kept in a layer it
# added ~600 MB to every build, and every release builds this layer again (uv.lock holds the version).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY clipper ./clipper
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    CLIPPER_IN_DOCKER=1 \
    CLIPPER_DATA_DIR=/data \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/cache/huggingface \
    DENO_DIR=/data/cache/deno \
    CODEX_HOME=/data/cache/codex-cli \
    PYTHONUNBUFFERED=1

# Load the OCR engine once so its models are inside the image, not fetched at run time.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

# Run unprivileged. On a Linux host whose user is not uid 1000, set `user:` in docker-compose.yml.
RUN useradd --uid 1000 --create-home clipper \
    && mkdir -p /data /output /videos && chown clipper:clipper /data /output
USER clipper

EXPOSE 8000
CMD ["clipper", "api", "--host", "0.0.0.0", "--port", "8000"]

# Blender is deliberately isolated from the API/migration image. The official Linux
# build is x86-64 only; fail on ARM rather than silently running an emulated renderer
# in production. Pass the published archive SHA-256 as BLENDER_SHA256 at build time.
FROM runtime AS blender-worker

ARG BLENDER_VERSION=4.5.16
ARG BLENDER_SHA256=23432b064977908beb1ab4b7f32f024e7331105c875c187dce002aff84b10c11
ARG TARGETARCH

USER root
RUN test -n "$BLENDER_SHA256" || (echo "BLENDER_SHA256 is required for the blender-worker target (see README.md)" >&2; exit 1)

# These are the runtime libraries used by Blender's headless Eevee binary. Keep the
# list here (instead of adding it to runtime) so API and migrate stay Blender-free.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      curl \
      xz-utils \
      libdbus-1-3 \
      libegl1 \
      libfontconfig1 \
      libfreetype6 \
      libgl1 \
      libglib2.0-0 \
      libgomp1 \
      libice6 \
      libpulse0 \
      libsm6 \
      libwayland-client0 \
      libwayland-egl1 \
      libx11-6 \
      libx11-xcb1 \
      libxcb1 \
      libxcursor1 \
      libxext6 \
      libxfixes3 \
      libxi6 \
      libxinerama1 \
      libxkbcommon0 \
      libxkbcommon-x11-0 \
      libxrandr2 \
      libxrender1 \
      libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) blender_arch=x64 ;; \
      *) echo "Blender $BLENDER_VERSION has no supported Linux build for $TARGETARCH; use an x86-64 VM (no emulation)." >&2; exit 1 ;; \
    esac; \
    archive="blender-${BLENDER_VERSION}-linux-${blender_arch}.tar.xz"; \
    url="https://download.blender.org/release/Blender4.5/${archive}"; \
    curl -fsSL "$url" -o "/tmp/$archive"; \
    echo "$BLENDER_SHA256  /tmp/$archive" | sha256sum -c -; \
    mkdir -p /opt/blender; \
    tar -xJf "/tmp/$archive" --strip-components=1 -C /opt/blender; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    rm -f "/tmp/$archive"; \
    chown -R clipper:clipper /opt/blender

# Build-time smoke check: no X server, network, or user data is needed. Eevee is
# intentionally rendered at 64x64 to catch missing headless/runtime dependencies.
USER clipper
RUN blender --background --factory-startup --version \
    && blender --background --factory-startup \
      --python-expr "import bpy; s=bpy.context.scene; s.render.engine='BLENDER_EEVEE_NEXT'; s.render.image_settings.file_format='PNG'; s.render.resolution_x=64; s.render.resolution_y=64; s.render.resolution_percentage=100; s.render.filepath='/tmp/blender-smoke.png'; bpy.ops.render.render(write_still=True)" \
    && test -s /tmp/blender-smoke.png \
    && rm -f /tmp/blender-smoke.png

CMD ["clipper", "worker"]
