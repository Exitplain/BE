# 배포 가이드 (Ubuntu / EC2 t3.small, 단일 인스턴스)

구성: `Nginx(80/443)` → `gunicorn(127.0.0.1:8000)` + `run_worker(systemd)` + SQLite. 프런트는 정적 빌드를 같은 Nginx가 서빙한다(same-origin).

## 0. 먼저 확인할 것
- **Python 버전:** `backend/requirements.txt`가 `Django==6.1.3`이다. Django 6.x는 Python 3.12 이상이 필요한 것으로 알고 있으니 `python3 --version`을 확인한다. Ubuntu 22.04(3.10)라면 3.12를 별도 설치하거나 24.04를 쓴다.
- (참고) 인수인계 문서에는 Django 5.2 LTS로 되어 있다. 버전이 어긋나 있으니 팀과 한 번 맞춘다.
- `.env`를 읽는 라이브러리(`python-dotenv` 등)가 requirements에 없다. 이 가이드는 **systemd `EnvironmentFile` + deploy.sh의 `source`** 로 주입한다. `.env`는 `KEY=value` 형식만 쓴다(`export` 금지, 값에 따옴표 불필요).

## 1. 서버 최초 세팅 (한 번만)
```bash
sudo apt update && sudo apt install -y git nginx python3-venv python3-pip

# 메모리 보강: t3.small은 2GiB라 Chromium 때문에 swap이 필요하다
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

sudo mkdir -p /srv/exitplain && sudo chown ubuntu:ubuntu /srv/exitplain
cd /srv/exitplain
git clone https://github.com/Exitplain/BE.git BE
python3 -m venv venv
mkdir -p data/artifacts static frontend
# /srv/exitplain/.env 작성 (커밋 금지). 키 목록은 아래 3절 참고.

# Playwright Chromium (agent 실행용)
./venv/bin/pip install -r BE/backend/requirements.txt
./venv/bin/pip install -r BE/requirements.lock   # agent 의존성이 여기 있다면
./venv/bin/python -m playwright install --with-deps chromium
```

## 2. 서비스 등록
```bash
cd /srv/exitplain/BE/deploy
sudo cp exitplain-web.service exitplain-worker.service /etc/systemd/system/
sudo cp nginx-exitplain.conf /etc/nginx/sites-available/exitplain
sudo ln -sf /etc/nginx/sites-available/exitplain /etc/nginx/sites-enabled/exitplain
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo systemctl daemon-reload
sudo systemctl enable --now exitplain-web
sudo systemctl enable --now exitplain-worker   # run_worker 구현됨 (대기 중인 실행을 가져와 에이전트를 돌린다)

./deploy.sh        # 이후 배포는 이 한 줄 (실행 권한은 저장소에 들어 있다. 서버에서 chmod 하면 git 이 로컬 변경으로 보고 pull 을 멈춘다)
```
`deploy.sh`가 `sudo systemctl restart`를 비밀번호 없이 쓰려면 ubuntu 계정에 sudo 권한이 필요하다(EC2 기본값은 가능).

## 3. 환경변수 (`/srv/exitplain/.env`)
인수인계 문서의 이름을 그대로 쓰고, 아래 "신규" 항목은 `settings.py` PR이 머지되어야 효과가 있다. 이미 만든 `.env`의 이름이 다르면 그쪽에 맞춘다.

