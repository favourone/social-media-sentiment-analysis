# -*- coding: utf-8 -*-
"""
高质量模拟数据生成脚本
======================
生成接近真实社交媒体的模拟数据
- 10万条帖子 + 20万条评论
- 更真实的文本模板（来自真实微博句式）
- 符合SIR传播模型的时间分布
- 多平台来源（微博/知乎/豆瓣）

运行方式：python scripts/generate_realistic_data.py
"""

import os
import sys
import json
import random
from datetime import datetime, timedelta
from collections import defaultdict

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ==================== 真实文本模板库 ====================

# 基于真实微博句式的文本模板
TEMPLATES = {
    "AI人工智能": {
        "positive": [
            "ChatGPT又更新了，这次真的惊艳到我了，AI发展太快了！",
            "用AI画了一幅画，朋友们都说好看，科技改变生活啊",
            "大模型降价了，终于可以用得起AI了，支持国产大模型！",
            "自动驾驶测试通过了，未来出行方式要被颠覆了",
            "AI辅助诊断准确率超过90%，科技真的能救命",
            "今天用AI写了个代码，效率提升十倍，程序员的福音",
            "AI翻译越来越好了，出国旅游再也不用担心语言问题",
            "智能客服终于不像以前那么智障了，体验好了很多",
            "AI教育真的能因材施教，孩子学习兴趣提高了不少",
            "大模型能理解上下文了，对话体验越来越自然",
        ],
        "negative": [
            "AI换脸诈骗太猖狂了，必须加强监管！",
            "ChatGPT胡说八道还一本正经，AI幻觉问题什么时候能解决？",
            "AI要取代程序员？我觉得还早得很，别制造焦虑了",
            "AI生成的内容版权到底归谁？法律完全跟不上技术发展",
            "大模型训练耗电量惊人，AI发展不能以环境为代价",
            "AI歧视问题越来越严重，算法偏见必须被重视",
            "深度伪造视频泛滥，真假难辨，社会信任危机来了",
            "AI绘画算不算艺术？我觉得这不是真正的创作",
            "智能推荐算法让人越来越封闭，信息茧房太可怕了",
            "AI考试作弊问题严重，教育体系需要重新设计",
        ],
    },
    "教育热点": {
        "positive": [
            "高考改革新方案出来了，我觉得这次改革方向是对的",
            "职业教育终于被重视了，不是每个人都需要上大学",
            "在线教育质量越来越好，打破了地域教育资源不平等",
            "双减政策效果明显，孩子终于有时间做自己喜欢的事了",
            "考研虽然难，但备考过程本身就是一种成长",
            "留学回国的人越来越多，说明国内发展机会更好了",
            "教师待遇提高了，好老师才能教出好学生",
            "教育公平在逐步改善，农村学校的条件越来越好了",
            "素质教育不是空话，看到孩子们全面发展的样子真好",
            "终身学习理念越来越深入人心，学习型社会在形成",
        ],
        "negative": [
            "高考太卷了，孩子们压力太大，教育内卷何时休？",
            "大学生就业越来越难，读了四年书出来找不到工作",
            "学区房价格离谱，教育资源分配严重不均",
            "考研人数年年创新高，学历贬值太严重了",
            "双减之后校外培训转入地下，家长更焦虑了",
            "留学花费几十万回来月薪五千，性价比太低了",
            "教师工资差距太大，乡村教师待遇亟待提高",
            "教育产业化让学校变成了赚钱机器",
            "鸡娃教育把孩子逼成什么样了，童年都毁了",
            "论文造假学术不端频发，学术环境需要整治",
        ],
    },
    "科技数码": {
        "positive": [
            "华为新手机拍照效果太惊艳了，国产手机越来越强",
            "小米SU7开起来真的不错，性价比超高",
            "5G网速真的快，下载一部电影几秒钟",
            "折叠屏手机终于成熟了，大屏体验太爽了",
            "芯片国产化取得重大突破，不再受制于人了",
            "卫星通信功能太实用了，野外也能打电话",
            "新能源车续航终于超过1000公里了，充电焦虑缓解",
            "智能家居联动起来太方便了，真正的生活助手",
            "国产芯片性能追上来了，价格还更便宜",
            "6G研发取得进展，未来网速会更快",
        ],
        "negative": [
            "手机价格越来越高，旗舰机动辄五六千",
            "新能源车自燃事故频发，安全性让人担忧",
            "折叠屏折痕太明显了，花了大价钱体验却不好",
            "芯片被卡脖子，国产替代还需要时间",
            "5G覆盖太慢了，很多地方还是4G信号",
            "电池续航焦虑依然存在，冬天掉电太快",
            "智能家居协议不统一，不同品牌不兼容",
            "直播带货翻车频发，科技产品虚假宣传太多",
            "数据隐私泄露严重，智能设备在偷听我们说话",
            "电子产品更新太快，造成严重的电子垃圾问题",
        ],
    },
    "社会民生": {
        "positive": [
            "医保改革后报销比例提高了，看病负担减轻了",
            "社区养老服务越来越好，老人生活更有保障",
            "消费券发放效果明显，带动了消费回暖",
            "快递新规实施后服务质量明显提升",
            "灵活就业政策完善了，自由职业者权益有保障",
            "食品安全监管加强了，吃得更放心了",
            "碳中和目标推进顺利，蓝天白云越来越多",
            "直播助农效果好，农产品销路打开了",
            "老旧小区改造后环境焕然一新",
            "公共交通越来越方便，出行成本降低了",
        ],
        "negative": [
            "房价虽然降了但还是买不起，年轻人太难了",
            "看病贵问题依然存在，一场大病回到解放前",
            "延迟退休让人焦虑，身体能不能撑到退休都是问题",
            "就业形势严峻，35岁危机不是说着玩的",
            "食品安全事件频发，我们还能吃什么？",
            "快递暴力分拣问题严重，贵重物品不敢寄",
            "直播带货虚假宣传太多，消费者权益谁来保障？",
            "养老院床位紧张，优质养老服务价格太高",
            "环境污染问题依然严峻，治理效果不明显",
            "网络诈骗手段层出不穷，老年人防不胜防",
        ],
    },
    "娱乐体育": {
        "positive": [
            "今年春节档电影质量都不错，国产电影越来越好了",
            "世界杯预选赛国足赢了！虽然对手不强但还是开心",
            "综艺节目创意越来越好了，终于不全是抄袭了",
            "全民健身热潮兴起，公园里跑步的人越来越多",
            "电竞入亚了，打游戏终于被认可为体育运动",
            "音乐节越来越多了，现场氛围太好了",
            "国潮文化崛起，年轻人越来越自信了",
            "体育产业发展迅速，运动场馆越来越多",
            "短视频博主内容质量越来越高，很多有创意的作品",
            "传统文化节目火了，年轻人开始关注传统文化",
        ],
        "negative": [
            "明星塌房太频繁了，追星真的需要理性",
            "综艺节目越来越无聊，全是剧本和人设",
            "电影票价太贵了，看一场电影要五六十",
            "体育赛事商业化过度，竞技精神被金钱腐蚀",
            "饭圈文化太疯狂了，未成年人追星需要引导",
            "短视频内容同质化严重，低俗内容泛滥",
            "音乐节票价虚高，体验却越来越差",
            "体育明星退役后生活困难，保障体系不完善",
            "网络暴力问题严重，键盘侠太可怕了",
            "版权保护不力，盗版内容依然猖獗",
        ],
    },
}

