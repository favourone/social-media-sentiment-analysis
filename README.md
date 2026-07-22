# 社交媒体舆情分析与热点事件预测平台 V1

面向舆情分析人员的非商业教学与竞赛项目。系统把数据采集或导入、标准化去重、分析任务、风险预警、结果解释和 PDF/CSV 报告串成一条可追踪的工作流。

> 重要边界：系统不会在采集失败时自动填入模拟结果。词典情感、LDA、SIR 和 ARIMA 都是辅助分析方法，必须结合原文、样本范围和数据来源人工复核。

## 已完成的产品闭环

- 单管理员登录、密码哈希、CSRF 防护、会话过期和登录限流；
- SQLite 正式业务存储，WAL、事务和“平台 + 原始 ID”唯一约束；
- `pending / running / paused / succeeded / failed / cancelled` 统一任务状态；
- JSON、JSONL、CSV 安全导入和外部 MediaCrawler 微博适配器；
- 摘要、词频、LDA、ARIMA、SIR 和已有大屏分析能力；
- 带样本量、来源和解释边界的预警、PDF 报告与 CSV 明细；
- Docker Compose 的 Web、RQ Worker、Redis 和持久卷；
- 健康检查、自动化测试、CI 和证据文档。

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

然后访问 <http://127.0.0.1:5000>，使用 `.env` 中的 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 登录。工作台地址是 <http://127.0.0.1:5000/workspace>，原分析大屏是 <http://127.0.0.1:5000/dashboard>。

如果不想激活环境，也可以直接运行：

```powershell
& "D:\an\envs\cv\python.exe" -m web
```

## 首次体验流程

1. 登录工作台，进入“数据采集”。
2. 先上传 JSON、JSONL 或 CSV 文件验证完整闭环；单文件最大 10 MB。
3. 进入“分析任务”，选择“完整分析”。
4. 在“预警中心”查看达到阈值的话题，并回到原文人工复核。
5. 在“报告中心”生成 PDF 报告或 CSV 明细。

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
docker compose logs -f web worker
```

Compose 会把任务模式强制设为 `rq`，并启动：

- `web`：Waitress 托管的 Flask 应用；
- `worker`：单个 RQ Worker，串行执行耗时任务；
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

## 测试

```powershell
conda activate cv
python -m unittest discover -s tests -v
python -m compileall -q .
```

覆盖率验证：

```powershell
python -m pip install coverage
python -m coverage run --source=crawler.adapters,services,storage.product_store,web.product_api -m unittest discover -s tests -v
python -m coverage report -m
```

CI 只使用固定测试夹具和模拟采集适配路径，不连接真实社交平台。真实微博冒烟测试必须由用户扫码授权后手动执行。

## 备份、恢复与故障排查

本地运行的数据默认位于 `runtime/`。停止 Web 和 Worker 后，备份整个目录即可。恢复时把备份复制回相同位置，再启动服务。

- 登录页提示“尚未初始化管理员”：检查 `.env` 是否设置 `ADMIN_PASSWORD`，然后重启。
- `ready` 返回 `503`：检查生产密钥、Redis 和 Worker；本地 `inline` 模式只需确认密钥配置。
- 任务显示 `queue_unavailable`：Docker 中运行 `docker compose ps` 和 `docker compose logs worker`。
- 采集任务显示 `license_confirmation_required`：先阅读许可证，不能通过修改代码绕过用途确认。
- 采集任务显示 `login_or_challenge_required`：在浏览器中人工完成平台验证后重试。
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

基线、候选方案、验收门槛、验证记录和答辩材料位于 [`docs/evidence/`](docs/evidence/)。
