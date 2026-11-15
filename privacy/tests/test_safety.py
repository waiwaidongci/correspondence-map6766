"""求解器安全失败与大规格压力测试。

运行：python3 -m privacy.tests.test_safety
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from privacy.server.engine import (  # noqa: E402
    Inconclusive,
    Solver,
    build_equations,
    protected_cells,
    tight_initial_bounds,
)


def _part(base, nr, nc, rg, cg, thr):
    supp = set()
    for a, gr in enumerate(rg):
        for b, gc in enumerate(cg):
            if any(0 < base[x][y] < thr for x in gr for y in gc):
                supp.add((a, b))
    bv = [[None] * len(cg) for _ in rg]
    for a, gr in enumerate(rg):
        for b, gc in enumerate(cg):
            if (a, b) not in supp:
                bv[a][b] = sum(base[x][y] for x in gr for y in gc)
    rm = [sum(base[x][y] for x in g for y in range(nc)) for g in rg]
    cm = [sum(base[x][y] for x in range(nr) for y in g) for g in cg]
    return {"row_groups": rg, "col_groups": cg, "suppressed": supp,
            "block_values": bv, "row_marginals": rm, "col_marginals": cm}


def test_time_budget_safe_failure():
    # 极小时间预算，任何无法瞬时判定的求解都必须 Inconclusive，而不是挂死或乱给结果
    eqs = [type("E", (), {"vars": [0, 1], "rhs": 5})]
    s = Solver(2, eqs, time_budget=0.0001)
    s.start()
    # 把截止时间设到过去，强制下一次检查触发
    s._deadline = time.monotonic() - 1
    try:
        s.variable_range(0, [0, 0], [5, 5])
        raised = False
    except Inconclusive:
        raised = True
    assert raised, "超时必须安全失败"
    print("test_time_budget_safe_failure PASS")


def test_node_budget_safe_failure():
    eqs = [type("E", (), {"vars": [0, 1, 2, 3], "rhs": 40})]
    s = Solver(4, eqs, node_budget=5, time_budget=30)
    s.start()
    try:
        s.variable_range(0, [0] * 4, [40] * 4)
        raised = False
    except Inconclusive:
        raised = True
    assert raised, "节点预算耗尽必须安全失败"
    print("test_node_budget_safe_failure PASS")


def test_12x12_bounded_runtime():
    import random
    random.seed(7)
    nr = nc = 12
    base = [[(random.randint(1, 4) if random.random() < 0.12
              else random.randint(8, 60)) for _ in range(nc)] for _ in range(nr)]
    thr = 5
    prot = protected_cells(base, thr)
    n = nr * nc
    total = sum(sum(r) for r in base)

    # 细分组：应快速精确（大量单格等式固定），且保护格被唯一确定 -> 拒绝
    rg = [[i] for i in range(nr)]
    cg = [[j] for j in range(nc)]
    p = _part(base, nr, nc, rg, cg, thr)
    eqs = build_equations(nr, nc, [p])
    lo, hi = tight_initial_bounds(nr, nc, [p], total)
    s = Solver(n, eqs, time_budget=3.0)
    s.start()
    i, j = prot[0]
    t = time.time()
    mn, mx = s.variable_range(i * nc + j, lo, hi)
    dt = time.time() - t
    assert dt < 3.0, f"细分组不应超时，实耗 {dt:.2f}s"
    assert (mn, mx) == (base[i][j], base[i][j]), "细分组应唯一确定保护格"
    print(f"test_12x12 细分组：唯一确定={mn}，耗时 {dt:.3f}s PASS")

    # 中分组：在有界时间内要么给出精确范围，要么安全失败（两者都可接受，
    # 绝不允许挂死或错误放行）
    rg4 = [list(range(k, k + 3)) for k in range(0, 12, 3)]
    p4 = _part(base, nr, nc, rg4, rg4, thr)
    eqs4 = build_equations(nr, nc, [p4])
    lo4, hi4 = tight_initial_bounds(nr, nc, [p4], total)
    s4 = Solver(n, eqs4, time_budget=1.0)
    s4.start()
    t = time.time()
    outcome = None
    try:
        mn4, mx4 = s4.variable_range(i * nc + j, lo4, hi4)
        outcome = ("exact", mn4, mx4)
    except Inconclusive:
        outcome = ("inconclusive",)
    dt = time.time() - t
    assert dt < 2.0, f"必须在预算内返回，实耗 {dt:.2f}s"
    assert outcome[0] in ("exact", "inconclusive")
    print(f"test_12x12 中分组：{outcome[0]}，耗时 {dt:.3f}s（有界返回）PASS")


if __name__ == "__main__":
    test_time_budget_safe_failure()
    test_node_budget_safe_failure()
    test_12x12_bounded_runtime()
    print("全部安全/压力测试通过")