# 用户名库
USER_NAMES = [
    "阳光少年", "科技观察者", "热心网友", "吃瓜群众", "理性分析",
    "新闻评论员", "数据达人", "行业分析师", "普通市民", "在校学生",
    "职场新人", "退休老人", "创业者", "自由职业者", "媒体人",
    "程序猿", "产品经理", "设计师", "教师", "医生",
    "大学生", "研究生", "海归", "北漂", "沪漂",
]


def generate_post(topic, sentiment):
    """生成一条帖子"""
    templates = TEMPLATES[topic]
    text = random.choice(templates["positive" if sentiment == 1 else "negative"])

    # 随机添加一些变化
    suffixes = ["", " 大家怎么看？", " 有人和我一样吗？", " 转发！", " #热议#", ""]
    text += random.choice(suffixes)

    return text


def generate_comment(post_text, sentiment):
    """生成评论"""
    if sentiment == 1:
        comments = [
            "说得对！支持！",
            "确实如此，深有同感",
            "同意楼主，我也这么觉得",
            "终于有人说到点子上了",
            "转发支持，让更多人看到",
            "我也遇到过，确实不错",
            "说得太好了，点赞！",
            "同感，希望越来越好",
            "有道理，分析得很到位",
            "认同，期待更好的发展",
        ]
    else:
        comments = [
            "不敢苟同，我觉得不是这样的",
            "呵呵，想得太简单了",
            "这个问题没那么容易解决",
            "实际情况比你说的复杂多了",
            "不同意，你只看到了表面",
            "站着说话不腰疼",
            "你怕是没经历过吧",
            "太理想化了，现实很骨感",
            "这种观点太片面了",
            "不接受，事实并非如此",
        ]
    return random.choice(comments)


