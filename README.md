# 社交媒体舆情分析与热点事件预测系统

## 项目简介

本系统是一个完整的大数据舆情分析平台，涵盖数据采集、存储、处理、建模、可视化全链路。

### 核心技术栈

| 模块 | 技术 | 作用 |
|------|------|------|
| 数据采集 | Python爬虫（requests + BeautifulSoup） | 爬取微博/知乎/豆瓣数据 |
| 数据存储 | MongoDB + 本地JSON | 非结构化文本存储 |
| 数据处理 | jieba分词 + TF-IDF | 文本清洗、特征提取 |
| 情感分类 | TextCNN（卷积神经网络） | 正面/负面情感判断 |
| 主题发现 | LDA（潜在狄利克雷分配） | 自动发现热门话题 |
| 传播预测 | SIR（微分方程模型） | 预测话题传播趋势和峰值 |
| 趋势预测 | ARIMA（时间序列模型） | 预测未来热度走势 |
| 可视化 | ECharts（词云+趋势图+关系图） | 舆情监控大屏 |
| Web应用 | Flask + HTML/CSS/JS | 完整Web应用 |

### 系统架构

```
┌─────────────────────────────────────────────────┐
│                  Web 大屏展示                     │
│  ┌──────────┬──────────┬──────────┬──────────┐   │
│  │  词云图   │ 情感趋势  │ SIR预测   │ ARIMA预测 │   │
│  └──────────┴──────────┴──────────┴──────────┘   │
├─────────────────────────────────────────────────┤
│                  Flask API 层                     │
├────────┬────────┬────────┬────────┬──────────────┤
│ TextCNN│  LDA   │  SIR   │ ARIMA  │  TF-IDF     │
│ 情感分类│ 主题模型│ 传播预测│ 趋势预测│  特征提取    │
├────────┴────────┴────────┴────────┴──────────────┤
│              数据处理层 (jieba + NLP)              │
├─────────────────────────────────────────────────┤
│           MongoDB / JSON 文件存储                  │
├─────────────────────────────────────────────────┤
│           数据采集 (爬虫)                          │
└─────────────────────────────────────────────────┘
```

## 快速开始

### 🚀 环境配置

```bash
# 创建 conda 环境
conda create -n sentiment python=3.9 -y
conda activate sentiment

# 安装完整依赖
pip install -r requirements.txt
```

### 📊 运行完整流水线

```bash
# 生成数据 + 训练模型 + 导入存储
python scripts/run_pipeline.py
```

### 🌐 启动Web大屏

```bash
# 启动 Flask Web 服务（实时运行所有模型）
python -m web.app
# 访问 http://localhost:5000
```

### 💾 使用 MongoDB（可选）

```bash
# 安装 MongoDB 后
python -c "from storage.mongo_client import MongoStorage; db = MongoStorage(); db.connect(); db.import_all_data()"
```

## 项目结构

```
├── config.py                  # 全局配置
├── requirements.txt           # 依赖清单
├── README.md                  # 项目说明
│
├── crawler/                   # 数据采集模块
│   ├── weibo_crawler.py       # 微博爬虫
│   └── zhihu_crawler.py       # 知乎爬虫（内嵌）
│
├── storage/                   # 数据存储模块
│   └── mongo_client.py        # MongoDB 客户端
│
├── processing/                # 数据处理模块
│   └── text_processor.py      # 分词、TF-IDF、特征提取
│
├── models/                    # 模型模块
│   ├── textcnn.py             # TextCNN 情感分类
│   ├── lda_model.py           # LDA 主题模型
│   ├── sir_model.py           # SIR 传播预测
│   └── arima_model.py         # ARIMA 趋势预测
│
├── web/                       # Web 应用
│   ├── app.py                 # Flask 后端
│   └── templates/
│       └── index.html         # ECharts 大屏
│
├── scripts/                   # 脚本
│   ├── generate_data.py       # 模拟数据生成
│   └── run_pipeline.py        # 完整流水线
│
└── data/                      # 数据目录
    ├── raw/                   # 原始数据
    ├── processed/             # 处理后数据
    └── stopwords.txt          # 停用词表
```

## 数学原理

### TextCNN 情感分类

```
Input → Embedding → [Conv1D(k=3), Conv1D(k=4), Conv1D(k=5)] → MaxPool → Dense → Softmax

损失函数：L = -Σ y_true × log(y_pred)
```

### SIR 传播模型

```
dS/dt = -β × S × I
dI/dt = β × S × I - γ × I
dR/dt = γ × I

R₀ = β/γ（基本再生数，R₀ > 1 则话题爆发）
```

### ARIMA 时间序列

```
ARIMA(p,d,q)：
  ΔᵈXₜ = c + ΣφᵢΔᵈXₜ₋ᵢ + Σθⱼεₜ₋ⱼ + εₜ

模型选择：AIC = 2k - 2ln(L)
```

### LDA 主题模型

```
每篇文档 = 多个主题的混合
每个主题 = 多个词的概率分布

θ_d ~ Dirichlet(α)
z ~ Multinomial(θ_d)
w ~ Multinomial(φ_z)
```

## 数据说明

- 本项目使用模拟数据（`scripts/generate_data.py` 生成）
- 包含 2000 条模拟微博帖子 + 8000 条评论
- 5 个话题类别：AI人工智能、教育热点、科技数码、社会民生、娱乐体育
- 60 天话题热度时间序列
