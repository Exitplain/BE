"""받은 코드(zip·공개 git)를 디스크에 안전하게 풀기. 코드를 실행하지 않는다.

- zip: 경로 탈출(../, 절대 경로)·심볼릭 링크는 버리고, 크기·파일 수·압축률 한도를 넘으면 멈춘다
  (선언된 크기를 믿지 않고 실제로 쓴 바이트를 센다).
- git: 공개 https 저장소만(허용한 호스트). 깊이 1로 받고 훅·서브모듈·LFS·로컬 파일 프로토콜을 끈다. 받은 뒤 .git 을 지운다.
- 둘 다 node_modules 같은 빌드 산출물 폴더와 비밀값으로 보이는 파일(.env, *.pem, id_rsa …)은 저장하지 않는다.
"""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from django.conf import settings

# 저장하지 않는 폴더. 빌드 결과물(dist·build·out)은 정적으로 열 수 있어 남긴다 (분석기는 따로 건너뛴다)
SKIP_DIRS = {"node_modules", ".git", ".svn", ".hg", ".next", ".nuxt", "__pycache__", ".idea", ".vscode", "coverage",
             ".turbo", ".cache", ".venv", "venv"}
SECRET_FILE = re.compile(
    r"(^|/)(\.env(\..*)?|\.npmrc|\.pypirc|\.netrc|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|credentials(\.json)?"
    r"|.*\.(pem|key|p12|pfx|jks|keystore|tfstate))$", re.I)
GIT_PATH = re.compile(r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?/?$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
CHUNK = 64 * 1024


def rmtree(path):
    """읽기 전용 파일(git pack 등)까지 지운다. 없으면 아무것도 하지 않는다."""
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if Path(path).exists():
        shutil.rmtree(path, onexc=retry)


class IntakeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def limits():
    return {"files": settings.EXITPLAIN_SOURCE_MAX_FILES, "total": settings.EXITPLAIN_SOURCE_MAX_TOTAL_BYTES,
            "file": settings.EXITPLAIN_SOURCE_MAX_FILE_BYTES, "ratio": settings.EXITPLAIN_SOURCE_MAX_RATIO}


def safe_parts(name):
    """zip 항목 이름·하위 폴더 → 안전한 상대 경로 조각. 위험하면 None."""
    name = str(name).replace("\\", "/")
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return None
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _excluded(parts):
    """저장하지 않을 이유 (없으면 None)."""
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return "excluded_dir"
    if SECRET_FILE.search("/".join(parts)):
        return "secret_like"
    return None


def _empty_stats():
    return {"files": 0, "bytes": 0, "skipped": {"excluded_dir": 0, "secret_like": 0, "too_large": 0, "unsafe_path": 0,
                                                "symlink": 0}}


def extract_zip(zip_path, dest):
    """zip 을 dest 에 푼다. 모든 항목이 한 폴더 안에 있으면(GitHub 'Download ZIP') 그 폴더를 뿌리로 쓴다."""
    lim, stats = limits(), _empty_stats()
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise IntakeError("ZIP_INVALID", "zip 파일을 열 수 없어요. 파일이 손상되지 않았는지 확인해주세요.") from None
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > lim["files"] * 2:
            raise IntakeError("SOURCE_TOO_LARGE", f"파일이 너무 많아요 (최대 {lim['files']}개).")
        tops = {(safe_parts(i.filename) or [""])[0] for i in infos}
        strip = 1 if len(tops) == 1 and all(len(safe_parts(i.filename) or []) > 1 for i in infos) else 0
        dest = Path(dest)
        for info in infos:
            parts = safe_parts(info.filename)
            if parts is None:
                stats["skipped"]["unsafe_path"] += 1
                continue
            parts = parts[strip:]
            if stat.S_ISLNK(info.external_attr >> 16):
                stats["skipped"]["symlink"] += 1
                continue
            reason = _excluded(parts)
            if reason:
                stats["skipped"][reason] += 1
                continue
            if info.file_size > lim["file"]:
                stats["skipped"]["too_large"] += 1
                continue
            if info.compress_size and info.file_size > 1_000_000 and info.file_size / info.compress_size > lim["ratio"]:
                raise IntakeError("ZIP_UNSAFE", "압축률이 비정상적으로 높은 파일이 있어 받지 않았어요.")
            if stats["files"] + 1 > lim["files"]:
                raise IntakeError("SOURCE_TOO_LARGE", f"파일이 너무 많아요 (최대 {lim['files']}개).")
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(CHUNK):
                    written += len(chunk)
                    if written > lim["file"] or stats["bytes"] + written > lim["total"]:
                        raise IntakeError("SOURCE_TOO_LARGE", "압축을 푼 크기가 한도를 넘어요.")
                    out.write(chunk)
            stats["files"] += 1
            stats["bytes"] += written
    if not stats["files"]:
        raise IntakeError("SOURCE_EMPTY", "받을 수 있는 파일이 없어요.")
    return stats


def validate_git_url(url):
    """공개 https 저장소 주소만. 정규화한 주소를 돌려준다."""
    p = urlsplit(str(url or "").strip())
    hosts = settings.EXITPLAIN_SOURCE_GIT_HOSTS
    if p.scheme != "https" or p.username or p.password or p.port or p.query or p.fragment:
        raise IntakeError("GIT_URL_INVALID", "https 로 시작하는 공개 저장소 주소를 입력해주세요.")
    if (p.hostname or "").lower() not in hosts:
        raise IntakeError("GIT_URL_INVALID", f"지금은 {', '.join(hosts)} 의 공개 저장소만 받을 수 있어요.")
    if not GIT_PATH.match(p.path):
        raise IntakeError("GIT_URL_INVALID", "저장소 주소는 https://호스트/소유자/저장소 형태여야 해요.")
    return f"https://{p.hostname.lower()}{p.path.rstrip('/')}"


def validate_ref(ref):
    ref = (ref or "").strip()
    if ref and (not REF.match(ref) or ".." in ref):
        raise IntakeError("GIT_REF_INVALID", "브랜치·태그 이름을 확인해주세요.")
    return ref


def validate_subdirectory(sub):
    sub = (sub or "").strip().strip("/")
    if not sub:
        return ""
    parts = safe_parts(sub)
    if parts is None:
        raise IntakeError("SUBDIRECTORY_INVALID", "하위 폴더 경로를 확인해주세요.")
    return "/".join(parts)


def _git(args, cwd=None, timeout=None):
    home = tempfile.mkdtemp(prefix="exitplain-git-home-")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ASKPASS": "echo",
           "SystemRoot": os.environ.get("SystemRoot", "")}
    hardening = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.hooksPath=" + os.devnull,
                 "-c", "core.symlinks=false", "-c", "submodule.recurse=false", "-c", "credential.helper="]
    try:
        return subprocess.run(["git", *hardening, *args], cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=timeout or settings.EXITPLAIN_SOURCE_GIT_TIMEOUT)
    except FileNotFoundError:
        raise IntakeError("GIT_UNAVAILABLE", "서버에서 git 을 쓸 수 없어요. 관리자에게 알려주세요.") from None
    except subprocess.TimeoutExpired:
        raise IntakeError("GIT_TIMEOUT", "저장소를 받는 데 너무 오래 걸려 멈췄어요. zip 으로 올려주세요.") from None
    finally:
        shutil.rmtree(home, ignore_errors=True)


