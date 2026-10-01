# 统一启动入口

> **入口已更新。** 以 [架构实现与验收记录](ARCHITECTURE_IMPLEMENTATION.md) 为准：现在使用单一运行器、即时取消和能力验收门槛。下列旧启动示例默认会被未验收门槛拒绝；只有有界验证使用 `--trial --max-seconds N`，且不会绕过缺失标定。

在工作目录 `wow-control-research` 运行。`wow_control.py` 是统一入口，旧的 `patrol_hunt.py` 命令仍兼容。所有硬件操作都要求显式 `--execute`。

无限循环，开启拾取、剥皮尝试和矿点检查：

```sh
.venv/bin/python wow_control.py --execute --rounds 0 --kills 0 --skinning --mining --output runs/my-infinite-run
```

确认打死 10 只后停止：

```sh
.venv/bin/python wow_control.py --execute --kills 10 --no-loot --output runs/my-ten-kills
```

最多尝试 50 轮，或累计确认 10 次击杀，先达到哪个就结束：

```sh
.venv/bin/python wow_control.py --execute --kills 10 --rounds 50 --combat-seconds 90 --no-loot --output runs/my-limited-run
```

`--kills`（别名 `--count`）统计控制器确认的经验事件并已脱战的击杀。搜索、目标丢失、未确认击杀都不计数；当前经验回执识别不可靠时，次数可能少计，不会用尝试次数冒充击杀数。

`--rounds` 统计巡游尝试轮次，与击杀数独立。两个参数默认都为 `0`，代表无限制；不限于旧版的 8/30 轮。无限循环不会绕过死亡、失去画面、低血量、撤离失败等现有保护条件，也不会自动重新启动故障进程。

## 可调参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--kills N` / `--count N` | 0 | 已确认击杀数上限，0 无限 |
| `--rounds N` | 0 | 尝试轮次上限，0 无限 |
| `--combat-seconds N` | 90 | 单次战斗控制预算，范围 1–300 秒；已有战斗收尾宽限最多 45 秒 |
| `--no-loot` | 不指定即开启拾取 | 只打怪；跳过背包、拾取、采集和商人阶段 |
| `--skinning` / `--no-skinning` | 关闭 | 拾取成功后尝试当前尸体剥皮，失败跳过 |
| `--mining` / `--no-mining` | 关闭 | 拾取成功后检查矿点与名称；实际世界矿石交互尚未校准，记录原因并跳过 |
| `--reserve-slots N` | 1 | 开启拾取时的空格保留阈值；0 空格时才触发商人路线 |
| `--vendor X Y` | 30.1 71.6 | 商人坐标；现有售卖界面仍未验证 |
| `--hunt-point X Y` | 24.2 73.6 | 商人流程后的返回坐标 |
| `--keep-cycles N` | 100 | 保留本次运行最近 N 轮的详细截图和日志，清理更旧轮次 |
| `--output PATH` | 必填 | 必须是不存在的新目录，防止覆盖旧运行 |

## 停止与状态

`status.json` 原子更新当前状态、轮数、累计确认击杀数；`config.json` 保存本次完整参数。`history.json` 保留最近 100 条事件；`history.jsonl` 在约 2 MB 时轮转，仅额外保留一份历史文件。旧运行目录不受新运行的清理影响。

正常停止：在本次输出目录创建名为 `STOP` 的文件。控制器在当前阶段结束后停止，不再开始下一阶段/轮次；正在进行的战斗仍由原有限时逻辑处理。

紧急中断：前台 Ctrl+C，或向主进程发送 SIGTERM。中断会结束当前子进程并记录停止状态；设备的按键仍使用硬件计时短按。中断不等于角色已经脱战。

整个调度生命周期持有独占锁，第二个统一入口不会同时启动；各硬件脚本仍保留 KMBox 自身的独占锁。

## 模块

- `patrol_config.py`：参数解析、类型及范围校验、可导入的 `PatrolConfig`。
- `patrol_runtime.py`：`PatrolRunner` 串行编排战斗、拾取、商人和巡游；`RunStore` 管理状态和日志保留。
- `wow_control.py` / `patrol_hunt.py`：启动及中断处理。
- 原有 CV、KMBox、战斗和采集脚本继续保持独立，由调度层调用。

采集开关由启动参数明确传递，旧的 `optional_gather_enabled` 标记文件不再生效。`--no-loot` 或满包卖货失败后的不拾取模式会同时跳过采集。独立 `hunt_loot.py` 与 `optional_gather.py` 也接受上述两组开关；`hunt_loot.py --skin` 保留为剥皮开关别名。
