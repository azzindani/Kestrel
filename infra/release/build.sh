#!/usr/bin/env bash
# Build the kestrel image with two tags: :latest (mutable) and :sha-<short>
# (immutable, rollback-able). Keeps the N most recent SHA tags.
#
# Usage:
#   ./infra/release/build.sh             # build with :latest + :sha-<HEAD>
#   ./infra/release/build.sh --no-cache  # forwarded to docker build
#
# Rollback:
#   KESTREL_TAG=sha-<short> docker compose up -d kestrel
#
# Promote a SHA tag back to :latest after a successful rollback:
#   docker tag kestrel-kestrel:sha-<short> kestrel-kestrel:latest

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

IMAGE="kestrel-kestrel"
KEEP_TAGS="${KESTREL_KEEP_TAGS:-5}"  # how many SHA tags to retain

# Fail loudly if git working tree is dirty — the SHA wouldn't reflect what's
# actually in the image. Override with KESTREL_ALLOW_DIRTY=1 for emergencies.
if ! git diff --quiet || ! git diff --cached --quiet; then
    if [[ "${KESTREL_ALLOW_DIRTY:-0}" != "1" ]]; then
        echo "ERROR: working tree has uncommitted changes. SHA tag would mislead." >&2
        echo "       Either commit, stash, or set KESTREL_ALLOW_DIRTY=1." >&2
        exit 1
    fi
    DIRTY_SUFFIX="-dirty"
else
    DIRTY_SUFFIX=""
fi

SHA="$(git rev-parse --short HEAD)${DIRTY_SUFFIX}"
SHA_TAG="sha-${SHA}"

echo "Building $IMAGE:latest + $IMAGE:$SHA_TAG"
docker compose build "$@" kestrel
docker tag "$IMAGE:latest" "$IMAGE:$SHA_TAG"

# Prune: keep the KEEP_TAGS newest sha-* tags, delete older ones.
mapfile -t OLD_TAGS < <(
    docker images "$IMAGE" --format '{{.Tag}} {{.CreatedAt}}' \
        | grep -E '^sha-' \
        | sort -k 2,3 -r \
        | tail -n +"$((KEEP_TAGS + 1))" \
        | awk '{print $1}'
)
if [[ ${#OLD_TAGS[@]} -gt 0 ]]; then
    echo "Pruning ${#OLD_TAGS[@]} old SHA tag(s)..."
    for t in "${OLD_TAGS[@]}"; do
        docker rmi "$IMAGE:$t" || true
    done
fi

echo
docker images "$IMAGE" --format 'table {{.Tag}}\t{{.CreatedAt}}\t{{.Size}}' | head -10
echo
echo "Built $IMAGE:$SHA_TAG. To deploy: docker compose up -d kestrel"
echo "To roll back: KESTREL_TAG=$SHA_TAG docker compose up -d kestrel"
