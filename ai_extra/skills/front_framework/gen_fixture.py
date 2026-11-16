"""Generate synthetic front-end apps whose funnel is known by construction.

The expected funnel comes from the generation spec, never from running the extractor. Route
names are random syllable tokens, so an extractor that relies on a stage-name vocabulary fails.
Page structure (distractor pages, a longer detour, back links, a global header link straight to
the success page) forces the extractor to compute the shortest path instead of echoing links.
"""
import json
import random
from pathlib import Path

FRAMEWORKS = ("react_router", "next_app", "next_pages", "vue_router", "spa_state")
SYLLABLES = ["zo", "qui", "mar", "plex", "ven", "tro", "bly", "dar", "kun", "fel", "sov", "rin", "gax", "mub", "yet", "wol"]


def make_names(rng, count):
    names = set()
    while len(names) < count:
        names.add("".join(rng.choice(SYLLABLES) for _ in range(rng.choice((2, 3)))))
    names = sorted(names)
    rng.shuffle(names)
    return names


def shortest_path(links, start, goal):
    """Breadth-first shortest path over the spec's link graph (ground truth, not extractor output)."""
    prev = {start: None}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        if cur == goal:
            break
        for nxt in sorted(links.get(cur, ())):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if goal not in prev:
        return None
    path, cur = [], goal
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def pascal(token):
    return token.capitalize() + "Page"


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def spec(seed, pages, distractors, detour, cut_at, name_seed):
    srng = random.Random(seed)
    nrng = random.Random(name_seed if name_seed is not None else seed)
    total = (pages - 1) + distractors + (2 if detour else 0)
    names = make_names(nrng, total)
    main = ["/"] + ["/" + n for n in names[:pages - 1]]
    rest = names[pages - 1:]
    dead = ["/" + n for n in rest[:distractors]]
    det = ["/" + n for n in rest[distractors:distractors + 2]] if detour else []
    links = {p: [] for p in main + dead + det}
    for i in range(len(main) - 1):
        if cut_at is None or i != cut_at:
            links[main[i]].append(main[i + 1])
    for i in range(1, len(main)):
        links[main[i]].append(main[i - 1])
    for i, d in enumerate(dead):
        links[main[(i % max(1, len(main) - 1))]].append(d)
        links[d].append(main[0])
    if det and len(main) >= 4:
        links[main[1]].append(det[0])
        links[det[0]].append(det[1])
        links[det[1]].append(main[3])
    elif det:
        for d in det:
            links[d].append(main[0])
    return srng, main, dead, det, links


def gen_react_router(out, srng, pages_all, links, success):
    write(out / "package.json", json.dumps({"dependencies": {"react": "^18.2.0", "react-router-dom": "^6.22.0"}}, indent=2))
    write(out / "src/components/Header.tsx",
          f'import {{ Link }} from "react-router-dom";\nexport default function Header() {{\n  return <nav><Link to="{success}">quick</Link></nav>;\n}}\n')
    imports, entries = [], []
    for p in pages_all:
        name = "Root" if p == "/" else pascal(p[1:])
        comp = "RootPage" if p == "/" else name
        body = []
        style = srng.choice(("link", "navigate"))
        for t in links[p]:
            if style == "navigate" and t == links[p][0]:
                body.append(f'      <button onClick={{() => navigate("{t}")}}>go</button>')
            else:
                body.append(f'      <Link to="{t}">go</Link>')
        write(out / f"src/pages/{comp}.tsx",
              'import { Link, useNavigate } from "react-router-dom";\nimport Header from "../components/Header";\n'
              f"export default function {comp}() {{\n  const navigate = useNavigate();\n  return (\n    <div>\n      <Header />\n"
              + "\n".join(body) + "\n    </div>\n  );\n}\n")
        imports.append(f'import {comp} from "./pages/{comp}";')
        entries.append((p, comp))
    if srng.random() < 0.5:
        routes = "\n".join(f'        <Route path="{p}" element={{<{c} />}} />' for p, c in entries)
        app = ('import { Routes, Route } from "react-router-dom";\n' + "\n".join(imports)
               + f"\nexport default function App() {{\n  return (\n    <Routes>\n{routes}\n    </Routes>\n  );\n}}\n")
    else:
        routes = ",\n".join(f'  {{ path: "{p}", element: <{c} /> }}' for p, c in entries)
        app = ('import { createBrowserRouter } from "react-router-dom";\n' + "\n".join(imports)
               + f"\nexport const router = createBrowserRouter([\n{routes},\n]);\n")
    write(out / "src/App.tsx", app)


