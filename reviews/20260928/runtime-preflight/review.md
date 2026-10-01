# 运行证据复盘

来源：/Users/chenqizhu/Documents/Codex/untitled folder/wow-control-research/runs/runtime-observe-20260928

这是事实导出；不会自动调整阈值、策略或代码。

最终结果：capture_failed: Window calibration unavailable: black, occluded or unsupported layout
已记录事件：4；失败／拒绝事件：1；证据片段：2。

## 待审改进流程

1. 根据 facts.json 和原始证据区分已知事实与原因假设。
2. 在 proposal.json 中记录修改候选、对应失败和预计影响。
3. 用独立正负例与完整事件序列回放验证；记录全部失败。
4. 完成有界实机复验后审核启用；不得仅凭离线通过修改能力验收状态。
