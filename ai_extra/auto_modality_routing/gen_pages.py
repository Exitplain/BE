"""Generate synthetic pages whose dominant modality is known by construction (Python 3.8 compatible).

Classes: text, image, canvas, media_ui. Each class has several layout templates; some templates are
reserved as UNSEEN (never used for training). Distractor elements (a caption on an image page, a header
on a canvas page, a description under a player) keep the classes from being trivially separable.
"""
import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1000, 800
WORDS = ("orbit lantern velvet harbor quiet signal marble river ember canvas meadow copper violet anchor "
         "window garden thunder pocket silver winter engine paper cloud forest ladder mirror cactus planet "
         "ribbon compass island tunnel whisper candle bridge saddle pepper rocket market village 여름 바다 "
         "도서관 지도 약속 시장 기차 구름 노래 정원").split()
FONTS = ["Helvetica, Arial, sans-serif", "Georgia, serif", "Menlo, monospace", "Verdana, sans-serif", "'Times New Roman', serif"]
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

TEMPLATES = {
    "text": ["article", "form", "table", "dashboard"],
    "image": ["hero", "gallery", "product", "poster"],
    "canvas": ["shapes", "particles", "chart"],
    "media_ui": ["video", "audio", "podcast", "playlist"],
}
UNSEEN = {("text", "dashboard"), ("image", "poster"), ("canvas", "chart"), ("media_ui", "playlist")}


def words(rng, lo, hi):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(lo, hi)))


def hexcolor(rng, light=None):
    if light is None:
        light = rng.random() < 0.6
    lo, hi = (200, 255) if light else (15, 90)
    return "#%02x%02x%02x" % (rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(lo, hi))


def accent(rng):
    return "hsl(%d,%d%%,%d%%)" % (rng.randint(0, 359), rng.randint(40, 90), rng.randint(35, 60))


