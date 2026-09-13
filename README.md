# 高校校园公共安全与民生服务舆情态势可视化平台

**产品简称：校园舆情雷达 V2。** 面向高校管理人员、校园公共安全与民生服务分析人员的非商业教学与竞赛项目。平台不以“运行几个模型”为产品主线，而是把校园风险发现与服务响应整理成一条可追踪的闭环：

```text
校园监测项目 → RSS / 微博 / 文件数据 → 规则筛选 → 事件聚合
        → 可解释预警 → 人工处置 → 证据简报 / PDF / CSV
```

## 正式定位与分析范围

平台正式聚焦高校校园公开舆情中的两类问题：

- **公共安全**：食品与环境安全、校园治安、反诈与缴费安全、消防及突发事件；
- **民生服务**：校园网络与信息系统、宿舍与后勤、教学保障、交通和其他公共服务体验。

系统用于发现“何时出现异常、哪些信号属于同一事件、证据来自哪里、响应是否形成闭环”，不用于监控私人聊天、替代事实调查或自动给个人贴标签。当前竞赛离线案例选取食品与餐饮安全、数字服务保障、反诈与缴费安全三条事件线。

> 重要边界：系统不会在采集失败时自动填入模拟结果。情感词典、相似度聚类、热度评分、可选 LLM、LDA、SIR 和 ARIMA 都是辅助筛查方法，必须回到引用原文、样本范围和数据来源人工复核。

完整的安装、操作、测试、备份和故障排查步骤见 [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)。

## V2 的实用功能

- 单管理员登录、密码哈希、CSRF 防护、会话过期和登录限流；
- SQLite 正式业务存储，WAL、事务和“平台 + 原始 ID”唯一约束；
- 可复用监测项目：关注词、必须词、排除词、风险词、运行周期和阈值；
- RSS/Atom、JSON/JSONL/CSV 和外部 MediaCrawler 微博适配器；
- 信号流：显示匹配规则、风险词、来源、情感标签和原文链接；
- 事件中心：使用字符 n-gram TF-IDF 聚合相似信号，展示热度、趋势、来源覆盖、样本量与证据 ID；
- 国赛态势总览：声量—风险演化、来源构成、事件风险矩阵、事件时间带和来源—事件证据关系网；
- 图表可追溯：展示观察时间、样本数、缺失时间、情感方法和数据性质，每张复杂图提供数据表备用视图；
- 一键离线演示：固定 54 条带版本、时间和来源的合成记录，稳定聚合为食品与餐饮安全、数字服务保障、反诈与缴费安全 3 条事件线，不依赖答辩现场网络；
- 预警处置：风险词、负面比例、讨论量突增三类规则，支持确认、调查、解决、误报和重新打开，并记录审计轨迹；
- 证据简报：默认使用完全本地的确定性摘要；可选接入 OpenAI 兼容接口，模型引用必须是数据库中真实存在的信号 ID；
- Webhook 通知审计，数据库只保存目标站点，不保存包含令牌的完整路径；
- 分析实验室：严格按所选监测项目统一展示词频、情感趋势、事件排行、来源结构，并按需运行摘要、LDA、ARIMA、SIR；
- PDF/CSV 报告记录样本范围、数据来源、模型版本和解释边界；
- `pending / running / paused / succeeded / failed / cancelled` 统一任务状态，采集失败会明确暂停或失败；
- Docker Compose 的 Web、RQ Worker、监测 Scheduler、Redis 和持久卷；
- 健康检查、自动化测试、GitHub Actions CI 模板和证据文档。

## 算法升级 V3（suanfa 分支）：预训练模型 + 语义聚类 + 混合预测

在经典规则/统计基线之上，suanfa 分支引入三个可配置的算法升级。每个升级都保留原基线作为显式回退路径，并在结果元数据中记录实际使用的算法版本，证据引用不受影响：

