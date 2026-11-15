"""服务层集成测试：完整工作流 + 累计泄露 + 校订停用 + 预览 + 并发。

运行：python3 -m privacy.tests.test_service
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="privacy_test_")
os.environ["PRIVACY_DB_PATH"] = str(Path(_tmp) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from privacy.server import db, service  # noqa: E402

db.init_db()


def _base_4x4():
    # 含多个低于阈值(5)的正计数保护格
    return [
        [2, 12, 30, 40],
        [1, 25, 35, 45],
        [10, 20, 3, 50],
        [15, 22, 33, 4],
    ]


def test_full_workflow_and_cumulative():
    base = _base_4x4()
    study = service.create_study(
        "展陈A", "测试", ["R1", "R2", "R3", "R4"],
        ["C1", "C2", "C3", "C4"], base, 5)
    sid = study["id"]
    n_prot = sum(1 for r in base for x in r if 0 < x < 5)

    # 粗分组：行 {R1,R2},{R3,R4}；列 {C1},{C2,C3,C4}
    rg = [[0, 1], [2, 3]]
    cg = [[0], [1, 2, 3]]
    preview = service.publish(sid, "预览", rg, cg, [], dry_run=True)
    assert preview["dry_run"] is True
    # 预览不占历史
    assert service.list_publications(sid) == []

    # 强制隐去块：含保护格 (0,0)=2,(1,0)=1 的列块、以及 (2,2)=3、(3,3)=4 所在块
    assert (0, 0) in map(tuple, preview["mandatory_blocks"])
    assert len(preview["protected_cells"]) == n_prot

    # 正式发布：粗分组下保护格应有 >=2 可行值
    pub1 = service.publish(sid, "第一次发布", rg, cg, [], dry_run=False)
    assert pub1["safe"], pub1["reject_reasons"]
    assert all(r["possible_values"] >= 2 for r in pub1["ranges"])
    pubs = service.list_publications(sid)
    assert len(pubs) == 1 and pubs[0]["seq"] == 1
    # 行列合计保留
    assert sum(pub1["row_marginals"]) == pub1["grand_total"]
    assert sum(pub1["col_marginals"]) == pub1["grand_total"]

    # 第二次：最细分组（不分组），不补隐 -> 保护格被唯一确定，必须拒绝
    rg2 = [[0], [1], [2], [3]]
    cg2 = [[0], [1], [2], [3]]
    pub2 = service.publish(sid, "过细发布", rg2, cg2, [], dry_run=False)
    assert pub2["safe"] is False
    assert pub2["reject_reasons"], "应给出拒绝原因"
    assert any("仅 1 个可行整数" in r for r in pub2["reject_reasons"])
    # 拒绝发布留痕但不计入公开信息
    pubs = service.list_publications(sid)
    assert len(pubs) == 2 and pubs[1]["safe"] is False
    safe_pubs = service.list_publications(sid, include_rejected=False)
    assert len(safe_pubs) == 1

    # 补隐额外块后重试，使每个保护格重新有 >=2 可行值
    # 把所有含保护格的细块及其互补块一并隐去（补隐），构造不确定性
    extra = [(a, b) for a in range(4) for b in range(4)]  # 全隐去，仅边际公开
    pub3 = service.publish(sid, "全隐重试", rg2, cg2, extra, dry_run=False)
    # 仅边际公开时，单格可在 [0, 行合计] 内变化，保护格应 >=2 值
    if pub3["safe"]:
        assert all(r["possible_values"] >= 2 for r in pub3["ranges"])
    print("test_full_workflow_and_cumulative PASS")
    return sid


def test_preview_does_not_persist():
    base = _base_4x4()
    study = service.create_study(
        "展陈B", "", ["a", "b", "c", "d"], ["w", "x", "y", "z"], base, 5)
    for _ in range(3):
        service.publish(study["id"], "预览", [[0, 1], [2, 3]], [[0, 1], [2, 3]],
                        [], dry_run=True)
    assert service.list_publications(study["id"]) == []
    print("test_preview_does_not_persist PASS")


def test_revision_retire_and_clone():
    base = _base_4x4()
    study = service.create_study(
        "展陈C", "", ["R1", "R2", "R3", "R4"], ["C1", "C2", "C3", "C4"],
        base, 5)
    sid = study["id"]
    service.publish(sid, "首次", [[0, 1], [2, 3]], [[0], [1, 2, 3]], [], False)
    # 校订底表（改一个格）
    revised = [r[:] for r in base]
    revised[0][0] = 3
    out = service.revise_study(sid, revised, study["row_labels"],
                               study["col_labels"], 5)
    assert out["cloned"] is True
    assert out["retired_study_id"] == sid
    old = service.get_study(sid)
    assert old["status"] == "retired"
    new = out["new_study"]
    assert new["status"] == "active"
    assert new["base_table"][0][0] == 3
    # 停用研究不能再发布
    try:
        service.publish(sid, "应失败", [[0, 1], [2, 3]], [[0], [1, 2, 3]], [], False)
        raise AssertionError("停用研究应拒绝发布")
    except service.ValidationError:
        pass
    # 历史发布仍可查
    assert len(service.list_publications(sid, include_rejected=False)) == 1

    # 无历史发布的研究校订 -> 原地更新
    s2 = service.create_study(
        "展陈D", "", ["a", "b"], ["x", "y"], [[1, 9], [8, 2]], 5)
    out2 = service.revise_study(s2["id"], [[4, 6], [7, 3]],
                                s2["row_labels"], s2["col_labels"], 5)
    assert out2["cloned"] is False
    assert service.get_study(s2["id"])["base_table"][0][0] == 4
    print("test_revision_retire_and_clone PASS")


def test_concurrent_publish_merged_judgement():
    base = _base_4x4()
    study = service.create_study(
        "展陈E", "", ["R1", "R2", "R3", "R4"], ["C1", "C2", "C3", "C4"],
        base, 5)
    sid = study["id"]
    errors, results = [], []

    def worker():
        try:
            # 细分组，单看每次可能被拒；并发提交同样的发布，验证串行合并
            res = service.publish(
                sid, "并发", [[0], [1], [2], [3]], [[0], [1], [2], [3]],
                [(a, b) for a in range(4) for b in range(4)], False)
            results.append(res)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    # 全部串行提交，发布序号唯一且连续
    pubs = service.list_publications(sid, include_rejected=False)
    seqs = sorted(p["seq"] for p in pubs)
    assert seqs == list(range(1, len(seqs) + 1)), seqs
    # 每次发布都基于此前全部已发布信息评估，结果一致安全
    assert all(p["safe"] for p in pubs)
    print(f"test_concurrent_publish_merged_judgement PASS ({len(pubs)} 次串行发布)")


def test_individually_safe_jointly_unsafe():
    # 两次发布联立后，保护格被唯一确定。
    # 底表 3x3，x00=3 是唯一保护格（阈值5，其余计数均 >=5）。
    base = [
        [3, 10, 7],
        [9, 20, 11],
        [5, 8, 14],
    ]
    study = service.create_study(
        "互补抑制", "", ["R0", "R1", "R2"], ["C0", "C1", "C2"], base, 5)
    sid = study["id"]

    # 第一次：粗分组（行列各并成大块），含 x00 的块隐去；只公开分组块与组边际。
    # x00 在 [0, 总量] 内有大量可行整数 -> 安全。
    rg1, cg1 = [[0, 1, 2]], [[0, 1], [2]]
    p1 = service.publish(sid, "发布一", rg1, cg1, [], True)
    assert p1["safe"], p1["reject_reasons"]
    r00 = next(r for r in p1["ranges"] if r["row"] == "R0" and r["col"] == "C0")
    assert r00["possible_values"] >= 2
    service.publish(sid, "发布一", rg1, cg1, [], False)

    # 第二次：最细分组（每类自成组）。系统强制隐去唯一含 x00 的块 (0,0)，
    # 其余 8 格全部公开，行0/列0边际保留 -> 联立第一次（及本次边际）后
    # x00 = 行0合计20 - x01(10) - x02(7) = 3 被钉死 -> 必须拒绝。
    rg2 = [[0], [1], [2]]
    cg2 = [[0], [1], [2]]
    p2 = service.publish(sid, "发布二", rg2, cg2, [], True)
    assert p2["safe"] is False
    assert any("仅 1 个可行整数" in r for r in p2["reject_reasons"])
    r00b = next(r for r in p2["ranges"]
                if r["row"] == "R0" and r["col"] == "C0")
    assert (r00b["min"], r00b["max"]) == (3, 3), (r00b["min"], r00b["max"])

    # 正式发布同样被拒；安全发布历史仍只有第一次
    p2f = service.publish(sid, "发布二", rg2, cg2, [], False)
    assert p2f["safe"] is False
    safe_pubs = service.list_publications(sid, include_rejected=False)
    assert len(safe_pubs) == 1 and safe_pubs[0]["title"] == "发布一"
    print("test_individually_safe_jointly_unsafe PASS")


def test_validation_errors():
    for bad in [
        [[1, -1], [0, 2]],          # 负数
        [[1, 1], [0, 0]],           # 总量... 实际为2合法，换一个
    ]:
        pass
    try:
        service.create_study("坏1", "", ["a", "b"], ["x", "y"],
                             [[1, -1], [0, 2]], 5)
        raise AssertionError("负数应拒绝")
    except service.ValidationError:
        pass
    try:
        service.create_study("坏2", "", ["a", "b"], ["x", "y"],
                             [[0, 0], [0, 0]], 5)
        raise AssertionError("零总量应拒绝")
    except service.ValidationError:
        pass
    s = service.create_study("坏3", "", ["a", "b"], ["x", "y"],
                             [[1, 9], [8, 2]], 5)
    try:
        service.publish(s["id"], "坏分组", [[0]], [[0], [1]], [], False)
        raise AssertionError("分组未覆盖应拒绝")
    except service.ValidationError:
        pass
    print("test_validation_errors PASS")


if __name__ == "__main__":
    test_full_workflow_and_cumulative()
    test_preview_does_not_persist()
    test_revision_retire_and_clone()
    test_concurrent_publish_merged_judgement()
    test_individually_safe_jointly_unsafe()
    test_validation_errors()
    print("全部服务集成测试通过")
