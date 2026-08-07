# 高校校园公共安全与民生服务舆情态势可视化平台使用手册

本文档适用于“高校校园公共安全与民生服务舆情态势可视化平台”（简称“校园舆情雷达 V2”）的本地使用、竞赛演示和基础运维。项目面向高校管理人员、校园公共安全与民生服务分析人员，以及非商业教学、个人研究和数据可视化竞赛场景。

> 使用边界：情感词典、事件聚合、热度评分、风险预警、LDA、SIR、ARIMA 和可选大模型都是辅助研判工具。任何结论都应结合原始证据、样本范围和数据来源人工复核。

## 1. 系统能做什么

系统围绕一条可追踪的舆情研判流程工作：

```text
创建监测项目
→ 导入文件或采集 RSS / 微博数据
→ 清洗、去重和规则匹配
→ 聚合校园安全与民生服务事件
→ 分析情感、热度和风险
→ 生成并处置预警
→ 查看交互式可视化
→ 导出证据简报、PDF 或 CSV
```

主要页面如下：

| 页面 | 主要用途 |
|---|---|
| 今日态势 | 查看声量—风险演化、来源构成、风险矩阵、事件时间带和证据关系网 |
| 监测项目 | 设置关键词、必须词、排除词、风险词、数据源、周期和阈值 |
| 信号流 | 查看命中规则的原始舆情、来源、情感标签和原文链接 |
| 事件中心 | 查看相似信号聚合后的校园事件、指标、演化和证据 |
| 预警处置 | 确认、调查、解决、标记误报或重新打开预警 |
| 数据接入 | 导入 JSON、JSONL、CSV，查看接入结果和来源错误 |
| 报告 | 生成并下载 PDF 报告或 CSV 明细 |
| 设置 | 查看运行配置以及修改管理员密码 |

## 2. 运行环境

项目已经针对以下本地环境进行过验证：

```text
Conda 环境：cv
Python 路径：D:\an\envs\cv\python.exe
Python 版本：3.10.20
本地端口：5000
```

项目目录：

```text
D:\智能科学与技术\人工智能与大数据应用创新2-校
```

## 3. 第一次配置

打开 PowerShell 或 Anaconda Prompt，依次执行：

```powershell
# 进入项目根目录。
# -LiteralPath 可以正确处理目录中的中文和特殊字符。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 激活项目使用的 Conda cv 环境。
# 成功后，命令行提示符前面会出现 (cv)。
conda activate cv

# 安装通过项目测试的锁定版本依赖。
# 第一次运行、requirements.lock 更新或提示缺少模块时执行。
python -m pip install -r requirements.lock

# 检查项目根目录是否已经存在本机配置文件 .env。
# 返回 True 表示已有配置，不要使用模板覆盖它；返回 False 才执行下一条复制命令。
Test-Path -LiteralPath '.env'

# 仅在上一条命令返回 False 时执行。
# 从安全模板创建本机配置文件；.env 已被 Git 忽略，不应提交到远程仓库。
Copy-Item -LiteralPath '.env.example' -Destination '.env'

# 生成一条安全随机密钥。
# 复制命令输出，稍后填写到 .env 的 APP_SECRET_KEY 中。
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 使用 Windows 记事本打开 .env。
# 修改并保存后，需要重新启动 Web 服务才会生效。
notepad .env
```

至少修改 `.env` 中的以下内容：

```dotenv
# Flask 会话加密密钥，填写刚才生成的随机字符串。
APP_SECRET_KEY=请替换为随机密钥

# 管理员登录名，可以继续使用 admin，也可以自行修改。
ADMIN_USERNAME=admin

# 管理员登录密码，建议至少 10 位，并包含大小写字母、数字和符号。
ADMIN_PASSWORD=请替换为自己的强密码

# 本地调试使用同步任务模式，不需要另外启动 Redis 和 Worker。
TASK_QUEUE_MODE=inline

# 未安装并授权微博采集器时保持 false。
COLLECTOR_LICENSE_ACCEPTED=false
```

项目没有写死的通用密码。登录密码就是当前 `.env` 中的 `ADMIN_PASSWORD`。

## 4. 启动与登录

### 4.1 正常启动

```powershell
# 进入项目目录，确保 Python 能找到 web 模块和本机配置。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 激活 Conda cv 环境。
conda activate cv

# 启动本地 Flask Web 服务。
# 看到 Running on http://127.0.0.1:5000 表示启动成功。
python -m web
```

如果不想先激活 Conda 环境，可以直接调用指定解释器：

