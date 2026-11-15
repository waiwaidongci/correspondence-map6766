"""隐私发布服务：分组、隐去、安全判定与持久化。"""
from __future__ import annotations

from . import db
from .engine import (
    Inconclusive,
    Solver,
    build_equations,
    protected_cells,
    tight_initial_bounds,
)


class ValidationError(Exception):
    pass


# ---------- 输入校验 ----------
def validate_matrix(rows: int, cols: int, table, labels_r, labels_c, threshold: int):
    if not (2 <= rows <= 12 and 2 <= cols <= 12):
        raise ValidationError("行列维度须在 2–12 之间")
    if not isinstance(table, list) or len(table) != rows:
        raise ValidationError("底表行数不匹配")
    norm = []
    total = 0
    for r in table:
        if not isinstance(r, list) or len(r) != cols:
            raise ValidationError("底表列数不匹配")
        line = []
        for x in r:
            if isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 1_000_000:
                raise ValidationError("每格须为 0–1000000 的非负整数")
            line.append(x)
            total += x
        norm.append(line)
    if total == 0:
        raise ValidationError("底表总计数为零，拒绝")
    if total > 144_000_000:
        raise ValidationError("总计数超出上限")
    if not isinstance(threshold, int) or threshold < 1:
        raise ValidationError("阈值须为 ≥1 的整数")
    lr = list(labels_r or [f"R{i+1}" for i in range(rows)])
    lc = list(labels_c or [f"C{j+1}" for j in range(cols)])
    if len(lr) != rows or len(lc) != cols:
        raise ValidationError("类别标签数量不匹配")
    if len(set(lr)) != rows or len(set(lc)) != cols:
        raise ValidationError("行/列类别标签须各自唯一")
    return norm, lr, lc, total


def validate_partition(groups, size: int, axis: str) -> list[list[int]]:
    if not isinstance(groups, list) or not groups:
        raise ValidationError(f"{axis} 分组不能为空")
    seen: list[int] = []
    norm: list[list[int]] = []
    for g in groups:
        if not isinstance(g, list) or not g:
            raise ValidationError(f"{axis} 分组含空组")
        idxs = []
        for x in g:
            if not isinstance(x, int) or not (0 <= x < size):
                raise ValidationError(f"{axis} 分组下标越界")
            idxs.append(x)
        if len(set(idxs)) != len(idxs):
            raise ValidationError(f"{axis} 分组下标重复")
        seen.extend(idxs)
        norm.append(sorted(idxs))
    if sorted(seen) != list(range(size)):
        raise ValidationError(f"{axis} 分组必须完整覆盖且互不相交")
    return norm


# ---------- 构造一次发布的公开信息 ----------
def _block_sum(base, grp_r, grp_c):
    return sum(base[i][j] for i in grp_r for j in grp_c)


def assemble_release(base, row_groups, col_groups, threshold, extra_suppressed):
    n_rows, n_cols = len(base), len(base[0])
    prot = set(protected_cells(base, threshold))

    # 强制隐去：块内包含任一保护格
    mandatory: set[tuple[int, int]] = set()
    for a, grp_r in enumerate(row_groups):
        for b, grp_c in enumerate(col_groups):
            if any((i, j) in prot for i in grp_r for j in grp_c):
                mandatory.add((a, b))
    suppressed = mandatory | set(extra_suppressed)

    n_rg, n_cg = len(row_groups), len(col_groups)
    block_values: list[list[int | None]] = []
    for a, grp_r in enumerate(row_groups):
        line = []
        for b, grp_c in enumerate(col_groups):
            if (a, b) in suppressed:
                line.append(None)
            else:
                line.append(_block_sum(base, grp_r, grp_c))
        block_values.append(line)

    row_marginals = [sum(base[i][j] for i in grp for j in range(n_cols))
                     for grp in row_groups]
    col_marginals = [sum(base[i][j] for i in range(n_rows) for j in grp)
                     for grp in col_groups]
    grand = sum(sum(r) for r in base)
    return {
        "block_values": block_values,
        "row_marginals": row_marginals,
        "col_marginals": col_marginals,
        "grand_total": grand,
        "suppressed": sorted(suppressed),
        "mandatory": sorted(mandatory),
        "protected_cells": sorted(prot),
    }


# ---------- 合并既往发布，构造约束并求范围 ----------
def _partitions_for_conn(conn, study, candidate=None):
    partitions = []
    rows = conn.execute(
        "SELECT * FROM publications WHERE study_id=? AND safe=1 ORDER BY seq",
        (study["id"],),
    ).fetchall()
    for r in rows:
        d = db.pub_to_dict(r)
        partitions.append({
            "row_groups": d["row_groups"],
            "col_groups": d["col_groups"],
            "suppressed": set(map(tuple, d["suppressed_blocks"])),
            "block_values": d["released_table"],
            "row_marginals": d["row_marginals"],
            "col_marginals": d["col_marginals"],
        })
    if candidate is not None:
        partitions.append(candidate)
    return partitions


