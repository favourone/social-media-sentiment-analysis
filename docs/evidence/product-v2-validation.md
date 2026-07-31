# 产品 V2 验证记录（2026-07-31）

## 问题与产品假设

V1 已经打通登录、导入、异步任务、模型分析、预警和报告，但主界面仍以 LDA、ARIMA、SIR 等算法入口组织。它能展示模型，却不能很好地支持分析人员反复执行“定义关注范围、发现新信号、归并事件、处置预警、引用原文形成简报”的日常工作。

V2 的可证伪假设是：把产品主线改为“监测项目 → 信号 → 事件 → 预警处置 → 证据简报”，并保留 V1 导入、分析和报告兼容能力后，固定测试数据应能完成整条闭环；规则、事件指标和简报引用必须可审计；采集、通知和可选模型失败不能伪装成成功；新增核心模块语句覆盖率不低于 80%。

## 实现范围

- `monitors` 保存关注词、必须词、排除词、风险词、来源、周期和阈值；
- `monitor_runs` 记录运行进度、来源错误、匹配数、事件数和预警数；
- `monitor_items` 记录每条信号的命中词、风险词与相关度；
- `events` / `event_items` 使用字符 n-gram TF-IDF 相似度形成可复现事件聚合；
- `incident_alerts` / `alert_actions` 支持新建、确认、调查、解决、误报和重新打开；
- `event_briefs` 默认生成确定性简报，可选调用 OpenAI 兼容接口，并校验引用 ID；
- `deliveries` 审计 Webhook 结果，只保存目标站点，不保存秘密路径；
- RSS/Atom 和 Webhook 在请求前校验目标地址，默认拒绝本机、内网和保留地址；
- Docker Compose 新增 Scheduler，按监测周期向 RQ 提交任务。

V1 表和 API 没有迁移或删除，因此已有数据库可以在启动时增量创建 V2 表，旧分析大屏和报告流程继续可用。

## 可复现验证

环境：

```text
Windows
D:\an\envs\cv\python.exe
Python 3.10.20
```

全量测试命令：

```powershell
$testTemp = Join-Path (Get-Location) 'runtime\test-temp'
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$env:TEMP=$testTemp
$env:TMP=$testTemp
& 'D:\an\envs\cv\python.exe' -m unittest discover -s tests -v
```

结果：

| 门槛 | V1 记录 | V2 结果 | 状态 |
|---|---:|---:|---|
| 全部自动化测试 | 29/29 | 39/39 | verified |
| V2 新增核心场景 | 0 | 10/10 | verified |
| V2 核心模块语句覆盖率 | 不适用 | 82%（886/1075） | verified，超过门槛 |
| 未登录访问 V2 | 不适用 | `401` | verified |
| 缺少 CSRF 创建监测 | 不适用 | `403` | verified |
| 完整监测闭环 | 不适用 | 6 条导入 → 6 条信号 → 事件 → 预警 → 简报 | verified，固定夹具 |
| 简报引用约束 | 不适用 | 非当前事件 ID 被剔除 | verified |
| Webhook 秘密路径 | 不适用 | 数据库仅保留 `scheme://host[:port]` | verified |
| 私有网络 RSS | 不适用 | 请求前拒绝 | verified |
| 暂停项目运行 | 不适用 | `409` | verified |
| Python 编译 | 通过 | 通过 | verified |
| JavaScript 语法 | 通过 | `node --check` 通过 | verified |

覆盖率命令：

```powershell
& 'D:\an\envs\cv\python.exe' -m coverage run `
  --source=crawler.rss_adapter,services.intelligence,services.briefing,services.delivery,services.network_safety,storage.monitor_store,web.intelligence_api `
  -m unittest tests.test_v2_monitoring
& 'D:\an\envs\cv\python.exe' -m coverage report -m
```

模块结果：RSS 76%、简报 84%、Webhook 81%、监测流水线 79%、出口安全 81%、V2 SQLite 89%、V2 API 80%，合并为 82%。覆盖率证明代码路径被固定测试执行，不证明真实平台覆盖度、舆情判断准确率或业务效果。

## 失败试验与修正

1. 第一次运行新测试时，受限 Windows 执行环境拒绝 Python 临时目录中的 SQLite 文件。改为在已授权且被 Git 忽略的 `runtime/test-temp` 中复测；这是执行沙箱限制，不是业务断言失败。
2. 最初 V2 核心覆盖率为 76%。补充真实价值较高的 Webhook 成功审计、可选模型引用过滤和监测来源增量更新测试后达到 82%，没有通过排除低覆盖模块缩小统计口径。
3. 多个监测项目可能把同一帖子归入不同事件。信号查询最初只按帖子连接事件，存在跨监测重复行风险；连接条件已加入当前 `monitor_id` 约束。
4. 事件热度和趋势采用描述性规则，不是预测准确率。界面和简报均展示样本数、时间窗口、来源数和不确定性，避免把评分解释为事实概率。

## 尚未验证

- 未在本机连接真实公开 RSS 做互联网冒烟测试；自动化测试使用固定 XML 与模拟请求。
- 未使用真实微博账号完成扫码采集；仍需用户在接受非商业许可证和平台条款后手动验证。
- 未启动真实 Redis/RQ/Scheduler 进行 Docker 冷启动、持久卷重启和定时触发测试。
- 未向真实 Webhook 服务发送通知，也未对外部 LLM 服务发送内容；自动化测试全部使用本地 Mock。
- 未进行目标分析人员的可用性测试、跨浏览器视觉检查、无障碍人工审查、并发压测或公网安全测试。
- 没有代表性标注数据，因此不声明情感、事件聚类或预警的精确率、召回率和业务收益。

下一项最高价值验证，是由用户先导入一组可公开核验的数据，在浏览器中完成一次从监测配置到预警处置的任务；随后在获得授权的前提下，分别进行一个公开 RSS 和一个微博扫码采集冒烟测试，保存来源、失败状态、去重统计与最终报告。
