#!/usr/bin/env bash
set -Eeuo pipefail
sha=${SSH_ORIGINAL_COMMAND:-${1:-}}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
exec 9>/run/lock/ytpaif-deploy.lock
flock -w 600 9
cd /opt/ytpaif
verify_redis_restart() {
  before=$(mktemp /tmp/ytpaif-redis-before.XXXXXX.json)
  after=$(mktemp /tmp/ytpaif-redis-after.XXXXXX.json)
  prototype/backend/.venv/bin/python prototype/backend/production_redis_check.py capture "$before"
  redis_unit=
  for candidate in redis-server.service redis.service; do
    if systemctl cat "$candidate" >/dev/null 2>&1; then redis_unit=$candidate; break; fi
  done
  test -n "$redis_unit"
  systemctl restart "$redis_unit"
  for attempt in $(seq 1 30); do
    if systemctl is-active --quiet "$redis_unit" && redis-cli ping 2>/dev/null | grep -qx PONG; then break; fi
    sleep 1
  done
  systemctl is-active --quiet "$redis_unit"
  prototype/backend/.venv/bin/python prototype/backend/production_redis_check.py compare "$before" "$after"
  rm -f "$before" "$after"
  echo "Redis restart preserved all application records"
}
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
restart_marker=/var/lib/ytpaif/redis-restart-verified-20261112-v2
if [[ ! -e "$restart_marker" ]]; then
  verify_redis_restart
  install -d -m 700 /var/lib/ytpaif
  install -m 600 /dev/null "$restart_marker"
fi
systemctl restart ytpaif.service
for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8766/api/health -o /dev/null; then
    echo "Deployed $sha successfully"
    exit 0
  fi
  sleep 3
done
false