def evaluate_conn(conn, study: dict, candidate: dict | None) -> dict:
    """在给定连接（同一事务）上，合并既往+候选约束求逐保护格范围。"""
    base = study["base_table"]
    n_rows, n_cols = len(base), len(base[0])
    n = n_rows * n_cols
    prot = protected_cells(base, study["threshold"])

    partitions = _partitions_for_conn(conn, study, candidate)
    eqs = build_equations(n_rows, n_cols, partitions)
    total = sum(sum(r) for r in base)
    lo0, hi0 = tight_initial_bounds(n_rows, n_cols, partitions, total)
    solver = Solver(n_vars=n, equations=eqs, time_budget=3.0)
    solver.start()

    # 校验真实底表可行（健全性）
    if not solver.feasible([x for row in base for x in row],
                           [x for row in base for x in row]):
        raise Inconclusive("真实底表在公开约束下不可行（数据或约束错误）")

    ranges = []
    reject: list[str] = []
    for (i, j) in prot:
        vid = i * n_cols + j
        mn, mx = solver.variable_range(vid, lo0, hi0)
        width = mx - mn + 1
        if width < 2:
            reject.append(
                f"保护格 ({study['row_labels'][i]},{study['col_labels'][j]}) "
                f"仅 1 个可行整数 {mn}，无法发布"
            )
        ranges.append({
            "row": study["row_labels"][i],
            "col": study["col_labels"][j],
            "true_value": base[i][j],
            "min": mn, "max": mx, "possible_values": width,
        })
    return {"ranges": ranges, "reject_reasons": reject,
            "n_equations": len(eqs), "n_publications": len(partitions)}


# ---------- 研究 CRUD ----------
def create_study(name, description, row_labels, col_labels, base_table, threshold):
    base, lr, lc, total = validate_matrix(
        len(base_table), len(base_table[0]), base_table, row_labels, col_labels, threshold
    )
    with db.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM studies WHERE name=?", (name,)).fetchone()
        if exists:
            raise ValidationError("研究名称已存在")
        cur = conn.execute(
            "INSERT INTO studies(name,description,row_labels,col_labels,base_table,threshold)"
            " VALUES(?,?,?,?,?,?)",
            (name, description or "", db.dumps(lr), db.dumps(lc),
             db.dumps(base), threshold),
        )
        sid = cur.lastrowid
    return get_study(sid)


def get_study(sid: int) -> dict | None:
    with db.read_transaction() as conn:
        row = conn.execute("SELECT * FROM studies WHERE id=?", (sid,)).fetchone()
        return db.study_to_dict(row) if row else None


def list_studies() -> list[dict]:
    with db.read_transaction() as conn:
        rows = conn.execute("SELECT * FROM studies ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = db.study_to_dict(r)
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM publications WHERE study_id=? AND safe=1",
                (r["id"],)).fetchone()["c"]
            d["publication_count"] = cnt
            out.append(d)
        return out


def revise_study(sid: int, base_table, row_labels, col_labels, threshold):
    """底表校订：有历史发布则停用原研究，并克隆为新研究再发布。"""
    old = get_study(sid)
    if old is None:
        raise ValidationError("研究不存在")
    base, lr, lc, _ = validate_matrix(
        len(base_table), len(base_table[0]), base_table, row_labels, col_labels, threshold
    )
    with db.transaction() as conn:
        pub_count = conn.execute(
            "SELECT COUNT(*) c FROM publications WHERE study_id=? AND safe=1", (sid,)
        ).fetchone()["c"]
        if pub_count > 0:
            # 停用原研究
            conn.execute(
                "UPDATE studies SET status='retired', revised_at=datetime('now') WHERE id=?",
                (sid,))
            new_name = f"{old['name']}（校订 {_stamp()}）"
            base_name = new_name
            k = 2
            while conn.execute("SELECT 1 FROM studies WHERE name=?", (new_name,)).fetchone():
                new_name = f"{base_name} #{k}"
                k += 1
            cur = conn.execute(
                "INSERT INTO studies(name,description,row_labels,col_labels,base_table,"
                "threshold) VALUES(?,?,?,?,?,?)",
                (new_name, old["description"], db.dumps(lr), db.dumps(lc),
                 db.dumps(base), threshold),
            )
            new_id = cur.lastrowid
            new_study = db.study_to_dict(
                conn.execute("SELECT * FROM studies WHERE id=?", (new_id,)).fetchone())
            return {"retired_study_id": sid, "new_study": new_study, "cloned": True}
        # 无历史发布：原地校订
        conn.execute(
            "UPDATE studies SET base_table=?,row_labels=?,col_labels=?,threshold=?,"
            "revised_at=datetime('now') WHERE id=?",
            (db.dumps(base), db.dumps(lr), db.dumps(lc), threshold, sid),
        )
        return {"retired_study_id": None, "new_study": get_study(sid), "cloned": False}


def _stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d%H%M%S")


