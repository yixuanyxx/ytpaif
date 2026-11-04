#!/usr/bin/env bash
set -Eeuo pipefail
sha=${SSH_ORIGINAL_COMMAND:-${1:-}}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
exec 9>/run/lock/ytpaif-deploy.lock
flock -w 600 9
cd /opt/ytpaif
test -z "$(git status --porcelain --untracked-files=no)"
git fetch origin main
test "$sha" = "$(git rev-parse origin/main)" || { echo 'Skipping superseded commit'; exit 0; }
previous=$(git rev-parse HEAD)
rollback() {
  trap - ERR
  git checkout --detach "$previous"
  prototype/backend/.venv/bin/pip install -r prototype/backend/requirements.txt
  systemctl restart ytpaif.service
  exit 1
}
trap rollback ERR
git checkout --detach "$sha"
prototype/backend/.venv/bin/pip install -r prototype/backend/requirements.txt
systemctl restart ytpaif.service
for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8766/api/health -o /dev/null; then
    echo "Deployed $sha successfully"
    exit 0
  fi
  sleep 3
done
false