```powershell
# 使用 cv 环境中的 Python 直接启动项目。
# 适合 conda activate 在当前 PowerShell 中不可用时使用。
& 'D:\an\envs\cv\python.exe' -m web
```

### 4.2 访问地址

- 登录入口：<http://127.0.0.1:5000>
- V2 工作台：<http://127.0.0.1:5000/workspace>
- 分析实验室：<http://127.0.0.1:5000/workspace#analytics>

`/`、`/workspace` 和 `/dashboard` 现在属于同一个产品入口。`/dashboard` 仅用于兼容旧书签，会跳转到工作台中的“分析实验室”，不会再打开另一套页面或读取另一套展示数据。

使用 `.env` 中的 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 登录。

### 4.3 停止与重启

在运行服务器的 PowerShell 窗口按 `Ctrl+C` 即可停止。修改 `.env` 或后端代码后，应停止再重新运行 `python -m web`。

## 5. 第一次体验：竞赛离线演示

这是最稳定的演示方式，不依赖答辩现场网络或第三方平台登录。

1. 登录后进入“今日态势”。
2. 点击“载入离线演示案例”。
3. 阅读合成数据声明并确认载入。
4. 等待页面显示 54 条带版本、时间和来源标记的合成记录。
5. 按“发现 → 聚合 → 核验 → 处置”的顺序讲解可视化。
6. 查看声量—风险演化和来源构成。
7. 点击事件风险矩阵中的节点或事件时间带中的事件。
8. 查看事件演化、来源、指标和原始证据信号。
9. 进入“预警处置”，演示确认、调查、解决或误报。
10. 进入“分析实验室”，选择演示监测项目，展示词频、情感趋势、事件排行和来源结构。
11. 需要时运行摘要、LDA、ARIMA 或 SIR；页面会先说明当前样本是否达到最低数据要求。
12. 进入“报告”，生成 PDF 或 CSV。

离线演示数据具有以下特点：

- 固定 54 条合成记录，不伪装成真实采集数据；
- 稳定聚合为食品与餐饮安全、数字服务保障、反诈与缴费安全 3 条事件线；
- 重复载入具有幂等性，不会不断产生重复数据；
- 页面会持续显示演示数据标记；
- 来源—事件图表示证据关联，不代表转发路径或因果传播关系。

完整的 5 分钟答辩流程见[竞赛演示脚本](evidence/competition-demo-script.md)。

### 5.1 分析实验室的数据口径

分析实验室不是独立系统，它复用“校园舆情雷达”的监测项目、信号和事件数据：

- 切换监测项目后，词频、情感趋势、事件排行、来源结构和高级模型会一起切换；
- 页面明确显示数据性质、样本数、有效时间、情感方法和解释边界；
- LDA 至少需要 10 篇有效文本；ARIMA 至少需要 10 个观测日；SIR 至少需要 6 个观测日；
- 数据不足时会显示原因，不会静默补入模拟数据；
- 高级模型结果只用于辅助解释，必须回到信号流和事件原文进行人工核验。

## 6. 使用真实数据

### 6.1 导入 JSON、JSONL 或 CSV

进入“数据接入”，选择文件并提交。单个文件最大为 10 MB。

每条记录至少需要正文。推荐字段如下：

| 含义 | 支持的字段示例 |
|---|---|
| 原始数据 ID | `id`、`source_id`、`post_id` |
| 正文 | `text`、`content`、`desc` |
| 作者 | `author` |
| 发布时间 | `created_at`、`published_at` |
| 点赞量 | `likes` |
| 评论量 | `comments` |
| 转发量 | `reposts` |
| 原始链接 | `source_url` |
| 数据平台 | `platform`、`source` |

JSON 示例：

```json
[
  {
    "id": "source-001",
    "text": "部分学生反映校园餐饮服务存在异常，相关部门正在核验",
    "author": "公开昵称",
    "created_at": "2026-08-06 09:30:00",
    "likes": 120,
    "comments": 35,
    "reposts": 18,
    "source_url": "https://example.com/post/001",
    "platform": "import"
  }
]
```

CSV 示例：

```csv
id,text,author,created_at,likes,comments,reposts,source_url,platform
source-001,部分学生反映校园餐饮服务存在异常，相关部门正在核验,匿名用户,2026-08-06 09:30:00,120,35,18,https://example.com/post/001,import
source-002,学校后勤部门已经发布阶段性情况通报,学校公开信息,2026-08-06 10:15:00,86,21,12,https://example.com/post/002,import
```

数据处理规则：