def clone(url, ref, dest):
    """깊이 1로 받고 commit SHA 를 돌려준다. .git 은 지운다."""
    args = ["clone", "--depth", "1", "--single-branch", "--no-tags", "--filter=blob:limit=20m"]
    if ref:
        args += ["--branch", ref]
    done = _git([*args, "--", url, str(dest)])
    if done.returncode != 0:
        raise IntakeError("GIT_CLONE_FAILED", "저장소를 받지 못했어요. 공개 저장소인지, 브랜치 이름이 맞는지 확인해주세요.")
    head = _git(["rev-parse", "HEAD"], cwd=str(dest), timeout=30)
    sha = head.stdout.strip()
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise IntakeError("GIT_CLONE_FAILED", "받은 저장소의 commit 을 확인하지 못했어요.")
    rmtree(Path(dest) / ".git")
    return sha


def prune_tree(root):
    """git 으로 받은 트리에 zip 과 같은 규칙을 적용한다 (제외 폴더·비밀값 파일 삭제, 한도 검사)."""
    lim, stats, root = limits(), _empty_stats(), Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).parts
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS:
                rmtree(Path(dirpath) / d)
                stats["skipped"]["excluded_dir"] += 1
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            path = Path(dirpath) / name
            parts = [*rel_dir, name]
            if path.is_symlink():
                path.unlink()
                stats["skipped"]["symlink"] += 1
                continue
            if _excluded(parts) == "secret_like":
                path.unlink()
                stats["skipped"]["secret_like"] += 1
                continue
            size = path.stat().st_size
            if size > lim["file"]:
                path.unlink()
                stats["skipped"]["too_large"] += 1
                continue
            stats["files"] += 1
            stats["bytes"] += size
            if stats["files"] > lim["files"] or stats["bytes"] > lim["total"]:
                raise IntakeError("SOURCE_TOO_LARGE", "저장소가 한도보다 커요. 필요한 폴더만 zip 으로 올려주세요.")
    if not stats["files"]:
        raise IntakeError("SOURCE_EMPTY", "받을 수 있는 파일이 없어요.")
    return stats


def tree_digest(root):
    """같은 내용이면 같은 값 (경로 + 파일 SHA-256 을 정렬해 해시)."""
    root, h = Path(root), hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
