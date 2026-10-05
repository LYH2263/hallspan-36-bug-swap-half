"""Exam seating: min Manhattan distance; same paper_id cannot be 4-neighbor adjacent."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass


class SwapError(ValueError):
    """对调被拒绝：找不到人、未在座、对调自身，或当前方案已作废。"""


@dataclass
class SeatAssign:
    candidate_id: int
    name: str
    ticket_no: str
    paper_id: int
    row: int
    col: int

@dataclass
class Violation:
    kind: str
    a_id: int
    b_id: int
    detail: str

def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

def neighbors4(r: int, c: int, rows: int, cols: int) -> list[tuple[int, int]]:
    out = []
    for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            out.append((nr, nc))
    return out

def place_candidates(rows: int, cols: int, min_dist: int, candidates: list[dict]) -> tuple[list[SeatAssign], list[dict]]:
    """Greedy: try seats row-major; accept if manhattan >= min_dist to all placed AND no same paper 4-neigh."""
    occupied: dict[tuple[int, int], SeatAssign] = {}
    unplaced: list[dict] = []
    for cand in candidates:
        placed = False
        for r in range(rows):
            for c in range(cols):
                if (r, c) in occupied:
                    continue
                ok = True
                for pos, other in occupied.items():
                    if manhattan((r, c), pos) < min_dist:
                        ok = False
                        break
                    if other.paper_id == cand["paper_id"] and (r, c) in neighbors4(pos[0], pos[1], rows, cols):
                        ok = False
                        break
                if not ok:
                    continue
                # also check 4-neigh same paper against current neighbors
                for nr, nc in neighbors4(r, c, rows, cols):
                    if (nr, nc) in occupied and occupied[(nr, nc)].paper_id == cand["paper_id"]:
                        ok = False
                        break
                if not ok:
                    continue
                assign = SeatAssign(cand["id"], cand["name"], cand["ticket_no"], cand["paper_id"], r, c)
                occupied[(r, c)] = assign
                placed = True
                break
            if placed:
                break
        if not placed:
            unplaced.append(cand)
    return list(occupied.values()), unplaced

def find_violations(rows: int, cols: int, min_dist: int, assigns: list[SeatAssign]) -> list[Violation]:
    viols: list[Violation] = []
    by_pos = {(a.row, a.col): a for a in assigns}
    for i, a in enumerate(assigns):
        for b in assigns[i + 1:]:
            d = manhattan((a.row, a.col), (b.row, b.col))
            if d < min_dist:
                viols.append(Violation("distance", a.candidate_id, b.candidate_id,
                                       f"曼哈顿距离 {d} < 最小要求 {min_dist}"))
            if a.paper_id == b.paper_id and (b.row, b.col) in neighbors4(a.row, a.col, rows, cols):
                viols.append(Violation("same_paper_adjacent", a.candidate_id, b.candidate_id,
                                       f"同试卷套 {a.paper_id} 四邻相邻"))
    return viols

def swap_candidates(assigns: list[SeatAssign], id_a: int, id_b: int) -> list[SeatAssign]:
    """对调两人座位，返回新方案（深拷贝，原方案一格不动）。找不到人或未在座则拒绝。"""
    if id_a == id_b:
        raise SwapError("不能与自身对调")
    by_id = {a.candidate_id: a for a in assigns}
    missing = [i for i in (id_a, id_b) if i not in by_id]
    if missing:
        raise SwapError(f"考生 {missing[0]} 不存在或未在座，无法对调")
    next_assigns = deepcopy(assigns)
    next_by_id = {a.candidate_id: a for a in next_assigns}
    a, b = next_by_id[id_a], next_by_id[id_b]
    # 行、列一起互换（对调两人格子，第三人座位不变）
    a.row, a.col = b.row, b.col
    return next_assigns


def assigns_from_dicts(items: list[dict]) -> list[SeatAssign]:
    fields = SeatAssign.__dataclass_fields__
    return [SeatAssign(**{k: v for k, v in item.items() if k in fields}) for item in items]


def validate_swapped(
    rows: int, cols: int, min_dist: int, assigns: list[SeatAssign], id_a: int, id_b: int
) -> tuple[list[Violation], list[Violation]]:
    """按现网约束对「对调后」方案全量重验。

    返回 (全部违规, 阻断性违规)：阻断性违规非空则整次对调失败、必须回滚。
    阻断范围 = 两名移动者新引入的距离不足 / 同卷四邻，以及同卷对角相邻；
    与移动者无关的旧违规位置未发生变化，不属于本次对调引入。
    """
    moved = {id_a, id_b}
    by_id = {a.candidate_id: a for a in assigns}
    if moved - set(by_id):
        raise SwapError("存在考生不在当前方案座位中，无法重验")
    viols = find_violations(rows, cols, min_dist, assigns)
    by_pos = {(a.row, a.col): a for a in assigns}
    seen_diag: set[frozenset[int]] = set()
    for m in (by_id[id_a], by_id[id_b]):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if abs(dr) + abs(dc) != 2:
                    continue
                other = by_pos.get((m.row + dr, m.col + dc))
                if other is None or other.candidate_id == m.candidate_id:
                    continue
                if other.paper_id != m.paper_id:
                    continue
                key = frozenset((m.candidate_id, other.candidate_id))
                if key in seen_diag:
                    continue
                seen_diag.add(key)
                viols.append(Violation("same_paper_diagonal", m.candidate_id, other.candidate_id,
                                       f"同试卷套 {m.paper_id} 对角相邻"))

    def introduced(v: Violation) -> bool:
        if v.kind == "same_paper_diagonal":
            return True
        return v.a_id in moved or v.b_id in moved

    blocking = [v for v in viols if introduced(v) and v.kind in (
        "distance", "same_paper_adjacent", "same_paper_diagonal")]
    return viols, blocking


def plan_to_dict(assigns: list[SeatAssign], unplaced: list[dict], viols: list[Violation], rows: int, cols: int) -> dict:
    return {
        "rows": rows,
        "cols": cols,
        "assignments": [asdict(a) for a in assigns],
        "unplaced": unplaced,
        "violations": [asdict(v) for v in viols],
        "stats": {
            "seated": len(assigns),
            "unplaced": len(unplaced),
            "violations": len(viols),
            "capacity": rows * cols,
        },
    }
