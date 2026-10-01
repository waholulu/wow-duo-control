# 离线测试：按改动范围运行

默认入口：`.venv/bin/python run_tests.py`。默认选择当前圣骑士战斗流程及共用输入/死亡保护；不会操作游戏。用 `--list` 获取当前实际收集数量，避免文档计数随新增或迁移测试失效。

主动受击共享模块回归在 `test_threat_state.py`，已加入combat组；包括真实圣骑士配置防御入口、跨阶段累计受伤、来源疑点与文字不授权移动。机制及完整回归证据见 [THREAT_HANDLING.md](THREAT_HANDLING.md)。

术士/圣骑士本轮审核回归包含`test_combat_audit_fixes.py`，已加入combat组；完整报告见`runs/combat-audit-20260929/tests-all.json`，行为与验证边界见`COMBAT_AUDIT_FIXES_20260929.md`。它验证运行许可、资源/技能等待期间保护、输入效果和日志事件边界，不代表新的实机验收。

| 改动范围 | 参数 |
|---|---|
| 战斗、圣印、审判、恢复、战后结算 | 默认或 `--suite combat` |
| 血条、目标框、法力OCR、布局 | `--suite vision` |
| 拾取、背包、收获确认 | `--suite loot` |
| 钓鱼 | `--suite fishing` |
| 导航、跑尸坐标、采矿、出售 | `--suite navigation` |
| 输入、串口、撤销、死亡门禁 | `--suite core` |
| 全部功能或测试基础设施修改 | `--suite all` |

可以重复指定范围，例如 `--suite combat --suite vision`；交集只执行一次。`--list`仅列出用例，`--report runs/test-report.json`保存范围、实际执行数和跳过原因。不要把未选中的其他功能报为已经通过。

`test_stationary_gather` 属于 navigation 组，验证原地采集的候选歧义、名称、一次点击与未确认收获停止；它不证明山洞、水下或原地点击不会触发客户端移动。

小修改优先直接运行相关测试文件，例如 `.venv/bin/python -m unittest test_combat_exit_watch test_combat_log_decisions`。模块之间存在依赖时扩大到相应功能组。涉及共享调度、输入保护或跨功能改动时扩大检查范围；不因一次测试已经通过而反复跑全量。

## 这次整理

以下数字是各次整理当时的历史快照；当前数量只以 `run_tests.py --list` 的输出为准。

- 原374次执行中只有353个独立用例。CompositionTests的7个用例被其他3个文件导入后又各执行一次，共21次重复。
- Driver、immediate和控制器构造合并到`test_support.py`。测试文件不再导入其他文件的TestCase类充当工厂；标准`unittest discover`也不会重复收集。
- 整理前后独立用例ID集合完全一致；没有为压低数字把不同场景塞进一个大测试，也没有删除死亡、残血、额外攻击者、停止输入、计数和拾取确认保护。
- 界面与战斗的可选历史回放增加`requires_archive`前置条件。缺少声明的runs资料时显示SkipTest并列出缺失路径；资料存在后的断言失败、损坏图片、坏JSON、模块导入错误仍是失败，不会吞掉。
- 发布验收依赖的固定fixtures及哈希检查仍保留。跳过历史回放不等于验证通过，也不能据此解除死亡门禁。
- 当前功能确实存在，但与本次改动无关的测试保留在对应组中，例如钓鱼、采矿和交易；不会每次改战斗都跑全部功能。部分共享技能文件仍包含多个功能的契约检查，因此分组不是严格隔离。

证据：`runs/test-audit-before.json`、`runs/test-audit-after.json`、`runs/test-audit-after.txt`。游戏操作继续暂停，计数和死亡门禁未变。

整理后完整回归：353项通过，0失败、0错误、0跳过；本机本次用时30.56秒。缺失历史资料的跳过行为及“资料存在时断言失败不被吞掉”已单独验证。

共享行为拆分与战后时限修复后：combat组154项通过（4.82秒），包括新增的延迟脱战与重复/过期帧回归；其他功能组本次未重跑。

流程收敛后：共享调度、组合流程和视觉状态均有修改，完整372项通过（44.09秒），0失败、0错误、0跳过。报告`runs/workflow-convergence-tests.json`；本轮新增5项有效回归，当前总数还包含其他已加入的功能测试，不代表这次新增了全部差额。
