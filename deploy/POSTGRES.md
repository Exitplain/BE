# SQLite → PostgreSQL 전환 가이드

코드는 환경변수 `DB_ENGINE` 으로 DB 를 고른다. **값이 없으면 SQLite**라서, 이 코드를 배포해도 서버는 바뀌지 않는다.
서버를 PostgreSQL 로 옮기는 것은 아래 순서로 **따로** 한다.

## 환경변수
| 이름 | 설명 | 예 |
|---|---|---|
| `DB_ENGINE` | `postgres` 이면 PostgreSQL. 비우면 SQLite | `postgres` |
| `DB_NAME` | 데이터베이스 이름 (필수) | `exitplain` |
| `DB_USER` | 사용자 (필수) | `exitplain` |
| `DB_PASSWORD` | 비밀번호 (필수, **비밀 값**) | |
| `DB_HOST` | 기본 `127.0.0.1` | RDS 엔드포인트 |
| `DB_PORT` | 기본 `5432` | |
| `DB_SSLMODE` | RDS 등 암호화 연결이 필요할 때 | `require` |
| `DB_CONN_MAX_AGE` | 연결 재사용 초, 기본 60 | |

필수 값이 빠지면 서버가 시작하지 않고 어떤 값이 없는지 알려 준다. `.env` 의 비밀번호는 채팅·깃에 올리지 않는다.

## 어디에 PostgreSQL 을 둘까
| 방식 | 장점 | 단점 |
|---|---|---|
| **EC2 에 직접 설치** | 추가 비용 없음 | t3.small(2GiB) 은 Chromium 과 메모리를 나눠 써서 빠듯함. 백업을 직접 해야 함 |
| **AWS RDS** | 서버 부담 없음, 자동 백업 | 월 비용 |

## A. EC2 에 설치하는 경우
```bash
sudo apt install -y postgresql
sudo -u postgres psql -c "CREATE USER exitplain WITH PASSWORD '<강한 비밀번호>';"
sudo -u postgres psql -c "CREATE DATABASE exitplain OWNER exitplain ENCODING 'UTF8';"
free -h    # 설치 후 available 메모리를 꼭 확인한다 (200Mi 아래면 위험)
```

## 전환 순서 (서비스가 잠깐 멈춘다)
1. **백업**: `cp /srv/exitplain/data/db.sqlite3 /srv/exitplain/data/db.sqlite3.bak`
2. 새 DB 를 만들고(위) `.env` 에 `DB_*` 를 넣되 **아직 `DB_ENGINE` 은 넣지 않는다.**
3. **데이터를 내보낸다** (SQLite 상태에서):
   ```bash
   cd /srv/exitplain/BE/backend && set -a && source /srv/exitplain/.env && set +a
   PYTHONPATH=/srv/exitplain/BE /srv/exitplain/venv/bin/python manage.py dumpdata \
     --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission \
     --exclude sessions.session --indent 2 -o /srv/exitplain/data/dump.json
   ```
   (시연 데이터뿐이면 이 단계를 건너뛰고 "새로 시작"해도 된다. 5번에서 `seed_sample_project` 만 다시 실행.)
4. `.env` 에 `DB_ENGINE=postgres` 를 추가하고 `sudo systemctl stop exitplain-web exitplain-worker`
5. PostgreSQL 에 테이블을 만든다:
   ```bash
   PYTHONPATH=/srv/exitplain/BE /srv/exitplain/venv/bin/python manage.py migrate
   ```
6. **데이터를 넣는다** (내보낸 경우):
   ```bash
   PYTHONPATH=/srv/exitplain/BE /srv/exitplain/venv/bin/python manage.py loaddata /srv/exitplain/data/dump.json
   ```
7. 서비스를 켜고 확인한다: `sudo systemctl start exitplain-web exitplain-worker`,
   `curl -s -o /dev/null -w "%{http_code}\n" https://exitplain.store/api/v1/sample/projects` 가 200.
8. 로그인(이메일 코드), 샘플 프로젝트, 실행 한 번을 확인한다.

## 되돌리기
`.env` 에서 `DB_ENGINE` 줄을 지우고(또는 `sqlite`) 서비스를 재시작하면 SQLite 로 돌아간다. 1번에서 백업해 둔 파일이 있으면 데이터도 그대로다.
PostgreSQL 로 전환한 뒤에 쌓인 데이터는 SQLite 에 없으니, 되돌릴 거면 그 전에 판단한다.

## 주의
- 테스트 계정 비밀번호(`testaccounts`)는 `DJANGO_SECRET_KEY` 로 암호화된다. 데이터를 옮긴 뒤에도 **시크릿 키를 바꾸면 안 된다.**
- 로그인 세션은 옮기지 않는다(`--exclude sessions.session`). 전환 후 모두 다시 로그인한다.
- CI 는 SQLite 와 PostgreSQL 두 곳에서 같은 테스트를 돌린다. PostgreSQL 쪽이 실패하면 전환 전에 코드를 고친다.