- 缺少正文的记录会被拒绝并计入失败统计；
- 缺少 ID 时，系统会根据正文与时间生成稳定哈希；
- 数据使用“平台 + 原始 ID”去重；
- 请只导入有权使用的数据，公开展示前应去除手机号、Cookie、密钥等敏感信息。

### 6.2 使用 RSS/Atom

在“监测项目”的 `RSS / Atom 地址` 中逐行填写公开订阅地址，例如：

```text
https://example.com/feed.xml
https://example.org/news.atom
```

服务器只接受 `http` 和 `https`，默认拒绝本机、局域网、链路本地和保留 IP，并限制响应大小和访问超时。不要为了访问不可信地址而关闭安全限制。

### 6.3 使用微博采集器

微博采集是可选功能，核心演示、文件导入、分析和报告不依赖它。项目通过外部 MediaCrawler 适配器接入，不复制第三方源码。

使用条件：

- 仅用于非商业学习和研究；
- 阅读并接受 MediaCrawler 许可证和平台规则；
- 使用本人有权使用的账号扫码授权；
- 只采集依法可以访问的公开数据；
- 遇到验证码、登录失效或权限限制时人工处理；
- 不绕过验证码、访问控制或平台安全措施。

安装命令：

```powershell
# 激活项目使用的 Conda 环境。
conda activate cv

# 进入项目目录。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 下载项目固定版本的外部 MediaCrawler，并记录你已接受非商业许可证。
# 只有确认项目用途符合其许可证时才能执行。
.\scripts\setup_mediacrawler.ps1 -AcceptNonCommercialLicense

# 进入外部 MediaCrawler 目录。
Set-Location -LiteralPath '.\external\MediaCrawler'

# 按外部项目的锁定依赖安装运行环境；执行前需要已经安装 uv。
uv sync

# 返回舆情雷达项目根目录。
Set-Location -LiteralPath '..\..'
```

完成扫码登录和外部项目配置后，在 `.env` 中设置：

```dotenv
COLLECTOR_LICENSE_ACCEPTED=true
MEDIACRAWLER_HOME=external/MediaCrawler
MEDIACRAWLER_OUTPUT_DIR=external/MediaCrawler/data
MEDIACRAWLER_COMMAND_JSON=["uv","run","main.py","--platform","wb","--lt","qrcode","--type","search"]
```

真实微博采集依赖本机浏览器交互，建议在 Conda 本地模式中运行。答辩现场建议同时准备脱敏文件和离线演示作为可靠备选。

## 7. 创建并运行监测项目

第一次测试可以使用以下配置：

| 字段 | 示例值 |
|---|---|
| 项目名称 | 校园食品安全舆情监测 |
| 任务说明 | 监测校园食堂、食品安全和学生健康相关公开信息 |
| 关键词 | 食堂, 食品安全, 校园餐, 卫生 |
| 必须词 | 校园 |
| 排除词 | 招聘, 广告, 推销 |
| 风险词 | 中毒, 投诉, 异物, 曝光, 送医, 封停 |
| RSS / Atom 地址 | 可暂时留空，或填写真实公开订阅源 |
| 微博采集器 | 首次测试不要勾选 |
| 间隔 | 60 分钟 |
| 负面阈值 | 50% |
| 突增条数 | 5 |
| Webhook | 没有接收服务时留空 |

创建后按以下顺序操作：

1. 点击监测卡片上的“立即运行”。
2. 查看运行状态、采集数量、重复数量和来源错误。
3. 打开“信号流”，核对命中内容和命中原因。
4. 打开“事件中心”，查看聚合后的校园安全与民生服务事件。
5. 在事件详情中核对原始证据信号。
6. 打开“预警处置”，记录人工判断和处理状态。
7. 生成事件证据简报或进入“报告”导出文件。

如果信号流为空，请依次检查：

- 是否已经导入数据或配置有效 RSS；
- 监测项目是否已经点击“立即运行”；
- 运行记录是否显示具体来源错误；
- 关键词和必须词是否过严；
- 排除词是否误删了需要的内容；
- 当前页面筛选条件是否过滤了全部数据。

## 8. 预警处置

系统可能根据风险词、负面比例或讨论量突增生成预警。建议处置流程如下：

1. 打开预警，核对规则说明、事件指标和原始证据。
2. 证据不足时标记为“调查中”，补充数据后再判断。
3. 确认风险后标记为“已确认”，并记录处置说明。
4. 风险处理完成后标记为“已解决”。
5. 确认规则误触发时标记为“误报”，保留审计记录。
6. 后续出现新证据时可以重新打开预警。

