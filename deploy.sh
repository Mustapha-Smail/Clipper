#!/usr/bin/env bash
# Deploy a release of Clipper on this machine: fetch it, check it out, rebuild the
# Docker stack and wait until the app answers with the new version.
#
#   ./deploy.sh v1.12.1      (or 1.12.1)
#   ./deploy.sh              lists the latest releases
#
# It runs from the checkout it sits in. Installed elsewhere, for instance as a command
# that no checkout can remove, it uses $CLIPPER_DIR (default /opt/clipper):
#   install -m 755 deploy.sh /usr/local/bin/clipper-deploy && clipper-deploy v1.12.1
#
# .env, docker-compose.override.yml and the data folders are not tracked by git,
# so they stay as they are. The database migrates forward on start; going back to
# an older tag after a release that changed the schema is not supported.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
if [ -f "$here/docker-compose.yml" ] && [ -d "$here/.git" ]; then
  cd "$here"
else
  cd "${CLIPPER_DIR:-/opt/clipper}" 2>/dev/null || { echo "No Clipper checkout at ${CLIPPER_DIR:-/opt/clipper}; set CLIPPER_DIR." >&2; exit 1; }
fi

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nError: %s\n' "$*" >&2; exit 1; }

say "Fetching releases"
git fetch --tags --prune --quiet origin

if [ $# -ne 1 ]; then
  latest=$(git tag --list 'v*' --sort=-v:refname | head -1)
  echo "Usage: $0 <release>, for example: $0 ${latest:-v1.0.0}"
  echo "Latest releases:"
  git tag --list 'v*' --sort=-v:refname | head -5 | sed 's/^/  /'
  exit 1
fi

tag=$1
[[ $tag == v* ]] || tag="v$tag"
git rev-parse --quiet --verify "refs/tags/$tag^{commit}" >/dev/null \
  || fail "there is no release $tag. Run $0 without arguments to list them."

if ! git diff --quiet || ! git diff --cached --quiet; then
  git status --short --untracked-files=no
  fail "files tracked by git were changed on this machine (listed above).
Keep them with 'git stash', or drop them with 'git checkout -- .', then run this again."
fi

previous=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)
port=${CLIPPER_PORT:-$( (sed -n 's/^CLIPPER_PORT=//p' .env 2>/dev/null || true) | tail -1)}
port=${port:-8000}

arch=$(uname -m)
case "$arch" in
  x86_64|amd64) ;;
  *) fail "the Blender worker requires an x86-64 VM; this host reports $arch (no emulated production renderer)." ;;
esac

# Compose reads .env itself, but validate the worker's pinned Blender checksum before
# spending time rebuilding the stack. Keep it in .env (not in git) and update it only
# when deliberately upgrading the Blender archive.
blender_sha=${BLENDER_SHA256:-$( (sed -n 's/^BLENDER_SHA256=//p' .env 2>/dev/null || true) | tail -1)}
blender_sha=${blender_sha:-23432b064977908beb1ab4b7f32f024e7331105c875c187dce002aff84b10c11}
if ! [[ $blender_sha =~ ^[0-9a-fA-F]{64}$ ]]; then
  fail "BLENDER_SHA256 must be a 64-character SHA-256 for Blender 4.5.16 in .env (see README.md)."
fi
export BLENDER_SHA256="$blender_sha"

failed() {
  echo
  docker compose ps || true
  docker compose logs --tail 30 migrate api worker 2>/dev/null || true
  fail "the update to $tag did not finish (details above). The code is on $tag; the app ran $previous before."
}
trap failed ERR

say "Switching from $previous to $tag"
git checkout --quiet --detach "refs/tags/$tag"

# A build needs room for a whole new image next to the running one. Unused images and
# build cache older than a week are safe to drop: containers keep the images they run.
NEED_GB=${CLIPPER_NEED_GB:-8}
free_gb() {
  local root
  root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
  df -Pk "$root" 2>/dev/null | awk 'NR==2 {print int($4 / 1048576)}'
}
free=$(free_gb || true)
if [ -n "$free" ] && [ "$free" -lt "$NEED_GB" ]; then
  say "Only $free GB free for Docker: removing unused images and old build cache"
  docker image prune -f >/dev/null || true
  docker builder prune -f --filter until=168h >/dev/null || true
  free=$(free_gb || true)
  if [ -n "$free" ] && [ "$free" -lt "$NEED_GB" ]; then
    trap - ERR
    docker system df || true
    fail "only $free GB free for Docker, a build needs about $NEED_GB GB. Free some space
(see above; 'docker builder prune -af' empties the whole build cache) or grow the disk, then run this again.
The code is on $tag; the app still runs $previous."
  fi
fi

say "Building and starting the stack (a few minutes)"
docker compose up -d --build

say "Waiting for the app on port $port"
version=""
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
    version=$(curl -fsS --max-time 5 "http://127.0.0.1:$port/api/v1/system/status" \
      | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')
    break
  fi
  sleep 2
done
trap - ERR

[ -n "$version" ] || { failed; }
if [ "$version" != "${tag#v}" ]; then
  fail "the app answers, but with version $version instead of ${tag#v}. Check 'docker compose ps'."
fi
docker image prune -f >/dev/null 2>&1 || true   # the image the old containers ran
say "Clipper $version is running (was $previous)"
