# -*- coding: utf-8 -*-
"""
微博数据采集模块
================
功能：
1. 爬取微博热搜榜
2. 爬取指定话题的微博帖子
3. 爬取帖子评论

注意：实际使用时需要配置 Cookie 或使用微博开放平台 API
本模块同时提供模拟数据生成作为 fallback
"""

import os
import sys
import json
import time
import random
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class WeiboCrawler:
    """微博爬虫"""

    def __init__(self):
        self.session = requests.Session() if HAS_REQUESTS else None
        self.headers = config.CRAWLER_HEADERS.copy()
        self.data_dir = config.RAW_DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)

    def get_hot_search(self):
        """
        获取微博热搜榜
        数据来源：微博热搜页面
        """
        url = "https://weibo.com/ajax/side/hotSearch"
        try:
            resp = self.session.get(url, headers=self.headers, timeout=10)
            data = resp.json()
            hot_list = []
            for item in data.get('data', {}).get('realtime', []):
                hot_list.append({
                    'rank': item.get('rank', 0),
                    'keyword': item.get('word', ''),
                    'hot_value': item.get('num', 0),
                    'category': item.get('category', ''),
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
            print(f"   获取到 {len(hot_list)} 条热搜")
            return hot_list
        except Exception as e:
            print(f"   ⚠️  热搜获取失败：{e}")
            return self._mock_hot_search()

    def search_topic(self, keyword, pages=5):
        """
        搜索指定话题的微博
        参数：
            keyword: 搜索关键词
            pages: 爬取页数
        """
        posts = []
        for page in range(1, pages + 1):
            url = f"https://weibo.com/ajax/search/wb?q={keyword}&page={page}"
            try:
                resp = self.session.get(url, headers=self.headers, timeout=10)
                data = resp.json()
                for card in data.get('data', {}).get('cards', []):
                    mblog = card.get('mblog', {})
                    if mblog:
                        posts.append({
                            'post_id': mblog.get('id', ''),
                            'user_name': mblog.get('user', {}).get('screen_name', ''),
                            'text': BeautifulSoup(mblog.get('text', ''), 'lxml').get_text(),
                            'reposts': mblog.get('reposts_count', 0),
                            'likes': mblog.get('attitudes_count', 0),
                            'comment_count': mblog.get('comments_count', 0),
                            'created_at': mblog.get('created_at', ''),
                            'source': '微博',
                            'topic': keyword,
                        })
                print(f"   第{page}页：获取 {len(posts)} 条")
                time.sleep(config.CRAWLER_DELAY)
            except Exception as e:
                print(f"   ⚠️  第{page}页获取失败：{e}")
                break

        # 保存
        save_path = os.path.join(self.data_dir, f'weibo_{keyword}.json')
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)

        return posts

    def _mock_hot_search(self):
        """模拟热搜数据（API不可用时的fallback）"""
        mock_data = [
            {"rank": 1, "keyword": "AI大模型突破", "hot_value": 987654},
            {"rank": 2, "keyword": "高考改革新方案", "hot_value": 876543},
            {"rank": 3, "keyword": "华为新品发布", "hot_value": 765432},
            {"rank": 4, "keyword": "房价最新政策", "hot_value": 654321},
            {"rank": 5, "keyword": "世界杯预选赛", "hot_value": 543210},
        ]
        print(f"   使用模拟热搜数据（{len(mock_data)} 条）")
        return mock_data


class ZhihuCrawler:
    """知乎爬虫"""

    def __init__(self):
        self.session = requests.Session() if HAS_REQUESTS else None
        self.headers = config.CRAWLER_HEADERS.copy()

    def get_hot_list(self):
        """获取知乎热榜"""
        url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total"
        try:
            resp = self.session.get(url, headers=self.headers, timeout=10)
            data = resp.json()
            hot_list = []
            for item in data.get('data', []):
                target = item.get('target', {})
                hot_list.append({
                    'title': target.get('title', ''),
                    'excerpt': target.get('excerpt', ''),
                    'heat': item.get('detail_text', ''),
                    'url': f"https://www.zhihu.com/question/{target.get('id', '')}",
                })
            return hot_list
        except Exception as e:
            print(f"   ⚠️  知乎热榜获取失败：{e}")
            return []


if __name__ == '__main__':
    print("🕷️  微博爬虫测试")
    crawler = WeiboCrawler()
    hot = crawler.get_hot_search()
    print(json.dumps(hot, ensure_ascii=False, indent=2))