负面、热度和风险词只是筛查指标，不应直接写成事实结论。

## 9. 报告导出

进入“报告”页面：

1. 选择监测项目。
2. 选择 PDF 报告或 CSV 明细。
3. 点击“生成报告”。
4. 等待任务状态变为成功。
5. 点击下载。

报告应保留关键词、观察范围、数据来源、样本量、模型或规则说明、指标和限制。公开展示前请检查报告中是否含有不应公开的个人信息。

## 10. 可选大模型

核心流程不需要大模型。默认事件简报由数据库中的指标和真实信号确定性生成。

如需接入本地 Ollama、LM Studio 或其他 OpenAI 兼容端点，可以在 `.env` 中设置：

```dotenv
# 开启可选大模型摘要功能。
LLM_ENABLED=true

# OpenAI 兼容接口地址；请根据本机实际服务修改。
LLM_BASE_URL=http://host.docker.internal:11434/v1

# 填写端点提供的模型名称。
LLM_MODEL=你的模型名称

# 本地服务不需要密钥时可以留空；远程端点请填写专用密钥。
LLM_API_KEY=
```

修改后重新启动项目。模型结果必须通过结构和证据信号 ID 校验；没有有效证据引用的结果不会被当作正式简报。

## 11. 自动化测试

```powershell
# 进入项目目录。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 激活项目指定的 Conda cv 环境。
conda activate cv

# 运行全部 Python 自动化测试。
# 当前版本的预期结果是 Ran 42 tests，并在最后显示 OK。
python -m unittest discover -s tests -v

# 编译检查全部 Python 文件。
# 命令没有错误输出通常表示 Python 语法检查通过。
python -m compileall -q .

# 检查竞赛可视化 JavaScript 文件语法。
# 没有输出且退出码为 0 表示语法通过；需要本机已经安装 Node.js。
node --check web/static/visualization.js

# 检查 V2 工作台 JavaScript 文件语法。
node --check web/static/product.js
```

覆盖率测试：

```powershell
# 在 cv 环境中安装 coverage；已经安装时会直接复用。
python -m pip install coverage

# 运行测试并采集核心模块覆盖率。
python -m coverage run --source=crawler,services,storage,web -m unittest discover -s tests -v

# 在终端显示逐文件覆盖率和缺失行。
python -m coverage report -m
```

## 12. Docker Compose 部署

本机演示优先使用 Conda `cv`。需要 Web、Worker、Scheduler 和 Redis 分离运行时，可以使用 Docker Compose。

```powershell
# 进入项目根目录，Docker Compose 会读取这里的 compose 文件和 .env。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 检查 .env 是否存在；如果返回 False，应先根据第 3 节创建并设置强密钥和密码。
Test-Path -LiteralPath '.env'

# 构建镜像并在后台启动 Web、Worker、Scheduler 和 Redis。
# Compose 会把任务模式切换为 rq。
docker compose up --build -d

# 查看各个容器是否正常运行以及健康状态。
docker compose ps

# 持续查看核心服务日志；按 Ctrl+C 只停止日志跟踪，不会停止容器。
docker compose logs -f web worker scheduler
```

健康检查：

```powershell
# 检查 Web 进程是否存活，正常时返回 ok=true。
Invoke-RestMethod 'http://127.0.0.1:5000/api/v1/health/live'

# 检查数据库、任务队列和生产配置是否就绪。
Invoke-RestMethod 'http://127.0.0.1:5000/api/v1/health/ready'
```

停止服务：

```powershell
# 停止并移除项目容器和网络，但保留 SQLite、报告和 Redis 持久卷。
docker compose down
```

不要执行 `docker compose down -v`，除非明确需要删除数据库、报告和队列数据。

## 13. 从 GitHub 更新本机

更新前先停止正在运行的 Web 服务，并确认自己的代码已经提交或备份。

```powershell
# 进入项目目录。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 查看当前分支以及是否存在未提交修改。
# 如果显示 working tree clean，说明工作区干净。
git status

# 切换到主分支。
git switch main

# 获取 GitHub 上最新的分支和提交信息，但暂时不修改本地文件。
git fetch origin

# 仅允许快进更新本地 main。
# 如果本地和远程已经分叉，命令会停止，避免自动产生意外合并提交。
git pull --ff-only origin main

# 激活项目环境。
conda activate cv

# 根据最新版锁定文件同步 Python 依赖。
python -m pip install -r requirements.lock

# 运行全部测试，确认更新后的版本可用。
python -m unittest discover -s tests -v
```

