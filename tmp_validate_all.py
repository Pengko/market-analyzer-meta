#!/usr/bin/env python3
"""端到端验证：build_payload('合力泰', '2026-04-29')"""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts'))
from datetime import datetime

t0 = datetime.now()
from build_stock_report import build_payload
t1 = datetime.now()
print(f"[OK] 模块加载: {(t1-t0).total_seconds():.1f}s")

t2 = datetime.now()
payload = build_payload("合力泰", "2026-04-29", checkpoint="auto")
t3 = datetime.now()
print(f"[OK] build_payload完成: {(t3-t2).total_seconds():.1f}s")
print(f"[OK] 总耗时: {(t3-t0).total_seconds():.1f}s")

print(f"\n{'='*60}")
print(f"  核心字段验证")
print(f"{'='*60}")
checks = {
    "symbol": payload.get("symbol"),
    "current_price": payload.get("current_price"),
    "trade_date": payload.get("trade_date"),
    "current_session": payload.get("current_session"),
    "market_context.status": payload.get("market_context",{}).get("status"),
    "sector_context.status": payload.get("sector_context",{}).get("status"),
    "news_sentiment.status": payload.get("news_sentiment",{}).get("status"),
    "narrative_context.status": payload.get("narrative_context",{}).get("status"),
    "auction_intent.status": payload.get("auction_intent",{}).get("status"),
    "intraday_strength.status": payload.get("intraday_strength",{}).get("status"),
    "next_day_bias.status": payload.get("next_day_bias",{}).get("status"),
    "trend_structure.status": payload.get("trend_structure",{}).get("status"),
    "chip_structure.status": payload.get("chip_structure",{}).get("status"),
    "volatility_context.status": payload.get("volatility_context",{}).get("status"),
    "financing_context.status": payload.get("financing_context",{}).get("status"),
    "fundamental": payload.get("fundamental"),
    "final_decision.signal_score": payload.get("final_decision",{}).get("signal_score"),
    "dimension_results.dragon_tiger": payload.get("dimension_results",{}).get("dragon_tiger",{}).get("status"),
    "freshness.status": payload.get("freshness",{}).get("status"),
}

all_ok = True
for name, val in checks.items():
    status = "✅" if val is not None and val != "missing" else "❌"
    if val is None:
        all_ok = False
        status = "❌"
    print(f"  {status} {name}: {val}")

print(f"\n{'='*60}")
print(f"  名称解析验证")
print(f"{'='*60}")
from build_stock_report import _resolve_symbol
name_tests = {
    "合力泰": "002217.SZ",
    "青山纸业": "600103.SH",
    "002217": "002217",
    "002217.SZ": "002217.SZ",
    "贵州茅台": "600519.SH",
}
for inp, expected in name_tests.items():
    got = _resolve_symbol(inp)
    ok = "✅" if got == expected else "❌"
    print(f"  {ok} \"{inp}\" → \"{got}\" (期望: \"{expected}\")")

print(f"\n{'='*60}")
if all_ok:
    print(f"  ✅ 全部验证通过 ({(t3-t0).total_seconds():.1f}s)")
else:
    print(f"  ❌ 存在缺失字段")
print(f"{'='*60}")
