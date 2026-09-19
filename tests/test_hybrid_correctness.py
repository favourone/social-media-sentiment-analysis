"""Regression tests for calendar spacing and leakage-free hybrid validation."""

import unittest
from unittest.mock import patch

import numpy as np

from models.hybrid_predictor import HAS_TORCH, HybridPredictor
from services.tasks import _hybrid_analysis, run_analysis_job


class HybridCalendarTest(unittest.TestCase):
    def test_missing_calendar_days_are_zero_observations(self):
        captured = {}

        class FakePredictor:
            def fit(self, counts, sentiment=None, engagement=None):
                captured['counts'] = counts
                captured['sentiment'] = sentiment
                captured['engagement'] = engagement

            def predict(self):
                return {'forecast': [0]}

        posts = [
            {'published_at': f'2026-09-{day:02d}T10:00:00+08:00',
             'sentiment': 0, 'engagement': {'likes': 3}}
            for day in (*range(1, 6), *range(7, 12))
        ]
        with patch('models.hybrid_predictor.HAS_TORCH', True), patch(
            'models.hybrid_predictor.HybridPredictor', FakePredictor
        ):
            result = _hybrid_analysis(posts)

        self.assertTrue(result['available'])
        self.assertEqual(result['observed_points'], 11)
        self.assertEqual(result['active_days'], 10)
        self.assertEqual(result['dates'][5], '2026-09-06')
        self.assertEqual(captured['counts'], [1] * 5 + [0] + [1] * 5)
        self.assertEqual(captured['sentiment'], [0] * 5 + [0.5] + [0] * 5)
        self.assertEqual(captured['engagement'], [3] * 5 + [0] + [3] * 5)

    def test_long_span_with_only_two_active_days_is_insufficient(self):
        posts = [
            {'published_at': '2026-09-01T10:00:00+08:00'},
            {'fetched_at': '2026-09-30T10:00:00+08:00'},
        ]
        result = _hybrid_analysis(posts)
        self.assertFalse(result['available'])
        self.assertEqual(result['observed_points'], 30)
        self.assertEqual(result['active_days'], 2)
        self.assertIn('10', result['reason'])


class AnalysisCancellationTest(unittest.TestCase):
    class FakeStore:
        def __init__(self, status):
            self.status = status
            self.updates = []

        def get_analysis_job(self, job_id):
            return {'id': job_id, 'status': self.status,
                    'analysis_type': 'summary', 'params': {}}

        def update_analysis_job(self, job_id, *, expected_statuses=None, **fields):
            self.updates.append((expected_statuses, fields.copy()))
            if expected_statuses is not None and self.status not in expected_statuses:
                return None
            self.status = fields.get('status', self.status)
            return self.get_analysis_job(job_id)

        def list_posts(self, **kwargs):
            return [{'published_at': '2026-09-01T10:00:00+08:00'}]

    def test_cancelled_pending_job_cannot_restart(self):
        store = self.FakeStore('cancelled')
        with patch('services.tasks.get_product_store', return_value=store), patch(
            'services.tasks._summary_analysis'
        ) as summary:
            run_analysis_job('job-1')
        summary.assert_not_called()
        self.assertEqual(store.status, 'cancelled')
        self.assertEqual(store.updates[0][0], {'pending'})

    def test_cancellation_before_completion_cannot_be_overwritten(self):
        store = self.FakeStore('pending')

        def cancel_during_refresh(_store, _posts):
            store.status = 'cancelled'

        with patch('services.tasks.get_product_store', return_value=store), patch(
            'services.tasks._summary_analysis', return_value={'total': 1}
        ), patch('services.tasks._refresh_alerts', side_effect=cancel_during_refresh):
            run_analysis_job('job-2')
        self.assertEqual(store.status, 'cancelled')
        self.assertEqual(store.updates[-1][0], {'running'})
        self.assertEqual(store.updates[-1][1]['status'], 'succeeded')


@unittest.skipUnless(HAS_TORCH, 'PyTorch 未安装')
class HybridValidationTest(unittest.TestCase):
    def test_validation_baselines_and_scaling_use_training_prefix_only(self):
        predictor = HybridPredictor(epochs=1, forecast_days=7)
        calls = []
        scales = []

        def sir_baseline(counts, steps):
            calls.append(('sir', list(counts), steps))
            return {
                'available': True, 'history': np.asarray(counts, dtype=float),
                'future': np.ones(steps), 'R0': 1.0, 'peak_day': 1.0,
            }

        def arima_baseline(counts, steps):
            calls.append(('arima', list(counts), steps))
            return {
                'available': True, 'history': np.asarray(counts, dtype=float),
                'future': np.ones(steps), 'order': [1, 0, 0], 'converged': True,
            }

        original_features = predictor._feature_tensors

        def capture_features(observed, sentiment, engagement, baseline, scale_prefix):
            output = original_features(
                observed, sentiment, engagement, baseline, scale_prefix
            )
            scales.append((scale_prefix, output[3]))
            return output

        counts = [1] * 25 + [1000] * 5
        with patch.object(predictor, '_sir_baseline', side_effect=sir_baseline), patch.object(
            predictor, '_arima_baseline', side_effect=arima_baseline
        ), patch.object(predictor, '_feature_tensors', side_effect=capture_features):
            predictor.fit(counts)

        self.assertEqual(predictor.validation_points, 5)
        self.assertEqual([(kind, len(values), steps) for kind, values, steps in calls], [
            ('sir', 25, 5), ('arima', 25, 5),
            ('sir', 30, 7), ('arima', 30, 7),
        ])
        self.assertEqual(scales, [(25, 1.0), (30, 1000.0)])
        result = predictor.predict()
        self.assertIsNone(result['confidence_level'])
        self.assertEqual(result['interval_type'], 'uncalibrated_holdout_error_band')
        self.assertEqual(len(result['lower_bound']), 7)

    def test_short_series_does_not_claim_validation_interval(self):
        predictor = HybridPredictor(epochs=1)
        with patch.object(predictor, '_sir_baseline', return_value={
            'available': False, 'history': np.ones(10), 'future': np.ones(7),
            'R0': None, 'peak_day': None, 'reason': 'fallback',
        }), patch.object(predictor, '_arima_baseline', return_value={
            'available': False, 'history': np.ones(10), 'future': np.ones(7),
            'order': None, 'converged': False, 'reason': 'fallback',
        }):
            predictor.fit([1] * 10)
            result = predictor.predict()
        self.assertEqual(result['validation_points'], 0)
        self.assertIsNone(result['confidence_level'])
        self.assertIsNone(result['lower_bound'])
        self.assertIsNone(result['upper_bound'])


if __name__ == '__main__':
    unittest.main()
