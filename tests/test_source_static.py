"""빌드 도구 없는 정적 사이트 분기 (agent/source/static_site.py): 코드만 읽어 라우트·이동·퍼널을 찾는다."""
from pathlib import Path

from agent.source.detect_framework import detect
from agent.source.extract_routes import extract
from agent.source.static_site import regex_pattern


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


SPA_TABLE = """const s = 1;
function start(){return `<a href="/" data-route="/a">a로</a>` + more('보기','/a');}
function viewA(){return `<a href="/b">b로</a>`;}
function viewB(){if(x){go('/done');} return '';}
function finished(){return '';}
function render(){const views={'/':start,'/a':viewA,'/b':viewB,'/done':finished};return views[location.pathname]();}
document.addEventListener('click',e=>{go('/b');});
const img = `<img src="/assets/${name}.png">`;
"""


def test_root_index_html_without_package_json_is_a_static_site(tmp_path):
    write(tmp_path, "index.html", "<!doctype html><script src='/app.js'></script>")
    got = detect(tmp_path)
    assert (got["framework"], got["router"], got["status"]) == ("static", "static-html", "ok")
    assert got["evidence"] == [{"file": "index.html", "line": 1, "dependency": None}]
    write(tmp_path / "only_src", "a.js", "1")
    assert detect(tmp_path / "only_src")["reason"] == "no_package_json"        # index.html 없으면 여전히 blocked


def test_spa_route_table_views_and_funnel(tmp_path):
    write(tmp_path, "index.html", "<!doctype html><a href='/a'>헤더</a>")
    write(tmp_path, "assets/app.js", SPA_TABLE)
    out = extract(tmp_path, "/", "/done")
    assert out["spa"] is True and out["status"] == "ok"
    assert [r["pattern"] for r in out["routes"]] == ["/", "/a", "/b", "/done"]
    pairs = {(e["from"], e["to"], e["kind"]) for e in out["edges"]}
    assert ("/", "/a", "link") in pairs                     # data-route
    assert ("/a", "/b", "link") in pairs and ("/b", "/done", "navigate") in pairs
    assert any(e["from"] == "/" and e["to"] == "/a" and e["line"] == 2 for e in out["edges"])     # 근거 줄
    assert {e["to"] for e in out["global_edges"]} >= {"/a", "/b"}                    # 헤더 링크·위임 클릭은 공통 이동
    assert [s["stage_key"] for s in out["funnel"]["stages"]] == ["/", "/a", "/b", "/done"]
    assert out["unresolved"] == []                                                   # 이미지 경로는 이동이 아님


def test_route_table_entries_are_not_edges(tmp_path):
    write(tmp_path, "index.html", "<!doctype html>")
    write(tmp_path, "app.js", "function render(){const v={'/':a,'/x':b};}\nfunction a(){}\nfunction b(){}\n")
    out = extract(tmp_path)
    assert out["edges"] == [] and out["global_edges"] == []


def test_if_chain_router_with_regex_routes_and_indented_views(tmp_path):
    write(tmp_path, "index.html", "<!doctype html>")
    write(tmp_path, "app.js", """(function () {
  function home() { return link(`/item/${code}`) + `<a href="/list">목록</a>`; }
  function list() { return ''; }
  function item(code) { return `<a href="/item/${code}/more">더</a>`; }
  function more(code) { go('/end'); return ''; }
  function end() { return ''; }
  function route(path) {
    let m;
    if (path === "/") return home();
    if (path === "/list") return list();
    if ((m = path.match(/^\\/item\\/(\\w+)$/))) return item(m[1]);
    if ((m = path.match(/^\\/item\\/(\\w+)\\/more$/))) return more(m[1]);
    if (path === "/end") return end();
  }
})();
""")
    out = extract(tmp_path, "/", "/end")
    assert [r["pattern"] for r in out["routes"]] == ["/", "/end", "/item/:p1", "/item/:p1/more", "/list"]
    assert {r["pattern"]: r["dynamic"] for r in out["routes"]}["/item/:p1"] is True
    pairs = {(e["from"], e["to"]) for e in out["edges"]}
    assert {("/", "/item/:p1"), ("/", "/list"), ("/item/:p1", "/item/:p1/more"), ("/item/:p1/more", "/end")} <= pairs
    stages = out["funnel"]["stages"]
    assert [s["stage_key"] for s in stages] == ["/", "/item/:p1", "/item/:p1/more", "/end"]
    assert [s["needs_gui_check"] for s in stages] == [False, True, True, False]       # 동적 경로는 화면 확인 필요


def test_multi_page_html_site(tmp_path):
    write(tmp_path, "index.html", "<a href='/about.html'>소개</a>")
    write(tmp_path, "about.html", "<a href='/docs'>문서</a>")
    write(tmp_path, "docs/index.html", "<p>끝</p>")
    out = extract(tmp_path, "/", "/docs")
    assert out["spa"] is False
    assert [s["stage_key"] for s in out["funnel"]["stages"]] == ["/", "/about.html", "/docs"]


def test_unreachable_success_is_reported_not_guessed(tmp_path):
    write(tmp_path, "index.html", "<!doctype html>")
    write(tmp_path, "app.js", "function render(){const v={'/':a,'/goal':b};}\nfunction a(){}\nfunction b(){}\n")
    out = extract(tmp_path, "/", "/goal")
    assert (out["status"], out["reason"], out["funnel"]) == ("unreachable", "success_not_reachable_from_entry", None)
    assert out["frontier"] == ["/"]


def test_regex_pattern_only_accepts_literal_segments_and_groups():
    assert regex_pattern(r"\/item\/(\w+)\/info") == "/item/:p1/info"
    assert regex_pattern(r"\/a\/(\d+)\/b\/([^/]+)") == "/a/:p1/b/:p2"
    assert regex_pattern(r"\/a|\/b") is None
    assert regex_pattern(r"\/files\/.*") is None