## 14. 备份与恢复

本地数据库、导入文件和报告默认位于 `runtime/`。备份前应停止 Web、Worker 和 Scheduler，避免复制过程中数据库仍在写入。

### 14.1 创建备份

```powershell
# 进入项目根目录，确保复制的是当前项目的 runtime 数据。
Set-Location -LiteralPath 'D:\智能科学与技术\人工智能与大数据应用创新2-校'

# 指定备份根目录；可以根据自己的磁盘情况修改。
$backupRoot = 'D:\舆情雷达备份'

# 使用当前时间生成独立备份目录名称，避免覆盖历史备份。
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# 组合本次备份的完整路径。
$target = Join-Path $backupRoot $timestamp

# 创建备份目录；如果上级目录不存在，会一并创建。
New-Item -ItemType Directory -Force -Path $target | Out-Null

# 复制整个 runtime 目录，不删除也不修改原始运行数据。
Copy-Item -LiteralPath '.\runtime' -Destination $target -Recurse

# 输出备份位置，便于人工检查。
Write-Host "备份完成：$target"
```

### 14.2 恢复备份

恢复会影响当前运行数据。先停止服务并把当前 `runtime/` 另行备份，再将目标备份中的 `runtime` 内容复制回项目同名目录。不要在服务运行期间覆盖 SQLite 文件。

## 15. 常见问题

### 15.1 忘记管理员密码

```powershell
# 打开本机 .env 配置文件。
# 修改 ADMIN_PASSWORD 后保存，并重新启动 Web 服务。
notepad 'D:\智能科学与技术\人工智能与大数据应用创新2-校\.env'
```

### 15.2 登录页提示“尚未初始化管理员”

确认 `.env` 中已经设置非模板值的 `APP_SECRET_KEY` 和 `ADMIN_PASSWORD`，保存后重启服务。

### 15.3 端口 5000 被占用

```powershell
# 查找正在监听本机 5000 端口的连接。
$connection = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue

# 如果找到连接，显示对应进程的名称和 PID。
# 请先确认它是不是之前启动的舆情雷达服务。
if ($connection) {
    Get-Process -Id $connection.OwningProcess
}
```

确认是旧服务后再执行：

```powershell
# 仅结束刚才已经人工确认的 5000 端口进程。
# 不要在未检查进程名称时直接运行。
if ($connection) {
    Stop-Process -Id $connection.OwningProcess
}
```

### 15.4 RSS 显示 `unsafe_feed_url`

目标地址解析到了本机、内网或保留地址。应改用公开可信的 RSS/Atom 地址，不要关闭保护来绕过检查。

### 15.5 运行显示 `sources_need_attention`

打开“数据接入记录”，查看具体数据源失败原因。系统会明确报告来源失败，不会把采集失败伪装成成功的空结果。

### 15.6 微博任务暂停

- `license_confirmation_required`：阅读许可证并确认用途符合非商业学习研究范围；
- `login_or_challenge_required`：在浏览器中人工完成扫码或平台验证后重试；
- 不要通过修改程序绕过用途确认、验证码或访问控制。

### 15.7 LDA 或 ARIMA 不可用

按照页面提示增加有效文本、多样性或连续日期观测点。系统不会在正式分析数据不足时静默填入模拟数据。

### 15.8 报告生成失败

确认 `reportlab` 已安装，并检查 `runtime/reports` 是否可写。Docker 模式还需要检查 Worker 日志：

```powershell
# 查看 Worker 最近的日志，定位报告任务失败原因。
docker compose logs --tail 200 worker
```

## 16. 数据与安全注意事项

- `.env`、SQLite、采集会话、导入数据和生成报告均不应提交 Git；
- 不要把密码、Cookie、访问令牌、Webhook 密钥或真实个人信息截图公开；
- 修改过已经泄露的密码或令牌后，还应在对应平台撤销旧凭据；
- RSS、Webhook 和外部接口只使用可信地址；
- 数据采集必须遵守平台条款、法律要求和授权范围；
- 比赛展示应明确区分真实数据、脱敏数据和合成演示数据；
- 情感与风险指标不等于事实判断或因果关系；
- 所有重要结论应能回到原始信号和来源链接进行核验。

## 17. 相关文档

- [项目说明](../README.md)
- [竞赛演示脚本](evidence/competition-demo-script.md)
- [数据可视化竞赛证据报告](evidence/competition-data-visualization.md)
- [验收门槛](evidence/acceptance-gates.md)
- [产品 V2 验证记录](evidence/product-v2-validation.md)

