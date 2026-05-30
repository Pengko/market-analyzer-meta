#!/usr/bin/env python3
"""
主要作用:
- 记录每个接口“首次成功拿到数据”的时间与条数
- 供轮询模式和更新摘要查看使用

记录规则:
- 每个接口只保留最新一条成功记录
- 失败和空数据只出现在运行日志，不写入记录文件
"""

from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any


class UpdateLogger:
    """更新记录管理器"""
    
    def __init__(self, log_file: Path = None):
        """
        初始化记录器
        
        Args:
            log_file: 记录文件路径，默认为 ~/quant-data/tushare/update_records.md
        """
        if log_file is None:
            # 自动检测数据目录
            from paths import get_tushare_dir
            self.log_file = get_tushare_dir() / "update_records.md"
        else:
            self.log_file = Path(log_file)
        
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._records = self._load_md()
    
    def _load_md(self) -> Dict[str, Any]:
        """从 Markdown 文件加载记录（如果存在）"""
        records = {}
        if not self.log_file.exists():
            return records
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 简单解析已有记录
            current_interface = None
            for line in content.split('\n'):
                if line.startswith('## 🔹 '):
                    current_interface = line[6:].strip()
                    records[current_interface] = {}
                elif line.startswith('### ') and current_interface:
                    parts = line[4:].strip().split()
                    if len(parts) >= 2:
                        trade_date = parts[0]
                        records[current_interface][trade_date] = {}
        except:
            pass
        
        return records
    
    def _save_md(self):
        """生成 Markdown 格式记录文件"""
        lines = [
            "# 📊 数据更新记录",
            "",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 记录规则: 每个接口只保留最新成功记录",
            "",
            "---",
            "",
        ]
        
        # 按接口分组
        for interface in sorted(self._records.keys()):
            interface_data = self._records[interface]
            if not interface_data:
                continue
            
            lines.append(f"## 🔹 {interface}")
            lines.append("")
            
            # 获取最新一条记录（按日期倒序）
            latest_date = max(interface_data.keys())
            record = interface_data[latest_date]
            
            status = record.get("status", "unknown")
            status_icon = {"success": "✅", "empty": "⚪", "error": "❌"}.get(status, "❓")
            
            first_success = record.get("first_success_at", "N/A")
            if first_success != "N/A":
                first_success = first_success[:19]
            
            first_count = record.get("first_success_count", 0)
            check_count = record.get("check_count", 0)
            
            lines.append(f"### {latest_date}")
            lines.append("")
            lines.append(f"- **状态**: {status_icon} {status}")
            lines.append(f"- **首次成功**: {first_success}")
            lines.append(f"- **记录数**: {first_count}")
            lines.append(f"- **检查次数**: {check_count}")
            
            # 备注
            error_msg = record.get("error_msg", "")
            if error_msg and status != "success":
                lines.append(f"- **备注**: {error_msg[:50]}")
            
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # 写入文件
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    
    def record(
        self, 
        interface: str, 
        trade_date: str, 
        record_count: int = 0,
        status: str = "success",
        error_msg: str = ""
    ) -> Dict[str, Any]:
        """
        记录一次更新结果（只记录成功）
        
        Args:
            interface: 接口名称 (如 'daily', 'rt_idx_k')
            trade_date: 交易日期 (如 '20260410')
            record_count: 记录数
            status: 状态 (success/empty/error)
            error_msg: 错误信息
        
        Returns:
            {"is_success": bool, "is_updated": bool}
        """
        now = datetime.now()
        now_str = now.isoformat()
        
        # 初始化接口记录
        if interface not in self._records:
            self._records[interface] = {}
        
        interface_records = self._records[interface]
        
        # 获取现有记录（如果有）
        existing = interface_records.get(trade_date, {})
        
        is_success = status == "success" and record_count > 0
        
        if is_success:
            # 成功：更新或创建记录
            is_updated = existing.get("first_success_at") is not None
            
            interface_records[trade_date] = {
                "trade_date": trade_date,
                "interface": interface,
                "first_success_at": now_str,
                "first_success_count": record_count,
                "check_count": existing.get("check_count", 0) + 1,
                "status": "success",
                "error_msg": "",
            }
            
            # 清理该接口的其他日期记录（只保留最新的）
            if len(interface_records) > 1:
                # 找到最新的日期
                latest = max(interface_records.keys())
                # 删除其他日期
                for date in list(interface_records.keys()):
                    if date != latest:
                        del interface_records[date]
            
            self._save_md()
            
            return {
                "is_success": True,
                "is_updated": is_updated,
            }
        else:
            # 失败：只更新检查次数（不保存到文件）
            if trade_date in interface_records:
                interface_records[trade_date]["check_count"] = \
                    interface_records[trade_date].get("check_count", 0) + 1
            else:
                # 第一次失败，创建临时记录（不保存）
                interface_records[trade_date] = {
                    "trade_date": trade_date,
                    "interface": interface,
                    "first_success_at": None,
                    "first_success_count": 0,
                    "check_count": 1,
                    "status": status,
                    "error_msg": error_msg,
                }
            
            return {
                "is_success": False,
                "is_updated": False,
            }
    
    def get_record(self, interface: str) -> Optional[Dict]:
        """获取接口的最新记录"""
        records = self._records.get(interface, {})
        if not records:
            return None
        latest_date = max(records.keys())
        return records[latest_date]
    
    def print_summary(self):
        """打印更新摘要"""
        print("\n" + "="*80)
        print("📊 数据更新记录摘要")
        print("="*80)
        
        for interface in sorted(self._records.keys()):
            record = self.get_record(interface)
            if not record:
                continue
            
            trade_date = record.get("trade_date", "N/A")
            first_time = record.get("first_success_at", "N/A")
            if first_time != "N/A":
                first_time = first_time[11:19]
            
            count = record.get("first_success_count", 0)
            
            print(f"\n🔹 {interface}")
            print(f"  最新日期: {trade_date}")
            print(f"  首次成功: {first_time}")
            print(f"  记录数: {count}")
        
        print("\n" + "="*80)
        print(f"\n📄 记录文件: {self.log_file}")

    def print_timeline(self, interface: str, trade_date: str):
        """打印单接口指定日期的记录（兼容旧 CLI 参数）。"""
        print("\n" + "=" * 80)
        print(f"📈 时间线查询: {interface} @ {trade_date}")
        print("=" * 80)
        if not interface:
            print("❌ 未提供接口名")
            return
        interface_records = self._records.get(interface, {})
        if not interface_records:
            print("⚪ 该接口暂无记录")
            return
        record = interface_records.get(trade_date)
        if record is None:
            latest = self.get_record(interface)
            latest_date = latest.get("trade_date", "N/A") if latest else "N/A"
            print(f"⚪ 未找到 {trade_date} 的记录（当前仅保留最新记录: {latest_date}）")
            return
        status = record.get("status", "unknown")
        first_time = record.get("first_success_at", "N/A")
        if first_time and first_time != "N/A":
            first_time = first_time[:19]
        print(f"接口: {interface}")
        print(f"日期: {trade_date}")
        print(f"状态: {status}")
        print(f"首次成功: {first_time}")
        print(f"记录数: {record.get('first_success_count', 0)}")
        print(f"检查次数: {record.get('check_count', 0)}")


