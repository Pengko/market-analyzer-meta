#!/usr/bin/env python3
"""
通过移动端同花顺概念板块页的“龙头股”标签获取当前龙头/前排。

示例：
  python3 discover_ths_mobile_theme_leaders.py --query 886032 --expect-name 固态电池
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
    "成分股",
    "龙头股",
    "板块统计",
    "板块掘金",
    "买板块",
    "加自选",
    "功能",
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
    for _ in range(3):
        nodes = dump_ui("ths_leader_search_probe")
        if find_node(nodes, resource_contains="search_input"):
            return
        for node in nodes:
            if "new_title_search" in node.resource_id or "股票搜索" in node.content_desc:
                tap(*node.center)
                time.sleep(1.0)
                break
        else:
            break
    raise RuntimeError("未找到同花顺搜索入口")


def clear_and_input(query: str) -> None:
    nodes = dump_ui("ths_leader_search_input")
    search_input = find_node(nodes, resource_contains="search_input")
    if not search_input:
        search_input = next(
            (
                node for node in nodes
                if ("search" in node.resource_id.lower() or "搜索" in node.content_desc or "搜索" in node.text)
                and node.bounds[1] <= 220
            ),
            None,
        )
    if not search_input:
        raise RuntimeError("未找到搜索输入框")
    tap(*search_input.center)
    for _ in range(12):
        run_adb("shell", "input", "keyevent", "67")
    run_adb("shell", "input", "text", query)
    time.sleep(1.0)


def open_query_result(query: str, expect_name: str | None) -> dict[str, str]:
    nodes = dump_ui("ths_leader_search_result")
    selected_name = None
    selected_code = None
    selected_label = None
    selected_bounds = None
    for idx, node in enumerate(nodes):
        if "stock_name" not in node.resource_id:
            continue
        if expect_name and node.text != expect_name:
            continue
        y1 = node.bounds[1]
        nearby = [n for n in nodes if abs(n.bounds[1] - y1) <= 40]
        code_node = next((n for n in nearby if "stock_code" in n.resource_id and n.text), None)
        label_node = next((n for n in nearby if n.text in {"板块", "股票", "基金"}), None)
        if expect_name or (code_node and code_node.text == query) or (node.text == query):
            selected_name = node.text
            selected_code = code_node.text if code_node else ""
            selected_label = label_node.text if label_node else ""
            selected_bounds = node.bounds
            break
        if idx == 0 and selected_name is None:
            selected_name = node.text
            selected_code = code_node.text if code_node else ""
            selected_label = label_node.text if label_node else ""
            selected_bounds = node.bounds
    if not selected_name or not selected_bounds:
        raise RuntimeError("未找到目标搜索结果")
    tap(*((selected_bounds[0] + selected_bounds[2]) // 2, (selected_bounds[1] + selected_bounds[3]) // 2))
    return {
        "selected_name": selected_name,
        "selected_code": selected_code,
        "selected_label": selected_label,
    }


def ensure_concept_page() -> dict[str, str]:
    nodes = dump_ui("ths_leader_concept_page")
    title = find_node(nodes, resource_contains="navi_title_text")
    if not title:
        raise RuntimeError("未进入概念页")
    code = next((n for n in nodes if n.resource_id.endswith("stock_code") and n.bounds[1] < 120 and n.text), None)
    return {"title": title.text, "code": code.text if code else ""}


def open_leader_tab() -> None:
    for _ in range(4):
        nodes = dump_ui("ths_leader_tab_probe")
        leader_tab = find_node(nodes, text="龙头股")
        if leader_tab:
            tap(*leader_tab.center)
            time.sleep(1.0)
            return
        run_adb("shell", "input", "swipe", "450", "1320", "450", "760", "250")
        time.sleep(0.8)
    raise RuntimeError("未找到龙头股标签")


def extract_leaders(nodes: list[Node]) -> list[dict[str, str]]:
    leaders: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in nodes:
        if "fixed_column" not in node.resource_id:
            continue
        desc = node.content_desc or ""
        if "#" not in desc:
            continue
        parts = [part.strip() for part in desc.split("#") if part.strip()]
        if len(parts) < 2:
            continue
        name, symbol = parts[0], parts[1]
        key = f"{name}#{symbol}"
        if key in seen or name in NOISE_TEXTS:
            continue
        seen.add(key)
        leaders.append({"name": name, "symbol": symbol})
        if len(leaders) >= 5:
            break
    return leaders


def main() -> None:
    parser = argparse.ArgumentParser(description="通过移动端同花顺获取概念板块当前龙头/前排")
    parser.add_argument("--query", required=True, help="概念代码或名称，例如 886032 或 固态电池")
    parser.add_argument("--expect-name", help="预期概念名称，用于精确点击搜索结果")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    ensure_device()
    launch_app()
    open_search()
    clear_and_input(args.query)
    search_result = open_query_result(args.query, args.expect_name)
    concept_page = ensure_concept_page()
    open_leader_tab()
    nodes = dump_ui("ths_leader_list")
    leaders = extract_leaders(nodes)
    payload = {
        "query": args.query,
        "search_result": search_result,
        "concept_page": concept_page,
        "leaders": leaders,
        "source": "移动端同花顺",
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"概念页：{concept_page['title']}({concept_page['code']})")
        print(f"搜索结果：{search_result['selected_name']} | {search_result['selected_code']} | {search_result['selected_label']}")
        print("龙头/前排：")
        for item in leaders:
            print(f"- {item['name']}({item['symbol']})")


if __name__ == "__main__":
    main()
