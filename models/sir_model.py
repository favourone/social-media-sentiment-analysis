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
        days = days or config.SIR_TIME_SPAN

        # 初始条件
        S0 = self.total_users - I0
        R0_init = 0
        y0 = [S0, I0, R0_init]

        # 时间点
        t = np.linspace(0, days, days * 10)  # 每天10个采样点

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
            print("⚠️  scipy 未安装，使用默认参数")
            return self.simulate(observed_data[0] if observed_data else 100)

        observed = np.array(observed_data, dtype=float)
        days = len(observed)
        I0 = max(observed[0], 1)

        def loss(params):
            beta, gamma = params
            if beta <= 0 or gamma <= 0 or beta > 2 or gamma > 2:
                return 1e10

            S0 = self.total_users - I0
            y0 = [S0, I0, 0]
            t = np.linspace(0, days, days)

            try:
                solution = odeint(
                    lambda y, t: [-beta * y[0] * y[1] / (y[0]+y[1]+y[2]),
                                   beta * y[0] * y[1] / (y[0]+y[1]+y[2]) - gamma * y[1],
                                   gamma * y[1]],
                    y0, t
                )
                I_pred = solution[:, 1]
                # 归一化后比较
                if np.max(I_pred) > 0:
                    I_pred_normalized = I_pred / np.max(I_pred) * np.max(observed)
                else:
                    return 1e10
                mse = np.mean((I_pred_normalized - observed) ** 2)
                return mse
            except Exception:
                return 1e10

        # 优化
        result = minimize(loss, [self.beta, self.gamma],
                          method='Nelder-Mead',
                          options={'maxiter': 1000})

        self.beta, self.gamma = result.x
        self.R0 = self.beta / self.gamma
        self.fitted = True

        print(f"   ✅ SIR 模型拟合完成")
        print(f"      β(传播率) = {self.beta:.4f}")
        print(f"      γ(恢复率) = {self.gamma:.4f}")
        print(f"      R₀(基本再生数) = {self.R0:.2f}")

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
        days = days or config.SIR_TIME_SPAN
        if not self.fitted:
            I0 = max(int(self.total_users * 0.001), 100)
        else:
            I0 = max(int(self.total_users * 0.01), 100)
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
