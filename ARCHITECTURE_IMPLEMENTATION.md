# 统一运行架构与验收记录

> 2026-09-30 增加了独立的原地采集第一版，详见 [STATIONARY_GATHER_V1.md](STATIONARY_GATHER_V1.md)。它需要人工确认安全站位，最多尝试一个矿或草，不使用下文尚未验证的坐标导航与矿点接近流程。离线代码不代表已完成现场标定或实机验收。

> 后续钓鱼更新：当前场景已完成连续十次收鱼，并增加配置文件、中文菜单及 `wow_control.py --task fish-trial` 分派入口，见 [FISHING_TRIAL.md](FISHING_TRIAL.md)。这是独立场景试验分支，不改变下文统一调度器的正式能力门槛。下文“静音样本/钓鱼缺口”描述的是较早的架构验收快照；后续已取得非静音 PCM 与局部视觉成功证据。

2026-09-28。本文对应本次架构实现；旧 README 和运行历史中的子进程编排说明不再描述新的命令入口。

## 当前交付状态

### 2026-09-30 主动受击共享状态

`threat_state.py` 将累计受伤、近期受击来源提示与脱战证据整合到单控制器共享的 `ThreatMonitor`。Scheduler、战斗、恢复、撤退和异常退出沿用同一份历史，各阶段保留自己的安静时长与时限。多来源提示结合视觉威胁停止输出并交给安全退出，不推断精确实体数量。常驻面板只读检查已与脱战切页要求分离，防御结算清理不再绑定已死目标。

新增15项专项回归，完整440项测试通过，0失败、0错误、0跳过；详见 [主动受击处理](THREAT_HANDLING.md) 与 `runs/threat-integration-20260930/tests-all.json`。本次仅离线整合，没有实机验收、自动避障或新攻击者自动选择，圣骑士暂停门禁保持。

### 2026-09-29 架构收敛

除独立钓鱼试验路径外，架构审计发现的问题已离线修正：验收哈希覆盖完整本地运行依赖图；旧子进程巡逻编排和旧配置模型已退出活动源码；兼容命令只转发到统一运行器；输入锁独立为共享基础设施；战斗目标重置归 Policy 单点负责，发送前拒绝只回滚动作建议状态，不丢弃同帧观察事实；入口文档改为引用结构化进度和动态测试收集结果。

验证报告为 `runs/architecture-convergence-nonfishing-tests.json` 和 `runs/architecture-convergence-all-tests.json`。非钓鱼功能组组合回归241项通过；最终完整回归375项通过，0失败、0错误、0跳过。完整回归用于检查共享模块没有破坏其他路径，不表示本次修改了钓鱼实现，也不替代任何实机验收。

已实现统一运行内核和可中断技能代码，配有离线回归、事件回放、证据导出和验收统计工具。**完整实机验收尚未通过，所有能力的 live_passed 保持 false。** 新命令默认不会绕过该门槛；有界验证需要显式 `--trial --max-seconds N`（N ≤ 300），并仍须具备对应标定。

当前硬件只读检查：OBS 截图可获取，但游戏画面无法通过既有窗口锚点校准；已保存 `runs/runtime-raw-20260928.jpg`。新运行器在校准失败后停止，未发送键鼠输入。USB Audio 已通过明确 UID 获取约 5.8 秒 16 kHz 单声道 PCM，但该段样本为静音；不能据此认定游戏声音、咬钩或受击检测通过。

| 阶段 | 代码与离线验证范围 | 实机验收缺口 |
| --- | --- | --- |
| 内核与现有技能 | 单输入所有权、取消／过期检查、异常终态；战斗、回蓝、背包、拾取、剥皮、撤离技能 | 当前 UI 校准失配；5 轮打怪拾取、3 轮剥皮、回蓝尚未复验 |
| 导航与恢复 | 坐标双读、途经点、卡住／振荡停止、遭遇中断与恢复 | 地图尚未确认，未录入已验证路线；3 次往返和遇袭实测未完成 |
| 采矿 | 持续候选跟踪、矿名确认、有界接近、世界模板定位、矿石回执与返程 | 缺矿物世界模板、采矿光标标定、独立正负例和实际采集验证 |
| 交易 | 白名单图标及名称确认、逐件售卖、金币加空槽双重确认、返程失败停止 | 缺商人 UI／金币 ROI／槽位及物品白名单标定，未售卖任何物品 |
| 音频钓鱼 | 指定 USB 设备、PCM／声音特征匹配、单次收竿、设备异常处理 | 缺非静音游戏样本、咬钩模板、音画偏移、浮漂模板和角色条件确认 |
| 复盘 | 事实包、待审建议、可回放事件、独立验收统计 | 未完成“候选改进→实机复验”闭环和两场 30 分钟运行 |

