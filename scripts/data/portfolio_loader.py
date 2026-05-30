"""
个人持仓与交易记录加载器

skill 维护的 portfolio.yaml 用于持久化用户持仓成本、交易流水、持仓量。
分析时自动加载并渲染到报告中。
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

# Portfolio 文件路径（与 skill 根目录关联）
PORTFOLIO_PATH = Path(__file__).parent.parent.parent / "references" / "portfolio" / "portfolio.yaml"


def load_portfolio(path: Path | str | None = None) -> dict[str, Any]:
    """加载 portfolio.yaml，返回 positions 字典"""
    target = Path(path) if path else PORTFOLIO_PATH
    if not target.exists():
        return {"positions": {}}
    try:
        with target.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return {"positions": {}}
        return data
    except Exception:
        return {"positions": {}}


def get_position(symbol: str, path: Path | str | None = None) -> dict[str, Any] | None:
    """根据标准 symbol（如 600103.SH）获取单个股票的持仓信息"""
    portfolio = load_portfolio(path)
    positions = portfolio.get("positions") or {}
    # 支持带前缀或不带前缀的查询
    if symbol in positions:
        return positions[symbol]
    # 尝试补充前缀
    for key in positions:
        if key.replace(".SH", "").replace(".SZ", "").replace(".BJ", "") == symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", ""):
            return positions[key]
    return None


def render_position_section(symbol: str, current_price: float | None, path: Path | str | None = None) -> str:
    """根据持仓信息渲染 Markdown 持仓分析区块，若无持仓返回空字符串"""
    pos = get_position(symbol, path)
    if not pos:
        return ""
    hold = pos.get("hold", 0)
    if hold <= 0 and not pos.get("trades"):
        # 无持仓且无历史交易，返回空
        return ""
    avg_cost = pos.get("avg_cost", 0.0)
    name = pos.get("name", symbol)
    notes = pos.get("notes", "")
    lines = ["## 持仓情况", ""]
    if hold > 0:
        lines.append(f"- 股票名称：{name}({symbol})")
        lines.append(f"- 持仓量：{hold} 股")
        lines.append(f"- 成本价：{avg_cost:.3f} 元")
        if current_price is not None:
            pnl = (current_price - avg_cost) * hold
            pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost else 0.0
            lines.append(f"- 当前价：{current_price:.2f} 元")
            lines.append(f"- 浮盈浮亏：{pnl:+.2f} 元 ({pnl_pct:+.2f}%)")
    else:
        lines.append(f"- 股票名称：{name}({symbol})")
        lines.append("- 当前持仓：已清仓")
        lines.append(f"- 最后成本价：{avg_cost:.3f} 元")
    trades = pos.get("trades") or []
    if trades:
        lines.append("- 交易记录：")
        for t in trades:
            action = "买入" if t.get("action") == "buy" else "卖出"
            lines.append(f"  - {t.get('date', '')} {action} {t.get('quantity', 0)}股 @ {t.get('price', 0):.3f}")
    if notes:
        lines.append(f"- 备注：{notes}")
    lines.extend(["", "---", ""])
    return "\n".join(lines)
