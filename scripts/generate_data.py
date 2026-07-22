# -*- coding: utf-8 -*-
"""
模拟数据生成脚本
================
生成用于开发和演示的模拟社交媒体数据
包含：微博帖子、评论、话题热度时间序列

运行方式：python scripts/generate_data.py
"""

import json
import os
import sys
import random
import argparse
from datetime import datetime, timedelta

# Windows 终端 UTF-8 编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ==================== 模拟数据池 ====================

# 热门话题模板
TOPIC_TEMPLATES = [
    {
        "topic": "AI人工智能",
        "keywords": ["ChatGPT", "大模型", "人工智能", "深度学习", "AI", "机器学习", "自动驾驶"],
        "hot_words": ["GPT-5发布", "AI绘画", "自动驾驶事故", "AI取代程序员", "大模型降价",
                      "AI换脸诈骗", "人工智能立法", "AI医疗诊断", "智能客服", "AI教育"],
    },
    {
        "topic": "教育热点",
        "keywords": ["高考", "考研", "就业", "教育改革", "双减", "留学", "职业教育"],
        "hot_words": ["高考改革新方案", "考研人数下降", "大学生就业难", "双减政策效果",
                      "留学回国潮", "职业教育崛起", "教师待遇", "在线教育", "学区房", "教育公平"],
    },
    {
        "topic": "科技数码",
        "keywords": ["华为", "苹果", "小米", "手机", "芯片", "5G", "新能源"],
        "hot_words": ["华为Mate70", "iPhone17", "小米SU7", "芯片突破", "5G覆盖",
                      "新能源补贴", "折叠屏手机", "卫星通信", "量子计算", "6G研发"],
    },
    {
        "topic": "社会民生",
        "keywords": ["房价", "医疗", "养老", "就业", "消费", "食品安全"],
        "hot_words": ["房价下跌", "医保改革", "延迟退休", "灵活就业", "消费券",
                      "食品安全事件", "快递新规", "直播带货", "社区团购", "碳中和"],
    },
    {
        "topic": "娱乐体育",
        "keywords": ["电影", "综艺", "足球", "篮球", "明星", "奥运会"],
        "hot_words": ["春节档电影", "世界杯预选", "综艺节目翻车", "明星塌房",
                      "电竞入奥", "全民健身", "体育产业", "音乐节", "短视频网红", "国潮文化"],
    },
]

# 正面情感词
POSITIVE_WORDS = [
    "太棒了", "支持", "点赞", "优秀", "厉害", "期待", "加油", "感动",
    "突破", "创新", "领先", "点赞", "好评", "推荐", "满意", "惊喜",
    "振奋人心", "前景光明", "值得期待", "越来越好", "重大进展",
]

# 负面情感词
NEGATIVE_WORDS = [
    "失望", "差评", "无语", "离谱", "过分", "愤怒", "担忧", "质疑",
    "造假", "翻车", "崩了", "退钱", "投诉", "曝光", "触目惊心",
    "不敢相信", "太过分了", "必须严查", "质量堪忧", "令人担忧",
]

# 用户名模板
USER_NAMES = [
    "阳光少年", "科技观察者", "热心网友", "吃瓜群众", "理性分析",
    "新闻评论员", "数据达人", "行业分析师", "普通市民", "在校学生",
    "职场新人", "退休老人", "创业者", "自由职业者", "媒体人",
]


def generate_post_text(topic_info, sentiment):
    """生成模拟帖子文本"""
    keyword = random.choice(topic_info["keywords"])
    hot_word = random.choice(topic_info["hot_words"])

    if sentiment == 1:  # 正面
        templates = [
            f"关于{hot_word}，我觉得{keyword}发展得{random.choice(POSITIVE_WORDS)}！未来可期。",
            f"刚看到{hot_word}的新闻，{keyword}真的{random.choice(POSITIVE_WORDS)}，必须{random.choice(POSITIVE_WORDS)}！",
            f"{hot_word}话题刷屏了，{keyword}领域{random.choice(POSITIVE_WORDS)}，{random.choice(POSITIVE_WORDS)}！",
            f"作为{keyword}的爱好者，看到{hot_word}真的很{random.choice(POSITIVE_WORDS)}。",
            f"{hot_word}！{keyword}这次{random.choice(POSITIVE_WORDS)}，大家怎么看？",
        ]
    else:  # 负面
        templates = [
            f"关于{hot_word}，{keyword}这次真的{random.choice(NEGATIVE_WORDS)}，太{random.choice(NEGATIVE_WORDS)}了。",
            f"{hot_word}的新闻看了，{keyword}怎么变成这样了？{random.choice(NEGATIVE_WORDS)}！",
            f"对{hot_word}很{random.choice(NEGATIVE_WORDS)}，{keyword}越来越{random.choice(NEGATIVE_WORDS)}了。",
            f"{hot_word}事件让人{random.choice(NEGATIVE_WORDS)}，{keyword}必须给个说法。",
            f"说实话，{hot_word}说明{keyword}存在很大问题，{random.choice(NEGATIVE_WORDS)}。",
        ]
    return random.choice(templates)