完整判定以 `acceptance/status.json` 为准；测试结果不能替代实机通过。程序不会自动把能力标记为已验收。

本轮整合后全量回归：**226 项通过，0 失败、0 错误、0 跳过**，原 99 项基线保留；详见 `acceptance/tests.json` 和 `acceptance/tests.txt`。正式 Swift 音频辅助已重新编译，两组独立离线回放符合预期。`acceptance/audit.json` 记录了编译摘要、回放及预检查结果；本轮未连接实际设备或发送游戏输入。

## 开发原则：避免过度设计

后续以最少必要改动完成当前角色、当前地图和已标定 UI 下的功能闭环。优先验证现有实现，针对真实失败修复；不为尚未出现的需求预建扩展层。

- **保持现有结构。** 一个 Python 主进程管理任务，一个调度器推进技能，一个动作执行器拥有 KMBox；取帧、OCR 和音频使用必要的后台工作线程或受管辅助进程。首版不引入微服务、消息中间件、通用插件平台或在线模型决策。
- **先复用，再抽取。** 优先使用现有函数和公共契约；只有出现实际重复、需要统一维护时才提取共享能力。技能各自保留确认规则，不为形式统一增加基类、配置层或间接调用。
- **保留必要保护。** 取消和期限、发送前时效检查、唯一输入所有权、未知物品保留、业务结果证据及明确失败终态，是当前功能的正确性要求。简化代码时仍须满足这些要求。
- **控制维护成本。** 重复逻辑优先合并，无使用方的抽象优先删除。旧入口保留薄封装；历史参考实现待统一路径覆盖相同业务断言和必要证据后再清理，避免长期维护两套流程。
- **按风险验证。** 测试聚焦真实故障、模块交接和零容忍项；简单文档或低影响改动只做相应检查，不为增加数量编写重复测试。新增机制应能说明解决了哪个具体问题、为什么现有结构不足，以及如何验收。

当前优先事项是补齐标定、真实样本和有界实机验收。新增抽象或基础设施应由这些验证中发现的实际需要驱动。本节记录后续约束，不表示现有代码已经完成全面精简，也不改变功能启用门槛。

## 结构与调用边界

```text
OBS → Perception ───────────┐
USB Audio → AudioSource ────┤
                           ↓
          Scheduler → cooperative skills → Executor → KMBox
              ↓               ↓               ↓
                 Store → review / replay / acceptance
```

- `runtime_types.py` 定义 Snapshot、Event、Intent、Result、Context 以及 Observe／Wait／Work；未知状态由明确的 known 字段表达，不通过 false 猜测。
- `runtime_engine.py` 提供持续取帧、50 ms 调度和唯一输入执行器。慢 OCR 和输入回执不在调度线程等待。撤销会更新任务版本，KMBox 在实际串口写入前检查版本、期限、当前画面及校准变换。
- `runtime_skills.py`、`runtime_interactions.py`、`runtime_audio.py` 分别实现已有技能、导航／交互扩展和音频钓鱼；技能不打开硬件。
- `interaction_vision.py` 共享原有光标、尸体与背包识别。原模块继续导出这些类，避免破坏现有导入。
- `runtime_main.py` 持有一个设备会话并组合技能。新的业务流程不读取 JSONL 最后一行，不通过子进程调用其他技能。

已切换的命令及其 `main()`：wow_control、patrol_hunt、hunt_loot、run_controller、bag_probe、loot_probe、patrol_roam、combat_escape、navigate_local、vendor_probe、mineral_approach、optional_gather。兼容命令现在只有参数翻译与统一入口转发，不再包含私有同步执行路径；旧 `PatrolRunner`、旧 `RunStore` 和旧巡逻配置模型已从活动源码移除。历史实现只存在于冻结归档和运行证据中。输入独占锁位于 `control_lock.py`，不再由旧编排模块提供。

验收摘要由 `runtime_world.RUNTIME_SOURCE_FILES` 明确列出所有能改变动作或安全判断的模块。资源等待、圣印计时、近战循环、威胁结算、死亡门禁和输入锁均纳入哈希；这些模块变化会使旧能力验收失效。该覆盖有离线回归保护，避免后续拆分模块再次漏出验收边界。

