"""Hand-made image features from a screenshot (numpy + PIL only, Python 3.8)."""
import numpy as np
from PIL import Image

W, H = 500, 400
CELL = 20


def entropy(counts):
    p = counts[counts > 0].astype(float)
    p /= p.sum()
    return float(-(p * np.log2(p)).sum())


def featurize(path):
    img = Image.open(path).convert("RGB").resize((W, H), Image.BILINEAR)
    rgb = np.asarray(img, dtype=np.float32)
    g = rgb.mean(axis=2)
    gx = np.abs(g[:, 1:] - g[:, :-1])
    gy = np.abs(g[1:, :] - g[:-1, :])
    e = np.hypot(gx[:-1, :], gy[:, :-1])
    f = {}
    f["edge_weak"] = float((e > 15).mean())
    f["edge_mid"] = float((e > 40).mean())
    f["edge_strong"] = float((e > 80).mean())
    f["mean_gray"] = float(g.mean())
    f["std_gray"] = float(g.std())
    f["frac_white"] = float((g > 240).mean())
    f["frac_black"] = float((g < 25).mean())
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    f["sat_mean"] = float(hsv[..., 1].mean() / 255)
    f["sat_std"] = float(hsv[..., 1].std() / 255)
    mask = hsv[..., 1] > 40
    hue_hist = np.histogram(hsv[..., 0][mask], bins=18, range=(0, 256))[0] if mask.any() else np.zeros(18)
    f["hue_entropy"] = entropy(hue_hist) if hue_hist.sum() else 0.0
    q = (rgb // 64).astype(np.int32)
    key = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    counts = np.bincount(key.ravel(), minlength=64)
    f["color_entropy"] = entropy(counts)
    f["n_colors_1pct"] = float((counts > 0.01 * key.size).sum())
    f["top_color_share"] = float(counts.max() / key.size)
    f["row_transitions"] = float((gx > 40).sum(axis=1).mean() / W)
    f["col_transitions"] = float((gy > 40).sum(axis=0).mean() / H)
    row_profile = e.mean(axis=1)
    rp = row_profile - row_profile.mean()
    acf = np.correlate(rp, rp, mode="full")[len(rp) - 1:]
    f["row_periodicity"] = float(acf[8:41].max() / acf[0]) if acf[0] > 1e-9 else 0.0
    lap = np.abs(g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    f["laplacian_mean"] = float(lap.mean())
    cells = g[:H // CELL * CELL, :W // CELL * CELL].reshape(H // CELL, CELL, W // CELL, CELL)
    cstd = cells.std(axis=(1, 3))
    cmean = cells.mean(axis=(1, 3))
    f["cell_flat"] = float((cstd < 4).mean())
    f["cell_busy"] = float((cstd > 35).mean())
    f["cell_mid"] = float(((cstd >= 4) & (cstd <= 35)).mean())
    f["cell_dark_flat"] = float(((cstd < 5) & (cmean < 50)).mean())
    ch, cw = cstd.shape
    inner = cstd[ch // 4: 3 * ch // 4, cw // 4: 3 * cw // 4]
    border = np.concatenate([cstd[:ch // 4].ravel(), cstd[3 * ch // 4:].ravel()])
    f["center_busy"] = float((inner > 25).mean())
    f["border_busy"] = float((border > 25).mean()) if border.size else 0.0
    bottom = e[int(H * 0.88):]
    top = e[:int(H * 0.12)]
    f["bottom_edge"] = float((bottom > 40).mean())
    f["top_edge"] = float((top > 40).mean())
    bg = counts.argmax()
    content = key != bg
    if content.any():
        ys, xs = np.where(content)
        f["content_bbox_area"] = float((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1) / (W * H))
        f["content_share"] = float(content.mean())
    else:
        f["content_bbox_area"] = 0.0
        f["content_share"] = 0.0
    return f

