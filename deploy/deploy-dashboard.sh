#!/usr/bin/env bash
#
# Deploy the ship-tracker dashboard to the site's /ais/ path.
#
# The committed web/*.html use a __CARTO_KEY__ placeholder. This script reads
# the real CARTO basemaps key from a gitignored .carto-key (repo root), injects
# it into a temp copy, and rsyncs to the site. The key never enters git.
#
#   bash deploy/deploy-dashboard.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEY_FILE="${REPO_DIR}/.carto-key"
PLACEHOLDER="__CARTO_KEY__"
DEST="${DEST:-pihole:/var/www/prosite/dist/ais/}"

if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: $KEY_FILE not found. Create it with your CARTO basemaps API key." >&2
  echo "       (cp the key from your site's .carto-key, or see the site repo.)" >&2
  exit 1
fi
CARTO_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
if [[ -z "$CARTO_KEY" ]]; then
  echo "ERROR: $KEY_FILE is empty." >&2
  exit 1
fi

# Build a temp dir with the key injected (never modify the committed source).
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/ais"
for f in index.html heatmap.html; do
  if [[ ! -f "${REPO_DIR}/web/${f}" ]]; then
    echo "ERROR: web/${f} not found" >&2
    exit 1
  fi
  sed "s/${PLACEHOLDER}/${CARTO_KEY}/g" "${REPO_DIR}/web/${f}" > "${TMP}/ais/${f}"
  echo "  patched ${f}"
done

echo "==> Deploying to ${DEST} ..."
rsync -avz --delete "${TMP}/ais/" "${DEST}"
echo "==> Done."