# 便捷函数
def get_logger() -> UpdateLogger:
    """获取默认记录器实例"""
    return UpdateLogger()


def record_update(
    interface: str, 
    trade_date: str, 
    record_count: int = 0,
    status: str = "success",
    error_msg: str = ""
) -> Dict[str, Any]:
    """
    便捷函数：记录一次更新
    
    Returns:
        {"is_success": bool, "is_updated": bool}
    """
    logger = get_logger()
    return logger.record(
        interface=interface,
        trade_date=trade_date,
        record_count=record_count,
        status=status,
        error_msg=error_msg
    )


if __name__ == "__main__":
    # 测试
    logger = get_logger()
    
    # 模拟记录
    print("模拟记录...")
    
    # 第一次：4月9日成功
    result = logger.record('daily', '20260409', record_count=5490, status='success')
    print(f"  4月9日成功: is_success={result['is_success']}")
    
    # 第二次：4月10日成功（应该替换掉9日的记录）
    result = logger.record('daily', '20260410', record_count=5495, status='success')
    print(f"  4月10日成功: is_success={result['is_success']}")
    
    # 第三次：4月10日再次成功（更新检查次数）
    result = logger.record('daily', '20260410', record_count=5495, status='success')
    print(f"  4月10日再次: is_success={result['is_success']}, is_updated={result['is_updated']}")
    
    # 失败记录（不保存）
    result = logger.record('rt_idx_k', '20260410', record_count=0, status='error', error_msg='配额上限')
    print(f"  rt_idx_k失败: is_success={result['is_success']}")
    
    print(f"\n📄 记录文件: {logger.log_file}")
    
    # 打印摘要
    logger.print_summary()
