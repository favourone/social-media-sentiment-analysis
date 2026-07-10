# -*- coding: utf-8 -*-
"""
MongoDB 数据存储模块
===================
封装 MongoDB 的连接和 CRUD 操作
支持：帖子、评论、话题、预测结果的存取
"""

import json
import os
import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    from pymongo import MongoClient, ASCENDING, DESCENDING
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False


class MongoStorage:
    """MongoDB 数据存储客户端"""

    def __init__(self):
        self.client = None
        self.db = None
        self._connected = False

    def connect(self):
        """建立 MongoDB 连接"""
        if not HAS_MONGO:
            print("⚠️  pymongo 未安装，将使用本地JSON文件存储")
            return False
        try:
            self.client = MongoClient(
                config.MONGO_HOST,
                config.MONGO_PORT,
                serverSelectionTimeoutMS=3000
            )
            self.db = self.client[config.MONGO_DB]
            # 测试连接
            self.client.admin.command('ping')
            self._connected = True
            print(f"✅ MongoDB 连接成功：{config.MONGO_HOST}:{config.MONGO_PORT}")
            return True
        except Exception as e:
            print(f"⚠️  MongoDB 连接失败：{e}，将使用本地JSON文件存储")
            self._connected = False
            return False

    @property
    def posts(self):
        return self.db[config.MONGO_COLLECTION_POSTS] if self._connected else None

    @property
    def comments(self):
        return self.db[config.MONGO_COLLECTION_COMMENTS] if self._connected else None

    @property
    def topics(self):
        return self.db[config.MONGO_COLLECTION_TOPICS] if self._connected else None

    @property
    def predictions(self):
        return self.db[config.MONGO_COLLECTION_PREDICTIONS] if self._connected else None

    # ==================== 数据导入 ====================

    def import_json_file(self, collection_name, json_path):
        """从 JSON 文件批量导入数据到 MongoDB"""
        if not self._connected:
            print(f"⚠️  MongoDB 未连接，跳过导入：{json_path}")
            return 0

        collection = self.db[collection_name]
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, list):
            result = collection.insert_many(data)
            count = len(result.inserted_ids)
        else:
            result = collection.insert_one(data)
            count = 1

        print(f"   导入 {count} 条数据到 {collection_name}")
        return count

    def import_all_data(self):
        """导入所有原始数据到 MongoDB"""
        print("\n📦 导入数据到 MongoDB...")

        file_map = {
            config.MONGO_COLLECTION_POSTS: os.path.join(config.RAW_DATA_DIR, 'posts.json'),
            config.MONGO_COLLECTION_COMMENTS: os.path.join(config.RAW_DATA_DIR, 'comments.json'),
        }

        total = 0
        for collection_name, file_path in file_map.items():
            if os.path.exists(file_path):
                total += self.import_json_file(collection_name, file_path)
            else:
                print(f"   ⚠️  文件不存在：{file_path}")

        # 创建索引
        if self._connected:
            self.posts.create_index([("post_id", ASCENDING)], unique=True)
            self.posts.create_index([("topic", ASCENDING)])
            self.posts.create_index([("created_at", DESCENDING)])
            self.comments.create_index([("post_id", ASCENDING)])
            self.comments.create_index([("created_at", DESCENDING)])
            print(f"   ✅ 索引创建完成")

        print(f"   共导入 {total} 条数据")
        return total

    # ==================== 数据查询 ====================

    def get_posts_by_topic(self, topic, limit=100):
        """按话题查询帖子"""
        if self._connected:
            return list(self.posts.find({"topic": topic}).limit(limit))
        return self._load_from_json('posts.json', topic=topic)[:limit]

    def get_comments_by_post(self, post_id):
        """按帖子ID查询评论"""
        if self._connected:
            return list(self.comments.find({"post_id": post_id}))
        return self._load_from_json('comments.json', post_id=post_id)

    def get_all_posts(self, limit=1000):
        """获取所有帖子"""
        if self._connected:
            return list(self.posts.find().limit(limit))
        return self._load_from_json('posts.json')[:limit]

    def get_topic_stats(self):
        """获取各话题的统计数据"""
        if self._connected:
            pipeline = [
                {"$group": {
                    "_id": "$topic",
                    "count": {"$sum": 1},
                    "avg_likes": {"$avg": "$likes"},
                    "total_reposts": {"$sum": "$reposts"},
                    "positive": {"$sum": {"$cond": [{"$eq": ["$sentiment", 1]}, 1, 0]}},
                    "negative": {"$sum": {"$cond": [{"$eq": ["$sentiment", 0]}, 1, 0]}},
                }},
                {"$sort": {"count": -1}}
            ]
            return list(self.posts.aggregate(pipeline))

        # 本地JSON fallback
        posts = self._load_from_json('posts.json')
        stats = {}
        for p in posts:
            topic = p.get('topic', '未知')
            if topic not in stats:
                stats[topic] = {'_id': topic, 'topic': topic, 'count': 0, 'positive': 0, 'negative': 0,
                                'total_reposts': 0, 'likes_sum': 0}
            stats[topic]['count'] += 1
            stats[topic]['positive'] += 1 if p.get('sentiment') == 1 else 0
            stats[topic]['negative'] += 1 if p.get('sentiment') == 0 else 0
            stats[topic]['total_reposts'] += p.get('reposts', 0)
            stats[topic]['likes_sum'] += p.get('likes', 0)

        result = []
        for topic, s in stats.items():
            s['avg_likes'] = s['likes_sum'] / max(s['count'], 1)
            result.append(s)
        return sorted(result, key=lambda x: x['count'], reverse=True)

    def save_prediction(self, prediction):
        """保存预测结果"""
        if self._connected:
            self.predictions.insert_one(prediction)
        else:
            path = os.path.join(config.PROCESSED_DATA_DIR, 'predictions.json')
            data = []
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            data.append(prediction)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # ==================== 工具方法 ====================

    def _load_from_json(self, filename, **filters):
        """从本地 JSON 文件加载数据（MongoDB 不可用时的 fallback）"""
        path = os.path.join(config.RAW_DATA_DIR, filename)
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not filters:
            return data

        result = []
        for item in data:
            match = True
            for key, value in filters.items():
                if item.get(key) != value:
                    match = False
                    break
            if match:
                result.append(item)
        return result

    def close(self):
        """关闭连接"""
        if self.client:
            self.client.close()
            self._connected = False


# 全局单例
db = MongoStorage()


def get_db():
    """获取数据库实例"""
    if not db._connected:
        db.connect()
    return db
