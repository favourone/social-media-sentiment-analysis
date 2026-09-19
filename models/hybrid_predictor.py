# -*- coding: utf-8 -*-
"""Hybrid trend forecasting: ARIMA and SIR baselines with an LSTM forecast.

设计动机（对应竞赛叙事“物理约束 + 数据驱动”）：

- ARIMA 擅长短期统计外推，但不理解传播动力学；
- SIR 用 β/γ 拟合出符合传播规律的单峰形状，R₀ 反映扩散强度，作为形状先验；
- LSTM 将两条基线作为输入特征预测下一日讨论量，再与 ARIMA 预测加权融合。

损失函数 = MSE(LSTM 输出, 实际值) + SIR_PRIOR_WEIGHT × MSE(LSTM 输出, SIR 基线)。

可信度边界：
- 验证段前的历史用于拟合基线、归一化和 LSTM；验证后再用全量历史重训未来模型；
- 仅在有留出验证段时给出未校准的残差参考带，不声称统计置信度；
- 混合预测仍是外推与情景模拟的组合，不构成因果预测。
"""

from __future__ import annotations

import numpy as np

try:
    import torch
    from torch import nn
    HAS_TORCH = True
except ImportError:  # pragma: no cover - depends on environment
    torch = None
    nn = None
    HAS_TORCH = False

import config

HYBRID_TREND_ALGORITHM_VERSION = 'hybrid_arima_sir_lstm_v4'
FEATURE_DIM = 5  # [讨论量, 情感均值, 互动量, SIR 基线, ARIMA 基线]


def _require_torch():
    if not HAS_TORCH:
        raise RuntimeError('PyTorch 未安装，混合预测组件不可用')