def generate_comment_text(sentiment):
    """生成模拟评论文本"""
    if sentiment == 1:
        templates = [
            f"说得对！{random.choice(POSITIVE_WORDS)}",
            f"确实如此，{random.choice(POSITIVE_WORDS)}",
            f"同意楼主，{random.choice(POSITIVE_WORDS)}！",
            f"{random.choice(POSITIVE_WORDS)}，转发了",
            f"终于有人说到点子上了，{random.choice(POSITIVE_WORDS)}",
        ]
    else:
        templates = [
            f"完全{random.choice(NEGATIVE_WORDS)}，不同意",
            f"呵呵，{random.choice(NEGATIVE_WORDS)}",
            f"你说的不对，{random.choice(NEGATIVE_WORDS)}",
            f"{random.choice(NEGATIVE_WORDS)}，取关了",
            f"这种观点真的{random.choice(NEGATIVE_WORDS)}",
        ]
    return random.choice(templates)


def generate_time_series(topic_name, days=60, reference_time=None):
    """
    生成话题热度时间序列
    模拟典型的舆论传播曲线：爆发 → 高峰 → 衰减
    """
    series = []
    reference_time = reference_time or datetime.now()
    base_date = reference_time - timedelta(days=days)

    # 随机选择爆发日
    outbreak_day = random.randint(5, days - 15)
    # 爆发峰值
    peak_value = random.randint(5000, 50000)
    # 衰减速度
    decay_rate = random.uniform(0.05, 0.15)

    for day in range(days):
        date = base_date + timedelta(days=day)
        if day < outbreak_day:
            # 爆发前：低热度
            value = random.randint(100, 500)
        elif day == outbreak_day:
            # 爆发日：峰值
            value = peak_value
        elif day < outbreak_day + 3:
            # 高峰期：高位震荡
            value = int(peak_value * random.uniform(0.7, 1.0))
        else:
            # 衰减期：指数衰减 + 噪声
            days_since_peak = day - outbreak_day
            value = int(peak_value * 0.7 * (1 - decay_rate) ** days_since_peak)
            value = max(100, value + random.randint(-200, 200))

        series.append({
            "date": date.strftime("%Y-%m-%d"),
            "value": max(0, value),
            "topic": topic_name,
            "data_kind": "synthetic",
        })

    return series


