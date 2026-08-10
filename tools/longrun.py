"""長期・多市場でルールを検証する。

`backtest.py` は J-Quants の5年（2021-08〜）しか見られない。**5年では足りない。**
2021-08〜2026-08 は日経が +138% 上がった一方向の相場で、そこでパラメータを決めると
必ずその局面に最適化される。実際、押し目買いは 2021年8月以降だけを見ると1取引
+1.26%（10市場平均）だが、それ以前は +0.26% しかなかった。

このスクリプトは Yahoo から指数の全期間（日経なら1970年〜）を取り、
下降相場を含めて回す。**新しいルールを採用する前に必ずここを通すこと。**

使い方
------
    python tools/longrun.py regime  --symbol ^N225      # 局面別・年代別
    python tools/longrun.py markets                      # 11市場を横断
    python tools/longrun.py era                          # 検証窓の内と外で比較
    python tools/longrun.py leverage --symbol ^SOX       # 合成レバレッジと減価

注意: `range=max` は月足を返す。日足は period1/period2 を明示すること。
指数は配当を含まないので、買い持ちのリターンは実際より低めに出る。
ルールの比較には影響しないが、買い持ちとの比較では割り引くこと。
"""
import argparse
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from backtest import add_indicators, fetch_yahoo, metrics, signals_dip, simulate  # noqa: E402

# 日経は1970年から。ドットコム崩壊・リーマン・バブル崩壊がすべて入る
MARKETS = ["^N225", "^GSPC", "^DJI", "^IXIC", "^FTSE", "^GDAXI", "^HSI", "^AXJO",
           "EWJ", "SPY", "1321.T"]

# 押し目買いの標準設定。出口は MA25 へのタッチ（MA25割れでは切らない）
DIP_SIM = dict(stop_atr=3.0, stop_floor=0.5, stop_cap=99.0, target=1e9,
               exit_ma25=False, exit_ma25_touch=True, exit_ma130=False, max_days=30)


def load(symbol, cache={}):
    if symbol not in cache:
        cache[symbol] = add_indicators(fetch_yahoo(symbol))
        time.sleep(0.25)
    return cache[symbol]


def dip_run(d, dev_th=-4.0, bull=False, lo=210, hi=None):
    sl = d.iloc[:hi].reset_index(drop=True) if hi else d
    ent = [i for i in signals_dip(sl, dev_th, bull=bull, warmup=lo)]
    tr, held = simulate(sl, ent, **DIP_SIM)
    return tr, held, sl


