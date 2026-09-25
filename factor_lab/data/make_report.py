# -*- coding: utf-8 -*-
r"""生成本地回测的**自包含 HTML 报告**（单文件，双击可看，无需 server、无需联网）。

输入：
    factor_lab/output/<backtest_dir>/equity_curves.parquet   净值曲线（列 <区间>_<strategy|pool|cash>）
    factor_lab/output/<backtest_dir>/metrics_*.parquet        各成本情景指标表
    factor_lab/output/<backtest_dir>/turnover_*.parquet       每期换手明细
    records/<...>.md                                          可选：提取"状态"与"已知局限"

输出：
    records/<策略目录>/report_v1.html（plotly.js 内嵌一次，离线可用）

用法：
    python make_report.py --strategy cb_double_low
    python make_report.py --strategy cb_double_low --scenario conservative --output out.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
REPO = LAB_DIR.parent
OUTPUT_ROOT = LAB_DIR / "output"
SEGMENTS = [("main", "主回测"), ("in_sample", "样本内"), ("out_sample", "样本外")]
COLORS = {"strategy": "#d62728", "pool": "#1f77b4", "cash": "#7f7f7f"}
NAMES = {"strategy": "策略", "pool": "可投池等权", "cash": "现金(3.83%/年)"}
SCENARIO_PREFERENCE = ["v3_fixed", "conservative", "v3_band", "neutral", "optimistic", "extreme"]

STRATEGIES = {
    "cb_double_low": {
        "label": "CB 双低",
        "backtest_dir": "cb_backtest",
        "record": REPO / "records" / "cb_double_low" / "cb_double_low_v1.md",
        "default_report": REPO / "records" / "cb_double_low" / "report_v1.html",
    },
    "etf_pool": {
        "label": "ETF 池等权",
        "backtest_dir": "etf_backtest",
        "record": REPO / "records" / "etf_pool_v1.md",
        "default_report": REPO / "records" / "etf_pool_v1" / "report_v1.html",
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 数据加载
def split_segments(curves: pd.DataFrame) -> dict:
    out = {}
    for prefix, label in SEGMENTS:
        cols = [c for c in curves.columns if c.startswith(prefix + "_")]
        if not cols:
            continue
        frame = curves[cols].dropna(how="all").copy()
        frame.columns = [c[len(prefix) + 1:] for c in cols]
        frame.index = pd.to_datetime(frame.index)
        out[label] = frame
    return out


def load_metrics(base: Path, scenario: str) -> pd.DataFrame:
    path = base / "metrics_{}.parquet".format(scenario)
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path)
    return frame.drop(columns=["turnover_detail"], errors="ignore")


def pick(frame: pd.DataFrame, prefix: str, mode: str):
    sub = frame[(frame["segment"] == prefix) & (frame["mode"] == mode)]
    return sub.iloc[0] if not sub.empty else None


def excess_stats(equity: pd.DataFrame):
    if equity is None or "strategy" not in equity.columns or "pool" not in equity.columns:
        return None, None
    s, p = equity["strategy"].dropna(), equity["pool"].dropna()
    ms = s.resample("ME").last().pct_change().dropna()
    mp = p.resample("ME").last().pct_change().dropna()
    ex = (ms - mp).dropna()
    if len(ex) < 6 or ex.std(ddof=1) == 0:
        return None, None
    te = ex.std(ddof=1) * np.sqrt(12)
    return float(((1 + ex.mean()) ** 12 - 1) / te), float(ex.mean() / ex.std(ddof=1) * np.sqrt(len(ex)))


def parse_record(path: Path) -> tuple[str, list[str]]:
    """从归档 md 提取（状态, 已知局限条目）。"""
    if not path.exists():
        return "", []
    text = path.read_text(encoding="utf-8")
    status = ""
    m = re.search(r"^- 状态：\**(.+?)\**\s*$", text, flags=re.M)
    if m:
        status = m.group(1).strip().strip("*")
    bullets = []
    sec = re.search(r"^## 已知局限\s*$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if sec:
        for line in sec.group(1).splitlines():
            s = line.strip()
            if s.startswith("- "):
                bullets.append(s[2:].strip())
    return status, bullets


# ---------------------------------------------------------------- 图表
def fig_equity(equity: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    for col in equity.columns:
        fig.add_trace(go.Scatter(x=equity.index, y=equity[col], mode="lines",
                                 name=NAMES.get(col, col), line=dict(color=COLORS.get(col), width=2)))
    fig.update_layout(title=title, height=380, hovermode="x unified", template="plotly_white",
                      yaxis_title="净值（起点=1.0）", legend=dict(orientation="h", y=1.02))
    return fig


def fig_drawdown(equity: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    for col in equity.columns:
        s = equity[col].dropna()
        dd = (s / s.cummax() - 1.0) * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd, mode="lines", name=NAMES.get(col, col),
                                 fill="tozeroy", line=dict(color=COLORS.get(col), width=1)))
    fig.update_layout(title=title, height=320, hovermode="x unified", template="plotly_white",
                      yaxis_title="回撤 %", legend=dict(orientation="h", y=1.02))
    return fig


def fig_yearly(equity: pd.DataFrame) -> go.Figure:
    s = equity["strategy"].dropna()
    yearly = s.resample("YE").last().pct_change().dropna() * 100
    if len(yearly):
        first_year_ret = (s[s.index.year == s.index[0].year].iloc[-1] / s.iloc[0] - 1) * 100
        yearly = pd.concat([pd.Series({s.index[0]: first_year_ret}), yearly]).sort_index()
    labels = [str(d.year) for d in yearly.index]
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in yearly.values]
    fig = go.Figure(go.Bar(x=labels, y=yearly.values, marker_color=colors, text=["{:+.1f}%".format(v) for v in yearly.values],
                           textposition="outside"))
    fig.update_layout(title="分年度收益（策略）", height=320, template="plotly_white", yaxis_title="%")
    return fig


def fig_monthly_heatmap(equity: pd.DataFrame) -> go.Figure:
    s = equity["strategy"].dropna()
    monthly = s.resample("ME").last().pct_change().dropna() * 100
    frame = pd.DataFrame({"y": monthly.index.year, "m": monthly.index.month, "r": monthly.values})
    pivot = frame.pivot_table(index="y", columns="m", values="r")
    pivot = pivot.reindex(columns=range(1, 13))
    fig = go.Figure(go.Heatmap(z=pivot.values, x=["%d月" % m for m in range(1, 13)], y=[str(y) for y in pivot.index],
                               colorscale="RdYlGn", zmid=0, colorbar=dict(title="%")))
    fig.update_layout(title="月度收益热力图（策略）", height=320, template="plotly_white")
    return fig


def fig_turnover(turnover: pd.DataFrame, title: str) -> go.Figure:
    t = turnover.copy()
    t["rebalance_date"] = pd.to_datetime(t["rebalance_date"])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=t["rebalance_date"], y=t["turnover"] * 100, name="单边换手率", marker_color="#ff7f0e"))
    fig.add_trace(go.Scatter(x=t["rebalance_date"], y=(t["turnover"].cumsum() / np.arange(1, len(t) + 1)) * 100,
                             name="累计均值", mode="lines", line=dict(color="#333", width=2)))
    fig.update_layout(title=title, height=320, template="plotly_white", yaxis_title="%",
                      hovermode="x unified", legend=dict(orientation="h", y=1.02))
    return fig


# ---------------------------------------------------------------- HTML
CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,'Microsoft YaHei',sans-serif;margin:0;background:#f7f8fa;color:#1a1a1a}
.wrap{max-width:1240px;margin:0 auto;padding:28px}
h1{margin:0 0 6px;font-size:26px}
h2{font-size:19px;margin:34px 0 10px;border-left:4px solid #d62728;padding-left:10px}
.meta{color:#666;font-size:14px;margin-bottom:18px}
.verdict{display:inline-block;padding:4px 12px;border-radius:14px;font-weight:600;font-size:14px}
.v-pass{background:#e3f6e6;color:#1a7f37}
.v-mod{background:#fff4e0;color:#a35b00}
.v-fail{background:#fde7e7;color:#b3261e}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:14px 0 8px}
.card{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:12px 14px}
.card .k{font-size:12px;color:#777}
.card .v{font-size:21px;font-weight:650;margin-top:3px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13.5px;border:1px solid #e6e8eb;border-radius:8px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid #eef0f2;text-align:right}
th:first-child,td:first-child{text-align:left}
th{background:#fafbfc;font-weight:600}
tr:last-child td{border-bottom:none}
ul{background:#fff;border:1px solid #e6e8eb;border-radius:8px;padding:14px 14px 14px 32px;font-size:14px;line-height:1.7}
.note{font-size:13px;color:#666;margin-top:6px}
"""


