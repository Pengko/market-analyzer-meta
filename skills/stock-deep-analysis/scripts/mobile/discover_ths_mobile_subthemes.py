#!/usr/bin/env python3
"""
通过移动端同花顺发现概念板块下的小题材标签。

示例：
  python3 discover_ths_mobile_subthemes.py --query 886032 --expect-name 固态电池
  python3 discover_ths_mobile_subthemes.py --query 固态电池 --max-swipes 4
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
    "全部",
    "成分股",
    "板块统计",
    "板块掘金",
    "强势板块回踩良机",
    "买板块",
    "分析",
    "龙头股",
    "3日内有涨停",
    "连板",
    "连续3日主力净流入",
    "热门",
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
    return subprocess.run(
        [str(ADB), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def ensure_device() -> str:
    if not ADB.exists():
        raise FileNotFoundError(f"adb 不存在: {ADB}")
    for _ in range(6):
        out = run_adb("devices").stdout
        for line in out.splitlines():
            if "\tdevice" in line:
                return line.split("\t", 1)[0].strip()
        time.sleep(1.0)
    raise RuntimeError(f"移动端设备未就绪: {out.strip()}")


def launch_app() -> None:
    run_adb("shell", "monkey", "-p", APP_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(1.0)


def dump_ui(tag: str) -> list[Node]:
    remote = f"/sdcard/{tag}.xml"
    local = Path(tempfile.gettempdir()) / f"{tag}.xml"
    last_error: Exception | None = None
    for _ in range(4):
        try:
            run_adb("shell", "uiautomator", "dump", remote)
            time.sleep(0.4)
            run_adb("pull", remote, str(local))
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.6)
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


def parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
    if not m:
        return None
    return tuple(int(part) for part in m.groups())  # type: ignore[return-value]


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
    nodes = dump_ui("ths_search_probe")
    if find_node(nodes, resource_contains="search_input"):
        return
    for node in nodes:
        if "new_title_search" in node.resource_id or "股票搜索" in node.content_desc:
            tap(*node.center)
            return
    raise RuntimeError("未找到同花顺搜索入口")


def clear_and_input(query: str) -> None:
    nodes = dump_ui("ths_search_input")
    search_input = find_node(nodes, resource_contains="search_input")
    if not search_input:
        raise RuntimeError("未找到搜索输入框")
    tap(*search_input.center)
    for _ in range(12):
        run_adb("shell", "input", "keyevent", "67")
    run_adb("shell", "input", "text", query)
    time.sleep(1.0)


def open_query_result(query: str, expect_name: str | None) -> dict[str, str]:
    nodes = dump_ui("ths_search_result")
    selected_name = None
    selected_code = None
    selected_label = None
    selected_bounds = None
    for idx, node in enumerate(nodes):
        if "stock_name" not in node.resource_id:
            continue
        if expect_name and node.text != expect_name:
            continue
        y1, _, _, _ = node.bounds
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
    tap((selected_bounds[0] + selected_bounds[2]) // 2, (selected_bounds[1] + selected_bounds[3]) // 2)
    return {
        "selected_name": selected_name,
        "selected_code": selected_code,
        "selected_label": selected_label,
    }


def ensure_concept_page() -> dict[str, str]:
    nodes = dump_ui("ths_concept_page")
    title = find_node(nodes, resource_contains="navi_title_text")
    code = next((n for n in nodes if n.resource_id.endswith("stock_code") and n.bounds[1] < 120 and n.text), None)
    if not title:
        raise RuntimeError("未进入概念页")
    return {"title": title.text, "code": code.text if code else ""}


def discover_subthemes(max_swipes: int) -> list[str]:
    # 先纵向滑到小题材区域
    swipe(450, 1320, 450, 760, 250)
    discovered: list[str] = []
    for _ in range(max_swipes):
        nodes = dump_ui("ths_concept_subthemes")
        tags = extract_subtheme_tags(nodes)
        for tag in tags:
            if tag not in discovered:
                discovered.append(tag)
        swipe(820, 865, 180, 865, 250)
    return discovered


def extract_subtheme_tags(nodes: list[Node]) -> list[str]:
    tags: list[str] = []
    for node in nodes:
        x1, y1, x2, y2 = node.bounds
        if not (780 <= y1 <= 930):
            continue
        if not node.text or node.text in NOISE_TEXTS:
            continue
        if node.resource_id.endswith("text_view"):
            if re.fullmatch(r"[+\-]?\d+(\.\d+)?%", node.text):
                continue
            if len(node.text) <= 1:
                continue
            tags.append(node.text)
    return tags


def main() -> None:
    parser = argparse.ArgumentParser(description="通过移动端同花顺发现板块小题材")
    parser.add_argument("--query", required=True, help="概念代码或名称，例如 886032 或 固态电池")
    parser.add_argument("--expect-name", help="预期概念名称，用于精确点击搜索结果")
    parser.add_argument("--max-swipes", type=int, default=cfg.mobile("discover", "max_swipes_subthemes", default=4), help="横向滑动小题材栏次数")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    ensure_device()
    launch_app()
    open_search()
    clear_and_input(args.query)
    search_result = open_query_result(args.query, args.expect_name)
    concept_page = ensure_concept_page()
    subthemes = discover_subthemes(args.max_swipes)

    payload = {
        "query": args.query,
        "search_result": search_result,
        "concept_page": concept_page,
        "subthemes": subthemes,
        "subtheme_count": len(subthemes),
        "source": "移动端同花顺",
    }
    if args.format == "text":
        print(f"概念页：{concept_page['title']}({concept_page['code']})")
        print(f"搜索结果：{search_result['selected_name']} | {search_result['selected_code']} | {search_result['selected_label']}")
        print("小题材：")
        for item in subthemes:
            print(f"- {item}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
