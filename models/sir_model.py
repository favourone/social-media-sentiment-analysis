# -*- coding: utf-8 -*-
"""
SIR 传播预测模型
================
数学原理：
---------
SIR 模型将舆论传播类比为病毒传播，将人群分为三类：
  S(t) = Susceptible（易感者）：尚未接触该话题的用户
  I(t) = Infected（感染者）：正在讨论/传播该话题的用户
  R(t) = Recovered（恢复者）：已知悉但不再讨论的用户

微分方程组：
  dS/dt = -β × S(t) × I(t)           ← 易感者因接触而减少
  dI/dt = β × S(t) × I(t) - γ × I(t) ← 感染者先增后减
  dR/dt = γ × I(t)                     ← 恢复者持续增加

其中：
  β = 传播率（用户看到话题后参与讨论的概率）
  γ = 恢复率（用户失去兴趣停止讨论的概率）
  R₀ = β/γ = 基本再生数
  - R₀ > 1：话题会爆发传播
  - R₀ < 1：话题会自然消亡

数值求解：使用四阶 Runge-Kutta 方法（scipy.integrate.odeint）
"""

import os
import sys
import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    from scipy.integrate import odeint
    from scipy.optimize import minimize
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class SIRModel:
    """
    SIR 传播预测模型

    用法：
        sir = SIRModel()
        result = sir.fit(observed_data)
        forecast = sir.predict(days=14)
    """

    def __init__(self, total_users=None, beta=None, gamma=None):
        self.total_users = total_users or config.SIR_TOTAL_USERS
        self.beta = beta or config.SIR_BETA     # 传播率
        self.gamma = gamma or config.SIR_GAMMA  # 恢复率
        self.R0 = self.beta / self.gamma         # 基本再生数
        self.fitted = False
        self.initial_infected = None
        self.fit_nrmse = None

    def _sir_equations(self, y, t):
        """
        SIR 微分方程组

        参数：
            y = [S, I, R]  当前状态
            t = 时间点
        返回：
            [dS/dt, dI/dt, dR/dt]
        """
        S, I, R = y
        N = S + I + R  # 总人数（守恒）

        dSdt = -self.beta * S * I / N
        dIdt = self.beta * S * I / N - self.gamma * I
        dRdt = self.gamma * I

        return [dSdt, dIdt, dRdt]

    def simulate(self, I0, days=None):
        """
        模拟传播过程

        参数：
            I0: 初始感染者数（初始讨论人数）
            days: 模拟天数
        返回：
            dict: {time, S, I, R, peak_day, peak_value, R0}
        """
        if not HAS_SCIPY:
            raise RuntimeError('SciPy 未安装，无法运行 SIR 模拟')
        days = config.SIR_TIME_SPAN if days is None else int(days)
        if days < 1:
            raise ValueError('days 必须大于等于 1')
        I0 = float(I0)
        if not np.isfinite(I0) or I0 <= 0:
            raise ValueError('初始讨论量必须是正数')
        if I0 >= self.total_users:
            raise ValueError('初始讨论量必须小于总情景规模')

        # 初始条件
        S0 = self.total_users - I0
        R0_init = 0
        y0 = [S0, I0, R0_init]

        # 时间点
        t = np.linspace(0, days, days * 10 + 1)  # 每天10个采样点，包含终点

        # 求解微分方程（四阶 Runge-Kutta）
        solution = odeint(self._sir_equations, y0, t)
        S, I, R = solution.T

        # 找峰值
        peak_idx = np.argmax(I)
        peak_day = t[peak_idx]
        peak_value = I[peak_idx]

        return {
            'time': t.tolist(),
            'S': S.tolist(),
            'I': I.tolist(),
            'R': R.tolist(),
            'peak_day': float(peak_day),
            'peak_value': float(peak_value),
            'peak_ratio': float(peak_value / self.total_users),
            'R0': self.R0,
            'total_users': self.total_users,
        }

    def fit(self, observed_data):
        """
        拟合模型参数（用观测数据反推 β 和 γ）

        参数：
            observed_data: 观测到的每日讨论量列表 [day1, day2, ...]

        数学方法：最小化观测值与模型预测值的 MSE
          min_{β,γ} Σ (I_observed(t) - I_model(t))²
        """
        if not HAS_SCIPY:
            raise RuntimeError('SciPy 未安装，无法拟合 SIR 模型')
        observed = np.array(observed_data, dtype=float)
        if observed.ndim != 1 or len(observed) < 3:
            raise ValueError('至少需要 3 个一维观测点')
        if not np.all(np.isfinite(observed)) or np.any(observed < 0):
            raise ValueError('观测数据必须是有限的非负数')
        days = len(observed)
        I0 = max(observed[0], 1)
        self.initial_infected = float(I0)
        observed_scale = max(float(np.max(observed)), 1.0)
        observed_normalized = observed / observed_scale

        def loss(params):
            beta, gamma = params
            S0 = self.total_users - I0
            y0 = [S0, I0, 0]
            t = np.arange(days, dtype=float)

            try:
                solution = odeint(
                    lambda y, t: [-beta * y[0] * y[1] / (y[0]+y[1]+y[2]),
                                   beta * y[0] * y[1] / (y[0]+y[1]+y[2]) - gamma * y[1],
                                   gamma * y[1]],
                    y0, t
                )
                I_pred = solution[:, 1]
                predicted_scale = float(np.max(I_pred))
                if predicted_scale <= 0 or not np.all(np.isfinite(I_pred)):
                    return 1e10
                I_pred_normalized = I_pred / predicted_scale
                mse = np.mean((I_pred_normalized - observed_normalized) ** 2)
                return mse
            except Exception:
                return 1e10

        # 对归一化曲线形状做有界拟合。讨论量不是人群规模，因此这里只能解释为情景参数。
        candidates = []
        for start in ([self.beta, self.gamma], [0.15, 0.10], [0.50, 0.25]):
            result = minimize(
                loss,
                start,
                method='L-BFGS-B',
                bounds=[(0.01, 1.5), (0.01, 1.5)],
                options={'maxiter': 1000},
            )
            if result.success and np.isfinite(result.fun):
                candidates.append(result)
        if not candidates:
            raise RuntimeError('参数优化未收敛')
        result = min(candidates, key=lambda item: item.fun)

        self.beta, self.gamma = result.x
        self.R0 = self.beta / self.gamma
        self.fit_nrmse = float(np.sqrt(result.fun))
        self.fitted = True

        print(f"   ✅ SIR 模型拟合完成")
        print(f"      β(传播率) = {self.beta:.4f}")
        print(f"      γ(恢复率) = {self.gamma:.4f}")
        print(f"      R₀(基本再生数) = {self.R0:.2f}")
        print(f"      归一化RMSE = {self.fit_nrmse:.4f}")

        if self.R0 > 1:
            print(f"      📈 R₀ > 1，话题将爆发传播")
        else:
            print(f"      📉 R₀ < 1，话题将自然消退")

        return self.simulate(I0, days)

    def predict(self, days=None):
        """
        预测未来传播趋势

        参数：
            days: 预测天数
        返回：
            预测结果字典
        """
        days = config.SIR_TIME_SPAN if days is None else days
        I0 = self.initial_infected if self.fitted else max(int(self.total_users * 0.001), 100)
        return self.simulate(I0, days)

    def get_status(self, current_day, observed_data):
        """
        获取当前传播状态

        返回：
            phase: 当前阶段（爆发期/高峰期/消退期）
            days_to_peak: 距离峰值还有几天
            risk_level: 风险等级
        """
        result = self.simulate(observed_data[0], len(observed_data) + 14)
        peak_day = result['peak_day']

        if current_day < peak_day * 0.7:
            phase = "潜伏期"
            risk_level = "低"
        elif current_day < peak_day:
            phase = "爆发期"
            risk_level = "高"
        elif current_day < peak_day * 1.5:
            phase = "高峰期"
            risk_level = "中"
        else:
            phase = "消退期"
            risk_level = "低"

        days_to_peak = max(0, peak_day - current_day)

        return {
            'phase': phase,
            'days_to_peak': round(days_to_peak, 1),
            'risk_level': risk_level,
            'R0': self.R0,
            'peak_day': peak_day,
        }
