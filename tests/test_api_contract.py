import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from scripts.run_pipeline import prepare_textcnn_data
from scripts.generate_data import generate_dataset
from storage import mongo_client
from storage.product_store import reset_store_cache
from web.app import app


class ApiContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.raw_dir = self.root / 'raw'
        self.processed_dir = self.root / 'processed'
        self.model_dir = self.root / 'models'
        self.raw_dir.mkdir()
        self.processed_dir.mkdir()
        self.model_dir.mkdir()

        self.original_config = {
            'RAW_DATA_DIR': config.RAW_DATA_DIR,
            'PROCESSED_DATA_DIR': config.PROCESSED_DATA_DIR,
            'MODEL_DIR': config.MODEL_DIR,
            'DATASET_METADATA_PATH': config.DATASET_METADATA_PATH,
            'MODEL_METRICS_PATH': config.MODEL_METRICS_PATH,
            'PRODUCT_DB_PATH': config.PRODUCT_DB_PATH,
            'MONGO_ENABLED': config.MONGO_ENABLED,
        }
        config.RAW_DATA_DIR = str(self.raw_dir)
        config.PROCESSED_DATA_DIR = str(self.processed_dir)
        config.MODEL_DIR = str(self.model_dir)
        config.DATASET_METADATA_PATH = str(self.raw_dir / 'metadata.json')
        config.MODEL_METRICS_PATH = str(self.processed_dir / 'model_metrics.json')
        config.PRODUCT_DB_PATH = str(self.root / 'product.db')
        config.MONGO_ENABLED = False
        self._reset_storage()
        self._write_fixtures()

        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        for name, value in self.original_config.items():
            setattr(config, name, value)
        self._reset_storage()
        self.temp_dir.cleanup()

    @staticmethod
    def _reset_storage():
        reset_store_cache()
        mongo_client.db.close()
        mongo_client.db.client = None
        mongo_client.db.db = None
        mongo_client.db._connected = False
        mongo_client.db._connect_attempted = False

    def _write_json(self, name, payload):
        (self.raw_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8'
        )

    def _write_fixtures(self):
        posts = [
            {'post_id': 'p1', 'text': '正面 示例一', 'topic': '话题A', 'sentiment': 1,
             'created_at': '2026-07-01 10:00:00', 'source': '测试源', 'likes': 3, 'reposts': 1},
            {'post_id': 'p2', 'text': '负面 示例二', 'topic': '话题A', 'sentiment': 0,
             'created_at': '2026-07-03 10:00:00', 'source': '测试源', 'likes': 2, 'reposts': 0},
            {'post_id': 'p3', 'text': '正面 示例三', 'topic': '话题B', 'sentiment': 1,
             'created_at': '2026-07-02 10:00:00', 'source': '测试源', 'likes': 4, 'reposts': 2},
        ]
        comments = [
            {'comment_id': 'c1', 'post_id': 'p1', 'created_at': '2026-07-01 11:00:00'},
            {'comment_id': 'c2', 'post_id': 'p1', 'created_at': '2026-07-02 11:00:00'},
            {'comment_id': 'c3', 'post_id': 'p2', 'created_at': '2026-07-03 11:00:00'},
            {'comment_id': 'c4', 'post_id': 'p3', 'created_at': '2026-07-02 11:00:00'},
        ]
        start = date(2026, 6, 1)
        series = []
        for index in range(14):
            series.append({
                'date': (start + timedelta(days=index)).isoformat(),
                'value': 10 + index * 2 + (index % 3),
                'topic': '话题A',
            })
        self._write_json('posts.json', posts)
        self._write_json('comments.json', comments)
        self._write_json('time_series.json', series)
        self._write_json('metadata.json', {
            'schema_version': 1,
            'dataset_type': 'synthetic',
            'evidence_status': 'verified',
            'generator': 'tests',
            'seed': 42,
            'limitations': ['测试夹具'],
        })

    def test_ten_critical_contract_scenarios(self):
        checks = []

        response = self.client.get('/api/overview')
        checks.append(response.status_code == 200)

        response = self.client.get('/api/overview', query_string={
            'start_date': '2030-01-01', 'end_date': '2030-01-02'
        })
        payload = response.get_json()
        checks.append(response.status_code == 200 and payload['total_posts'] == 0
                      and payload['total_comments'] == 0)

        checks.append(self.client.get('/api/overview', query_string={
            'start_date': 'not-a-date'
        }).status_code == 400)
        checks.append(self.client.get('/api/overview', query_string={
            'start_date': '2026-07-03', 'end_date': '2026-07-01'
        }).status_code == 400)
        checks.append(self.client.get('/api/sir_prediction', query_string={
            'days': 'abc'
        }).status_code == 400)
        checks.append(self.client.get('/api/sir_prediction', query_string={
            'days': -2
        }).status_code == 400)
        checks.append(self.client.get('/api/arima_prediction', query_string={
            'steps': 'abc'
        }).status_code == 400)
        checks.append(self.client.get('/api/arima_prediction', query_string={
            'steps': 0
        }).status_code == 400)
        response = self.client.get('/api/arima_prediction', query_string={
            'topic': '不存在'
        })
        checks.append(response.status_code == 422
                      and response.get_json()['error'] == 'insufficient_data')
        checks.append(self.client.get('/api/topics').status_code == 200)

        self.assertEqual(sum(checks), 10, checks)

    def test_overview_comments_follow_parent_topic_and_date(self):
        response = self.client.get('/api/overview', query_string={
            'topic': '话题A',
            'start_date': '2026-07-01',
            'end_date': '2026-07-02',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['total_posts'], 1)
        self.assertEqual(payload['total_comments'], 2)

    def test_valid_predictions_include_scope_and_finite_values(self):
        sir = self.client.get('/api/sir_prediction', query_string={
            'topic': '话题A', 'days': 7
        })
        self.assertEqual(sir.status_code, 200, sir.get_data(as_text=True))
        sir_payload = sir.get_json()
        self.assertEqual(sir_payload['interpretation'], 'scenario_simulation')
        self.assertTrue(math.isfinite(sir_payload['R0']))
        self.assertLessEqual(sir_payload['R0'], 150)

        arima = self.client.get('/api/arima_prediction', query_string={
            'topic': '话题A', 'steps': 3
        })
        self.assertEqual(arima.status_code, 200, arima.get_data(as_text=True))
        arima_payload = arima.get_json()
        self.assertEqual(len(arima_payload['forecast']), 3)
        self.assertEqual(arima_payload['interpretation'], 'statistical_forecast')

    def test_data_status_exposes_verified_provenance(self):
        response = self.client.get('/api/data_status')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['counts']['posts'], 3)
        self.assertEqual(payload['provenance']['dataset_type'], 'synthetic')
        self.assertEqual(payload['provenance']['evidence_status'], 'verified')

    def test_model_metrics_endpoint_is_read_only(self):
        response = self.client.get('/api/model_metrics')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(list(self.model_dir.iterdir()), [])

        report = {
            'evaluation_type': 'offline_holdout',
            'accuracy': 0.75,
            'loss': 0.5,
            'precision': 0.75,
            'recall': 0.75,
            'f1_score': 0.75,
            'train_loss': [0.7],
            'val_loss': [0.8],
            'train_acc': [0.7],
            'val_acc': [0.6],
            'epochs': 1,
        }
        Path(config.MODEL_METRICS_PATH).write_text(json.dumps(report), encoding='utf-8')
        response = self.client.get('/api/model_metrics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['evaluation_type'], 'offline_holdout')
        self.assertEqual(list(self.model_dir.iterdir()), [])


class TextCnnSplitTest(unittest.TestCase):
    def test_vocabulary_is_built_from_training_text_only(self):
        posts = [
            {
                'text': f'sharedtoken uniquevalue{index:03d}',
                'sentiment': index % 2,
            }
            for index in range(120)
        ]
        prepared = prepare_textcnn_data(posts, random_state=42)
        processor = prepared['processor']

        train_tokens = {
            token
            for text in prepared['train_texts']
            for token in processor.tokenize(text)
        }
        holdout_tokens = {
            token
            for text in prepared['val_texts'] + prepared['test_texts']
            for token in processor.tokenize(text)
        }
        holdout_only = holdout_tokens - train_tokens
        self.assertTrue(holdout_only)
        self.assertTrue(holdout_only.isdisjoint(processor.vocab))


class SyntheticGeneratorTest(unittest.TestCase):
    def test_seed_and_reference_time_reproduce_records_and_metadata(self):
        original = {
            'DATA_DIR': config.DATA_DIR,
            'RAW_DATA_DIR': config.RAW_DATA_DIR,
            'DATASET_METADATA_PATH': config.DATASET_METADATA_PATH,
        }
        reference_time = datetime(2026, 7, 1, 12, 0, 0)
        generated = []
        try:
            for _ in range(2):
                with tempfile.TemporaryDirectory() as temp_dir:
                    data_dir = Path(temp_dir) / 'data'
                    raw_dir = data_dir / 'raw'
                    config.DATA_DIR = str(data_dir)
                    config.RAW_DATA_DIR = str(raw_dir)
                    config.DATASET_METADATA_PATH = str(raw_dir / 'metadata.json')
                    posts, comments, series = generate_dataset(
                        num_posts=12,
                        num_comments=24,
                        seed=7,
                        reference_time=reference_time,
                    )
                    metadata = json.loads(
                        Path(config.DATASET_METADATA_PATH).read_text(encoding='utf-8')
                    )
                    generated.append((posts, comments, series, metadata))
        finally:
            for name, value in original.items():
                setattr(config, name, value)

        self.assertEqual(generated[0][:3], generated[1][:3])
        self.assertEqual(generated[0][3]['dataset_type'], 'synthetic')
        self.assertEqual(generated[0][3]['evidence_status'], 'verified')
        self.assertEqual(generated[0][3]['seed'], 7)
        self.assertTrue(all(post['data_kind'] == 'synthetic' for post in generated[0][0]))


if __name__ == '__main__':
    unittest.main()
