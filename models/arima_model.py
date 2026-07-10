# -*- coding: utf-8 -*-
"""
ARIMA 时间序列预测模型
======================
数学原理：
---------
ARIMA(p, d, q) 模型由三部分组成：

1. AR(p) - 自回归部分：
   X_t = c + φ₁X_{t-1} + φ₂X_{t-2} + ... + φ_pX_{t-p} + ε_t
   含义：当前值 = 过去 p 个值的加权和 + 常数 + 噪声

2. I(d) - 差分部分（使序列平稳）：
   ΔX_t = X_t - X_{t-1}  （一阶差分）
   Δ²X_t = ΔX_t - ΔX_{t-1}  （二阶差分）
   含义：去掉趋势，使均值和方差不随时间变化

3. MA(q) - 移动平均部分：
   X_t = μ + ε_t + θ₁ε_{t-1} + θ₂ε_{t-2} + ... + θ_qε_{t-q}
   含义：当前值 = 当前噪声 + 过去 q 个噪声的加权和

完整模型（差分后）：
   Δ^d X_t = c + ΣφᵢΔ^d X_{t-i} + Σθⱼε_{t-j} + ε_t

模型选择：AIC（赤池信息准则）
   AIC = 2k - 2ln(L)
   k = 参数个数，L = 似然函数值
   AIC 越小越好
"""

import os
import sys
import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config

try:
    from statsmodels.tsa.arima.model import ARIMA as ARIMA_Model
    from statsmodels.tsa.stattools import adfuller
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


class ARIMAPredictor:
    """
    ARIMA 时间序列预测器

    用法：
        arima = ARIMAPredictor()
        result = arima.fit(historical_data)
        forecast = arima.predict(steps=7)
    """

    def __init__(self, order=None):
        self.order = order or config.ARIMA_ORDER  # (p, d, q)
        self.model = None
        self.fitted = None
        self.data = None

    def check_stationarity(self, data):
        """
        ADF 平稳性检验

        数学：检验序列是否有单位根
        H₀: 序列有单位根（非平稳）
        H₁: 序列无单位根（平稳）
        p < 0.05 → 拒绝 H₀ → 序列平稳
        """
        if not HAS_STATSMODELS:
            return {'is_stationary': True, 'p_value': 0.0}

        result = adfuller(data)
        p_value = result[1]
        is_stationary = p_value < 0.05

        return {
            'is_stationary': is_stationary,
            'p_value': p_value,
            'adf_statistic': result[0],
            'critical_values': result[4],
        }

    def auto_determine_order(self, data, max_p=5, max_d=2, max_q=5):
        """
        自动确定 ARIMA 参数 (p, d, q)

        方法：遍历参数组合，选择 AIC 最小的
        """
        if not HAS_STATSMODELS:
            return self.order

        best_aic = float('inf')
        best_order = (1, 1, 1)

        for d in range(max_d + 1):
            for p in range(max_p + 1):
                for q in range(max_q + 1):
                    try:
                        model = ARIMA_Model(data, order=(p, d, q))
                        fitted = model.fit()
                        if fitted.aic < best_aic:
                            best_aic = fitted.aic
                            best_order = (p, d, q)
                    except Exception:
                        continue

        self.order = best_order
        print(f"   最优参数：ARIMA{best_order}，AIC={best_aic:.2f}")
        return best_order

    def fit(self, data, auto_order=False):
        """
        训练 ARIMA 模型

        参数：
            data: 时间序列数据（list 或 numpy array）
            auto_order: 是否自动确定参数
        """
        if not HAS_STATSMODELS:
            print("⚠️  statsmodels 未安装")
            return None

        self.data = np.array(data, dtype=float)

        # 平稳性检验
        stationarity = self.check_stationarity(self.data)
        print(f"   ADF 检验 p值：{stationarity['p_value']:.4f}")
        print(f"   序列{'平稳' if stationarity['is_stationary'] else '非平稳'}")

        # 自动确定参数
        if auto_order:
            self.auto_determine_order(self.data)

        # 拟合模型
        self.model = ARIMA_Model(self.data, order=self.order)
        self.fitted = self.model.fit()

        print(f"   ✅ ARIMA{self.order} 模型拟合完成")
        print(f"      AIC = {self.fitted.aic:.2f}")
        print(f"      BIC = {self.fitted.bic:.2f}")

        return self.fitted

    def predict(self, steps=None):
        """
        预测未来值

        参数：
            steps: 预测步数（天数）
        返回：
            dict: {forecast, lower_bound, upper_bound, dates}
        """
        steps = steps or config.ARIMA_FORECAST_DAYS

        if self.fitted is None:
            print("⚠️  模型未训练")
            return None

        # 预测
        forecast_result = self.fitted.get_forecast(steps=steps)
        forecast = forecast_result.predicted_mean
        conf_int = forecast_result.conf_int(alpha=0.05)  # 95% 置信区间

        # 确保预测值非负
        forecast = np.maximum(forecast, 0)
        # 兼容不同版本的 statsmodels（DataFrame 或 ndarray）
        if hasattr(conf_int, 'iloc'):
            lower = np.maximum(conf_int.iloc[:, 0].values, 0)
            upper = conf_int.iloc[:, 1].values
        else:
            conf_int = np.array(conf_int)
            lower = np.maximum(conf_int[:, 0], 0)
            upper = conf_int[:, 1]

        result = {
            'forecast': forecast.tolist(),
            'lower_bound': lower.tolist(),
            'upper_bound': upper.tolist(),
            'steps': steps,
            'order': self.order,
        }

        # 判断趋势
        if len(forecast) >= 2:
            trend = "上升" if forecast[-1] > forecast[0] else "下降"
            change_rate = (forecast[-1] - forecast[0]) / max(forecast[0], 1) * 100
            result['trend'] = trend
            result['change_rate'] = round(change_rate, 2)

        print(f"   📊 未来{steps}天预测：")
        print(f"      趋势：{result.get('trend', '未知')}")
        print(f"      变化率：{result.get('change_rate', 0):.1f}%")

        return result

    def get_diagnostics(self):
        """
        模型诊断

        检验残差是否为白噪声（模型是否充分提取了信息）
        """
        if self.fitted is None:
            return {}

        residuals = self.fitted.resid
        return {
            'mean': float(np.mean(residuals)),
            'std': float(np.std(residuals)),
            'skewness': float(np.mean(((residuals - np.mean(residuals)) / np.std(residuals)) ** 3)),
            'kurtosis': float(np.mean(((residuals - np.mean(residuals)) / np.std(residuals)) ** 4) - 3),
        }
