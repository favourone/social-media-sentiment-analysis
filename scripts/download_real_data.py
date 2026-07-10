# -*- coding: utf-8 -*-
"""
真实数据下载脚本
================
从公开数据源下载中文情感分析数据，转换为项目统一格式

数据来源：
1. ChnSentiCorp - 中文情感语料库（酒店评论，约12000条）
2. 微博情感数据集 - 从GitHub公开仓库获取
3. 外卖/电商评论 - 大规模中文评论数据

运行方式：python scripts/download_real_data.py
"""

import os
import sys
import json
import csv
import random
from datetime import datetime, timedelta

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ==================== 数据源定义 ====================

# 数据源1：ChnSentiCorp 酒店评论（GitHub 直链）
CHNSENTICORP_URLS = [
    "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/datasets/ChnSentiCorp_htl_all/ChnSentiCorp_htl_all.csv",
    "https://raw.githubusercontent.com/huggingface/datasets/main/datasets/ChnSentiCorp/ChnSentiCorp_htl_all.csv",
]

# 数据源2：外卖评价
WAIMAI_URLS = [
    "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/datasets/waimai_10k/waimai_10k.csv",
]


def download_file(url, save_path, timeout=30):
    """下载文件"""
    if not HAS_REQUESTS:
        print("   ⚠️  requests 未安装")
        return False

    try:
        print(f"   下载中: {url[:80]}...")
        resp = requests.get(url, timeout=timeout, verify=False, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
        })
        resp.raise_for_status()

        with open(save_path, 'wb') as f:
            f.write(resp.content)

        size_kb = len(resp.content) / 1024
        print(f"   ✅ 下载成功: {save_path} ({size_kb:.1f} KB)")
        return True
    except Exception as e:
        print(f"   ❌ 下载失败: {e}")
        return False


def try_download(urls, save_path):
    """尝试从多个URL下载"""
    for url in urls:
        if download_file(url, save_path):
            return True
    return False


# ==================== 数据转换 ====================

def load_chnsenticorp(csv_path):
    """
    加载 ChnSentiCorp 数据集
    格式：label, review
    label: 1=正面, 0=负面
    """
    data = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)  # 跳过表头
        for row in reader:
            if len(row) >= 2:
                label = int(row[0])
                text = row[1].strip()
                if text and len(text) > 5:
                    data.append({'label': label, 'text': text})
    return data


def load_waimai(csv_path):
    """
    加载外卖评价数据集
    格式：label, review
    """
    data = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 2:
                try:
                    label = int(row[0])
                    text = row[1].strip()
                    if text and len(text) > 3:
                        data.append({'label': label, 'text': text})
                except ValueError:
                    continue
    return data