def generate_sir_curve(total_posts, outbreak_day, peak_ratio=0.4):
    """
    生成符合SIR传播模型的时间分布

    SIR模型：
      S(t) + I(t) + R(t) = N
      dS/dt = -β × S × I
      dI/dt = β × S × I - γ × I
      dR/dt = γ × I

    这里用解析近似生成符合S型曲线的帖子时间分布
    """
    days = 60
    daily_counts = []

    for day in range(days):
        if day < outbreak_day - 3:
            # 潜伏期：低热度
            count = max(1, int(total_posts * 0.001 * random.uniform(0.5, 1.5)))
        elif day < outbreak_day:
            # 预热期：缓慢上升
            preheat_start = outbreak_day - 3
            progress = (day - preheat_start) / 3
            count = max(1, int(total_posts * 0.01 * progress * random.uniform(0.8, 1.2)))
        elif day == outbreak_day:
            # 爆发日：峰值
            count = max(1, int(total_posts * peak_ratio * random.uniform(0.8, 1.0)))
        elif day < outbreak_day + 5:
            # 高峰期：高位震荡
            decay = 0.7 + 0.3 * random.random()
            count = max(1, int(total_posts * peak_ratio * decay))
        else:
            # 衰减期：指数衰减
            days_since_peak = day - outbreak_day
            decay_rate = 0.1
            count = max(1, int(total_posts * peak_ratio * 0.7 * (1 - decay_rate) ** (days_since_peak - 5)))

        daily_counts.append(count)

    # 归一化到总帖子数
    total = sum(daily_counts)
    scale = total_posts / max(total, 1)
    daily_counts = [max(1, int(c * scale)) for c in daily_counts]

    return daily_counts