| 模块 | 基线（可回退） | 新算法 | 版本号 | 启用方式 |
|---|---|---|---|---|
| 情感分析 | `transparent_lexicon_v2` 透明词典 | 微调中文 BERT/RoBERTa（默认 `hfl/chinese-roberta-wwm-ext`） | `bert_finetuned_v3` | `SENTIMENT_ENGINE=auto` + 训练检查点 |
| 事件聚合 | `char_ngram_tfidf_v2` 字符 n-gram | BERTopic（多语言句向量 + UMAP + HDBSCAN） | `bertopic_v3` | `CLUSTERING_ENGINE=auto` |
| 趋势预测 | ARIMA / SIR 独立基线 | 混合预测：ARIMA/SIR 基线 + LSTM 残差 + SIR 形状先验 | `hybrid_arima_sir_lstm_v3` | 分析类型 `hybrid`（`full` 附带） |

- **情感分析**：`score_sentiment()` 按来源标签 > BERT > 词典的顺序执行。BERT 结果保留词典交叉核对证据（`evidence.lexicon_cross_check`）；没有微调检查点或依赖缺失时自动回退词典，不会伪造模型结果。微调训练：

  ```powershell
  python scripts/finetune_bert_sentiment.py --train data/sentiment/train.jsonl --epochs 4
  ```

  数据约定 0=负面、1=正面，支持 JSON/JSONL/CSV；检查点写入 `models/saved/bert_sentiment` 并附带训练元数据。
- **事件聚合**：`CLUSTERING_ENGINE=auto` 时信号数 ≥ `BERTOPIC_MIN_DOCS` 才启用 BERTopic；HDBSCAN 噪声点保持为独立事件，依赖缺失或拟合失败自动回退 n-gram 基线。事件 `metrics.clustering_engine` 记录引擎、主题数与自动生成的中文主题词。
- **混合预测**：分析任务新增 `hybrid` 类型，`full` 分析也会附带。LSTM 在 ARIMA/SIR 双基线上学习残差，损失函数加入 SIR 形状约束（`HYBRID_SIR_PRIOR_WEIGHT`）；训练/验证按时间切分避免时序泄漏；置信区间来自验证集残差。

依赖安装：`pip install -r requirements-ml.txt`（PyTorch 已在 requirements.txt 中；首次运行 BERTopic 会下载多语言句向量模型）。核心监测流程仍不依赖这些可选组件，CI 只测试基线路径。

## 使用 Conda `cv` 环境运行（推荐）

本项目已经按你的环境验证。`requirements.lock` 保存了本次通过测试的直接依赖版本，`requirements.txt` 保留兼容版本范围：

```text
D:\an\envs\cv\python.exe
Python 3.10.20
```

在 Anaconda Prompt 或 PowerShell 中执行：