战斗策略仍是轻量本地状态机。目标切换的重置由 `Policy.reset_for_target_change` 单点维护；输入发送前被拒绝时只回滚与动作建议绑定的字段，保留当前帧已经确认的经验、伤害和目标血量事实，避免整对象复制把观察证据一起撤销。

动作日志区分 proposed、sent、acknowledged、effect_confirmed；离线回放记录 action_simulated。设备确认不表示游戏效果。击杀数量仍采用关联经验事件并确认脱战的旧计数语义，结果明确保存 `identity_verified=false`，不声称唯一目标死亡证明。

本轮并行审计进一步收紧了这些边界：

- 恢复与连续多次中断共享原任务期限；防御必须保持已关联目标的可观测连续性及同一标定版本，窗口重新标定或目标变化会撤销关联。该连续性不是游戏实体的唯一身份认证。
- 串口部分写入单独记录；完整命令写完才记为 sent。撤销不等待设备 ACK，所有输入也会等待前一次计时按键自然释放。
- 识别工作线程有界关闭；超出退出期限会落盘明确失败，不能将尚未退出的 Python 线程宣称为已强制终止。
- 采矿保存离开路线的位置、原目标和剩余预算，恢复先返回衔接点；返程使用独立进度。售卖在实际发送前再检查物品、提示框与指针依据是否仍有效。
- 录像完整性按实际采样覆盖判断，分别标明音频／视频前后窗口和同步状态；断流不会因等待够 10 秒变成完整记录。轮转、淘汰和丢失均可见。单次运行默认 2 GB 记录预算，包括 OCR 文件，每秒检查；超预算停止并保留已有证据，最终元数据及检查间隔内的写入可能有少量超额。
- 离线回放保留原采样时间、标定代次和目标连续标记；延迟识别不会刷新帧年龄，缺少已知状态或预期断言不会默认为通过。

## 使用方式

工作目录为项目根；每次输出使用新目录。

```sh
# 不连接设备：查看任务所需标定和验收状态
.venv/bin/python wow_control.py --task preflight --output runs/preflight-new
.venv/bin/python wow_control.py --task preflight --check-task fish --output runs/fishing-preflight-new

# 只观察，不打开 KMBox；当前校准未修复前预期明确失败
.venv/bin/python wow_control.py --task observe --audio --max-seconds 12 --output runs/observe-new

# 有界背包验证：会产生真实输入，仅用于现场校准恢复后的验证
.venv/bin/python wow_control.py --task bag --execute --trial --max-seconds 45 --output runs/bag-trial-new

# 完成实机验收并审核 capability 配置后，才允许常规启用
.venv/bin/python wow_control.py --execute --rounds 5 --skinning --max-seconds 600 --output runs/patrol-new

# 独立离线回放：不会打开 OBS、USB Audio 或 KMBox
.venv/bin/python runtime_replay.py --input tests/replays/stale.json --output runs/replay-new

# 导出事实和待审建议，不改运行策略
.venv/bin/python runtime_review.py --run runs/runtime-observe-20260928 --output reviews/new-review

# 保存用户给定位置；map-id 应使用已确认的同一地图标识
.venv/bin/python runtime_world.py --name vendor --map-id CONFIRMED_MAP --coordinate 30.1 71.6
```

`--rounds`、`--kills/--count`、`--no-loot`、`--skinning`、`--mining`、`--reserve-slots` 等仍保留。新增运行总时限默认 1800 秒、最多 86400 秒，原无限轮数不会绕过总期限。`hunt_loot.py --until-full` 仍在满包时结束，不自行回城；巡游入口才组合商人流程。`run_controller.py --log x.jsonl` 对应新的 `x-runtime/` 证据目录，业务不再生成或解析旧 kill.jsonl。

在输出目录创建 `STOP`，或发送 SIGINT／SIGTERM，调度器会撤销当前任务输入资格。已写入设备的计时按键自然结束；这不保证角色已脱战。

## 标定与功能启用

`calibration/runtime.json` 保存地图、交互 ROI、模板、键位、音源及事件参数；`places.json` 保存地点和已验证路线；`capabilities.json` 保存对应配置哈希和离线／实机验收状态。

