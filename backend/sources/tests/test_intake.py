"""받은 코드를 디스크에 푸는 안전 규칙 (sources/intake.py). 코드는 실행하지 않는다."""
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from sources import intake


def make_zip(entries):
    """entries: [(name, bytes) | (ZipInfo, bytes)]"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


class ZipTests(SimpleTestCase):
    def unpack(self, entries, **limits):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in.zip").write_bytes(make_zip(entries))
        with override_settings(**{f"EXITPLAIN_SOURCE_MAX_{k.upper()}": v for k, v in limits.items()}):
            stats = intake.extract_zip(tmp / "in.zip", tmp / "out")
        return stats, tmp / "out"

    def test_github_style_top_folder_is_stripped_and_secrets_are_not_stored(self):
        stats, out = self.unpack([("site-main/index.html", b"<html>"), ("site-main/assets/app.js", b"1"),
                                  ("site-main/.env", b"KEY=secret"), ("site-main/keys/server.pem", b"x"),
                                  ("site-main/node_modules/pkg/index.js", b"x")])
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()),
                         ["assets/app.js", "index.html"])
        self.assertEqual((stats["files"], stats["skipped"]["secret_like"], stats["skipped"]["excluded_dir"]), (2, 2, 1))

    def test_path_traversal_and_symlinks_are_dropped(self):
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        stats, out = self.unpack([("../evil.txt", b"x"), ("/abs.txt", b"x"), ("C:/win.txt", b"x"), (link, b"/etc/passwd"),
                                  ("ok.html", b"ok")])
        self.assertEqual([p.name for p in out.rglob("*")], ["ok.html"])
        self.assertEqual((stats["skipped"]["unsafe_path"], stats["skipped"]["symlink"]), (3, 1))
        self.assertFalse((out.parent / "evil.txt").exists())

    def test_compression_bomb_is_rejected(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            self.unpack([("zeros.txt", b"\0" * 3_000_000)])
        self.assertEqual(ctx.exception.code, "ZIP_UNSAFE")

    def test_limits_on_file_count_and_total_size(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            self.unpack([(f"f{i}.txt", b"x") for i in range(5)], files=3)
        self.assertEqual(ctx.exception.code, "SOURCE_TOO_LARGE")
        with self.assertRaises(intake.IntakeError) as ctx:
            self.unpack([("a.txt", b"a" * 600), ("b.txt", b"b" * 600)], total_bytes=1000)
        self.assertEqual(ctx.exception.code, "SOURCE_TOO_LARGE")
        stats, _ = self.unpack([("big.bin", b"x" * 500), ("small.txt", b"x")], file_bytes=100)
        self.assertEqual((stats["files"], stats["skipped"]["too_large"]), (1, 1))

    def test_not_a_zip_and_empty_zip(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bad.zip").write_bytes(b"not a zip")
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.extract_zip(tmp / "bad.zip", tmp / "out")
        self.assertEqual(ctx.exception.code, "ZIP_INVALID")
        with self.assertRaises(intake.IntakeError) as ctx:
            self.unpack([(".env", b"x")])
        self.assertEqual(ctx.exception.code, "SOURCE_EMPTY")


class GitInputTests(SimpleTestCase):
    def test_only_public_https_repositories_on_allowed_hosts(self):
        ok = intake.validate_git_url("https://GitHub.com/Exitplain/FE.git/")
        self.assertEqual(ok, "https://github.com/Exitplain/FE.git")
        for bad in ("http://github.com/a/b", "https://user:token@github.com/a/b", "https://github.com:8443/a/b",
                    "https://evil.example/a/b", "https://github.com/a", "https://github.com/a/b?x=1",
                    "git@github.com:a/b.git", "file:///etc", "https://github.com/a/b/../c"):
            with self.subTest(bad), self.assertRaises(intake.IntakeError):
                intake.validate_git_url(bad)

    def test_ref_and_subdirectory(self):
        self.assertEqual(intake.validate_ref(" release/1.2 "), "release/1.2")
        self.assertEqual(intake.validate_subdirectory("/demo-shop/"), "demo-shop")
        for bad in ("--upload-pack=x", "-b", "a..b", "a b"):
            with self.subTest(bad), self.assertRaises(intake.IntakeError):
                intake.validate_ref(bad)
        for bad in ("../x", "a/../../b", "C:/x"):
            with self.subTest(bad), self.assertRaises(intake.IntakeError):
                intake.validate_subdirectory(bad)

    def test_clone_is_hardened_and_never_prompts(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            out = "a" * 40 if "rev-parse" in args else ""
            return type("Done", (), {"returncode": 0, "stdout": out})()
        dest = Path(tempfile.mkdtemp()) / "repo"
        with patch("sources.intake.subprocess.run", side_effect=fake_run):
            self.assertEqual(intake.clone("https://github.com/a/b", "main", dest), "a" * 40)
        clone_args, kwargs = calls[0]
        joined = " ".join(clone_args)
        for flag in ("protocol.allow=never", "protocol.https.allow=always", "core.symlinks=false", "--depth 1",
                     "submodule.recurse=false", "--branch main", "-- https://github.com/a/b"):
            self.assertIn(flag, joined)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("GITHUB_TOKEN", kwargs["env"])

    def test_clone_failure_is_reported_without_git_output(self):
        def fake_run(args, **kwargs):
            return type("Done", (), {"returncode": 128, "stdout": "", "stderr": "fatal: secret path /srv/x"})()
        with patch("sources.intake.subprocess.run", side_effect=fake_run), self.assertRaises(intake.IntakeError) as ctx:
            intake.clone("https://github.com/a/b", "", Path(tempfile.mkdtemp()) / "r")
        self.assertEqual(ctx.exception.code, "GIT_CLONE_FAILED")
        self.assertNotIn("/srv", ctx.exception.message)
