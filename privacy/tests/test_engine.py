"""引擎正确性自测：小例完整枚举（按总量生成组合）校验可行范围。

运行：python3 -m privacy.tests.test_engine
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from privacy.server.engine import Solver, build_equations, protected_cells  # noqa: E402


def compositions(total, n):
    """生成总和为 total 的 n 个非负整数的全部组合。"""
    if n == 1:
        yield (total,)
        return
    for first in range(total + 1):
        for rest in compositions(total - first, n - 1):
            yield (first,) + rest


def brute_force_ranges(base, n_rows, n_cols, partitions, prot):
    eqs = build_equations(n_rows, n_cols, partitions)
    total = sum(sum(r) for r in base)
    n = n_rows * n_cols
    mins = {p: None for p in prot}
    maxs = {p: None for p in prot}
    for vals in compositions(total, n):
        if all(sum(vals[v] for v in eq.vars) == eq.rhs for eq in eqs):
            for (i, j) in prot:
                x = vals[i * n_cols + j]
                if mins[(i, j)] is None or x < mins[(i, j)]:
                    mins[(i, j)] = x
                if maxs[(i, j)] is None or x > maxs[(i, j)]:
                    maxs[(i, j)] = x
    return {p: (mins[p], maxs[p]) for p in prot}


def make_partition(base, row_groups, col_groups, suppressed):
    bv = [[None] * len(col_groups) for _ in row_groups]
    for a, gr in enumerate(row_groups):
        for b, gc in enumerate(col_groups):
            if (a, b) not in suppressed:
                bv[a][b] = sum(base[i][j] for i in gr for j in gc)
    rm = [sum(base[i][j] for i in g for j in range(len(base[0]))) for g in row_groups]
    cm = [sum(base[i][j] for i in range(len(base)) for j in g) for g in col_groups]
    return {"row_groups": row_groups, "col_groups": col_groups,
            "suppressed": set(suppressed), "block_values": bv,
            "row_marginals": rm, "col_marginals": cm}


def test_2x3_vs_bruteforce():
    base = [[1, 9, 0], [2, 0, 0]]  # 总量12，保护格 (0,0)=1,(1,0)=2
    n_rows, n_cols = 2, 3
    prot = protected_cells(base, 5)
    assert set(prot) == {(0, 0), (1, 0)}, prot
    row_groups = [[0], [1]]
    col_groups = [[0], [1, 2]]
    # 块(0,0)含保护格1、块(1,0)含保护格2 -> 强制隐去
    suppressed = {(0, 0), (1, 0)}
    parts = [make_partition(base, row_groups, col_groups, suppressed)]
    eqs = build_equations(n_rows, n_cols, parts)
    n = n_rows * n_cols
    solver = Solver(n_vars=n, equations=eqs)
    flat = [x for row in base for x in row]
    assert solver.feasible(flat, flat), "真实底表必须可行"
    expected = brute_force_ranges(base, n_rows, n_cols, parts, prot)
    for (i, j) in prot:
        mn, mx = solver.variable_range(i * n_cols + j, [0] * n, [12] * n)
        emn, emx = expected[(i, j)]
        assert (mn, mx) == (emn, emx), (
            f"格({i},{j}) 引擎=({mn},{mx}) 枚举=({emn},{emx})")
        print(f"  格({i},{j}) 真实={base[i][j]} 可行范围=[{mn},{mx}] "
              f"可能值数={mx-mn+1}")
    print("test_2x3_vs_bruteforce PASS")


def test_cumulative_two_publications():
    # 两次发布合并后，某保护格范围被收窄到唯一 -> 必须拒绝第二次
    base = [[1, 9], [9, 1]]  # 总量20
    n_rows = n_cols = 2
    prot = protected_cells(base, 5)
    assert set(prot) == {(0, 0), (1, 1)}, prot

    # 第一次：粗分组（行列各 1 组），整块隐去；边际只给总量 -> 范围宽
    rg1, cg1 = [[0, 1]], [[0, 1]]
    p1 = make_partition(base, rg1, cg1, {(0, 0)})
    eqs1 = build_equations(n_rows, n_cols, [p1])
    s1 = Solver(4, eqs1)
    mn1, mx1 = s1.variable_range(0, [0] * 4, [20] * 4)
    assert mx1 - mn1 + 1 >= 2, "首次粗分组应安全"
    print(f"  首次发布后 x00 范围=[{mn1},{mx1}] 安全")

    # 第二次：细分组，隐去(0,0)，其余公开 + 行列边际 -> x00 被唯一确定
    rg2, cg2 = [[0], [1]], [[0], [1]]
    p2 = make_partition(base, rg2, cg2, {(0, 0)})
    eqs2 = build_equations(n_rows, n_cols, [p1, p2])
    s2 = Solver(4, eqs2)
    mn2, mx2 = s2.variable_range(0, [0] * 4, [20] * 4)
    assert (mn2, mx2) == (1, 1), f"合并后应唯一，实得({mn2},{mx2})"
    print(f"  合并两次后 x00 范围=[{mn2},{mx2}] -> 第二次必须拒绝")
    print("test_cumulative_two_publications PASS")


def test_protected_cells():
    base = [[0, 1, 9], [4, 5, 100]]
    assert protected_cells(base, 5) == [(0, 1), (1, 0)]
    print("test_protected_cells PASS")


if __name__ == "__main__":
    test_protected_cells()
    test_2x3_vs_bruteforce()
    test_cumulative_two_publications()
    print("全部引擎自测通过")
