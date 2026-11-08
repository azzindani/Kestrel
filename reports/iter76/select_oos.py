"""Out-of-sample bot/cell selection test across three independent trade slates.

Select cells (strategy x pair x direction x hour ...) on a TRAIN period, measure the
selected cells' net bps on a later TEST period. If selection works, the selected set
stays positive out of sample and cell means correlate across periods.
"""

import re
import sys

import numpy as np
import pandas as pd

SP = sys.argv[1]
MIN_N = 30


def norm(tok: str) -> str:
    tok = re.sub(r"^(cur_|hw33_)", "", tok)
    return re.sub(r"_hw33$", "", tok)


def load(name: str) -> pd.DataFrame:
    d = pd.read_csv(f"{SP}/{name}.csv")
    d["period"] = name
    d["strat"] = d.bot_id.str.split("-").str[3].map(norm)
    d["hour"] = (d.entry_ts // 3_600_000) % 24
    d["pair"] = d.pair.str.replace("/USDT", "", regex=False)
    d["bot"] = d.strat + "|" + d.pair + "|" + d.timeframe
    return d


P = {p: load(p) for p in ("P1", "P2", "P3")}
DIMS = {
    "strategy": ["strat"],
    "pair": ["pair"],
    "hour": ["hour"],
    "bot (strat x pair)": ["bot"],
    "strat x dir": ["strat", "direction"],
    "strat x hour": ["strat", "hour"],
    "pair x hour": ["pair", "hour"],
    "bot x dir": ["bot", "direction"],
}


def cells(d: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = d.groupby(keys).net_bps
    out = pd.DataFrame({"n": g.size(), "mean": g.mean(), "sd": g.std()})
    out["t"] = out["mean"] / (out["sd"] / np.sqrt(out["n"]))
    return out


def evaluate(train: pd.DataFrame, test: pd.DataFrame, keys: list[str]) -> dict:
    tr, te = cells(train, keys), cells(test, keys)
    tr = tr[tr.n >= MIN_N]
    both = tr.join(te, rsuffix="_te", how="inner")
    both = both[both.n_te >= MIN_N]
    rho = both["mean"].corr(both["mean_te"], method="spearman") if len(both) > 5 else np.nan
    res = {"cells": len(tr), "rho": rho}
    for name, sel in (
        ("train>0", tr[tr["mean"] > 0].index),
        ("train t>=2", tr[tr["t"] >= 2].index),
        ("top10%", tr["mean"].nlargest(max(1, len(tr) // 10)).index),
    ):
        idx = pd.MultiIndex.from_frame(test[keys]) if len(keys) > 1 else pd.Index(test[keys[0]])
        mask = idx.isin(sel)
        picked = test.loc[mask, "net_bps"]
        res[name] = (len(sel), int(mask.sum()), picked.mean() if len(picked) else np.nan)
    return res


print(f"baseline net bps/trade: " + "  ".join(f"{p} {d.net_bps.mean():+.2f} (n={len(d)})" for p, d in P.items()))
print(f"pre-cost: gross+exit-slip  " + "  ".join(
    f"{p} {(d.gross_bps + np.where(d.close_reason != 'take_profit', 5, 0)).mean():+.2f}" for p, d in P.items()))
splits = [("P1->P2", P["P1"], P["P2"]), ("P2->P3", P["P2"], P["P3"]),
          ("P1+P2->P3", pd.concat([P["P1"], P["P2"]]), P["P3"])]
for label, train, test in splits:
    print(f"\n=== {label}   test baseline {test.net_bps.mean():+.2f} bps")
    print(f"{'dimension':20s}{'cells':>6s}{'rho':>7s}   {'train>0: sel/testN/net':>26s}   {'t>=2':>22s}   {'top10%':>22s}")
    for dim, keys in DIMS.items():
        r = evaluate(train, test, keys)
        fmt = lambda x: f"{x[0]:4d}/{x[1]:6d}/{x[2]:+7.2f}"
        print(f"{dim:20s}{r['cells']:6d}{r['rho']:7.2f}   {fmt(r['train>0']):>26s}   {fmt(r['train t>=2']):>22s}   {fmt(r['top10%']):>22s}")

# cells net-positive in ALL three periods vs a label-shuffled null
print("\n=== cells net>0 in all 3 periods (n>=30 each) vs shuffled-label null (200 perms)")
rng = np.random.default_rng(7)
for dim in ("bot (strat x pair)", "strat x hour", "pair x hour", "bot x dir"):
    keys = DIMS[dim]

    def count_persistent(frames):
        cs = [cells(f, keys) for f in frames]
        cs = [c[c.n >= MIN_N]["mean"] for c in cs]
        j = pd.concat(cs, axis=1, join="inner")
        return int((j > 0).all(axis=1).sum()), len(j)

    real, tot = count_persistent(list(P.values()))
    null = []
    for _ in range(200):
        fr = []
        for d in P.values():
            s = d.copy()
            s["net_bps"] = rng.permutation(s.net_bps.values)
            fr.append(s)
        null.append(count_persistent(fr)[0])
    print(f"{dim:20s} real {real:3d}/{tot:4d}   null mean {np.mean(null):5.1f}  95th {np.percentile(null, 95):5.1f}")