# ---------- 发布 ----------
def publish(sid: int, title: str, row_groups, col_groups, extra_suppressed,
            dry_run: bool) -> dict:
    # 先做无副作用的入参校验（只读，不占写锁）
    with db.read_transaction() as conn:
        srow = conn.execute("SELECT * FROM studies WHERE id=?", (sid,)).fetchone()
        if srow is None:
            raise ValidationError("研究不存在")
        study = db.study_to_dict(srow)
    if study["status"] != "active":
        raise ValidationError("研究已停用（底表曾校订），请在新研究上发布")
    n_rows, n_cols = len(study["base_table"]), len(study["base_table"][0])
    rg = validate_partition(row_groups, n_rows, "行")
    cg = validate_partition(col_groups, n_cols, "列")
    extra = set()
    for pair in extra_suppressed or []:
        a, b = pair
        if not (0 <= a < len(rg) and 0 <= b < len(cg)):
            raise ValidationError("补隐块坐标越界")
        extra.add((a, b))

    rel = assemble_release(study["base_table"], rg, cg, study["threshold"], extra)
    candidate = {
        "row_groups": rg,
        "col_groups": cg,
        "suppressed": set(map(tuple, rel["suppressed"])),
        "block_values": rel["block_values"],
        "row_marginals": rel["row_marginals"],
        "col_marginals": rel["col_marginals"],
    }

    if dry_run:
        # 预览：只读事务，不落库、不占发布历史
        with db.read_transaction() as conn:
            try:
                ev = evaluate_conn(conn, study, candidate)
            except Inconclusive as e:
                ev = {"ranges": [],
                      "reject_reasons": [f"推断不确定，按安全策略拒绝：{e}"],
                      "n_equations": -1, "n_publications": -1}
        return _result_payload(title, rg, cg, rel, ev, dry_run=True)

    # 正式发布：单事务内 读既往→合并评估→写入，杜绝并发 TOCTOU
    with db.transaction() as conn:
        locked = conn.execute(
            "SELECT * FROM studies WHERE id=?", (sid,)).fetchone()
        study = db.study_to_dict(locked)
        if study["status"] != "active":
            raise ValidationError("研究已停用，请在新研究上发布")
        try:
            ev = evaluate_conn(conn, study, candidate)
        except Inconclusive as e:
            ev = {"ranges": [],
                  "reject_reasons": [f"推断不确定，按安全策略拒绝：{e}"],
                  "n_equations": -1, "n_publications": -1}
        safe = not ev["reject_reasons"]
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 s FROM publications WHERE study_id=?",
            (sid,)).fetchone()["s"]
        cur = conn.execute(
            "INSERT INTO publications(study_id,seq,title,row_groups,col_groups,"
            "suppressed_blocks,protected_cells,released_table,row_marginals,"
            "col_marginals,grand_total,ranges_json,safe,reject_reasons) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, seq, title, db.dumps(rg), db.dumps(cg),
             db.dumps(rel["suppressed"]), db.dumps(rel["protected_cells"]),
             db.dumps(rel["block_values"]), db.dumps(rel["row_marginals"]),
             db.dumps(rel["col_marginals"]), rel["grand_total"],
             db.dumps(ev["ranges"]), 1 if safe else 0,
             db.dumps(ev["reject_reasons"])),
        )
        result = _result_payload(title, rg, cg, rel, ev, dry_run=False)
        result["seq"] = seq
        result["id"] = cur.lastrowid
        return result


def _result_payload(title, rg, cg, rel, ev, dry_run):
    return {
        "title": title,
        "row_groups": rg, "col_groups": cg,
        "suppressed_blocks": rel["suppressed"],
        "mandatory_blocks": rel["mandatory"],
        "protected_cells": rel["protected_cells"],
        "released_table": rel["block_values"],
        "row_marginals": rel["row_marginals"],
        "col_marginals": rel["col_marginals"],
        "grand_total": rel["grand_total"],
        "ranges": ev["ranges"],
        "safe": not ev["reject_reasons"],
        "reject_reasons": ev["reject_reasons"],
        "n_equations": ev.get("n_equations"),
        "n_publications": ev.get("n_publications"),
        "dry_run": dry_run,
    }


def list_publications(sid: int, include_rejected: bool = True) -> list[dict]:
    with db.read_transaction() as conn:
        sql = "SELECT * FROM publications WHERE study_id=?"
        if not include_rejected:
            sql += " AND safe=1"
        sql += " ORDER BY seq"
        rows = conn.execute(sql, (sid,)).fetchall()
        return [db.pub_to_dict(r) for r in rows]


def csv_text(pub: dict, study: dict) -> str:
    import io
    buf = io.StringIO()
    rg_labels = ["|".join(study["row_labels"][i] for i in g) for g in pub["row_groups"]]
    cg_labels = ["|".join(study["col_labels"][j] for j in g) for g in pub["col_groups"]]
    buf.write("分组块," + ",".join(cg_labels) + ",行组合计\n")
    for a, line in enumerate(pub["released_table"]):
        cells = ["隐去" if v is None else str(v) for v in line]
        buf.write(rg_labels[a] + "," + ",".join(cells) +
                  "," + str(pub["row_marginals"][a]) + "\n")
    buf.write("列组合计," + ",".join(map(str, pub["col_marginals"])) +
              "," + str(pub["grand_total"]) + "\n")
    return buf.getvalue()