def cmd_regime(args):
    d = load(args.symbol)
    print(f"{args.symbol}  {d.date.iloc[0].date()} 〜 {d.date.iloc[-1].date()}  "
          f"{len(d)}本  最大DD {(d.close / d.close.cummax() - 1).min() * 100:.1f}%")
    print(f"\n押し目買い: MA25から{args.dev}%下で買い、MA25に戻ったら売る\n")

    for bull, label in [(False, "26週線フィルタ なし"), (True, "26週線フィルタ あり")]:
        tr, held, sl = dip_run(d, args.dev, bull)
        m = metrics(tr, sl, held, size=1.0, warmup=210)
        if m is None:
            continue
        print(f"  【{label}】{m['n']}回  勝率{m['win_rate']:.0f}%  1取引{m['avg']:+.2f}%  "
              f"最悪{m['worst']:+.1f}%  最大連敗{m['max_lose_streak']}  資金DD{m['dd']:.1f}%")

    tr, held, sl = dip_run(d, args.dev, False)
    if tr.empty:
        return
    info = sl.set_index(sl.date.dt.date)
    tr["dd250"] = [info.dd250.get(x, np.nan) for x in tr.entry_date]
    tr["bull"] = [info.ma130_slope.get(x, np.nan) > 0 for x in tr.entry_date]
    tr["year"] = [x.year for x in tr.entry_date]

    print(f"\n{'局面':<32}{'取引':>6}{'勝率':>7}{'1取引':>9}{'最悪':>9}")
    for label, sub in [("26週線が上向き", tr[tr.bull]),
                       ("26週線が下向き", tr[~tr.bull]),
                       ("1年高値から -10%以内", tr[tr.dd250 > -10]),
                       ("1年高値から -10〜-20%", tr[(tr.dd250 <= -10) & (tr.dd250 > -20)]),
                       ("1年高値から -20%超（弱気相場）", tr[tr.dd250 <= -20])]:
        if len(sub) < 5:
            continue
        print(f"{label:<32}{len(sub):>6}{(sub.pnl > 0).mean() * 100:>6.0f}%"
              f"{sub.pnl.mean():>+8.2f}%{sub.pnl.min():>+8.1f}%")

    print(f"\n{'年代':<10}{'取引':>6}{'勝率':>7}{'1取引':>9}{'最悪':>9}{'指数の10年変化':>16}")
    for dec, sub in tr.groupby((tr.year // 10) * 10):
        if len(sub) < 5:
            continue
        w = d[(d.date.dt.year >= dec) & (d.date.dt.year < dec + 10)]
        ch = (w.close.iloc[-1] / w.close.iloc[0] - 1) * 100 if len(w) > 50 else np.nan
        print(f"{dec}年代{'':<3}{len(sub):>6}{(sub.pnl > 0).mean() * 100:>6.0f}%"
              f"{sub.pnl.mean():>+8.2f}%{sub.pnl.min():>+8.1f}%{ch:>+15.0f}%")


def cmd_markets(args):
    print(f"押し目買い（MA25{args.dev}% → MA25回帰）を各市場の全期間で\n")
    print(f"{'市場':<10}{'期間':>8}{'取引':>6}{'勝率':>7}{'1取引':>9}{'最悪':>8}"
          f"{'年率':>9}{'資金DD':>9}{'買持年率':>10}")
    rows = []
    for s in MARKETS:
        try:
            d = load(s)
        except Exception as exc:
            print(f"{s:<10} 取得失敗: {exc}")
            continue
        if len(d) < 800:
            continue
        tr, held, sl = dip_run(d, args.dev)
        m = metrics(tr, sl, held, size=1.0, warmup=210)
        if m is None or m["n"] < 20:
            continue
        rows.append(m)
        print(f"{s:<10}{m['years']:>7.0f}年{m['n']:>6}{m['win_rate']:>6.0f}%{m['avg']:>+8.2f}%"
              f"{m['worst']:>+7.1f}%{m['annual']:>+8.2f}%{m['dd']:>8.1f}%{m['buy_hold']:>+9.1f}%")
    if rows:
        avg = pd.DataFrame(rows)
        print(f"\n  平均: 勝率{avg.win_rate.mean():.0f}%  1取引{avg.avg.mean():+.2f}%  "
              f"年率{avg.annual.mean():+.2f}%  資金DD{avg.dd.mean():.1f}%  "
              f"vs 買い持ち{avg.buy_hold.mean():+.1f}%")
        print(f"  1取引がプラスの市場: {int((avg.avg > 0).sum())}/{len(avg)}   "
              f"買い持ちに勝った市場: {int((avg.annual > avg.buy_hold).sum())}/{len(avg)}")


def cmd_era(args):
    """検証窓（2021年8月以降）の内と外を比べる。ここが一番効く確認。"""
    cut_date = pd.Timestamp(args.cut)
    print(f"押し目買いを {cut_date.date()} の前後で分ける\n")
    print(f"{'市場':<10}{'  ── 前 ──':>26}{'  ── 後（検証窓）──':>28}")
    print(f"{'':<10}{'取引':>8}{'勝率':>8}{'1取引':>10}{'取引':>10}{'勝率':>10}{'1取引':>10}")
    pre, post = [], []
    for s in MARKETS:
        try:
            d = load(s)
        except Exception:
            continue
        cut = int((d.date < cut_date).sum())
        if cut < 700 or len(d) - cut < 200:
            continue
        t1, _, _ = dip_run(d, args.dev, hi=cut)
        t2, _, _ = dip_run(d, args.dev, lo=cut)
        if len(t1) < 10 or len(t2) < 3:
            continue
        pre.append(t1.pnl.mean())
        post.append(t2.pnl.mean())
        print(f"{s:<10}{len(t1):>8}{(t1.pnl > 0).mean() * 100:>7.0f}%{t1.pnl.mean():>+9.2f}%"
              f"{len(t2):>10}{(t2.pnl > 0).mean() * 100:>9.0f}%{t2.pnl.mean():>+9.2f}%")
    if pre:
        print(f"\n  平均: 前 {np.mean(pre):+.2f}%  /  後 {np.mean(post):+.2f}%")
        print("  後のほうが大きく良いなら、その窓で決めたパラメータは信用できない。")


def cmd_leverage(args):
    """指数に日次 n 倍を合成する。レバレッジETFの『存在しなかった期間』を見る。

    SOXL(3倍・半導体) は2010年3月設定。ドットコム崩壊の後にできた商品なので、
    実データには壊滅イベントが入っていない。^SOX に3倍を合成すると
    2000-2003 で -100%（消滅）になる。
    """
    d = load(args.symbol)
    r = d.close.pct_change().fillna(0)
    print(f"{args.symbol}  {d.date.iloc[0].date()} 〜 {d.date.iloc[-1].date()}\n")
    print(f"{'倍率':<8}{'年率':>9}{'最大DD':>10}{'MA200フィルタの年率':>22}{'そのDD':>10}{'保有日':>8}")
    for lev in (1, 2, 3):
        fee = 0.0075 / 252 if lev > 1 else 0.0
        px = (1 + lev * r - fee).cumprod()
        years = (d.date.iloc[-1] - d.date.iloc[0]).days / 365.25
        dd = (px / px.cummax() - 1).min() * 100
        ann = (px.iloc[-1] ** (1 / years) - 1) * 100

        # MA200 の上にいるときだけ持つ（レバレッジETFの定番の守り方）
        ma = d.close.rolling(200).mean().values
        cl = d.close.values
        pos, eq, held = 0, [1.0], 0
        for i in range(200, len(d)):
            eq.append(eq[-1] * (1 + pos * (lev * (cl[i] / cl[i - 1] - 1) - fee)))
            held += pos
            pos = 1 if cl[i] > ma[i] else 0
        e = np.array(eq)
        y2 = (d.date.iloc[-1] - d.date.iloc[200]).days / 365.25
        print(f"{lev}倍{'':<6}{ann:>+8.1f}%{dd:>9.1f}%{(e[-1] ** (1 / y2) - 1) * 100:>+21.1f}%"
              f"{(e / np.maximum.accumulate(e) - 1).min() * 100:>9.1f}%"
              f"{held / (len(d) - 200) * 100:>7.0f}%")
    print("\n  レバレッジを上げると長期リターンが落ちる（ボラティリティ・ドラッグ）。")
    print("  最適レバレッジの目安は μ/σ²。年率15%・σ38% の資産なら約1倍で、3倍は行き過ぎ。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="長期・多市場の検証")
    ap.add_argument("command", choices=["regime", "markets", "era", "leverage"])
    ap.add_argument("--symbol", default="^N225")
    ap.add_argument("--dev", type=float, default=-4.0, help="押し目の深さ（MA25からの乖離%）")
    ap.add_argument("--cut", default="2021-08-10", help="era で前後に割る日付")
    args = ap.parse_args(argv)
    {"regime": cmd_regime, "markets": cmd_markets,
     "era": cmd_era, "leverage": cmd_leverage}[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