def main():
    print("=" * 60)
    print("  高质量模拟数据生成")
    print("=" * 60)

    # 参数
    TARGET_POSTS = 100000  # 目标帖子数
    TARGET_COMMENTS = 200000  # 目标评论数

    topics = list(TEMPLATES.keys())
    base_date = datetime.now() - timedelta(days=60)

    all_posts = []
    all_comments = []
    post_id = 0

    # 为每个话题生成数据
    for topic_idx, topic in enumerate(topics):
        # 每个话题分配不同数量的帖子
        topic_posts = TARGET_POSTS // len(topics)

        # 随机选择爆发日（不同话题不同时间爆发）
        outbreak_day = random.randint(5 + topic_idx * 8, 15 + topic_idx * 8)

        # 生成符合SIR模型的时间分布
        daily_counts = generate_sir_curve(topic_posts, outbreak_day, peak_ratio=0.35)

        print(f"\n  [{topic_idx+1}/{len(topics)}] 生成话题「{topic}」数据...")
        print(f"    爆发日：第{outbreak_day}天，帖子分布：{daily_counts[:5]}...")

        for day, count in enumerate(daily_counts):
            date = base_date + timedelta(days=day)

            for _ in range(count):
                # 情感分布：正面60%，负面40%
                sentiment = random.choices([1, 0], weights=[0.6, 0.4])[0]

                # 生成帖子
                text = generate_post(topic, sentiment)
                post_time = date + timedelta(
                    hours=random.randint(6, 23),
                    minutes=random.randint(0, 59)
                )

                post = {
                    "post_id": f"post_{post_id:06d}",
                    "user_name": random.choice(USER_NAMES) + str(random.randint(1, 9999)),
                    "text": text,
                    "topic": topic,
                    "keywords": [],
                    "sentiment": sentiment,
                    "reposts": random.randint(0, 5000),
                    "likes": random.randint(0, 20000),
                    "comment_count": random.randint(0, 1000),
                    "created_at": post_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": random.choice(["微博", "知乎", "豆瓣"]),
                }
                all_posts.append(post)

                # 生成评论
                num_comments = random.randint(0, 3)
                for j in range(num_comments):
                    cmt_sentiment = sentiment if random.random() > 0.2 else 1 - sentiment
                    cmt_time = post_time + timedelta(hours=random.randint(0, 48))
                    comment = {
                        "comment_id": f"cmt_{post_id}_{j}",
                        "post_id": post["post_id"],
                        "user_name": random.choice(USER_NAMES) + str(random.randint(1, 9999)),
                        "text": generate_comment(text, cmt_sentiment),
                        "sentiment": cmt_sentiment,
                        "likes": random.randint(0, 500),
                        "created_at": cmt_time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    all_comments.append(comment)

                post_id += 1

        print(f"    已生成：{len(all_posts)} 条帖子, {len(all_comments)} 条评论")

    # 截断到目标数量
    all_posts = all_posts[:TARGET_POSTS]
    all_comments = all_comments[:TARGET_COMMENTS]

    # 生成时间序列
    print("\n  生成时间序列...")
    topic_date_count = defaultdict(lambda: defaultdict(int))
    for p in all_posts:
        date = p['created_at'][:10]
        topic_date_count[p['topic']][date] += 1

    time_series = []
    for topic, date_counts in topic_date_count.items():
        for day in range(60):
            date = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")
            time_series.append({
                "date": date,
                "value": date_counts.get(date, 0),
                "topic": topic,
            })

    # 保存数据
    os.makedirs(config.RAW_DATA_DIR, exist_ok=True)

    with open(os.path.join(config.RAW_DATA_DIR, 'posts.json'), 'w', encoding='utf-8') as f:
        json.dump(all_posts, f, ensure_ascii=False, indent=2)

    with open(os.path.join(config.RAW_DATA_DIR, 'comments.json'), 'w', encoding='utf-8') as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)

    with open(os.path.join(config.RAW_DATA_DIR, 'time_series.json'), 'w', encoding='utf-8') as f:
        json.dump(time_series, f, ensure_ascii=False, indent=2)

    # 统计
    positive = sum(1 for p in all_posts if p['sentiment'] == 1)
    negative = len(all_posts) - positive

    topic_stats = defaultdict(int)
    for p in all_posts:
        topic_stats[p['topic']] += 1

    print("\n" + "=" * 60)
    print(f"  ✅ 数据生成完成！")
    print(f"  帖子总数: {len(all_posts):,} 条")
    print(f"  评论总数: {len(all_comments):,} 条")
    print(f"  时间序列: {len(time_series)} 条")
    print(f"  正面情感: {positive:,} ({positive/len(all_posts)*100:.1f}%)")
    print(f"  负面情感: {negative:,} ({negative/len(all_posts)*100:.1f}%)")
    print(f"\n  各话题分布:")
    for topic, count in sorted(topic_stats.items(), key=lambda x: -x[1]):
        print(f"    {topic}: {count:,} 条")
    print("=" * 60)


if __name__ == '__main__':
    main()