- core 使用已有 CV 标定，但新的运行器实机门槛尚未满足。
- 路线需有 map、points 和 verified；普通运行只能使用验证过的路线。`--destination NAME` 读取已保存地点并核对地图和路线末点；`--target X Y` 或无路线的命名地点只用于已确认地图内的有界导航试验。`--max-steps` 保留并限制在 1 至 120。地图 ID 来自明确配置，首版不声称自动识别地图切换。
- 采矿配置需当前角色 `skill_confirmed=true`、`tool_confirmed=true`，以及矿名、矿石回执名称、采矿光标模板及世界矿物模板（file、roi、threshold）。缺任何一项，试验模式也不启动交互；两个角色条件当前均为 false。
- 交易配置需窗口模板、金币／提示框 ROI、槽位 point／roi／empty_template，以及 approved=true、quality=poor、category=junk 的物品白名单（name、icon）。装备、材料、任务或未知分类不允许出售。往返路线须到达请求的商人与返回坐标。
- 钓鱼需当前角色 `skill_confirmed=true`、`tool_confirmed=true`，以及技能键、浮漂模板／世界 ROI、收获物品名称、咬钩 WAV 模板及实测音画 offset_seconds。两个角色条件当前均为 false；视觉回退只有独立验证并配置 visual_verified 后才启用。
- 声音样本使用 16-bit PCM WAV。编译 `swiftc audio_capture.swift -o audio_capture`；`./audio_capture --list` 显示设备 UID 和授权状态。固定 UID 消失时不改用系统默认麦克风，也不自动恢复旧音频事件。

配置、执行代码、视觉模板、地点／路线及实际使用的自定义标定文件变更会改变 profile_sha256（通过 runtime_world.profile_digest 计算；自定义文件作为 extra_paths 传入），原验收不会自动覆盖新版本。能力启用须审核；不能通过把 calibrated 改成 true 来替代模板和实测证据。

## 验收与复盘

运行 `.venv/bin/python -m unittest discover -p 'test_*.py'`。原始截图与 OCR 证据已复制到 `tests/fixtures`，manifest 保存来源、大小和 SHA-256。测试缺证据直接失败；不要将 fixtures 纳入日志清理。

`runtime_acceptance.py --input acceptance/evidence.json --output acceptance/status.json` 统计全部试验和独立样本。schema v2 要求范围、版本、配置、起始条件、完整尝试台账、证据链、逐项安全计数和场景检查。缺失值不按零处理，人工接管保留并打断连续成功；NaN、伪布尔或合成数据不能用于实机放行。矿点与声音检测的开发／验收 session 必须分离。字段说明见 [acceptance/README.md](acceptance/README.md)。它不会自动激活 capability。

回归中已覆盖取消排队输入、旧／未来帧、窗口变换、慢 OCR、异常终态、未知攻击者撤离、不确定交易不重放、背包稳定性、拾取证据、导航停滞、单次收竿、售卖前后确认、缺证据不能验收等行为。合成回放明确标记 synthetic，不计入真实样本或实机成功次数。

三个历史案例的初版复盘包位于 `reviews/20260928/`，增强后的版本位于 `reviews/20260928-audit/`：导航无进展、矿点歧义、剥皮回执未确认。facts.json 保存事实、递归源文件哈希和显式证据缺口，timeline.jsonl 保留时间线与源行号。proposal.json 区分假设和建议，activate 始终为 false。导出后已逐一复核源文件未变；这不补齐历史上未记录的证据。尚未取得新实机复验的建议不得启用。

验收仍要求计划中的真实样本量、往返／采集／交易次数、30 次抛竿、遇袭恢复以及两场 30 分钟运行。日志轮转只作用于本次新运行，证据不足必须补测，不能从历史成功中推算新运行器已通过。

## 职业差异与共享能力边界（2026-09-28）

完整刷怪编排仍由同一个Controller.patrol负责，巡游、恢复、拾取确认、背包、导航、异常处理和输入执行器共享。职业配置只提供战前准备、接近选择、输出节奏、技能绑定和资源阈值；共享模块按能力/参数选择实现，不按职业名特判。血条和背包布局是当前人物UI标定，并非职业规则，目前由vision_profile引用以兼容已有入口。

本次移除“仅圣骑士拾取未确认就停止”的特判，所有职业启用拾取时统一确认后才进入下一轮；--no-loot仍为显式任务开关。新增编排回归对术士、圣骑士检查同一条“无目标→巡游→重搜→击杀→拾取”顺序以及拾取失败/跳过/取消时停止。归档快照中的公共文件只表示历史版本，当前共享代码继续维护一份。详见classes/README.md；本次整理未进行游戏输入或实机验收。