| 키 | 용도 | 비고 |
|---|---|---|
| `ARTIFACT_ROOT` | 스크린샷·events.jsonl 저장 | 예: `/srv/exitplain/data/artifacts`. 비우면 `EXITPLAIN_RUN_OUTPUT_DIR`를 따른다 |
| `EXITPLAIN_RUN_OUTPUT_DIR` | B의 세션 상세가 events.jsonl을 읽는 폴더 | `ARTIFACT_ROOT`와 **같은 경로**로 둔다 (둘 중 하나만 적어도 된다) |
| `EXITPLAIN_WORKER_PROCESSES` | 워커가 동시에 띄우는 에이전트 프로세스 수 | t3.small은 1 권장 |
| `EXITPLAIN_MAX_ACTIVE_RUNS_PER_PROJECT` | 프로젝트당 동시 실행 수 | 기본 1 |
| `ALLOWED_TARGET_ORIGINS` | 테스트 대상 허용 출처 | SSRF 방어 |
| `AGENT_PYTHON` | 에이전트 실행 파이썬 | `/srv/exitplain/venv/bin/python` |
| `AGENT_POLICY` | shop_scripted / scripted / openai / claude | 데모 쇼핑몰은 shop_scripted (API 키 불필요). openai는 AGENT_MODEL·OPENAI_API_KEY 필요. claude는 ANTHROPIC_API_KEY 필요 (AGENT_MODEL 비우면 Haiku 4.5, 막힌 스텝만 Sonnet 5) |
| `ANTHROPIC_API_KEY` | Claude API 키 | `AGENT_POLICY=claude`일 때만. 워커가 에이전트 프로세스에 그대로 물려준다. 커밋 금지 |
| `AGENT_MODEL` | LLM 모델명 | LLM 정책일 때만 |
| `DROPSPOT_DEFAULT_MODEL` | Claude 모델 전체 기본값 (기본 `claude-haiku-4-5`) | 아래 용도별 변수가 비어 있을 때 쓴다. 값이 비어 있으면 무시 |
| `DROPSPOT_CLAUDE_MODEL`, `DROPSPOT_ESCALATION_MODEL` | 에이전트가 평소 고르는 모델 / 막혔을 때 다시 묻는 모델 | `AGENT_POLICY=claude`. `AGENT_MODEL`이 있으면 앞의 것은 그 값이 우선 |
| `DROPSPOT_JUDGE_MODEL` | 성공 기준 판정 모델 | |
| `DROPSPOT_EXPLORE_MODEL`, `DROPSPOT_CRITERIA_MODEL` | 사이트 둘러보기·과업 제안 / 직접 추가한 과업의 성공 기준 | 뒤의 것은 없으면 앞의 것을 따른다 |
| `EXITPLAIN_PERSONA_MODEL`, `EXITPLAIN_STAGE_NAMER_MODEL` | 페르소나 생성 / 리포트 화면 이름 | 백엔드(Django)가 읽는다 |
| `EXITPLAIN_DISCOVERY_MODE` | `demo` / `explore` | 기본 demo(고정 결과). explore는 모르는 URL을 실제로 둘러보고 Claude가 과업을 제안한다 (ANTHROPIC_API_KEY 필요, 30초~2분 걸려 gunicorn `--timeout`·Nginx `proxy_read_timeout`을 180으로 둔다) |
| `DJANGO_SECRET_KEY` | 시크릿 키 | `DEBUG=False`인데 없으면 서버가 시작되지 않는다 |
| `DJANGO_DEBUG` | 서버에서는 `False` | 없으면 `True`(로컬 개발 기본값) |
| `DJANGO_ALLOWED_HOSTS` | 공개 IP/도메인, 콤마 구분 | |
| `CSRF_TRUSTED_ORIGINS` | `http://<IP>` 또는 `https://<도메인>` | 콤마 구분 |
| `DJANGO_STATIC_ROOT` | `/srv/exitplain/static` | **Nginx `/static/` alias와 같아야 한다.** 안 맞으면 admin 화면 CSS가 404 |
| `DJANGO_DB_PATH` | `/srv/exitplain/data/db.sqlite3` | 없으면 `backend/db.sqlite3` |
| `DJANGO_HTTPS` | HTTPS를 붙인 뒤 `True` | 보안 쿠키·프록시 헤더가 켜진다 |
| `GOOGLE_OAUTH_CLIENT_ID` | 구글 로그인 클라이언트 ID | FE와 같은 값. 구글은 https 도메인만 등록 가능 (IP·http 불가, localhost 예외) |
| `EMAIL_HOST_USER` | 로그인 코드 메일을 보낼 Gmail 주소 | 비우면 메일을 보내지 않고 로그에만 찍는다 |
| `EMAIL_HOST_PASSWORD` | 그 Gmail의 **앱 비밀번호**(16자리) | 구글 계정 비밀번호가 아님. 2단계 인증을 켜야 만들 수 있다 |
| `TEST_ACCOUNT_KMS_KEY_ID` | 테스트 계정 비밀번호용 KMS 키 ARN (또는 `alias/exitplain-test-accounts`) | 비우면 Fernet(SECRET_KEY 기반). EC2 IAM 역할에 이 키의 `kms:GenerateDataKey`·`kms:Decrypt` 권한 필요. 넣은 뒤 `manage.py reencrypt_test_accounts` |
| `TEST_ACCOUNT_KMS_REGION` | KMS 리전 | 기본 `ap-northeast-2` |
| `TEST_ACCOUNT_RETENTION_DAYS` | 테스트 계정 보관 기간(일) | 기본 90. `manage.py purge_test_accounts`를 하루 한 번 실행 |
| `EXITPLAIN_SOURCE_ROOT` | 코드 연결(zip·git)로 받은 코드를 두는 폴더 | 예: `/srv/exitplain/data/source_snapshots`. 레포 밖에 둔다. nginx 로 직접 서빙하지 않는다 |
| `EXITPLAIN_SOURCE_GIT_HOSTS` | 받을 수 있는 공개 git 호스트 | 기본 `github.com,gitlab.com`. 서버에 `git` 이 설치돼 있어야 한다 |
| `EXITPLAIN_SOURCE_MAX_UPLOAD_BYTES` 등 | zip 크기·파일 수·압축률 한도 | 기본 30MB 업로드 / 200MB·1만 개 해제. nginx `source-snapshots` 위치의 `client_max_body_size` 와 맞춘다 |
| `EXITPLAIN_PUBLIC_URL` | 실측 수집 스크립트(`/api/v1/e.js`) 주소의 앞부분 | **https 프런트 주소** (예: Vercel 도메인). 고객 사이트가 https 라서 http IP 를 쓰면 mixed content 로 막힌다. 비우면 요청 주소를 쓴다 |
| `EXITPLAIN_MEASURE_PER_MINUTE` | 사이트 하나가 1분에 보낼 수 있는 수집 건수 | 기본 600. 넘는 건 조용히 버린다 |

