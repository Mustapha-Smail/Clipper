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

failed() {
  echo
  docker compose ps || true
  docker compose logs --tail 30 migrate api worker 2>/dev/null || true
  fail "the update to $tag did not finish (details above). The code is on $tag; the app ran $previous before."
}
trap failed ERR

say "Switching from $previous to $tag"
git checkout --quiet --detach "refs/tags/$tag"

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
say "Clipper $version is running (was $previous)"