def card(label: str, value: str) -> str:
    return '<div class="card"><div class="k">{}</div><div class="v">{}</div></div>'.format(
        html.escape(label), html.escape(value))


def verdict_html(status: str) -> str:
    if not status:
        return ""
    cls = "v-pass" if "通过" in status else ("v-mod" if "需修改" in status else "v-fail")
    return '<span class="verdict {}">{} · {}</span>'.format(cls, "门槛判定", html.escape(status))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成自包含 HTML 回测报告")
    ap.add_argument("--strategy", default="cb_double_low", choices=sorted(STRATEGIES))
    ap.add_argument("--scenario", default=None, help="成本情景（须与净值曲线同一运行）")
    ap.add_argument("--backtest-dir", type=Path, default=None, help="独立回测产物目录")
    ap.add_argument("--output", default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    cfg = STRATEGIES[args.strategy]
    base = args.backtest_dir or OUTPUT_ROOT / cfg["backtest_dir"]

    avail = [p.stem.replace("metrics_", "") for p in sorted(base.glob("metrics_*.parquet"))]
    scenario = args.scenario or next((s for s in SCENARIO_PREFERENCE if s in avail), avail[0] if avail else None)
    if scenario is None:
        log("没有可用的 metrics_*.parquet")
        return 1

    tagged = base / "equity_curves_{}.parquet".format(scenario)
    if tagged.exists():
        curves_path = tagged
    elif args.strategy == "etf_pool" and not args.backtest_dir:
        curves_path = base / "equity_curves.parquet"  # legacy ETF artifact
    else:
        log("找不到同一情景的净值曲线：{}".format(tagged))
        return 2
    curves = split_segments(pd.read_parquet(curves_path))
    metrics = load_metrics(base, scenario)
    manifest_path = base / "manifest_{}.json".format(scenario)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if args.backtest_dir and args.strategy == "cb_double_low" and manifest is None:
        log("独立 CB 复核目录缺少同一情景的运行清单：{}".format(manifest_path))
        return 2
    status, limits = parse_record(cfg["record"])
    if args.backtest_dir:
        status = ""
        limits = ["这是独立复核产物，旧归档结论不适用于本次回测。", *limits]
    scen_list = [s for s in SCENARIO_PREFERENCE if s in avail] + [s for s in avail if s not in SCENARIO_PREFERENCE]

    figs, fig_count = [], 0
    def emit(fig):
        nonlocal fig_count
        figs.append(pio.to_html(fig, include_plotlyjs=(fig_count == 0), full_html=False,
                                config={"displaylogo": False, "responsive": True}))
        fig_count += 1

    # 标题 + 指标卡片（用主回测策略行 + 现算 IR/t）
    main_row = pick(metrics, "main", "strategy")
    ir, t = excess_stats(curves.get("主回测"))
    cards = []
    if main_row is not None:
        cards.append(card("年化收益（主回测）", "{:.2%}".format(float(main_row["annual_return"]))))
        cards.append(card("夏普 rf=0", "{:.3f}".format(float(main_row["sharpe_raw"]))))
        cards.append(card("最大回撤", "{:.2%}".format(float(main_row["max_drawdown"]))))
        cards.append(card("Calmar", "{:.3f}".format(float(main_row["calmar"]))))
    cards.append(card("超额 IR", "-" if ir is None else "{:.2f}".format(ir)))
    cards.append(card("超额 t", "-" if t is None else "{:+.2f}".format(t)))
    out_row = pick(metrics, "out_sample", "strategy")
    if out_row is not None:
        cards.append(card("样本外年化", "{:.2%}".format(float(out_row["annual_return"]))))
        cards.append(card("样本外换手率", "{:.2%}".format(float(out_row["turnover_avg"]))))

    # 图表：净值 / 回撤（三区间）
    for label, equity in curves.items():
        emit(fig_equity(equity, "净值曲线 · {}".format(label)))
    for label, equity in curves.items():
        emit(fig_drawdown(equity, "回撤曲线（水下期）· {}".format(label)))
    if "主回测" in curves:
        emit(fig_yearly(curves["主回测"]))
        emit(fig_monthly_heatmap(curves["主回测"]))
    for prefix, label in (("main", "主回测"), ("out_sample", "样本外")):
        path = base / "turnover_{}_{}.parquet".format(scenario, prefix)
        if path.exists():
            emit(fig_turnover(pd.read_parquet(path), "换手率 · {}".format(label)))

    # 指标对照表
    rows = []
    for prefix, label in SEGMENTS:
        for mode in ("strategy", "pool", "cash"):
            r = pick(metrics, prefix, mode)
            if r is None:
                continue
            rows.append({"区间": label, "对象": NAMES.get(mode, mode),
                         "年化": "{:.2%}".format(float(r["annual_return"])),
                         "夏普(rf=0)": "-" if pd.isna(r.get("sharpe_raw")) else "{:.3f}".format(float(r["sharpe_raw"])),
                         "最大回撤": "{:.2%}".format(float(r["max_drawdown"])) if pd.notna(r.get("max_drawdown")) else "-",
                         "Calmar": "-" if pd.isna(r.get("calmar")) else "{:.3f}".format(float(r["calmar"])),
                         "换手率/期": "-" if pd.isna(r.get("turnover_avg")) else "{:.2%}".format(float(r["turnover_avg"]))})
    table_metrics = pd.DataFrame(rows).to_html(index=False, escape=True, border=0)

    # 四情景对比表
    scen_rows = []
    for s in scen_list:
        try:
            m = pd.read_parquet(base / "metrics_{}.parquet".format(s)).drop(columns=["turnover_detail"], errors="ignore")
        except Exception:
            continue
        st_row, pl_row = pick(m, "out_sample", "strategy"), pick(m, "out_sample", "pool")
        if st_row is None:
            continue
        scen_rows.append({"情景": s,
                          "样本外年化": "{:.2%}".format(float(st_row["annual_return"])),
                          "样本外超额": "{:.2%}".format(float(st_row["annual_return"]) - float(pl_row["annual_return"])) if pl_row is not None else "-",
                          "夏普(rf=0)": "{:.3f}".format(float(st_row["sharpe_raw"])) if pd.notna(st_row.get("sharpe_raw")) else "-"})
    table_scen = pd.DataFrame(scen_rows).to_html(index=False, escape=True, border=0) if scen_rows else "<p>无</p>"

    limits_html = ("<ul>" + "".join("<li>{}</li>".format(html.escape(x)) for x in limits) + "</ul>") if limits \
        else '<p>（归档中未找到「已知局限」章节）</p>'

    body = []
    body.append("<h1>{}</h1>".format(html.escape(args.title or "{} · 本地回测报告".format(cfg["label"]))))
    body.append('<div class="meta">情景：{} ｜ 生成时间：{} ｜ 回测目录：{} ｜ 归档：{}</div>'.format(
        html.escape(scenario), pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        html.escape(str(base.resolve())), html.escape(cfg["record"].name)))
    if manifest is not None:
        shown = {key: manifest[key] for key in (
            "generated_utc", "credit_filter", "credit_tier", "top_quantile",
            "hysteresis_quantile", "weight_band", "max_weight", "commission",
            "slippage", "min_commission_sh", "min_commission_sz",
            "predictions_sha256", "daily_sha256", "code_sha256") if key in manifest}
        body.append("<h2>运行参数与输入校验</h2><pre>{}</pre>".format(
            html.escape(json.dumps(shown, ensure_ascii=False, indent=2))))
    body.append(verdict_html(status))
    body.append("<h2>指标卡片</h2><div class='cards'>{}</div>".format("".join(cards)))
    for i, f in enumerate(figs):
        body.append(f)
    body.append("<h2>指标对照表（策略 / 可投池等权 / 现金）</h2>{}".format(table_metrics))
    body.append("<h2>成本情景对比（样本外）</h2>{}".format(table_scen))
    body.append("<h2>已知局限</h2>{}".format(limits_html))
    body.append('<p class="note">数据来源：{}（本地回测）。报告为自包含单文件，plotly.js 已内嵌，可离线打开。</p>'.format(
        html.escape(str(base.resolve()))))

    html_text = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                 "<title>{}</title><style>{}</style></head><body><div class='wrap'>{}</div></body></html>").format(
        html.escape(cfg["label"]), CSS, "".join(body))

    dst = Path(args.output) if args.output else cfg["default_report"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html_text, encoding="utf-8")
    log("报告已写出: {} （{:.2f} MB，{} 个图表，情景 {}）".format(
        dst, dst.stat().st_size / 1e6, fig_count, scenario))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