def convert_to_project_format(raw_data, source_name, topics=None):
    """
    将原始数据转换为项目统一格式

    参数：
        raw_data: [{'label': 0/1, 'text': '...'}, ...]
        source_name: 数据来源名称
        topics: 话题列表（随机分配）
    返回：
        posts: 帖子列表
        comments: 评论列表
    """
    if topics is None:
        topics = ["AI人工智能", "教育热点", "科技数码", "社会民生", "娱乐体育"]

    posts = []
    comments = []

    for i, item in enumerate(raw_data):
        text = item['text']
        sentiment = item['label']  # 1=正面, 0=负面

        # 随机生成时间（过去60天内）
        days_ago = random.randint(0, 60)
        hours = random.randint(0, 23)
        minutes = random.randint(0, 59)
        post_time = datetime.now() - timedelta(days=days_ago, hours=hours, minutes=minutes)

        # 随机分配话题
        topic = random.choice(topics)

        # 生成帖子
        post = {
            "post_id": f"real_{i:06d}",
            "user_name": f"user_{random.randint(1000, 9999)}",
            "text": text,
            "topic": topic,
            "keywords": [],  # 后续由NLP提取
            "sentiment": sentiment,
            "reposts": random.randint(0, 500),
            "likes": random.randint(0, 2000),
            "comment_count": random.randint(0, 200),
            "created_at": post_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source_name,
        }
        posts.append(post)

        # 每个帖子生成0-3条评论
        num_comments = random.randint(0, 3)
        for j in range(num_comments):
            comment_time = post_time + timedelta(hours=random.randint(0, 48))
            comment = {
                "comment_id": f"cmt_{i}_{j}",
                "post_id": post["post_id"],
                "user_name": f"user_{random.randint(1000, 9999)}",
                "text": text[:50] if len(text) > 50 else text,  # 评论通常较短
                "sentiment": sentiment if random.random() > 0.2 else 1 - sentiment,  # 80%同向
                "likes": random.randint(0, 100),
                "created_at": comment_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            comments.append(comment)

    return posts, comments


def generate_time_series_from_posts(posts, days=60):
    """从帖子数据生成时间序列"""
    from collections import defaultdict

    # 按话题和日期统计
    topic_date_count = defaultdict(lambda: defaultdict(int))
    for p in posts:
        date = p['created_at'][:10]
        topic_date_count[p['topic']][date] += 1

    # 生成完整时间序列
    base_date = datetime.now() - timedelta(days=days)
    time_series = []

    for topic, date_counts in topic_date_count.items():
        for day in range(days):
            date = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")
            value = date_counts.get(date, random.randint(10, 100))
            time_series.append({
                "date": date,
                "value": value,
                "topic": topic,
            })

    return time_series


# ==================== 主流程 ====================

def main():
    print("=" * 60)
    print("  真实数据下载与处理")
    print("=" * 60)

    os.makedirs(config.RAW_DATA_DIR, exist_ok=True)
    all_posts = []
    all_comments = []

    # ---- 1. 下载 ChnSentiCorp ----
    print("\n[1/3] 下载 ChnSentiCorp 酒店评论数据集...")
    chn_path = os.path.join(config.RAW_DATA_DIR, 'ChnSentiCorp_htl_all.csv')
    if os.path.exists(chn_path):
        print("   文件已存在，跳过下载")
    else:
        success = try_download(CHNSENTICORP_URLS, chn_path)
        if not success:
            print("   ⚠️  自动下载失败，请手动下载：")
            print("   https://github.com/SophonPlus/ChineseNlpCorpus")
            print(f"   放到: {chn_path}")

    if os.path.exists(chn_path):
        raw_data = load_chnsenticorp(chn_path)
        print(f"   加载 {len(raw_data)} 条数据")
        # 取前10000条（太多会很慢）
        sample = raw_data[:10000] if len(raw_data) > 10000 else raw_data
        posts, comments = convert_to_project_format(sample, "酒店评论")
        all_posts.extend(posts)
        all_comments.extend(comments)
        print(f"   转换: {len(posts)} 条帖子, {len(comments)} 条评论")

    # ---- 2. 下载外卖评价 ----
    print("\n[2/3] 下载外卖评价数据集...")
    waimai_path = os.path.join(config.RAW_DATA_DIR, 'waimai_10k.csv')
    if os.path.exists(waimai_path):
        print("   文件已存在，跳过下载")
    else:
        success = try_download(WAIMAI_URLS, waimai_path)
        if not success:
            print("   ⚠️  自动下载失败，请手动下载")

    if os.path.exists(waimai_path):
        raw_data = load_waimai(waimai_path)
        print(f"   加载 {len(raw_data)} 条数据")
        posts, comments = convert_to_project_format(raw_data, "外卖评价")
        all_posts.extend(posts)
        all_comments.extend(comments)
        print(f"   转换: {len(posts)} 条帖子, {len(comments)} 条评论")

    # ---- 3. 生成时间序列 ----
    print("\n[3/3] 生成时间序列...")
    time_series = generate_time_series_from_posts(all_posts)
    print(f"   生成 {len(time_series)} 条时间序列数据")

    # ---- 保存数据 ----
    if all_posts:
        # 合并已有模拟数据（如果存在）
        existing_posts_path = os.path.join(config.RAW_DATA_DIR, 'posts.json')
        if os.path.exists(existing_posts_path):
            with open(existing_posts_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                # 重命名ID避免冲突
                for p in existing:
                    p['post_id'] = f"sim_{p['post_id']}"
                all_posts.extend(existing)
                print(f"\n   合并已有模拟数据: {len(existing)} 条")

        # 保存
        with open(existing_posts_path, 'w', encoding='utf-8') as f:
            json.dump(all_posts, f, ensure_ascii=False, indent=2)

        comments_path = os.path.join(config.RAW_DATA_DIR, 'comments.json')
        with open(comments_path, 'w', encoding='utf-8') as f:
            json.dump(all_comments, f, ensure_ascii=False, indent=2)

        series_path = os.path.join(config.RAW_DATA_DIR, 'time_series.json')
        with open(series_path, 'w', encoding='utf-8') as f:
            json.dump(time_series, f, ensure_ascii=False, indent=2)

        # 统计
        positive = sum(1 for p in all_posts if p['sentiment'] == 1)
        negative = len(all_posts) - positive

        print("\n" + "=" * 60)
        print(f"  ✅ 数据准备完成！")
        print(f"  帖子总数: {len(all_posts)} 条")
        print(f"  评论总数: {len(all_comments)} 条")
        print(f"  时间序列: {len(time_series)} 条")
        print(f"  正面情感: {positive} ({positive/len(all_posts)*100:.1f}%)")
        print(f"  负面情感: {negative} ({negative/len(all_posts)*100:.1f}%)")
        print("=" * 60)
    else:
        print("\n⚠️  没有获取到数据，请检查网络连接或手动下载")


if __name__ == '__main__':
    main()
