"""Modelo predictivo de representaciones, estilo JEPA, en NumPy y con gradientes a mano.

Para un vínculo (u, r, v):
    contexto  s_u = ½·x_u + ½·promedio(x_n, n vecino de u, n ≠ v)
    c   = W · s_u                       codificador de contexto (entrena)
    p   = B · relu(A · [c ; E_r] + b1) + b2   predictor
    t_v = W̄ · x_v                       codificador objetivo: EMA de W, sin gradiente
    pérdida = 1 − cos(p, t_v)                         predicción en el espacio latente
            + InfoNCE(p, t_v contra todas las t)     que el destino real gane a los demás
            + varianza y covarianza de W·x (VICReg)   que las representaciones no colapsen

Como en I-JEPA, se predice la representación del destino y no sus rasgos crudos, y el objetivo
sale de un codificador EMA sin gradiente. En grafos chicos eso solo no alcanza para distinguir
entidades del mismo tipo (todas las «dominio» terminan parecidas), por eso se suman el término
contrastivo y la regularización de VICReg. Los grafos de un caso son chicos (cientos a miles de
vínculos): se entrena con el lote completo en segundos, en CPU.
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Adam:
    lr: float
    b1: float = 0.9
    b2: float = 0.999
    eps: float = 1e-8
    t: int = 0
    m: dict = field(default_factory=dict)
    v: dict = field(default_factory=dict)

    def step(self, params: dict[str, np.ndarray], grads: dict[str, np.ndarray]) -> None:
        self.t += 1
        for name, g in grads.items():
            m = self.m.setdefault(name, np.zeros_like(g))
            v = self.v.setdefault(name, np.zeros_like(g))
            m[:] = self.b1 * m + (1 - self.b1) * g
            v[:] = self.b2 * v + (1 - self.b2) * g * g
            m_hat = m / (1 - self.b1 ** self.t)
            v_hat = v / (1 - self.b2 ** self.t)
            params[name] -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


class Jepa:
    def __init__(self, in_dim: int, n_relations: int, *, dim: int = 32, hidden: int = 64,
                 rel_dim: int = 8, ema: float = 0.98, var_weight: float = 1.0, cov_weight: float = 0.04,
                 nce_weight: float = 1.0, temperature: float = 0.1,
                 weight_decay: float = 1e-4, lr: float = 0.01, seed: int = 7):
        rng = np.random.default_rng(seed)
        self.dim, self.rel_dim = dim, rel_dim
        self.ema, self.var_weight, self.weight_decay = ema, var_weight, weight_decay
        self.cov_weight, self.nce_weight, self.temperature = cov_weight, nce_weight, temperature
        self.p = {
            "W": rng.normal(0, 1 / np.sqrt(in_dim), (dim, in_dim)),
            "E": rng.normal(0, 0.5, (max(1, n_relations), rel_dim)),
            "A": rng.normal(0, np.sqrt(2 / (dim + rel_dim)), (hidden, dim + rel_dim)),
            "b1": np.zeros(hidden),
            "B": rng.normal(0, np.sqrt(1 / hidden), (dim, hidden)),
            "b2": np.zeros(dim),
        }
        self.W_target = self.p["W"].copy()
        self.opt = Adam(lr=lr)
        self.loss_history: list[float] = []

    # -- inferencia --

    def targets(self, X: np.ndarray) -> np.ndarray:
        return X @ self.W_target.T

    def predict(self, S: np.ndarray, rel: np.ndarray) -> np.ndarray:
        c = S @ self.p["W"].T
        a = np.hstack([c, self.p["E"][rel]])
        h = np.maximum(0.0, a @ self.p["A"].T + self.p["b1"])
        return h @ self.p["B"].T + self.p["b2"]

    # -- entrenamiento --

    def step(self, S: np.ndarray, rel: np.ndarray, X: np.ndarray, target_idx: np.ndarray,
             weights: np.ndarray) -> float:
        """Un paso con el lote completo. `target_idx[i]` es la fila de X del destino del par i."""
        P_ = self.p
        c = S @ P_["W"].T
        a = np.hstack([c, P_["E"][rel]])
        pre = a @ P_["A"].T + P_["b1"]
        h = np.maximum(0.0, pre)
        p = h @ P_["B"].T + P_["b2"]

        p_norm = np.linalg.norm(p, axis=1, keepdims=True) + 1e-8
        T_all = self.targets(X)
        T_hat = T_all / (np.linalg.norm(T_all, axis=1, keepdims=True) + 1e-8)
        t_hat = T_hat[target_idx]
        cos = np.sum(p * t_hat, axis=1, keepdims=True) / p_norm
        w = (weights / weights.sum())[:, None]
        loss_pred = float(np.sum(w * (1 - cos)))
        # d(1 − cos)/dp = −(t̂/|p| − cos·p/|p|²)
        dp = -w * (t_hat / p_norm - cos * p / (p_norm ** 2))

        # Contrastivo: el destino real tiene que ganarle a las demás entidades (softmax sobre todas).
        loss_nce = 0.0
        if self.nce_weight:
            cos_all = (p / p_norm) @ T_hat.T
            logits = cos_all / self.temperature
            logits -= logits.max(1, keepdims=True)
            soft = np.exp(logits)
            soft /= soft.sum(1, keepdims=True)
            rows = np.arange(len(p))
            loss_nce = float(self.nce_weight * np.sum(w[:, 0] * -np.log(soft[rows, target_idx] + 1e-12)))
            g = soft.copy()
            g[rows, target_idx] -= 1.0
            g *= self.nce_weight * w / self.temperature
            # Σ_j g_ij · d cos(p_i, t_j)/dp_i
            dp += g @ T_hat / p_norm - np.sum(g * cos_all, axis=1, keepdims=True) * p / (p_norm ** 2)

        grads = {"B": dp.T @ h, "b2": dp.sum(0)}
        dh = dp @ P_["B"]
        dpre = dh * (pre > 0)
        grads["A"] = dpre.T @ a
        grads["b1"] = dpre.sum(0)
        da = dpre @ P_["A"]
        dc = da[:, : self.dim]
        dE = np.zeros_like(P_["E"])
        np.add.at(dE, rel, da[:, self.dim:])
        grads["E"] = dE

        grads["W"] = dc.T @ S + self.weight_decay * P_["W"]

        # VICReg sobre las representaciones de todas las entidades: cada dimensión con desvío ≥ 1
        # (varianza) y dimensiones no redundantes (covarianza). Es lo que evita el colapso.
        loss_reg = 0.0
        n = len(X)
        if n > 2 and (self.var_weight or self.cov_weight):
            Z = X @ P_["W"].T
            Zc = Z - Z.mean(0)
            std = np.sqrt(Zc.var(0) + 1e-4)
            low = std < 1.0
            loss_reg += float(self.var_weight * np.mean(np.maximum(0.0, 1 - std)))
            dZ = (-self.var_weight / self.dim) * low * Zc / (n * std)
            cov = Zc.T @ Zc / (n - 1)
            np.fill_diagonal(cov, 0.0)
            loss_reg += float(self.cov_weight * np.sum(cov ** 2) / self.dim)
            dZ += self.cov_weight * (4.0 / (self.dim * (n - 1))) * Zc @ cov
            grads["W"] += dZ.T @ X

        self.opt.step(P_, grads)
        self.W_target = self.ema * self.W_target + (1 - self.ema) * P_["W"]
        loss = loss_pred + loss_nce + loss_reg
        self.loss_history.append(loss)
        return loss