def gen_next_app(out, srng, pages_all, links, success):
    write(out / "package.json", json.dumps({"dependencies": {"next": "14.1.0", "react": "^18.2.0"}}, indent=2))
    write(out / "app/layout.tsx",
          f'import Link from "next/link";\nexport default function Layout({{ children }}) {{\n  return <html><body><nav><Link href="{success}">quick</Link></nav>{{children}}</body></html>;\n}}\n')
    for p in pages_all:
        group = "(grp)/" if p != "/" and srng.random() < 0.4 else ""
        rel = "" if p == "/" else p[1:] + "/"
        body = []
        client = srng.random() < 0.5
        for t in links[p]:
            if client and t == links[p][0]:
                body.append(f'      <button onClick={{() => router.push("{t}")}}>go</button>')
            else:
                body.append(f'      <Link href="{t}">go</Link>')
        head = ('"use client";\nimport { useRouter } from "next/navigation";\n' if client else "") + 'import Link from "next/link";\n'
        hook = "  const router = useRouter();\n" if client else ""
        write(out / f"app/{group}{rel}page.tsx",
              head + f"export default function Page() {{\n{hook}  return (\n    <div>\n" + "\n".join(body) + "\n    </div>\n  );\n}\n")


def gen_next_pages(out, srng, pages_all, links, success):
    write(out / "package.json", json.dumps({"dependencies": {"next": "13.5.0", "react": "^18.2.0"}}, indent=2))
    write(out / "pages/_app.tsx",
          f'import Link from "next/link";\nexport default function App({{ Component, pageProps }}) {{\n  return <><nav><Link href="{success}">quick</Link></nav><Component {{...pageProps}} /></>;\n}}\n')
    write(out / "pages/api/ping.ts", 'export default function handler(req, res) { res.redirect("/"); }\n')
    for p in pages_all:
        rel = "index" if p == "/" else p[1:]
        body = "\n".join(f'      <Link href="{t}">go</Link>' for t in links[p])
        write(out / f"pages/{rel}.tsx",
              'import Link from "next/link";\nexport default function Page() {\n  return (\n    <div>\n' + body + "\n    </div>\n  );\n}\n")


def gen_vue_router(out, srng, pages_all, links, success):
    write(out / "package.json", json.dumps({"dependencies": {"vue": "^3.4.0", "vue-router": "^4.2.0"}}, indent=2))
    imports, entries = [], []
    for p in pages_all:
        comp = "RootView" if p == "/" else pascal(p[1:]).replace("Page", "View")
        body = "\n".join(f'    <router-link to="{t}">go</router-link>' for t in links[p])
        write(out / f"src/views/{comp}.vue",
              f"<template>\n  <div>\n{body}\n  </div>\n</template>\n<script setup>\nimport {{ useRouter }} from 'vue-router';\nconst router = useRouter();\n</script>\n")
        imports.append(f"import {comp} from '../views/{comp}.vue';")
        entries.append((p, comp))
    write(out / "src/components/NavBar.vue",
          f'<template><nav><router-link to="{success}">quick</router-link></nav></template>\n')
    routes = ",\n".join(f"  {{ path: '{p}', component: {c} }}" for p, c in entries)
    write(out / "src/router/index.ts",
          "import { createRouter, createWebHistory } from 'vue-router';\n" + "\n".join(imports)
          + f"\nexport default createRouter({{\n  history: createWebHistory(),\n  routes: [\n{routes},\n  ],\n}});\n")


def gen_spa_state(out, srng, pages_all, links, success):
    write(out / "package.json", json.dumps({"dependencies": {"react": "^18.2.0"}}, indent=2))
    cases = "\n".join(f'    case "{p[1:] or "start"}": return <button onClick={{() => setStep("x")}}>next</button>;' for p in pages_all)
    write(out / "src/App.tsx",
          'import { useState } from "react";\nexport default function App() {\n  const [step, setStep] = useState("start");\n  switch (step) {\n'
          + cases + "\n    default: return null;\n  }\n}\n")


GENERATORS = {"react_router": gen_react_router, "next_app": gen_next_app, "next_pages": gen_next_pages,
              "vue_router": gen_vue_router, "spa_state": gen_spa_state}
ROUTERS = {"react_router": "react-router-v6", "next_app": "next-app", "next_pages": "next-pages",
           "vue_router": "vue-router-v4", "spa_state": "none"}


def generate(out_dir, framework, seed, pages=5, distractors=2, detour=True, cut_at=None, name_seed=None):
    out = Path(out_dir)
    srng, main, dead, det, links = spec(seed, pages, distractors, detour, cut_at, name_seed)
    success = main[-1]
    GENERATORS[framework](out, srng, main + dead + det, links, success)
    expected = {"framework": framework, "router": ROUTERS[framework], "entry": "/", "success": success,
                "routes": sorted(main + dead + det)}
    path = shortest_path(links, "/", success)  # ground truth from the spec graph, independent of the extractor
    if framework == "spa_state":
        expected.update(status="blocked", reason="no_router_detected", funnel=None)
    elif path is None:
        expected.update(status="unreachable", reason="success_not_reachable_from_entry", funnel=None)
    else:
        expected.update(status="ok", reason=None, funnel=path)
    write(out / "expected.json", json.dumps(expected, indent=2, ensure_ascii=False))
    return expected


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--framework", choices=FRAMEWORKS, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pages", type=int, default=5)
    a = ap.parse_args()
    print(json.dumps(generate(a.out, a.framework, a.seed, a.pages), indent=2))
