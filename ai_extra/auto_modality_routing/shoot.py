"""Render generated .html pages to PNG with local Chrome headless (file:// only, throwaway profile)."""
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def shoot(html):
    png = html.with_suffix(".png")
    if png.exists() and png.stat().st_size > 0:
        return html.name, True
    profile = tempfile.mkdtemp(prefix="shoot_")
    try:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--window-size=1000,800", "--user-data-dir=" + profile, "--virtual-time-budget=2500",
                        "--screenshot=" + str(png), "file://" + str(html.resolve())],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        pass
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return html.name, png.exists() and png.stat().st_size > 0


def main():
    files = sorted(Path(sys.argv[1]).glob("*.html"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(shoot, files))
    bad = [n for n, ok in results if not ok]
    print("%s: %d rendered, %d failed %s" % (sys.argv[1], len(results) - len(bad), len(bad), bad[:5]))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