def photo(rng, w, h, path, text=None):
    base = tuple(rng.randint(30, 220) for _ in range(3))
    img = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(base[i] * (1 - t) + (255 - base[i]) * t * 0.6) for i in range(3)))
    for _ in range(rng.randint(6, 18)):
        x0, y0 = rng.randint(0, w), rng.randint(0, h)
        r = rng.randint(w // 14, w // 3)
        col = tuple(rng.randint(0, 255) for _ in range(3))
        (d.ellipse if rng.random() < 0.6 else d.rectangle)([x0 - r, y0 - r // 2, x0 + r, y0 + r // 2], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(rng.randint(1, 6)))
    px = img.load()
    for _ in range(w * h // 12):  # grain
        x, y = rng.randrange(w), rng.randrange(h)
        v = rng.randint(-25, 25)
        p = px[x, y]
        px[x, y] = (max(0, min(255, p[0] + v)), max(0, min(255, p[1] + v)), max(0, min(255, p[2] + v)))
    if text:
        try:
            font = ImageFont.truetype(BOLD, rng.randint(w // 10, w // 6))
        except Exception:
            font = ImageFont.load_default()
        d = ImageDraw.Draw(img)
        y = h // 4
        for line in text.split("|"):
            d.text((w // 12, y), line, fill=(255, 255, 255), font=font, stroke_width=3, stroke_fill=(0, 0, 0))
            y += int(font.size * 1.25) if hasattr(font, "size") else 60
    img.save(str(path))


def page(body, bg, extra_css="", font=None, scripts=""):
    return ("<!doctype html><meta charset=utf-8><style>body{margin:0;background:%s;font-family:%s;%s}*{box-sizing:border-box}</style>"
            "%s%s" % (bg, font or "Helvetica, Arial, sans-serif", extra_css, body, scripts))


def nav(rng):
    items = "".join("<span style='margin-right:18px'>%s</span>" % rng.choice(WORDS) for _ in range(rng.randint(3, 6)))
    return "<div style='padding:10px 24px;background:%s;font-size:14px'>%s</div>" % (hexcolor(rng), items)


# ---------- text templates
def t_article(rng, d, assets):
    paras = "".join("<p style='margin:0 0 14px'>%s</p>" % words(rng, 30, 80) for _ in range(rng.randint(3, 8)))
    thumb = ""
    if rng.random() < 0.4:
        photo(rng, 260, 180, assets / (d + "_t.png"))
        thumb = "<img src='%s_t.png' style='float:right;margin:0 0 10px 16px;width:%dpx'>" % (d, rng.randint(140, 260))
    dark = rng.random() < 0.3
    fg = "#e8e8e8" if dark else "#222"
    body = "<div style='max-width:%dpx;margin:%dpx auto;padding:0 20px;color:%s;font-size:%dpx;line-height:1.6'><h1 style='font-size:%dpx'>%s</h1>%s%s</div>" % (
        rng.randint(560, 900), rng.randint(20, 60), fg, rng.randint(14, 20), rng.randint(26, 44), words(rng, 3, 7), thumb, paras)
    return page(body, "#15171a" if dark else hexcolor(rng, True), font=rng.choice(FONTS))


def t_form(rng, d, assets):
    rows = "".join("<div style='margin:14px 0'><label style='display:block;font-size:13px;margin-bottom:4px'>%s</label>"
                   "<input style='width:100%%;padding:10px;border:1px solid #999;border-radius:4px' value='%s'></div>" % (
                       words(rng, 1, 3), words(rng, 0, 2)) for _ in range(rng.randint(4, 12)))
    body = "<div style='width:%dpx;margin:%dpx auto;padding:24px;background:%s;border-radius:8px'><h2>%s</h2>%s<button style='padding:12px 28px;background:%s;color:#fff;border:0;border-radius:4px'>%s</button></div>" % (
        rng.randint(380, 620), rng.randint(20, 50), hexcolor(rng, True), words(rng, 2, 4), rows, accent(rng), words(rng, 1, 2))
    return page(body, hexcolor(rng, True), font=rng.choice(FONTS))


def t_table(rng, d, assets):
    cols = rng.randint(3, 6)
    head = "".join("<th style='text-align:left;padding:8px;background:%s;color:#fff'>%s</th>" % (accent(rng), rng.choice(WORDS)) for _ in range(cols))
    rows = "".join("<tr>%s</tr>" % "".join("<td style='padding:8px;border-bottom:1px solid #ccc'>%s</td>" % (
        rng.choice(WORDS) if rng.random() < 0.5 else rng.randint(1, 99999)) for _ in range(cols)) for _ in range(rng.randint(10, 26)))
    body = "<div style='margin:%dpx auto;width:%d%%'><table style='border-collapse:collapse;width:100%%;font-size:%dpx'><tr>%s</tr>%s</table></div>" % (
        rng.randint(16, 40), rng.randint(70, 95), rng.randint(13, 17), head, rows)
    return page(body, hexcolor(rng, True), font=rng.choice(FONTS))


def t_dashboard(rng, d, assets):  # UNSEEN: text-heavy but colourful cards
    cards = "".join("<div style='display:inline-block;width:%dpx;height:%dpx;margin:10px;padding:16px;background:%s;color:#fff;border-radius:10px;vertical-align:top'>"
                    "<div style='font-size:%dpx;font-weight:bold'>%d</div><div>%s</div></div>" % (
                        rng.randint(180, 280), rng.randint(110, 190), accent(rng), rng.randint(28, 54), rng.randint(1, 99999), words(rng, 1, 3))
                    for _ in range(rng.randint(4, 10)))
    return page("<div style='padding:20px'><h2>%s</h2>%s</div>" % (words(rng, 2, 4), cards), hexcolor(rng, True), font=rng.choice(FONTS))


# ---------- image templates
def i_hero(rng, d, assets):
    photo(rng, W, rng.randint(560, 800), assets / (d + "_p.png"))
    cap = "<div style='padding:8px 20px;font-size:13px;color:#444'>%s</div>" % words(rng, 3, 9) if rng.random() < 0.6 else ""
    return page("%s<img src='%s_p.png' style='width:100%%;display:block'>%s" % (nav(rng) if rng.random() < 0.5 else "", d, cap), hexcolor(rng))


def i_gallery(rng, d, assets):
    n = rng.choice([4, 6, 9])
    cols = 2 if n == 4 else 3
    tiles = ""
    for i in range(n):
        photo(rng, 320, 240, assets / ("%s_%d.png" % (d, i)))
        tiles += "<img src='%s_%d.png' style='width:%d%%;margin:%dpx;vertical-align:top'>" % (d, i, int(92 / cols) - 2, rng.randint(2, 8))
    return page("<div style='padding:%dpx'>%s</div>" % (rng.randint(4, 20), tiles), hexcolor(rng))


def i_product(rng, d, assets):
    photo(rng, 640, 640, assets / (d + "_p.png"))
    side = "<div style='position:absolute;left:%d%%;top:60px;width:%d%%;font-size:15px'><h2>%s</h2><p>%s</p><button style='padding:10px 22px'>%s</button></div>" % (
        rng.randint(62, 68), rng.randint(26, 32), words(rng, 2, 4), words(rng, 8, 20), words(rng, 1, 2))
    return page("<img src='%s_p.png' style='width:%d%%;display:block;margin:20px'>%s" % (d, rng.randint(52, 60), side), hexcolor(rng, True))


def i_poster(rng, d, assets):  # UNSEEN: text rendered INSIDE the image
    photo(rng, W, rng.randint(600, 800), assets / (d + "_p.png"), text="|".join(rng.choice(WORDS).upper() for _ in range(rng.randint(2, 3))))
    return page("<img src='%s_p.png' style='width:100%%;display:block'>" % d, "#000")


# ---------- canvas templates
def c_shapes(rng, d, assets):
    w, h = rng.randint(600, 1000), rng.randint(450, 800)
    js = ("<script>var c=document.getElementById('c'),x=c.getContext('2d');x.fillStyle='%s';x.fillRect(0,0,%d,%d);"
          "for(var i=0;i<%d;i++){x.fillStyle='hsl('+Math.floor(Math.random()*360)+',70%%,55%%)';x.beginPath();"
          "x.arc(Math.random()*%d,Math.random()*%d,10+Math.random()*60,0,6.3);x.fill();}</script>" % (hexcolor(rng, rng.random() < 0.4), w, h, rng.randint(15, 80), w, h))
    head = "<h3 style='margin:12px 20px'>%s</h3>" % words(rng, 2, 4) if rng.random() < 0.5 else ""
    return page("%s<canvas id=c width=%d height=%d style='display:block;margin:0 auto'></canvas>" % (head, w, h), hexcolor(rng), scripts=js)


def c_particles(rng, d, assets):
    w, h = rng.randint(700, 1000), rng.randint(500, 800)
    js = ("<script>var c=document.getElementById('c'),x=c.getContext('2d'),g=x.createLinearGradient(0,0,%d,%d);g.addColorStop(0,'%s');g.addColorStop(1,'%s');"
          "x.fillStyle=g;x.fillRect(0,0,%d,%d);x.strokeStyle='rgba(255,255,255,.4)';for(var i=0;i<%d;i++){var a=Math.random()*%d,b=Math.random()*%d;"
          "x.fillStyle='rgba(255,255,255,.8)';x.fillRect(a,b,3,3);x.beginPath();x.moveTo(a,b);x.lineTo(a+Math.random()*60-30,b+Math.random()*60-30);x.stroke();}</script>" % (
              w, h, hexcolor(rng, False), accent(rng), w, h, rng.randint(80, 400), w, h))
    return page("<canvas id=c width=%d height=%d style='display:block'></canvas>" % (w, h), "#000", scripts=js)


def c_chart(rng, d, assets):  # UNSEEN: chart drawn in canvas with axes and text labels
    w, h = 900, 520
    pts = ",".join(str(rng.randint(40, 420)) for _ in range(rng.randint(8, 16)))
    js = ("<script>var c=document.getElementById('c'),x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,%d,%d);x.strokeStyle='#ccc';"
          "for(var i=0;i<10;i++){x.beginPath();x.moveTo(60,40+i*45);x.lineTo(%d,40+i*45);x.stroke();x.fillStyle='#333';x.font='13px sans-serif';x.fillText(String(100-i*10),20,45+i*45);}"
          "var p=[%s];x.strokeStyle='%s';x.lineWidth=3;x.beginPath();for(var i=0;i<p.length;i++){var X=70+i*(%d/p.length),Y=460-p[i];i?x.lineTo(X,Y):x.moveTo(X,Y);}x.stroke();</script>" % (
              w, h, w - 20, pts, accent(rng), w - 120))
    return page("<h3 style='margin:16px 40px'>%s</h3><canvas id=c width=%d height=%d style='display:block;margin:0 auto'></canvas>" % (words(rng, 2, 4), w, h), hexcolor(rng, True), scripts=js)


# ---------- media UI templates
def m_video(rng, d, assets):
    w = rng.randint(60, 95)
    prog = rng.randint(5, 95)
    player = ("<div style='position:relative;width:%d%%;aspect-ratio:16/9;margin:%dpx auto 0;background:%s;border-radius:6px'>"
              "<div style='position:absolute;left:50%%;top:50%%;margin:-30px 0 0 -20px;border-left:56px solid #fff;border-top:34px solid transparent;border-bottom:34px solid transparent;opacity:.85'></div>"
              "<div style='position:absolute;left:0;right:0;bottom:0;height:46px;background:rgba(0,0,0,.65);color:#fff;font-size:13px;line-height:46px;padding:0 16px'>"
              "<span>&#9654;</span><span style='display:inline-block;width:60%%;height:5px;background:#666;margin:0 14px;vertical-align:middle'><i style='display:block;width:%d%%;height:5px;background:%s'></i></span>%d:%02d / %d:%02d</div></div>") % (
        w, rng.randint(10, 40), rng.choice(["#000", "#10151c", "#1c1030", "#222"]), prog, accent(rng), rng.randint(0, 9), rng.randint(0, 59), rng.randint(5, 59), rng.randint(0, 59))
    desc = "<div style='width:%d%%;margin:14px auto;color:#ddd;font-size:15px'><b>%s</b><p>%s</p></div>" % (w, words(rng, 2, 6), words(rng, 10, 30))
    return page(player + desc, rng.choice(["#111", "#fff", "#f3f3f3"]), font=rng.choice(FONTS))


def m_audio(rng, d, assets):
    bars = "".join("<i style='display:inline-block;width:5px;margin-right:3px;height:%dpx;background:%s;vertical-align:bottom'></i>" % (rng.randint(6, 80), accent(rng) if i < 30 else "#bbb") for i in range(rng.randint(40, 70)))
    photo(rng, 240, 240, assets / (d + "_c.png"))
    card = ("<div style='width:%dpx;margin:%dpx auto;padding:22px;background:%s;border-radius:14px;box-shadow:0 4px 18px rgba(0,0,0,.25)'>"
            "<img src='%s_c.png' style='width:%dpx;border-radius:8px;display:block;margin:0 auto'><h3>%s</h3><div style='height:90px'>%s</div>"
            "<div style='margin-top:12px'><span style='display:inline-block;width:46px;height:46px;border-radius:50%%;background:%s;text-align:center;line-height:46px;color:#fff'>&#9654;</span></div></div>") % (
        rng.randint(420, 640), rng.randint(30, 90), hexcolor(rng, True), d, rng.randint(140, 240), words(rng, 2, 5), bars, accent(rng))
    return page(card, hexcolor(rng))


def m_podcast(rng, d, assets):
    photo(rng, 400, 400, assets / (d + "_c.png"))
    tag = "audio" if rng.random() < 0.6 else "video"
    media = "<%s controls style='width:%dpx;display:block;margin:20px auto'></%s>" % (tag, rng.randint(300, 640), tag)
    body = "<div style='text-align:center;padding-top:%dpx'><img src='%s_c.png' style='width:%dpx;border-radius:10px'><h2>%s</h2>%s<p style='width:60%%;margin:0 auto;font-size:15px'>%s</p></div>" % (
        rng.randint(20, 60), d, rng.randint(160, 320), words(rng, 2, 5), media, words(rng, 12, 30))
    return page(body, hexcolor(rng, True), font=rng.choice(FONTS))


def m_playlist(rng, d, assets):  # UNSEEN: text-heavy rows with play buttons + a bottom player bar
    rows = "".join("<div style='padding:10px 20px;border-bottom:1px solid #ddd;font-size:15px'><span style='display:inline-block;width:30px;height:30px;border-radius:50%%;background:%s;color:#fff;text-align:center;line-height:30px;margin-right:14px'>&#9654;</span>%s<span style='float:right'>%d:%02d</span></div>" % (
        accent(rng), words(rng, 2, 4), rng.randint(1, 6), rng.randint(0, 59)) for _ in range(rng.randint(8, 16)))
    bar = "<div style='position:fixed;left:0;right:0;bottom:0;height:64px;background:#111;color:#fff;line-height:64px;padding:0 24px'>&#9654; &nbsp;<span style='display:inline-block;width:55%%;height:5px;background:#555;vertical-align:middle'><i style='display:block;width:%d%%;height:5px;background:%s'></i></span></div>" % (rng.randint(5, 90), accent(rng))
    return page("<h2 style='padding:16px 20px;margin:0'>%s</h2>%s%s" % (words(rng, 2, 4), rows, bar), "#fff", font=rng.choice(FONTS))


BUILDERS = {("text", "article"): t_article, ("text", "form"): t_form, ("text", "table"): t_table, ("text", "dashboard"): t_dashboard,
            ("image", "hero"): i_hero, ("image", "gallery"): i_gallery, ("image", "product"): i_product, ("image", "poster"): i_poster,
            ("canvas", "shapes"): c_shapes, ("canvas", "particles"): c_particles, ("canvas", "chart"): c_chart,
            ("media_ui", "video"): m_video, ("media_ui", "audio"): m_audio, ("media_ui", "podcast"): m_podcast, ("media_ui", "playlist"): m_playlist}


def ood_page(rng, kind):
    if kind == "blank":
        return page("", "#ffffff")
    if kind == "solid":
        return page("", hexcolor(rng))
    if kind == "dot":
        return page("<div style='width:6px;height:6px;background:#888;margin:%dpx'></div>" % rng.randint(50, 600), "#fff")
    if kind == "stripes":
        return page("", "repeating-linear-gradient(45deg,%s 0 14px,%s 14px 28px)" % (hexcolor(rng), hexcolor(rng)))
    return page("<div style='padding:300px 0;text-align:center;color:#bbb'>...</div>", "#fff")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--split", choices=("train", "val", "test_seen", "test_unseen", "ood"), required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    out = Path(a.out) / a.split
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    meta = []
    if a.split == "ood":
        kinds = ["blank", "solid", "dot", "stripes", "sparse"]
        for i in range(a.n):
            kind = kinds[i % len(kinds)]
            name = "ood_%04d" % i
            (out / (name + ".html")).write_text(ood_page(rng, kind), encoding="utf-8")
            meta.append({"file": name, "label": "ood", "template": kind, "split": a.split})
    else:
        pool = []
        for cls, temps in TEMPLATES.items():
            for t in temps:
                unseen = (cls, t) in UNSEEN
                if (a.split == "test_unseen") == unseen:
                    pool.append((cls, t))
        for i in range(a.n):
            cls, t = pool[i % len(pool)]
            name = "%s_%04d" % (a.split, i)
            html = BUILDERS[(cls, t)](random.Random(rng.random()), name, out)
            (out / (name + ".html")).write_text(html, encoding="utf-8")
            meta.append({"file": name, "label": cls, "template": t, "split": a.split})
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(a.split, len(meta))


if __name__ == "__main__":
    main()
