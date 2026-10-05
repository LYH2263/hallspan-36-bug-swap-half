import pytest

from app.services.seat_engine import (
    SeatAssign,
    SwapError,
    find_violations,
    place_candidates,
    swap_candidates,
    validate_swapped,
)


def seed_candidates(n: int = 12):
    return [
        {"id": i + 1, "name": f"考生{i + 1}", "ticket_no": f"T{2026001 + i}",
         "paper_id": 1 + (i % 3)}
        for i in range(n)
    ]


def find_legal_swap_pair(rows, cols, min_dist, assigns):
    """枚举座上人对，返回第一对重验无阻断性违规的组合。"""
    ids = [a.candidate_id for a in assigns]
    for i, ia in enumerate(ids):
        for ib in ids[i + 1:]:
            trial = swap_candidates(assigns, ia, ib)
            _, blocking = validate_swapped(rows, cols, min_dist, trial, ia, ib)
            if not blocking:
                return ia, ib, trial
    raise AssertionError("种子方案中应至少存在一对可成功对调的考生")


def test_manhattan():
    from app.services.seat_engine import manhattan
    assert manhattan((0, 0), (2, 1)) == 3


def test_min_distance_placement():
    cands = [{"id": i, "name": f"C{i}", "ticket_no": f"T{i}", "paper_id": 1 + (i % 2)} for i in range(4)]
    assigns, unplaced = place_candidates(4, 4, 2, cands)
    assert len(assigns) + len(unplaced) == 4
    from app.services.seat_engine import manhattan
    for i, a in enumerate(assigns):
        for b in assigns[i+1:]:
            assert manhattan((a.row, a.col), (b.row, b.col)) >= 2


def test_same_paper_not_adjacent_in_result():
    cands = [
        {"id": 1, "name": "A", "ticket_no": "T1", "paper_id": 1},
        {"id": 2, "name": "B", "ticket_no": "T2", "paper_id": 1},
        {"id": 3, "name": "C", "ticket_no": "T3", "paper_id": 2},
    ]
    assigns, _ = place_candidates(3, 3, 1, cands)
    viols = find_violations(3, 3, 1, assigns)
    assert not any(v.kind == "same_paper_adjacent" for v in viols)


def test_violation_detection():
    assigns = [
        SeatAssign(1, "A", "T1", 1, 0, 0),
        SeatAssign(2, "B", "T2", 1, 0, 1),
    ]
    viols = find_violations(2, 2, 2, assigns)
    kinds = {v.kind for v in viols}
    assert "distance" in kinds
    assert "same_paper_adjacent" in kinds


def test_seed_swap_rows_cols_exchanged_and_third_person_untouched():
    # 种子考室 5x6、最小曼哈顿距离 2、12 人按 A/B/C 循环
    rows, cols, min_dist = 5, 6, 2
    assigns, unplaced = place_candidates(rows, cols, min_dist, seed_candidates())
    before = {a.candidate_id: (a.row, a.col) for a in assigns}
    assert not unplaced

    id_a, id_b, trial = find_legal_swap_pair(rows, cols, min_dist, assigns)
    after = {a.candidate_id: (a.row, a.col) for a in trial}

    # 行列互换：两人坐到对方原来的格子
    assert after[id_a] == before[id_b]
    assert after[id_b] == before[id_a]
    # 第三人座位不动
    for cid, pos in before.items():
        if cid not in (id_a, id_b):
            assert after[cid] == pos

    # 原方案必须一格不动（对调是新版本，不允许就地改人）
    assert {a.candidate_id: (a.row, a.col) for a in assigns} == before


def test_swap_same_paper_diagonal_must_fail():
    # 手工布局（min_dist=2，3x3）：Z 卷1 在 (0,0)，X 卷1 在 (2,2)，Y 卷2 在 (1,1)。
    # 对调 X、Y 后，X(卷1) 落到 (1,1)，与 (0,0) 的 Z(卷1) 成对角相邻 → 必须整次失败。
    assigns = [
        SeatAssign(1, "Z", "T1", 1, 0, 0),
        SeatAssign(2, "X", "T2", 1, 2, 2),
        SeatAssign(3, "Y", "T3", 2, 1, 1),
    ]
    before_viols = find_violations(3, 3, 2, assigns)
    assert not before_viols

    trial = swap_candidates(assigns, 2, 3)
    _, blocking = validate_swapped(3, 3, 2, trial, 2, 3)
    assert any(v.kind == "same_paper_diagonal" for v in blocking)

    # 原方案不得被改动（调用方据此整版回滚）
    pos = {a.candidate_id: (a.row, a.col) for a in assigns}
    assert pos == {1: (0, 0), 2: (2, 2), 3: (1, 1)}


def test_swap_unknown_or_unseated_rejected():
    assigns, _ = place_candidates(5, 6, 2, seed_candidates())

    # 找不到人
    with pytest.raises(SwapError):
        swap_candidates(assigns, 1, 99999)
    # 未在座（存在考生但不在方案座位里）
    with pytest.raises(SwapError):
        swap_candidates(assigns, 1, 13)
    # 与自身对调
    with pytest.raises(SwapError):
        swap_candidates(assigns, 1, 1)
