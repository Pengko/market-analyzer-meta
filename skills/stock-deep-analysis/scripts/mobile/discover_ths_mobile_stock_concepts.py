#!/usr/bin/env python3
"""
通过移动端同花顺获取个股当前概念标签。

示例：
  python3 discover_ths_mobile_stock_concepts.py --symbol 603031 --expect-name 安孚科技
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from common import DEFAULT_MOBILE_STOCK_APP_PACKAGE, resolve_adb_path

ADB = resolve_adb_path()
APP_PACKAGE = DEFAULT_MOBILE_STOCK_APP_PACKAGE
NOISE_TEXTS = {
    "股票概况",
    "公司亮点",
    "主营业务",
    "所属同花顺行业",
    "相关新闻",
    "同业比较",
    "F10",
    "诊股",
    "资金",
    "公告",
    "研报",
    "买入",
    "卖出",
    "功能",
    "加自选",
}


@dataclass
class Node:
    text: str
    resource_id: str
    content_desc: str
    bounds: tuple[int, int, int, int]
    clickable: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)


def run_adb(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(ADB), *args], check=check, text=True, capture_output=True)


def ensure_device() -> None:
    if not ADB.exists():
        raise FileNotFoundError(f"adb 不存在: {ADB}")
    last = ""
    for _ in range(8):
        out = run_adb("devices").stdout
        last = out
        if any("\tdevice" in line for line in out.splitlines()):
            return
        time.sleep(1.0)
    raise RuntimeError(f"移动端设备未就绪: {last.strip()}")


def launch_app() -> None:
    run_adb("shell", "monkey", "-p", APP_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    time.sleep(1.0)


def parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
    if not m:
        return None
    return tuple(int(part) for part in m.groups())  # type: ignore[return-value]


def dump_ui(tag: str) -> list[Node]:
    remote = f"/sdcard/{tag}.xml"
    local = Path(tempfile.gettempdir()) / f"{tag}.xml"
    last_error: Exception | None = None
    for _ in range(6):
        try:
            run_adb("shell", "uiautomator", "dump", remote)
            time.sleep(0.5)
            run_adb("pull", remote, str(local))
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.8)
    else:
        raise RuntimeError(f"UI dump 失败: {last_error}")
    root = ET.parse(local).getroot()
    nodes: list[Node] = []
    for raw in root.iter("node"):
        bounds = parse_bounds(raw.attrib.get("bounds", ""))
        if not bounds:
            continue
        nodes.append(
            Node(
                text=(raw.attrib.get("text") or "").strip(),
                resource_id=(raw.attrib.get("resource-id") or "").strip(),
                content_desc=(raw.attrib.get("content-desc") or "").strip(),
                bounds=bounds,
                clickable=(raw.attrib.get("clickable") or "").lower() == "true",
            )
        )
    return nodes


def tap(x: int, y: int) -> None:
    run_adb("shell", "input", "tap", str(x), str(y))
    time.sleep(0.8)


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 250) -> None:
    run_adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    time.sleep(0.8)


def find_node(nodes: list[Node], *, resource_contains: str | None = None, text: str | None = None, desc_contains: str | None = None) -> Node | None:
    for node in nodes:
        if resource_contains and resource_contains not in node.resource_id:
            continue
        if text is not None and node.text != text:
            continue
        if desc_contains and desc_contains not in node.content_desc:
            continue
        return node
    return None


def open_search() -> None:
    nodes = dump_ui("ths_stock_search_probe")
    if find_node(nodes, resource_contains="search_input"):
        return
    for node in nodes:
        if "new_title_search" in node.resource_id or "股票搜索" in node.content_desc:
            tap(*node.center)
            return
    raise RuntimeError("未找到同花顺搜索入口")


def clear_and_input(query: str) -> None:
    nodes = dump_ui("ths_stock_search_input")
    search_input = find_node(nodes, resource_contains="search_input")
    if not search_input:
        raise RuntimeError("未找到搜索输入框")
    tap(*search_input.center)
    for _ in range(12):
        run_adb("shell", "input", "keyevent", "67")
    run_adb("shell", "input", "text", query)
    time.sleep(1.0)


def open_stock_result(query: str, expect_name: str | None) -> dict[str, str]:
    nodes = dump_ui("ths_stock_search_result")
    chosen = None
    for idx, node in enumerate(nodes):
        if "stock_name" not in node.resource_id:
            continue
        y1 = node.bounds[1]
        nearby = [n for n in nodes if abs(n.bounds[1] - y1) <= 40]
        code_node = next((n for n in nearby if "stock_code" in n.resource_id and n.text), None)
        label_node = next((n for n in nearby if n.text in {"股票", "板块", "基金"}), None)
        if expect_name and node.text != expect_name:
            continue
        if label_node and label_node.text != "股票":
            continue
        if expect_name or (code_node and code_node.text == query) or (node.text == query):
            chosen = (node, code_node, label_node)
            break
        if idx == 0 and chosen is None:
            chosen = (node, code_node, label_node)
    if not chosen:
        raise RuntimeError("未找到目标股票搜索结果")
    node, code_node, label_node = chosen
    tap(*node.center)
    return {
        "selected_name": node.text,
        "selected_code": code_node.text if code_node else "",
        "selected_label": label_node.text if label_node else "",
    }


def ensure_stock_page(expect_name: str | None) -> dict[str, str]:
    nodes = dump_ui("ths_stock_page")
    title = find_node(nodes, resource_contains="stock_name_tv")
    if not title:
        title = find_node(nodes, resource_contains="navi_title_text")
    if not title:
        raise RuntimeError("未进入股票页")
    if expect_name and title.text and expect_name not in title.text:
        # 有时标题只显示简称，不强制失败
        pass
    code = next((n for n in nodes if "stock_code_tv" in n.resource_id and n.text), None)
    return {"title": title.text, "code": code.text if code else ""}


def extract_concepts(nodes: list[Node]) -> list[str]:
    tags: list[str] = []
    for node in nodes:
        if not node.text or node.text in NOISE_TEXTS:
            continue
        x1, y1, x2, y2 = node.bounds
        if not (600 <= y1 <= 1450):
            continue
        if len(node.text) <= 1:
            continue
        if re.fullmatch(r"[+\-]?\d+(\.\d+)?%?", node.text):
            continue
        if "题材" in node.text or "概念" in node.text:
            continue
        if node.resource_id.endswith("tab_name_tv") or node.resource_id.endswith("text_view"):
            tags.append(node.text)
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return deduped


def discover_stock_concepts(max_swipes: int) -> list[str]:
    discovered: list[str] = []
    for _ in range(max_swipes):
        nodes = dump_ui("ths_stock_concepts")
        tags = extract_concepts(nodes)
        for tag in tags:
            if tag not in discovered:
                discovered.append(tag)
        swipe(450, 1320, 450, 760, 250)
    return discovered


def main() -> None:
    parser = argparse.ArgumentParser(description="通过移动端同花顺获取个股概念标签")
    parser.add_argument("--symbol", required=True, help="股票代码，支持 603031 或 603031.SH")
    parser.add_argument("--expect-name", help="股票名称，用于精确点击搜索结果")
    parser.add_argument("--max-swipes", type=int, default=cfg.mobile("discover", "max_swipes_concepts", default=3), help="向下滑动抓取概念区域次数")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    code = args.symbol.split(".")[0]
    ensure_device()
    launch_app()
    open_search()
    clear_and_input(code)
    search_result = open_stock_result(code, args.expect_name)
    stock_page = ensure_stock_page(args.expect_name)
    concepts = discover_stock_concepts(args.max_swipes)
    payload = {
        "symbol": args.symbol,
        "search_result": search_result,
        "stock_page": stock_page,
        "concepts": concepts,
        "source": "移动端同花顺",
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"股票页：{stock_page['title']}({stock_page['code']})")
        print(f"搜索结果：{search_result['selected_name']} | {search_result['selected_code']} | {search_result['selected_label']}")
        print("概念：")
        for tag in concepts:
            print(f"- {tag}")


if __name__ == "__main__":
    main()
