"""리포트 내보내기 (11/16 Markdown, 11/17 PDF).

- report_markdown(report): 리포트 데이터 → Markdown 문자열
- markdown_to_html(text): 위 Markdown을 인쇄용 HTML로 (이 파일이 만드는 형식만 처리하는 작은 변환기)
- render_pdf(html): 에이전트가 쓰는 Playwright(Chromium)로 PDF 인쇄. 새 라이브러리가 필요 없다.
  서버(Docker)에서는 한글 글꼴(예: fonts-noto-cjk)이 설치돼 있어야 글자가 깨지지 않는다.
"""
import html
import re
from zoneinfo import ZoneInfo

from django.utils import timezone

KST = ZoneInfo("Asia/Seoul")      # 서버 설정(TIME_ZONE)이 UTC여도 리포트에는 한국 시간으로 적는다

STATUS_NAMES = {"final": "완료", "partial": "일부 결과"}


def _pct(rate):
    return "-" if rate is None else f"{rate * 100:.1f}%"


def _range(r, pct=False):
    if not r or r.get("min") is None:
        return "-"
    lo, hi = (_pct(r["min"]), _pct(r["max"])) if pct else (r["min"], r["max"])
    return f"{lo}" if lo == hi else f"{lo}~{hi}"


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _context(report):
    plan_data = report.plan_snapshot.data
    project = report.plan_snapshot.plan.project
    run = report.run
    return {
        "project": getattr(project, "name", ""),
        "run": (run.name or f"#{run.sequence_no}") if run else report.run_ref,
        "entry_url": (plan_data.get("site") or {}).get("entry_url", ""),
        "audience": (plan_data.get("audience") or {}).get("context", ""),
    }


def report_markdown(report):
    data, ctx = report.data, _context(report)
    s = data.get("summary", {})
    out = ["# Exitplain 진단 리포트", "",
           f"- 프로젝트: {ctx['project']}",
           f"- 실행: {ctx['run']}",
           f"- 대상 URL: {ctx['entry_url']}",
           f"- 타겟 고객: {ctx['audience']}",
           f"- 만든 시각: {timezone.localtime(report.created_at, KST):%Y-%m-%d %H:%M} KST (revision {report.revision})",
           f"- 상태: {STATUS_NAMES.get(report.status, report.status)}", ""]
    out += [f"> {note}" for note in data.get("notes", [])] + [""]
    out += ["## 요약", ""]
    out += _table(["항목", "값"], [
        ["예상 세션", s.get("expected_sessions", 0)], ["목표 도달", s.get("success", 0)],
        ["가상 이탈", s.get("dropout", 0)], ["기술 실패", s.get("technical", 0)],
        ["결과 없음", s.get("incomplete", 0)], ["달성률", _pct(s.get("achievement_rate"))]]) + [""]

    for task in data.get("tasks", []):
        ranges, kpis = task.get("seed_ranges", {}), task.get("kpis", {})
        out += [f"## 과업: {task.get('name')}", "",
                f"- 달성률 {_pct(task.get('achievement_rate'))} (시드별 {_range(ranges.get('achievement_rate'), pct=True)})",
                f"- 목표 도달 {task.get('success', 0)}명 (시드별 {_range(ranges.get('success'))}), "
                f"가상 이탈 {task.get('dropout', 0)}명 (시드별 {_range(ranges.get('dropout'))}), "
                f"기술 실패 {task.get('technical', 0)}건",
                f"- 세션 시간 중앙값 {kpis.get('median_duration_seconds') or '-'}초, "
                f"행동 수 중앙값 {kpis.get('median_steps') or '-'}", "", "### 퍼널", ""]
        out += _table(["단계", "도달", "시드별 범위", "직전 대비", "이 단계 이탈"],
                      [[f["name"], f["reached"], _range(f.get("reached_range")), _pct(f.get("rate_of_previous")),
                        f.get("dropouts_here", 0)] for f in task.get("funnel", [])]) + [""]
        top = task.get("top_causes", [])
        out += ["### 이탈 원인 Top 3", ""]
        out += [f"{i}. {c['name']}: 이탈 {c['dropouts']}명 ({_pct(c.get('share'))})" for i, c in enumerate(top, 1)] \
            or ["- 가상 이탈 없음"]
        out += ["", "### 세그먼트별", ""]
        out += _table(["세그먼트", "목표 도달", "가상 이탈", "기술 실패", "달성률"],
                      [[g["name"], g["success"], g["dropout"], g["technical"], _pct(g.get("achievement_rate"))]
                       for g in task.get("segments", [])]) + [""]

    failures = data.get("technical_failures", [])
    if failures:
        out += [f"## 기술 실패 ({len(failures)}건, 이탈률 계산에서 제외)", ""]
        out += _table(["과업", "시드", "페르소나", "상태", "사유"],
                      [[f["task_id"], f["seed"], f["persona_id"], f["status"], f.get("reason", "")]
                       for f in failures[:30]])
        if len(failures) > 30:
            out.append(f"- 외 {len(failures) - 30}건")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------- Markdown → HTML (PDF용) ----------

STYLE = """
body { font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
       color: #1f2933; font-size: 11pt; line-height: 1.5; }
h1 { font-size: 20pt; margin: 0 0 8pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #d9dee5; padding-bottom: 3pt; }
h3 { font-size: 12pt; margin: 12pt 0 4pt; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0; page-break-inside: avoid; }
th, td { border: 1px solid #d9dee5; padding: 4pt 6pt; text-align: left; }
th { background: #f3f5f8; }
blockquote { margin: 6pt 0; padding: 4pt 8pt; border-left: 3px solid #9aa5b1; color: #52606d; }
ul, ol { margin: 4pt 0 4pt 16pt; padding: 0; }
"""


def _cells(line):
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [html.escape(p.strip().replace("\\|", "|")) for p in parts]


def markdown_to_html(text, title="Exitplain 진단 리포트"):
    body, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            head, rest = _cells(rows[0]), [r for r in rows[1:] if not re.fullmatch(r"\|(-+\|)+", r.strip())]
            body.append("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in _cells(r)) + "</tr>" for r in rest)
                        + "</tbody></table>")
            continue
        for tag, pattern in (("ul", r"- (.*)"), ("ol", r"\d+\. (.*)"), ("blockquote", r"> (.*)")):
            if re.fullmatch(pattern, line):
                items = []
                while i < len(lines) and re.fullmatch(pattern, lines[i]):
                    items.append(html.escape(re.fullmatch(pattern, lines[i]).group(1)))
                    i += 1
                inner = "<br>".join(items) if tag == "blockquote" else "".join(f"<li>{x}</li>" for x in items)
                body.append(f"<{tag}>{inner}</{tag}>")
                break
        else:
            heading = re.fullmatch(r"(#{1,3}) (.*)", line)
            if heading:
                level = len(heading.group(1))
                body.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            elif line.strip():
                body.append(f"<p>{html.escape(line)}</p>")
            i += 1
    return (f"<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title>"
            f"<style>{STYLE}</style></head><body>{''.join(body)}</body></html>")


class PdfUnavailable(RuntimeError):
    pass


def render_pdf(page_html):
    """HTML → PDF 바이트. Chromium이 없거나 실행에 실패하면 PdfUnavailable."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(page_html, wait_until="load")
                return page.pdf(format="A4", print_background=True,
                                margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
            finally:
                browser.close()
    except Exception as exc:   # 브라우저 미설치, 실행 실패 등
        raise PdfUnavailable(f"{type(exc).__name__}") from exc


def export_filename(report, ext):
    return f"exitplain-report-{str(report.run_ref)[:8]}-r{report.revision}.{ext}"