# CI/CD 가이드

## 구성
| 단계 | 파일 | 하는 일 |
|---|---|---|
| CI | `.github/workflows/ci.yml` | PR·push 마다 `check`, 마이그레이션 누락 검사, 전체 테스트 |
| CD | `.github/workflows/deploy.yml` | **Actions 탭에서 직접 실행**하면 서버에서 `deploy.sh main` 을 실행 |

- 서버 접속에 **SSH(22번)를 쓰지 않는다.** GitHub 가 AWS 에서 임시 권한(OIDC)을 받아 **SSM `SendCommand`** 로 서버 안에서 배포 스크립트를 실행한다.
- 장기 액세스 키를 GitHub 에 저장하지 않는다.
- 서버는 SSM 에 등록되어 있어야 한다 (Fleet Manager → Managed nodes 에 인스턴스가 `Online`).

## 처음 한 번 하는 AWS 설정
`<ACCOUNT_ID>` 는 내 AWS 계정 번호, `<INSTANCE_ID>` 는 `i-...` 로 바꾼다.

### 1. GitHub OIDC 공급자 추가
IAM → Identity providers → Add provider
- Provider type: **OpenID Connect**
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

### 2. 배포용 역할 만들기
IAM → Roles → Create role → **Web identity**
- Identity provider: `token.actions.githubusercontent.com`, Audience: `sts.amazonaws.com`
- 이름: `exitplain-github-deploy`
- 만든 뒤 **Trust relationships** 를 아래처럼 **main 브랜치로 제한**한다 (운영 = main, develop 은 통합용).

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:Exitplain/BE:ref:refs/heads/main"}
    }
  }]
}
```

### 3. 역할에 권한(인라인 정책) 추가
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ssm:SendCommand",
     "Resource": [
       "arn:aws:ec2:ap-northeast-2:<ACCOUNT_ID>:instance/<INSTANCE_ID>",
       "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript"
     ]},
    {"Effect": "Allow",
     "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:DescribeInstanceInformation"],
     "Resource": "*"}
  ]
}
```

### 4. GitHub 저장소 변수 등록
Settings → Secrets and variables → Actions → **Variables** 탭
| 이름 | 값 |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | 위 역할의 ARN (`arn:aws:iam::<ACCOUNT_ID>:role/exitplain-github-deploy`) |
| `EC2_INSTANCE_ID` | `i-...` |

역할 ARN 과 인스턴스 ID 는 비밀이 아니라 **Variables** 에 둔다 (Secrets 가 아님).

## 배포하는 법
1. GitHub → **Actions** → **Deploy** → **Run workflow** (브랜치 `main`)
2. 로그에서 `상태: Success` 와 `deploy.sh` 출력, `HTTP 200` 확인

## 자동 배포로 바꾸려면 (안정된 뒤)
`deploy.yml` 의 `on:` 을 아래처럼 바꾼다. CI 가 성공한 `main` 에서만 배포된다.
```yaml
on:
  workflow_dispatch:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
```
그리고 job 의 `if:` 를 `github.event.workflow_run.conclusion == 'success' || github.event_name == 'workflow_dispatch'` 로 바꾼다.
자동 배포는 마이그레이션이 실패하면 서버가 깨질 수 있으니, 배포 전에 DB 백업 단계를 먼저 넣는 것을 권한다.

## 주의
- 배포 중 문제가 생기면 서버에서 직접 확인한다: `sudo journalctl -u exitplain-web -n 50 --no-pager`
- 롤백: 서버에서 이전 커밋으로 `git checkout <이전 커밋>` 후 `./deploy.sh` (DB 마이그레이션은 되돌려지지 않으니 주의)
