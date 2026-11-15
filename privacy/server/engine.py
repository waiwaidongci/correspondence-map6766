"""整数推断引擎：在所有已发布线性等式约束下，求每个底表格的可行整数范围。

约束为 0/1 系数线性等式（分组块计数、行/列组合计），变量为底表各格非负整数。

方法（精确，且在最坏情况下安全失败）：
1. 边际收紧初始上界：每个变量不超过其所在任一行组/列组的合计。
2. 线性和等式的区间传播反复收窄域。
3. 完全 DFS 做整数可行性判定，对目标变量二分搜索整数最小/最大值。
4. 全局节点预算 + 墙钟时间预算：跨所有可行性检查累计；耗尽即抛
   Inconclusive，上层按"安全失败"拒绝发布，绝不放行不确定结论。

NP 难最坏情况下无法保证快速精确求解，预算保证服务不被拖垮，
并以"拒绝"维持安全语义（宁可不可发布，不可泄露）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class Inconclusive(RuntimeError):
    """求解预算或时间耗尽，结论不确定。"""


@dataclass
class Equation:
    vars: list[int]
    rhs: int


@dataclass
class Solver:
    n_vars: int
    equations: list[Equation]
    node_budget: int = 40000
    time_budget: float = 2.0          # 每次范围求解的墙钟上限（秒）
    _nodes: int = field(default=0, init=False)
    _deadline: float = field(default=0.0, init=False)

    def propagate(self, lo: list[int], hi: list[int]):
        """线性和等式的区间传播（arc consistency）。"""
        lo, hi = lo[:], hi[:]
        changed = True
        while changed:
            if self._deadline and time.monotonic() > self._deadline:
                raise Inconclusive("传播阶段时间预算耗尽")
            changed = False
            for eq in self.equations:
                sum_lo = 0
                sum_hi = 0
                for v in eq.vars:
                    sum_lo += lo[v]
                    sum_hi += hi[v]
                if sum_lo > eq.rhs or sum_hi < eq.rhs:
                    return None
                for v in eq.vars:
                    # x_v = rhs - Σothers：用其余变量的界反推本变量
                    other_hi = sum_hi - hi[v]
                    other_lo = sum_lo - lo[v]
                    new_lo = eq.rhs - other_hi
                    new_hi = eq.rhs - other_lo
                    if new_lo > lo[v]:
                        lo[v] = new_lo
                        changed = True
                    if new_hi < hi[v]:
                        hi[v] = new_hi
                        changed = True
                    if lo[v] > hi[v]:
                        return None
        return lo, hi

    def feasible(self, lo: list[int], hi: list[int]) -> bool:
        pr = self.propagate(lo, hi)
        if pr is None:
            return False
        lo, hi = pr
        if all(lo[v] == hi[v] for v in range(self.n_vars)):
            return all(
                sum(lo[v] for v in eq.vars) == eq.rhs for eq in self.equations
            )
        self._nodes += 1
        if self._nodes > self.node_budget or time.monotonic() > self._deadline:
            raise Inconclusive("节点/时间预算耗尽")
        # 选域最窄的未定型变量（失败优先剪枝）
        k = min(
            (v for v in range(self.n_vars) if lo[v] < hi[v]),
            key=lambda v: hi[v] - lo[v],
        )
        for val in range(lo[k], hi[k] + 1):
            nlo, nhi = lo[:], hi[:]
            nlo[k] = nhi[k] = val
            if self.feasible(nlo, nhi):
                return True
        return False

    def start(self) -> None:
        """开始一次整体评估，设置统一截止时间与节点计数。"""
        self._deadline = time.monotonic() + self.time_budget
        self._nodes = 0

    def variable_range(self, idx: int, lo0: list[int], hi0: list[int]):
        if self._deadline == 0.0:
            self.start()
        pr = self.propagate(lo0, hi0)
        if pr is None:
            raise Inconclusive("初始域不可行")
        lo, hi = pr

        def bisect(pick_min: bool):
            l, r = lo[idx], hi[idx]
            best = None
            while l <= r:
                mid = (l + r) // 2
                tlo, thi = lo[:], hi[:]
                if pick_min:
                    thi[idx] = mid
                else:
                    tlo[idx] = mid
                ok = self.feasible(tlo, thi)
                if ok:
                    best = mid
                    if pick_min:
                        r = mid - 1
                    else:
                        l = mid + 1
                else:
                    if pick_min:
                        l = mid + 1
                    else:
                        r = mid - 1
            return best

        mn = bisect(True)
        mx = bisect(False)
        if mn is None or mx is None:
            raise Inconclusive(f"变量 {idx} 范围搜索失败")
        return mn, mx


def tight_initial_bounds(n_rows, n_cols, partitions, total):
    """用各组行列合计收紧每个底表格的初始上界。"""
    n = n_rows * n_cols
    hi = [total] * n
    lo = [0] * n
    for p in partitions:
        rg, cg = p["row_groups"], p["col_groups"]
        row_of = [0] * n_rows
        col_of = [0] * n_cols
        for a, g in enumerate(rg):
            for i in g:
                row_of[i] = a
        for b, g in enumerate(cg):
            for j in g:
                col_of[j] = b
        for i in range(n_rows):
            for j in range(n_cols):
                cap = min(p["row_marginals"][row_of[i]],
                          p["col_marginals"][col_of[j]])
                vid = i * n_cols + j
                if cap < hi[vid]:
                    hi[vid] = cap
    return lo, hi


def build_equations(n_rows, n_cols, partitions) -> list[Equation]:
    """把若干次分组发布转成底表变量上的 0/1 等式。"""
    eqs: list[Equation] = []
    for p in partitions:
        rg, cg = p["row_groups"], p["col_groups"]
        suppressed = p["suppressed"]
        for a, grp_r in enumerate(rg):
            for b, grp_c in enumerate(cg):
                if (a, b) in suppressed:
                    continue
                vs = [i * n_cols + j for i in grp_r for j in grp_c]
                eqs.append(Equation(vs, int(p["block_values"][a][b])))
        for a, grp_r in enumerate(rg):
            vs = [i * n_cols + j for i in grp_r for j in range(n_cols)]
            eqs.append(Equation(vs, int(p["row_marginals"][a])))
        for b, grp_c in enumerate(cg):
            vs = [i * n_cols + j for i in range(n_rows) for j in grp_c]
            eqs.append(Equation(vs, int(p["col_marginals"][b])))
    uniq: set[tuple[tuple[int, ...], int]] = set()
    out: list[Equation] = []
    for eq in eqs:
        key = (tuple(sorted(eq.vars)), eq.rhs)
        if key not in uniq:
            uniq.add(key)
            out.append(eq)
    return out


def protected_cells(base, threshold):
    """低于阈值的正计数格 = 必须保护格。"""
    return [
        (i, j)
        for i, row in enumerate(base)
        for j, x in enumerate(row)
        if 0 < x < threshold
    ]