```powershell
conda activate cv
cd "D:\智能科学与技术\人工智能与大数据应用创新2-校"
python -m pip install -r requirements.lock
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

编辑 `.env`，至少替换以下两项：

```dotenv
APP_SECRET_KEY=刚才生成的随机字符串
ADMIN_PASSWORD=你自己的强密码，至少10位
```

本地调试保持：

```dotenv
TASK_QUEUE_MODE=inline
COLLECTOR_LICENSE_ACCEPTED=false
```

启动：

```powershell
python -m web
```

然后访问 <http://127.0.0.1:5000>，使用 `.env` 中的 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 登录。登录后统一进入 <http://127.0.0.1:5000/workspace>；分析功能位于 <http://127.0.0.1:5000/workspace#analytics>。旧书签 `/dashboard` 会自动跳转到分析实验室，不再维护第二套界面和第二套数据口径。

如果不想激活环境，也可以直接运行：

```powershell
& "D:\an\envs\cv\python.exe" -m web
```

## 建议的首次体验流程

1. 登录后在“今日态势”点击“载入离线演示案例”，先体验完整的竞赛叙事；页面会始终标明这是合成数据。
2. 沿“发现 → 聚合 → 核验 → 处置”阅读图表，点击风险矩阵节点或事件时间带查看事件演化、来源构成与原始证据。
3. 真实使用时进入“数据接入”，上传 JSON、JSONL 或 CSV；单文件最大 10 MB。
4. 进入“监测项目”，配置关注词、排除词、风险词和阈值，点击“立即运行”扫描已入库数据。
5. 在“信号流”核对命中原因，在“预警处置”记录确认、调查、解决或误报。
6. 进入“分析实验室”，确认当前监测项目后查看词频、情感趋势、事件排行和来源结构；高级模型也只使用这个项目的匹配信号。
7. 从事件详情生成带信号 ID 的证据简报，或在“报告”导出 PDF/CSV。

导入记录至少需要正文，推荐字段如下：

```json
[
  {
    "id": "wb-001",
    "text": "帖子正文",
    "author": "公开昵称",
    "created_at": "2026-07-22 10:30:00",
    "likes": 12,
    "comments": 3,
    "reposts": 1,
    "source_url": "https://weibo.com/example/wb-001"
  }
]
```

`id` 也可写成 `post_id` 或 `source_id`；正文可写成 `text`、`content` 或 `desc`。缺少 ID 时系统使用正文与时间生成稳定哈希，缺少正文的记录会被拒绝并计入任务统计。

## RSS、Webhook 与可选模型

监测项目可以逐行填写公开 RSS/Atom 地址。服务端只接受 `http`/`https`，会在请求前和重定向后解析地址，默认拒绝回环、内网、链路本地和保留 IP，并限制响应大小和超时。Webhook 使用同一套出口校验。只有在受信任的隔离网络中确实需要内部地址时，才设置：

```dotenv
ALLOW_PRIVATE_NETWORK_URLS=true
```

核心流程完全不依赖大模型。默认事件简报由事件指标和证据信号确定性生成。若要使用本地 Ollama、LM Studio 或其他 OpenAI 兼容接口，可配置：

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=你的模型名称
LLM_API_KEY=
```

模型返回内容会经过结构和引用校验；没有有效信号 ID 的结果会失败，不会冒充有证据的简报。请勿把密钥或未公开个人信息写入提示词。

## 微博采集器