def generate_dataset(num_posts=2000, num_comments=8000, seed=42, reference_time=None):
    """生成完整数据集"""
    if num_posts < 1 or num_comments < 0:
        raise ValueError('num_posts 必须大于 0，num_comments 不能为负数')
    random.seed(seed)
    reference_time = reference_time or datetime.now()
    print("=" * 60)
    print("  社交媒体舆情分析系统 - 模拟数据生成")
    print("=" * 60)

    posts = []
    comments = []
    time_series_all = []

    # 生成帖子
    print(f"\n[1/3] 生成 {num_posts} 条模拟微博帖子...")
    for i in range(num_posts):
        topic_info = random.choice(TOPIC_TEMPLATES)
        sentiment = random.choices([0, 1], weights=[0.4, 0.6])[0]  # 60%正面
        post_time = reference_time - timedelta(
            days=random.randint(0, 60),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59)
        )

        post = {
            "post_id": f"post_{i:06d}",
            "user_name": random.choice(USER_NAMES) + str(random.randint(1, 9999)),
            "text": generate_post_text(topic_info, sentiment),
            "topic": topic_info["topic"],
            "keywords": random.sample(topic_info["keywords"], min(3, len(topic_info["keywords"]))),
            "sentiment": sentiment,
            "reposts": random.randint(0, 10000),
            "likes": random.randint(0, 50000),
            "comment_count": random.randint(0, 5000),
            "created_at": post_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": random.choice(["微博", "知乎", "豆瓣"]),
            "data_kind": "synthetic",
        }
        posts.append(post)

    # 生成评论
    print(f"[2/3] 生成 {num_comments} 条模拟评论...")
    for i in range(num_comments):
        parent = random.choice(posts)
        sentiment = random.choices([0, 1], weights=[0.45, 0.55])[0]
        comment_time = datetime.strptime(parent["created_at"], "%Y-%m-%d %H:%M:%S") + timedelta(
            hours=random.randint(0, 48),
            minutes=random.randint(0, 59)
        )

        comment = {
            "comment_id": f"cmt_{i:06d}",
            "post_id": parent["post_id"],
            "user_name": random.choice(USER_NAMES) + str(random.randint(1, 9999)),
            "text": generate_comment_text(sentiment),
            "sentiment": sentiment,
            "likes": random.randint(0, 1000),
            "created_at": comment_time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_kind": "synthetic",
        }
        comments.append(comment)

    # 生成时间序列
    print("[3/3] 生成话题热度时间序列...")
    for topic_info in TOPIC_TEMPLATES:
        series = generate_time_series(
            topic_info["topic"], days=60, reference_time=reference_time
        )
        time_series_all.extend(series)

    # 保存数据
    os.makedirs(config.RAW_DATA_DIR, exist_ok=True)

    posts_path = os.path.join(config.RAW_DATA_DIR, 'posts.json')
    comments_path = os.path.join(config.RAW_DATA_DIR, 'comments.json')
    series_path = os.path.join(config.RAW_DATA_DIR, 'time_series.json')

    with open(posts_path, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    with open(comments_path, 'w', encoding='utf-8') as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)
    with open(series_path, 'w', encoding='utf-8') as f:
        json.dump(time_series_all, f, ensure_ascii=False, indent=2)
    metadata = {
        "schema_version": 1,
        "dataset_type": "synthetic",
        "evidence_status": "verified",
        "generator": "scripts/generate_data.py",
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "reference_time": reference_time.isoformat(timespec='seconds'),
        "seed": seed,
        "counts": {
            "posts": len(posts),
            "comments": len(comments),
            "time_series_points": len(time_series_all),
        },
        "limitations": [
            "文本、账号、互动量、平台和时间均为程序生成，仅用于开发与演示。",
            "模型结果不得表述为真实社交媒体准确率、传播规模或用户影响。",
        ],
    }
    with open(config.DATASET_METADATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据生成完成！")
    print(f"   帖子数据：{posts_path} ({len(posts)} 条)")
    print(f"   评论数据：{comments_path} ({len(comments)} 条)")
    print(f"   时间序列：{series_path} ({len(time_series_all)} 条)")
    print(f"   来源清单：{config.DATASET_METADATA_PATH}")

    # 生成停用词表
    stopwords_path = os.path.join(config.DATA_DIR, 'stopwords.txt')
    stopwords = [
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
        "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
        "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
        "吗", "那", "么", "被", "从", "但", "还", "能", "对", "里",
        "后", "什么", "啊", "嗯", "哈", "呢", "吧", "呀", "嘛", "哦",
        "http", "https", "com", "cn", "www", "的", "了", "是", "在",
    ]
    with open(stopwords_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(stopwords))
    print(f"   停用词表：{stopwords_path} ({len(stopwords)} 个)")

    return posts, comments, time_series_all


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成带来源清单的可复现模拟数据')
    parser.add_argument('--posts', type=int, default=2000)
    parser.add_argument('--comments', type=int, default=8000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument(
        '--reference-time',
        help='ISO 时间；与 seed 一起可完全复现数据，例如 2026-07-01T12:00:00',
    )
    args = parser.parse_args()
    reference_time = datetime.fromisoformat(args.reference_time) if args.reference_time else None
    generate_dataset(args.posts, args.comments, args.seed, reference_time)
