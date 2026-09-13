# -*- coding: utf-8 -*-
"""Hybrid trend forecasting: ARIMA + SIR baselines corrected by an LSTM.

设计动机（对应竞赛叙事“物理约束 + 数据驱动”）：

- ARIMA 擅长短期统计外推，但不理解传播动力学；
- SIR 用 β/γ 拟合出符合传播规律的单峰形状，R₀ 反映扩散强度，作为形状先验；
- LSTM 在两条基线之上学习残差，用数据驱动修正物理约束的偏差。

损失函数 = MSE(LSTM 输出, 实际值) + SIR_PRIOR_WEIGHT × MSE(LSTM 输出, SIR 基线)。

可信度边界：
- 训练/验证按时间切分（后 20% 窗口做验证），不做随机打乱，避免时间序列泄漏；
- 输出置信区间来自验证集残差标准差，样本少时区间偏窄，需要人工复核；
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

HYBRID_TREND_ALGORITHM_VERSION = 'hybrid_arima_sir_lstm_v3'
FEATURE_DIM = 5  # [讨论量, 情感均值, 互动量, SIR 基线, ARIMA 基线]


def _require_torch():
    if not HAS_TORCH:
        raise RuntimeError('PyTorch 未安装，混合预测组件不可用')


class HybridPredictor:
    """Residual LSTM on top of ARIMA/SIR baselines with an SIR shape prior.

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
    def _engagement_series(counts, engagement):
        if engagement is None:
            return np.zeros(len(counts))
        values = np.asarray(engagement, dtype=float)
        if len(values) != len(counts) or not np.all(np.isfinite(values)):
            return np.zeros(len(counts))
        values = np.maximum(values, 0.0)
        peak = float(np.max(values))
        if peak <= 0:
            return np.zeros(len(counts))
        return np.log1p(values) / np.log1p(peak)

    def fit(self, counts, sentiment=None, engagement=None):
        _require_torch()
        observed = np.asarray(counts, dtype=float)
        if observed.ndim != 1 or len(observed) < 10:
            raise ValueError('混合预测至少需要 10 个一维观测点')
        if not np.all(np.isfinite(observed)) or np.any(observed < 0):
            raise ValueError('混合预测观测数据必须是有限的非负数')
        if len(observed) < self.window + 3:
            raise ValueError('观测点数量不足以构建滑动窗口')

        steps = self.forecast_days
        self._baseline = {
            'sir': self._sir_baseline(observed, steps),
            'arima': self._arima_baseline(observed, steps),
        }
        sir = self._baseline['sir']
        arima = self._baseline['arima']
        self.R0 = sir['R0']

        y_max = max(float(np.max(observed)), 1.0)
        y_norm = observed / y_max
        sir_norm = np.maximum(sir['history'] / y_max, 0.0)
        arima_norm = np.maximum(arima['history'] / y_max, 0.0)
        sent_norm = self._sentiment_series(observed, sentiment)
        eng_norm = self._engagement_series(observed, engagement)
        self._y_max = y_max
        self._y_norm = y_norm
        self._sent_norm = sent_norm
        self._eng_norm = eng_norm

        sequences, targets, sir_targets, positions = [], [], [], []
        for position in range(self.window - 1, len(observed) - 1):
            window_rows = [
                [y_norm[s], sent_norm[s], eng_norm[s], sir_norm[s], arima_norm[s]]
                for s in range(position - self.window + 1, position + 1)
            ]
            sequences.append(window_rows)
            targets.append(y_norm[position + 1])
            sir_targets.append(sir_norm[position + 1])
            positions.append(position + 1)
        x = torch.tensor(np.asarray(sequences, dtype=float), dtype=torch.float32)
        y = torch.tensor(np.asarray(targets, dtype=float), dtype=torch.float32).view(-1, 1)
        sir_y = torch.tensor(
            np.asarray(sir_targets, dtype=float), dtype=torch.float32
        ).view(-1, 1)

        total = len(sequences)
        val_size = max(1, total // 5) if total >= 10 else 0
        train_end = total - val_size
        x_train, y_train, sir_train = x[:train_end], y[:train_end], sir_y[:train_end]
        x_val, y_val = x[train_end:], y[train_end:]

        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        self._model = nn.LSTM(
            input_size=FEATURE_DIM, hidden_size=self.hidden_size,
            num_layers=1, batch_first=True,
        )
        head = nn.Linear(self.hidden_size, 1)
        self._head = head
        optimizer = torch.optim.AdamW(
            list(self._model.parameters()) + list(head.parameters()), lr=self.lr
        )
        mse = nn.MSELoss()
        for _ in range(self.epochs):
            optimizer.zero_grad()
            output, _ = self._model(x_train)
            prediction = head(output[:, -1, :])
            data_loss = mse(prediction, y_train)
            sir_loss = mse(prediction, sir_train)
            loss = data_loss + self.sir_prior_weight * sir_loss
            loss.backward()
            optimizer.step()

        with torch.no_grad():
            if val_size:
                output, _ = self._model(x_val)
                val_pred = head(output[:, -1, :]).view(-1)
                residuals = (val_pred - y_val.view(-1)).numpy() * y_max
                self.validation_residual_std = float(np.std(residuals)) if len(residuals) > 1 else float(np.abs(residuals).mean() if len(residuals) else 0.0)
                val_error = float(np.mean(np.abs(residuals)))
            else:
                output, _ = self._model(x_train)
                train_pred = head(output[:, -1, :]).view(-1)
                residuals = (train_pred - y_train.view(-1)).numpy() * y_max
                self.validation_residual_std = float(np.std(residuals))
                val_error = float(np.mean(np.abs(residuals)))
            arima_val_error = float(np.mean(np.abs(
                (arima_norm[np.asarray(positions[train_end:])] - y_val.view(-1).numpy())
                * y_max
            ))) if val_size else val_error
        total_error = val_error + arima_val_error
        if total_error > 0:
            self.blend_weight_lstm = min(0.8, max(0.2, arima_val_error / total_error))
        return self

    # ---------- 预测 ----------

    def predict(self, steps=None):
        _require_torch()
        if self._model is None or self._baseline is None:
            raise RuntimeError('混合预测器尚未拟合，请先调用 fit()')
        steps = self.forecast_days if steps is None else int(steps)
        if steps < 1:
            raise ValueError('steps 必须大于等于 1')
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
        sigma = float(self.validation_residual_std or 0.0)
        lower = [round(max(value - 1.96 * sigma, 0.0), 2) for value in forecast]
        upper = [round(value + 1.96 * sigma, 2) for value in forecast]
        return {
            'algorithm_version': HYBRID_TREND_ALGORITHM_VERSION,
            'steps': steps,
            'forecast': forecast,
            'lower_bound': lower,
            'upper_bound': upper,
            'confidence_level': '95%',
            'validation_residual_std': round(sigma, 3),
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
                '置信区间来自验证残差，样本不足时需人工研判。'
            ),
        }