项目选择高关注的 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 作为外部适配对象，并固定到提交 [`0625e01a6bc717a3fc9c96d3dac7fb8957043838`](https://github.com/NanmiCoder/MediaCrawler/commit/0625e01a6bc717a3fc9c96d3dac7fb8957043838)。它不被复制进本仓库，也不会随主项目自动安装。

其许可证限定非商业学习研究，且要求遵守平台条款、控制频率、禁止大规模采集。请先完整阅读 [NON-COMMERCIAL LEARNING LICENSE](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)。只有接受这些条件后才运行：

```powershell
conda activate cv
cd "D:\智能科学与技术\人工智能与大数据应用创新2-校"
.\scripts\setup_mediacrawler.ps1 -AcceptNonCommercialLicense
cd external\MediaCrawler
uv sync
cd ..\..
```

按照 MediaCrawler 官方说明启用 Chrome CDP 和扫码登录，再在 `.env` 中设置：

```dotenv
COLLECTOR_LICENSE_ACCEPTED=true
MEDIACRAWLER_HOME=external/MediaCrawler
MEDIACRAWLER_OUTPUT_DIR=external/MediaCrawler/data
MEDIACRAWLER_COMMAND_JSON=["uv","run","main.py","--platform","wb","--lt","qrcode","--type","search"]
```

适配器只会临时改写外部项目的关键词、采集上限、输出格式和低并发设置，任务结束或失败时恢复原配置。遇到登录失效、验证码或权限限制时任务会进入 `paused`，不会绕过平台验证，也不会生成假数据。

交互式扫码依赖本机浏览器，因此推荐在 Conda 本地模式中运行真实采集。Docker 部署默认支持文件导入、分析与报告；没有经过授权的 Cookie、登录状态或第三方代码不会打进镜像。

## Docker 单机部署

先创建并编辑 `.env`，务必使用非示例密钥和密码：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
docker compose logs -f web worker scheduler
```

Compose 会把任务模式强制设为 `rq`，并启动：

- `web`：Waitress 托管的 Flask 应用；
- `worker`：单个 RQ Worker，串行执行耗时任务；
- `scheduler`：扫描到期监测项目并送入队列，避免同一项目并发重复运行；
- `redis`：队列服务和持久化队列元数据；
- `app-runtime`：SQLite、导入文件和报告；
- `redis-data`：Redis AOF 数据。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:5000/api/v1/health/ready
```

停止服务：

```powershell
docker compose down
```

不要执行 `docker compose down -v`，除非明确希望删除数据库、报告和队列数据。

## API

旧只读分析 API 保持兼容，例如 `/api/overview`、`/api/word_freq`、`/api/lda_topics`、`/api/sir_prediction` 和 `/api/arima_prediction`。

新增接口使用统一格式：成功为 `{"ok": true, "data": ...}`，失败为 `{"ok": false, "error": {"code": ..., "message": ...}}`。

| 接口 | 方法 | 认证 | 用途 |
|---|---|---:|---|
| `/api/v1/auth/login` | POST | 否 | 管理员登录 |
| `/api/v1/auth/logout` | POST | 是 + CSRF | 退出登录 |
| `/api/v1/auth/me` | GET | 是 | 当前账号和 CSRF token |
| `/api/v1/auth/password` | POST | 是 + CSRF | 修改密码 |
| `/api/v1/collection-jobs` | GET/POST | 是 | 采集任务列表与创建 |
| `/api/v1/collection-jobs/{id}` | GET | 是 | 任务状态和统计 |
| `/api/v1/collection-jobs/{id}/cancel` | POST | 是 + CSRF | 取消任务 |
| `/api/v1/collection-jobs/{id}/retry` | POST | 是 + CSRF | 重试暂停或失败任务 |
| `/api/v1/imports` | POST | 是 + CSRF | 上传 JSON/JSONL/CSV |
| `/api/v1/analysis-jobs` | GET/POST | 是 | 创建与查看分析任务 |
| `/api/v1/alerts` | GET | 是 | 查看可解释预警 |
| `/api/v1/reports` | GET/POST | 是 | 创建与查看报告 |
| `/api/v1/reports/{id}.pdf` | GET | 是 | 下载 PDF |
| `/api/v1/reports/{id}.csv` | GET | 是 | 下载 CSV |
| `/api/v1/health/live` | GET | 否 | 进程存活 |
| `/api/v1/health/ready` | GET | 否 | 数据库、队列和生产配置就绪状态 |

V2 工作台接口：

| 接口 | 方法 | 用途 |
|---|---|---|
| `/api/v2/overview` | GET | 今日态势、重点事件和待处置预警 |
| `/api/v2/visual-story` | GET | 按监测项目返回可追溯的时间线、来源、事件和证据网数据 |
| `/api/v2/analytics` | GET | 按单个监测项目返回词频、情感趋势、事件排行、来源结构和模型就绪条件 |
| `/api/v2/demo/seed` | POST | 幂等载入明确标记的固定合成演示数据 |
| `/api/v2/monitors` | GET/POST | 监测项目列表与创建 |
| `/api/v2/monitors/{id}` | GET/PATCH | 项目详情与规则修改 |
| `/api/v2/monitors/{id}/status` | POST | 启用、暂停或归档 |
| `/api/v2/monitors/{id}/run` | POST | 立即执行一次完整监测 |
| `/api/v2/monitor-runs` | GET | 运行记录、进度、来源错误和统计 |
| `/api/v2/signals` | GET | 按项目、关键词、平台和情感筛选信号 |
| `/api/v2/events` | GET | 事件聚合列表 |
| `/api/v2/events/{id}` | GET | 事件指标、证据信号和最新简报 |
| `/api/v2/events/{id}/briefs` | POST | 生成证据约束的事件简报 |
| `/api/v2/alerts` | GET | 预警队列 |
| `/api/v2/alerts/{id}/transition` | POST | 处置状态流转并写入审计记录 |
| `/api/v2/deliveries` | GET | Webhook 通知审计 |
| `/api/v2/settings` | GET | Scheduler、网络和简报模式状态 |

除登录和健康检查外，业务 API 都要求管理员登录；所有 V1/V2 写请求都要求当前会话的 CSRF token。

## 测试

```powershell
conda activate cv
python -m unittest discover -s tests -v
python -m compileall -q .
```

覆盖率验证：

```powershell
python -m pip install coverage
python -m coverage run --source=crawler,services,storage,web -m unittest discover -s tests -v
python -m coverage report -m
```

本机 Conda `cv` 环境的当前结果是 **59/59 通过**，包括原有 API 回归、V2 监测闭环、可视化聚合、54 条固定演示数据的幂等验证，以及 suanfa 分支新增的算法升级回归（BERT 情感开关与回退、BERTopic 语义聚类确定性、混合预测区间）。CI 覆盖率口径的当前结果为 **81%（1013/1250）**。V2 原核心模块的历史记录覆盖率为 **82%（886/1075）**；本次新增的合成案例与可视化聚合服务为 **93%（183/196）**。复测记录见 [`docs/evidence/competition-data-visualization.md`](docs/evidence/competition-data-visualization.md)。已启用的 CI 工作流位于 `.github/workflows/ci.yml`，只使用固定测试夹具和模拟采集适配路径，不连接真实社交平台。真实微博冒烟测试必须由用户扫码授权后手动执行。

## 备份、恢复与故障排查

本地运行的数据默认位于 `runtime/`。停止 Web 和 Worker 后，备份整个目录即可。恢复时把备份复制回相同位置，再启动服务。

- 登录页提示“尚未初始化管理员”：检查 `.env` 是否设置 `ADMIN_PASSWORD`，然后重启。
- `ready` 返回 `503`：检查生产密钥、Redis 和 Worker；本地 `inline` 模式只需确认密钥配置。
- 任务显示 `queue_unavailable`：Docker 中运行 `docker compose ps` 和 `docker compose logs worker`。
- 采集任务显示 `license_confirmation_required`：先阅读许可证，不能通过修改代码绕过用途确认。
- 采集任务显示 `login_or_challenge_required`：在浏览器中人工完成平台验证后重试。
- RSS 任务显示 `unsafe_feed_url`：目标解析到了本机、内网或保留地址；不要为了绕过校验而关闭保护。
- 监测运行显示 `sources_need_attention`：打开数据接入记录查看具体来源错误；系统不会把失败显示成空数据成功。
- LDA 或 ARIMA 显示不可用：按照提示增加有效文本、多样性或日观测点；系统不会自动填入模拟结果。
- 报告生成失败：确认 `reportlab` 已安装且 `runtime/reports` 可写。

## 安全与结论边界

- `.env`、SQLite、采集会话、导入数据和报告均被 Git 忽略；
- 管理员密码只保存 Werkzeug 哈希，不保存明文；
- 页面不会回显密码、Cookie、密钥或异常堆栈；
- 默认只允许同源请求，跨域需显式配置 `CORS_ORIGINS`；
- 采集结果会受到登录权限、时间窗口、关键词与采样机制影响；
- 透明词典基线适合筛查，不是人工标注精度声明；
- 当前没有代表性真实用户测试，也没有跨平台独立测试集，不能宣称生产级舆情判断准确率。

基线、候选方案、验收门槛、验证记录和答辩材料位于 [`docs/evidence/`](docs/evidence/)。数据可视化赛道的 5 分钟演示脚本见 [`docs/evidence/competition-demo-script.md`](docs/evidence/competition-demo-script.md)。
