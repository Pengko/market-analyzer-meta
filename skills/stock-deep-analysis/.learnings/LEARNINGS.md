## [LRN-20260425-001] best_practice
**Logged**: 2026-04-25T13:40:00+08:00
**Priority**: high
**Status**: resolved
**Area**: docs | data-strategy

### Summary
SKILL.md 中数据获取规则重构的 4 个关键教训。

### Details
1. **当日数据必须独立分类**：历史数据（T-1及以前）和当日数据（T日）不能混在同一分类中。当日日线、当日分钟线需要有独立的获取策略，而非简单套用"本地优先"或"浏览器优先"。
2. **分钟线必须时段分策**：盘中/午间/盘后/盘前四种时段的分钟线获取策略完全不同。盘中直接浏览器/API，午间/盘后先本地后浏览器，盘前用T-1本地历史。
3. **降级链必须有边界**：curl→浏览器→本地的降级规则只适用于"浏览器/API优先"类数据。本地only数据（龙虎榜、筹码、融资融券等）严禁降级到浏览器补抓。
4. **技能文档禁止存放过时的具体数字**："已知路径陷阱"和"数据质量概览"等表格中，具体文件数、覆盖股票数、最新日期等会迅速过时的数字应该删除，只保留结构性描述。历史修复记录中的数字可以保留。
5. **关键区分不能丢失**：`top_list` vs `limit_list_ths` 的区分说明在上次 patch 中被意外删除，说明 patch 时需要格外注意不删除未明确要求删除的内容。

### Suggested Action
- 已在 SKILL.md 中落实上述全部 5 点
- 已将本次更新记录到 CHANGELOG.md
- 未来修改 SKILL.md 的表格时，注意 patch 的 old_string 要包含完整上下文，避免误删相邻内容

### Metadata
- Source: user_feedback + self-correction
- Related Files: SKILL.md, CHANGELOG.md
- Tags: data-strategy, skill-maintenance, patch-safety
- See Also: AGENTS.md "Self-Improvement Workflow"

---