코드 연결 nginx 설정(`source-snapshots` 의 30MB 업로드)은 `deploy.sh` 가 복사하지 않는다. 바꾼 뒤 한 번 `sudo cp deploy/nginx-exitplain.conf /etc/nginx/sites-available/exitplain && sudo nginx -t && sudo systemctl reload nginx`.

### 코드 미리보기 서버 (URL 없는 프로젝트, 한 번만)
"코드만 있어요"로 받은 정적 사이트를 에이전트가 열 수 있게 127.0.0.1:8090 에서만 내보낸다 (`backend/sources/preview.py`).
주소는 `http://s-<스냅샷 id>.localhost:8090/` 이고 Chromium 이 루프백으로 푼다. nginx·보안 그룹으로 외부에 열지 않는다.
```bash
sudo cp /srv/exitplain/BE/deploy/exitplain-preview.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now exitplain-preview
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/    # 404 면 켜진 것 (주소에 스냅샷이 없어서)
```
이후 `deploy.sh` 가 함께 재시작한다. 포트를 바꾸려면 `.env` 에 `EXITPLAIN_PREVIEW_PORT` 를 넣고 웹·워커·미리보기를 모두 재시작한다
(세 곳이 같은 값을 써야 실행 입력의 주소와 서버가 맞는다).

## 4. `settings.py` 반영 내용 (`a/deploy-settings` 브랜치, 문서 규칙상 PR로 머지)
위 3절의 환경변수를 `settings.py`가 읽도록 이미 반영했다. 환경변수가 없으면 로컬 개발 기본값으로 동작한다.
- SQLite는 WAL 모드 + `transaction_mode=IMMEDIATE` + 타임아웃 20초로 웹·워커 동시 쓰기 시 `database is locked`를 줄인다.
- DB 파일은 `DJANGO_DB_PATH`로 레포 밖에 둘 수 있다. 그래도 서버에서 `git clean -fdx`는 **실행하지 않는다**.
- 알려진 경고: `manage.py check --deploy`가 `mail.E001`(콘솔 메일 백엔드)을 에러로 낸다. 서버 실행에는 영향이 없고, 메일을 보내지 않는 동안은 무시해도 된다.

## 5. 보안 체크 (AWS)
- **보안 그룹:** 인바운드 80/443만 전체 공개, 22는 내 IP만. 8000은 열지 않는다.
- **IMDSv2 강제** (인스턴스 메타데이터 탈취 방어):
  ```bash
  aws ec2 modify-instance-metadata-options --instance-id <i-xxxx> \
    --http-tokens required --http-endpoint enabled
  ```
- **SSRF:** URL 확인 로직의 차단 목록에 사설 대역과 함께 `169.254.169.254`(링크 로컬)를 반드시 포함한다.
- **Elastic IP:** 재시작해도 주소가 바뀌지 않게 연결한다. 바뀌면 `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`가 깨진다.
- `.env`, `db.sqlite3`, `artifacts/`는 커밋하지 않는다.

## 6. HTTPS (도메인이 생겼을 때)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <도메인>
```
그다음 위 4절의 `*_COOKIE_SECURE`와 `CSRF_TRUSTED_ORIGINS`를 https로 바꾼다.

## 7. 프런트 반영
프런트 빌드 결과물(`dist` 또는 `out`)을 `/srv/exitplain/frontend/`에 복사한다. API 호출은 상대경로(`/api/...`)로 쓰면 된다.

## 8. 운영 명령
```bash
sudo journalctl -u exitplain-web -f          # 웹 로그
sudo journalctl -u exitplain-worker -f       # 워커 로그
free -h && swapon --show                     # 메모리/스왑 상태
sudo systemctl status exitplain-web
```

## 9. 첫 배포 점검 순서
1. `cat /srv/exitplain/.env` 로 3절 키가 모두 있는지 확인 (값은 화면에 공유하지 않는다).
2. `./deploy.sh <브랜치>` → 마지막 줄의 `health check: HTTP 200` 확인.
3. `sudo systemctl status exitplain-web exitplain-worker` 둘 다 `active (running)`.
4. 브라우저로 `http://<IP>/admin/` 접속 → 관리자 계정(`manage.py createsuperuser`)으로 로그인.
5. 실행 한 번: 샘플 프로젝트 등록 → 계획 확정 → `POST /api/v1/runs` → `GET /api/v1/runs/{id}` 가 `completed` 가 되는지 본다.
   안 끝나면 `sudo journalctl -u exitplain-worker -f` 로 에이전트 오류를 확인한다.
6. `free -h` 를 보면서 실행한다. 메모리가 부족하면 `.env` 의 `EXITPLAIN_WORKER_PROCESSES=1` 을 유지하고, 계획의 동시 실행(concurrency) 값을 낮춘다 (기본 5는 t3.small에 무겁다).
