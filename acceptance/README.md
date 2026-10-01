# 验收证据格式（schema v2）

`runtime_acceptance.py` 只核算已记录证据，不能证明人工填写的声明为真，也不会修改 capability。原始录像、时间线及标签仍须审核。`counts` 是描述性统计；是否通过请看每阶段的 `passed` 与 `blockers`。

现有 `evidence.json` 缺实机试验，阶段保持未通过。不得用补写 `true`、合成片段或统计汇总代替测量。

## 可执行格式示例

[test_acceptance_integrity.py](../test_acceptance_integrity.py) 的 `complete_data` 是格式完整的**虚构单元测试样例**，用于确认验收器不会永远拒绝或错误放行。它不是真实验收数据，不能复制成实机记录。该文件同时覆盖漏报、人工接管、合成数据、NaN、错误出售、重复收竿等拒绝条件。

运行：

```sh
.venv/bin/python runtime_acceptance.py --input acceptance/evidence.json --output acceptance/status.json
```

所有证据路径相对输入 JSON 所在目录；文件必须存在且非空。事实仍以引用的原始证据为准。

## 顶层字段

- `offline_tests`：`run >= 99`，`failures/errors/skipped = 0`，以及原始测试输出文件 `evidence`。整数必须是整数，不能用布尔值替代。
- `scope`：`version`、64 位小写十六进制 `configuration_sha256`、`character`、`map_id`、`calibration_version`、`evidence`。地图等不可为 `unconfirmed`。
- `attempt_inventory`：完整尝试台账的 JSON 路径。格式为 `{"complete": true, "attempts": [...]}`；每项含 `id/kind/run_id/attempt_index`，必须与全部 `trials` 顺序及内容一致。先保留所有失败和接管，再形成汇总，不能挑选成功试验。
- `measurements`：`revocation_seconds`、`device_pulse_ms`、`sent_frame_age_seconds`。各项包含全部测量值 `samples` 和 `evidence`；上限分别为 0.1 秒、500 毫秒、0.5 秒，不接收缺值、负数、NaN 或无穷。
- `checks`：`common` 与六个阶段各自的场景核验；键名见 `runtime_acceptance.COMMON_CHECKS` 和 `SCENARIOS`。每项需 `passed: true`、`mode` 和 `evidence`。默认 `mode: replay`；确认游戏音源、拔设备不切麦克风、重连重新同步这三项必须为 `live`。
- `holdout`：采矿、钓鱼独立数据集统计，详见下文。

## 每次 trial

每项包含唯一 `id`、`kind`、严格布尔 `passed/human_intervention/synthetic`、`mode`、`version`、`configuration_sha256`、非空 `initial_conditions`、`run_id`、递增且不重复的非负 `attempt_index`、`evidence`、`trace_evidence`、`violations`，以及对应业务 `facts`。

实机项为 `mode: live`、`synthetic: false`；两种遇袭回放为 `mode: replay`。真实失败复盘为 `mode: review`，额外注明 `source_mode: live` 或 `historical_live`。历史失败复盘不算新系统实机成功。

`trace_evidence` 指向可核对观测、动作与结果的来源。每个 trial 都须明确填写五项非负整数安全计数：`protected_item_sold`、`cancelled_input_sent`、`concurrent_input`、`unsupported_success`、`duplicate_reel`。遗漏不视为零；任意非零均阻止全部阶段放行。

人工接管试验保留在台账，不能算无人介入成功，并会打断“连续成功”。连续序列必须在同一 `run_id`。三个剥皮结果须用 `facts.hunt_round_id` 关联到连续五轮中的三个不同打怪轮次。

## 业务证据

- 导航往返：`facts.endpoints` 必须有 A/B 两个终点，各含 `error <= 0.2` 和 `stationary_frames >= 2`。
- 采矿：尝试均用 `mineral_attempt`，失败也保留；收获仅从 `passed: true` 且 `facts.mineral_received: true` 计算，不能另外填写虚构收获次数。`detour_resume` 需 `total_seconds <= 90`、`returned_to_route: true`、`original_goal_resumed: true`。
- 商人往返：`facts.empty_before/empty_after` 明确容量增长，`returned_to_task: true`。`sales` 每项需 `quality: poor`、`category: junk`、`recognized: true`、`quantity_before/quantity_after`、`coins_before/coins_after`、`evidence`；数量必须减少且实际金币增加。
- 每次抛竿：`facts.bite_labeled/conditions_met` 为严格布尔，`reel_count` 为非负整数。有标注咬钩且条件满足者，用 `bite_at/reel_sent_at` 的实际差值计算 800 毫秒内收竿率；汇总中的自报成功率不采用。无依据收竿或一轮多次收竿直接失败。30 轮连续抛竿包含未成功的轮次。
- 复盘闭环：`improvement_cycle.steps` 分别引用 `real_failure/candidate_change/old_failure_replay/negative_regression/controlled_live_recheck` 的证据。两场运行需实际 `duration_seconds >= 1800` 和 `coverage`（综合场 navigation/combat/mining/vendor；钓鱼场 fishing/audio）。

## 独立样本

`holdout.mining` 与 `holdout.fishing` 均需非空且不重叠的 `development_sessions/evaluation_sessions`，`synthetic: false`、`complete_segments: true`、`evidence`，以及非负整数 `tp/fp/fn`。相邻帧不能拆成独立 session。

采矿还需 `positive_clips >= 30`、`negative_clips >= 30`、`tp + fn = positive_clips`、精确率不低于 95%、召回率不低于 90%、`latency_p95_seconds <= 2`。

音频还需 `positive_events >= 30`、`negative_seconds >= 1800`、`tp + fn = positive_events`、召回率不低于 95%、`false_reels = 0`、`reel_latency_p95_seconds <= 0.8`、`includes_capture_latency: true`。所有时间与比例须来自完整样本的测量，禁止排除失败尝试后重新计算。

## 音频与复盘记录

音频配置 `offset_seconds` 是音画校准修正量；事件模板可提供 `event_offset_seconds` 表示咬钩起点在 0.5 秒窗口中的位置，默认 0（保守取窗口起点）。模板须非静音。原生辅助程序使用 AVFoundation 的 `synchronizationClock` 转换到 CoreMedia host clock；Python 与其共享 `mach_absolute_time`，不会用管道收包时间抹掉实际采集延迟。协议变化后须重新编译 `audio_capture.swift`，并重新做音画同步实测。

音频 JSONL 的 `at` 是校准后的**起始样本**单调时刻，`captured_at` 保存原始采集时刻，`synchronized` 表示是否完成音画 offset 配置。没有校准只录音，不授权声音事件。序列断裂、时间异常、设备断开会使旧事件失效。

`runtime_review.py` 导出 `timeline.jsonl`、来源文件的递归哈希清单和 `evidence_gaps`。缺帧、缺音频、日志轮转、容量淘汰及截断 JSON 都显式列出。片段仍引用原运行目录；移动或清理前保留原始文件，复盘前调用 `verify_sources(facts)` 检查文件是否缺失或变化。`proposal.json.activate` 始终为 false。
