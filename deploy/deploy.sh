#!/usr/bin/env bash
# 서버(EC2)에서 실행: ./deploy/deploy.sh [브랜치, 기본 main]
set -euo pipefail

APP_ROOT=/srv/exitplain
REPO="$APP_ROOT/BE"
VENV="$APP_ROOT/venv"
BRANCH="${1:-main}"

cd "$REPO"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

"$VENV/bin/pip" install -r backend/requirements.txt
# gunicorn 은 아직 requirements.txt 에 없다. PR 로 추가하면 아래 줄은 지워도 된다.
"$VENV/bin/pip" show gunicorn >/dev/null 2>&1 || "$VENV/bin/pip" install gunicorn

# manage.py 는 systemd 가 아니라 이 셸에서 실행하므로 .env 를 직접 읽어 넣는다.
set -a
# shellcheck disable=SC1091
source "$APP_ROOT/.env"
set +a

cd backend
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput

sudo systemctl restart exitplain-web
# run_worker 가 구현되어 enable 한 뒤부터만 재시작한다.
if systemctl is-enabled --quiet exitplain-worker 2>/dev/null; then
  sudo systemctl restart exitplain-worker
fi

sleep 2
systemctl is-active exitplain-web
curl -fsS -o /dev/null -w "health check: HTTP %{http_code}\n" http://127.0.0.1:8000/admin/login/
echo "deployed branch '$BRANCH' @ $(git -C "$REPO" rev-parse --short HEAD)"
