"""실행 퍼널: 페르소나들이 실제로 지나간 화면으로 과업마다 단계를 만든다 (단계 목록·이름·경로 단어를 미리 정하지 않는다).

7단계 퍼널(agent/funnel.py)은 모든 과업에 같은 쇼핑 단계를 쓰고 경로 단어로 화면을 끼워 맞춰서,
지나가지 않은 단계가 0명으로 찍히거나(예: 목록 화면 없는 사이트의 '탐색') 다른 단계로 잘못 들어간다
(예: '/delivery' → 결제 정보). 실행 퍼널은 실제 이동 기록만 쓰므로 그런 단계가 생기지 않는다.

규칙
- 단계 ID = 정규화한 화면 경로. 쿼리·조각은 빼고, 숫자·UUID·긴 해시 구간은 ':id' 로 묶는다 ('/products/12' → '/products/:id').
- 주요 단계 = 유효 세션(목표 도달·가상 이탈)의 MIN_SHARE 이상이 지나간 화면, 또는 목표에 도달한 세션이 지나간 화면.
  나머지는 단계로 만들지 않고 'other' 로 개수만 남긴다 (한두 명이 헤매다 들른 화면 때문에 퍼널이 길어지지 않게).
- 단계 순서 = 세션들이 그 화면에 처음 도달한 순번의 중앙값. 과업의 성공 경로 화면은 맨 끝.
- 단계 이름 = 그 화면에서 가장 많이 본 제목. 모든 제목에 공통으로 붙은 사이트 이름(' | 반찬로드')은 뗀다.
  제목이 없거나 다른 단계와 같으면 경로를 쓴다.
- 도달 = 세션마다 처음 도달만 1번. 이탈 위치 = 이탈한 세션이 마지막으로 본 화면 (주요 단계가 아니면 'other').
- 앞 단계를 거치지 않고 뒤 단계로 간 세션이 있으면 rate_of_previous 가 1을 넘을 수 있다. 그대로 보여준다.
"""
import re
from collections import Counter, defaultdict
from statistics import median
from urllib.parse import urlsplit

MIN_SHARE = 0.2
OTHER = "other"
_ID_SEGMENT = re.compile(r"\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?=[a-z0-9_-]*\d)[a-z0-9_-]{16,}")


def screen_id(url):
    """화면 주소 → 단계 ID. 같은 종류의 화면(상품 12번·34번)은 같은 ID가 된다."""
    path = urlsplit(url or "/").path.lower()
    segments = [":id" if _ID_SEGMENT.fullmatch(s) else s for s in path.split("/") if s]
    return "/" + "/".join(segments)


_TITLE_SEPARATORS = (" | ", " - ", " · ", " :: ")


def _strip_site_name(titles):
    """'장바구니 | 반찬로드', '주문 완료 | 반찬로드' 처럼 모든 제목에 붙은 사이트 이름(구분자 뒤 공통 꼬리)을 뗀다."""
    for sep in _TITLE_SEPARATORS:
        tails = {t.rsplit(sep, 1)[1] for t in titles if sep in t}
        if len(tails) == 1 and all(sep in t for t in titles) and len(titles) > 1:
            return {t: t.rsplit(sep, 1)[0].strip() or t for t in titles}
    return {t: t for t in titles}


def _first_visits(screens):
    seen, order = set(), []
    for screen in screens:
        key = screen_id(screen.get("url"))
        if key not in seen:
            seen.add(key)
            order.append(key)
    return order


def _rate(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _range(values):
    values = list(values)
    return {"min": min(values), "max": max(values)} if values else {"min": None, "max": None}


def journey_funnel(rows, *, success_path=None, seeds=1):
    """rows: 유효 세션 [{kind: success|dropout, seed, segment: (id, name), screens: [{url, title}, ...]}] (화면 순서대로)."""
    rows = [r for r in rows if r.get("screens")]
    valid = len(rows)
    visits = [_first_visits(r["screens"]) for r in rows]
    counts, positions, titles = Counter(), defaultdict(list), defaultdict(Counter)
    for row, order in zip(rows, visits):
        counts.update(order)
        for index, key in enumerate(order):
            positions[key].append(index)
        for screen in row["screens"]:
            if screen.get("title"):
                titles[screen_id(screen.get("url"))][screen["title"].strip()] += 1
    on_success = {key for row, order in zip(rows, visits) if row["kind"] == "success" for key in order}
    main = {key for key in counts if counts[key] >= MIN_SHARE * valid or key in on_success}
    goal = screen_id(success_path) if success_path else None
    stages = sorted(main, key=lambda k: (k == goal, median(positions[k]), -counts[k], k))

    best_title = {k: titles[k].most_common(1)[0][0] for k in stages if titles[k]}
    short = _strip_site_name(set(best_title.values()))
    best_title = {k: short[t] for k, t in best_title.items()}
    shared = {t for t, n in Counter(best_title.values()).items() if n > 1}
    names = {k: best_title[k] if best_title.get(k) and best_title[k] not in shared else k for k in stages}

    dropouts_at, heat, per_seed = Counter(), defaultdict(Counter), defaultdict(Counter)
    segments = set()
    for row, order in zip(rows, visits):
        segments.add(row["segment"])
        per_seed[row["seed"]].update(k for k in order if k in main)
        if row["kind"] == "dropout":
            last = screen_id(row["screens"][-1].get("url"))
            where = last if last in main else OTHER
            dropouts_at[where] += 1
            heat[row["segment"]][where] += 1

    funnel, previous = [], None
    for order, key in enumerate(stages):
        funnel.append({
            "id": key, "path": key, "name": names[key], "order": order,
            "reached": counts[key], "reached_range": _range(per_seed[s][key] for s in range(1, seeds + 1)),
            "rate_of_valid": _rate(counts[key], valid),
            "rate_of_previous": _rate(counts[key], previous) if previous else None,
            "dropouts_here": dropouts_at[key], "is_goal": key == goal,
        })
        previous = counts[key]
    columns = stages + ([OTHER] if dropouts_at[OTHER] else [])
    return {
        "basis": "observed_screens",
        "valid_sessions": valid,
        "stages": funnel,
        "other": {"screens": len(set(counts) - main), "dropouts": dropouts_at[OTHER]},
        "heatmap": {"stages": columns,
                    "rows": [{"segment_id": sid, "name": name, "dropouts": [heat[(sid, name)][k] for k in columns]}
                             for sid, name in sorted(segments)]},
    }