class HybridPredictor:
    """LSTM forecast using ARIMA/SIR features with an SIR shape prior.

    用法：
        predictor = HybridPredictor()
        predictor.fit(counts, sentiment=sentiment, engagement=engagement)
        forecast = predictor.predict(steps=7)
    """

    def __init__(
        self,
        hidden_size=None,
        epochs=None,
        lr=5e-3,
        window=5,
        sir_prior_weight=None,
        seed=None,
        forecast_days=None,
    ):
        self.hidden_size = int(
            config.HYBRID_HIDDEN_SIZE if hidden_size is None else hidden_size
        )
        self.epochs = int(config.HYBRID_EPOCHS if epochs is None else epochs)
        self.lr = float(lr)
        self.window = int(window)
        self.sir_prior_weight = float(
            config.HYBRID_SIR_PRIOR_WEIGHT
            if sir_prior_weight is None
            else sir_prior_weight
        )
        self.seed = int(config.RANDOM_SEED if seed is None else seed)
        self.forecast_days = int(
            config.HYBRID_FORECAST_DAYS if forecast_days is None else forecast_days
        )
        self.R0 = None
        self.blend_weight_lstm = 0.5
        self.validation_residual_std = None
        self.validation_mae = None
        self.validation_points = 0
        self.reference_band_half_width = None
        self._model = None
        self._baseline = None

    # ---------- 基线构建 ----------

    def _sir_baseline(self, counts, steps):
        """Fit SIR on the observed shape and extend it over the forecast range."""
        try:
            from models.sir_model import SIRModel

            model = SIRModel()
            model.fit(counts[:30])
            total_days = max(len(counts) - 1 + steps, len(counts))
            simulation = model.simulate(model.initial_infected, total_days)
            curve = np.asarray(simulation['I'], dtype=float)[::10]
            if len(curve) < len(counts) + steps:
                raise RuntimeError('SIR 模拟曲线长度不足')
            history, future = curve[:len(counts)], curve[len(counts):len(counts) + steps]
            denominator = float(np.sum(history ** 2))
            scale = (
                float(np.sum(np.asarray(counts, dtype=float) * history) / denominator)
                if denominator > 0
                else 0.0
            )
            return {
                'available': True,
                'history': history * scale,
                'future': future * scale,
                'R0': float(simulation['R0']),
                'peak_day': float(simulation['peak_day']),
            }
        except Exception as exc:  # noqa: BLE001 - 基线失败不阻塞混合预测
            flat = float(np.mean(counts))
            return {
                'available': False,
                'reason': f'SIR 基线拟合失败（{exc}），退化为常数均值先验',
                'history': np.full(len(counts), flat),
                'future': np.full(steps, flat),
                'R0': None,
                'peak_day': None,
            }

    def _arima_baseline(self, counts, steps):
        try:
            from models.arima_model import ARIMAPredictor

            predictor = ARIMAPredictor()
            predictor.fit(counts)
            forecast = predictor.predict(steps=steps)
            try:
                fitted_values = np.asarray(
                    predictor.fitted.predict(start=0, end=len(counts) - 1),
                    dtype=float,
                )
                if len(fitted_values) != len(counts) or not np.all(np.isfinite(fitted_values)):
                    raise RuntimeError('ARIMA 样本内拟合值长度异常')
            except Exception:  # noqa: BLE001 - 退化为一阶滞后基线
                fitted_values = np.concatenate(([counts[0]], counts[:-1]))
            return {
                'available': True,
                'history': fitted_values,
                'future': np.asarray(forecast['forecast'], dtype=float),
                'order': list(predictor.order),
                'converged': bool(predictor.converged),
            }
        except Exception as exc:  # noqa: BLE001
            last = float(counts[-1])
            return {
                'available': False,
                'reason': f'ARIMA 基线拟合失败（{exc}），退化为持续水平基线',
                'history': np.concatenate(([counts[0]], counts[:-1])),
                'future': np.full(steps, last),
                'order': None,
                'converged': False,
            }

    # ---------- 特征与训练 ----------

    @staticmethod
    def _sentiment_series(counts, sentiment):
        if sentiment is None:
            return np.full(len(counts), 0.5)
        values = np.asarray(sentiment, dtype=float)
        if len(values) != len(counts):
            return np.full(len(counts), 0.5)
        return np.clip(values, 0.0, 1.0)

    @staticmethod
    def _engagement_series(counts, engagement, scale_prefix=None):
        if engagement is None:
            return np.zeros(len(counts))
        values = np.asarray(engagement, dtype=float)
        if len(values) != len(counts) or not np.all(np.isfinite(values)):
            return np.zeros(len(counts))
        values = np.maximum(values, 0.0)
        peak = float(np.max(values[:scale_prefix])) if scale_prefix else float(np.max(values))
        if peak <= 0:
            return np.zeros(len(counts))
        return np.log1p(values) / np.log1p(peak)

    def _feature_tensors(self, observed, sentiment, engagement, baseline, scale_prefix):
        """Build one-step windows; all fitted scales use only ``scale_prefix``."""
        y_max = max(float(np.max(observed[:scale_prefix])), 1.0)
        y_norm = observed / y_max
        sir_curve = np.concatenate((baseline['sir']['history'], baseline['sir']['future']))
        arima_curve = np.concatenate((baseline['arima']['history'], baseline['arima']['future']))
        sir_norm = np.maximum(sir_curve / y_max, 0.0)
        arima_norm = np.maximum(arima_curve / y_max, 0.0)
        sent_norm = self._sentiment_series(observed, sentiment)
        eng_norm = self._engagement_series(observed, engagement, scale_prefix)

        sequences, targets, sir_targets = [], [], []
        for target in range(self.window, len(observed)):
            sequences.append([
                [y_norm[s], sent_norm[s], eng_norm[s], sir_norm[s], arima_norm[s]]
                for s in range(target - self.window, target)
            ])
            targets.append(y_norm[target])
            sir_targets.append(sir_norm[target])
        x = torch.tensor(np.asarray(sequences, dtype=float), dtype=torch.float32)
        y = torch.tensor(np.asarray(targets, dtype=float), dtype=torch.float32).view(-1, 1)
        sir_y = torch.tensor(np.asarray(sir_targets, dtype=float), dtype=torch.float32).view(-1, 1)
        return x, y, sir_y, y_max, y_norm, sent_norm, eng_norm

    def _train_lstm(self, x, y, sir_y):
        torch.manual_seed(self.seed)
        model = nn.LSTM(
            input_size=FEATURE_DIM, hidden_size=self.hidden_size,
            num_layers=1, batch_first=True,
        )
        head = nn.Linear(self.hidden_size, 1)
        optimizer = torch.optim.AdamW(
            list(model.parameters()) + list(head.parameters()), lr=self.lr
        )
        mse = nn.MSELoss()
        for _ in range(self.epochs):
            optimizer.zero_grad()
            output, _ = model(x)
            prediction = head(output[:, -1, :])
            data_loss = mse(prediction, y)
            sir_loss = mse(prediction, sir_y)
            loss = data_loss + self.sir_prior_weight * sir_loss
            loss.backward()
            optimizer.step()
        return model, head

    def fit(self, counts, sentiment=None, engagement=None):
        _require_torch()
        observed = np.asarray(counts, dtype=float)
        if observed.ndim != 1 or len(observed) < 10:
            raise ValueError('混合预测至少需要 10 个一维观测点')
        if not np.all(np.isfinite(observed)) or np.any(observed < 0):
            raise ValueError('混合预测观测数据必须是有限的非负数')
        if len(observed) < self.window + 3:
            raise ValueError('观测点数量不足以构建滑动窗口')

        total = len(observed) - self.window
        val_size = max(1, total // 5) if total >= 10 else 0
        train_end = len(observed) - val_size
        self.validation_points = val_size
        self.validation_residual_std = None
        self.validation_mae = None
        self.reference_band_half_width = None
        self.blend_weight_lstm = 0.5

        if val_size:
            # The validation baseline, normalization and LSTM see only the
            # prefix. Holdout targets are never used for fitting or scaling.
            prefix = observed[:train_end]
            validation_baseline = {
                'sir': self._sir_baseline(prefix, val_size),
                'arima': self._arima_baseline(prefix, val_size),
            }
            x, y, sir_y, y_max, _, _, _ = self._feature_tensors(
                observed, sentiment, engagement, validation_baseline, train_end
            )
            train_windows = train_end - self.window
            model, head = self._train_lstm(
                x[:train_windows], y[:train_windows], sir_y[:train_windows]
            )
            with torch.no_grad():
                output, _ = model(x[train_windows:])
                lstm_values = head(output[:, -1, :]).view(-1).numpy() * y_max
            actual = observed[train_end:]
            arima_values = np.asarray(validation_baseline['arima']['future'], dtype=float)
            lstm_error = float(np.mean(np.abs(lstm_values - actual)))
            arima_error = float(np.mean(np.abs(arima_values - actual)))
            total_error = lstm_error + arima_error
            if total_error > 0:
                self.blend_weight_lstm = min(0.8, max(0.2, arima_error / total_error))
            blended = np.clip(
                self.blend_weight_lstm * lstm_values
                + (1 - self.blend_weight_lstm) * arima_values,
                0.0, 1.5 * y_max,
            )
            residuals = blended - actual
            self.validation_residual_std = float(np.std(residuals))
            self.validation_mae = float(np.mean(np.abs(residuals)))
            self.reference_band_half_width = float(np.max(np.abs(residuals)))

        # Future forecasts may use every observation; train a fresh model and
        # baselines rather than reusing the holdout-only validation fit.
        self._baseline = {
            'sir': self._sir_baseline(observed, self.forecast_days),
            'arima': self._arima_baseline(observed, self.forecast_days),
        }
        self.R0 = self._baseline['sir']['R0']
        x, y, sir_y, self._y_max, self._y_norm, self._sent_norm, self._eng_norm = (
            self._feature_tensors(observed, sentiment, engagement, self._baseline, len(observed))
        )
        self._model, self._head = self._train_lstm(x, y, sir_y)
        return self

    # ---------- 预测 ----------

    def predict(self, steps=None):
        _require_torch()
        if self._model is None or self._baseline is None:
            raise RuntimeError('混合预测器尚未拟合，请先调用 fit()')
        steps = self.forecast_days if steps is None else int(steps)
        if steps < 1:
            raise ValueError('steps 必须大于等于 1')
        if steps > self.forecast_days:
            raise ValueError('steps 不得超过拟合时配置的 forecast_days')
        baseline = self._baseline
        sir = baseline['sir']
        arima = baseline['arima']

        y_max = self._y_max
        y_ext = list(self._y_norm)
        sent_ext = list(self._sent_norm)
        eng_ext = list(self._eng_norm)
        sir_full = np.maximum(
            np.concatenate([sir['history'], sir['future']]) / y_max, 0.0
        )
        arima_full = np.maximum(
            np.concatenate([arima['history'], arima['future']]) / y_max, 0.0
        )

        forecast_norm = []
        with torch.no_grad():
            for step in range(steps):
                target_index = len(self._y_norm) + step
                position = target_index - 1
                sequence = [
                    [y_ext[s], sent_ext[s], eng_ext[s], sir_full[s], arima_full[s]]
                    for s in range(position - self.window + 1, position + 1)
                ]
                x = torch.tensor(
                    np.asarray([sequence], dtype=float), dtype=torch.float32
                )
                output, _ = self._model(x)
                lstm_norm = float(self._head(output[:, -1, :]).view(-1)[0])
                blended = (
                    self.blend_weight_lstm * lstm_norm
                    + (1 - self.blend_weight_lstm) * float(arima_full[target_index])
                )
                blended = min(max(blended, 0.0), 1.5)
                forecast_norm.append(blended)
                y_ext.append(blended)
                sent_ext.append(sent_ext[-1])
                eng_ext.append(eng_ext[-1])

        forecast = [round(value * y_max, 2) for value in forecast_norm]
        if self.reference_band_half_width is None:
            lower = upper = None
        else:
            width = self.reference_band_half_width
            lower = [round(max(value - width, 0.0), 2) for value in forecast]
            upper = [round(value + width, 2) for value in forecast]
        return {
            'algorithm_version': HYBRID_TREND_ALGORITHM_VERSION,
            'steps': steps,
            'forecast': forecast,
            'lower_bound': lower,
            'upper_bound': upper,
            'confidence_level': None,
            'interval_type': 'uncalibrated_holdout_error_band' if lower is not None else None,
            'validation_points': self.validation_points,
            'validation_mae': (
                round(self.validation_mae, 3) if self.validation_mae is not None else None
            ),
            'validation_residual_std': (
                round(self.validation_residual_std, 3)
                if self.validation_residual_std is not None else None
            ),
            'blend_weight_lstm': round(self.blend_weight_lstm, 3),
            'sir_prior_weight': self.sir_prior_weight,
            'R0': self.R0,
            'baseline': {
                'arima': {
                    'available': arima['available'],
                    'order': arima['order'],
                    'converged': arima['converged'],
                    'forecast': [round(float(value), 2) for value in arima['future']],
                    **(
                        {'reason': arima['reason']}
                        if not arima['available']
                        else {}
                    ),
                },
                'sir': {
                    'available': sir['available'],
                    'R0': sir['R0'],
                    'peak_day': sir['peak_day'],
                    **(
                        {'reason': sir['reason']}
                        if not sir['available']
                        else {}
                    ),
                },
            },
            'claim_scope': (
                '混合预测是统计外推与传播情景的组合，不证明因果关系；'
                '留出段是逐日单步预测，使用前一天已知的真实观测；'
                '多步未来预测的上下界仅为留出段最大绝对误差形成的未校准参考带，'
                '不具有置信区间或覆盖率保证；缺少留出段时不提供上下界。'
            ),
        }
