import json
import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")  # 须在导入 app.* 之前：engine 在导入时创建

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Candidate, Hall, PaperSet, SeatPlan


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    hall = Hall(code="H101", name="一号考室", rows=5, cols=6, min_manhattan=2)
    db.add(hall); db.flush()
    p1 = PaperSet(code="P-A", title="A 卷")
    p2 = PaperSet(code="P-B", title="B 卷")
    p3 = PaperSet(code="P-C", title="C 卷")
    db.add_all([p1, p2, p3]); db.flush()
    for i in range(12):
        db.add(Candidate(hall_id=1, name=f"考生{i + 1}", ticket_no=f"T{2026001 + i}",
                         paper_id=[p1.id, p2.id, p3.id][i % 3]))
    db.commit()

    def override_get_db():
        sess = TestingSession()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不使用 with：避免触发 lifespan（会按默认配置连 Postgres 种子）
    c = TestClient(app)
    yield c, TestingSession
    app.dependency_overrides.clear()
    engine.dispose()


def positions(plan_row: SeatPlan):
    data = json.loads(plan_row.result_json)
    return {a["candidate_id"]: (a["row"], a["col"]) for a in data["assignments"]}, data


def find_legal_pair(c, v1):
    data = c.get("/api/seating/latest?hall_id=1").json()
    ids = [a["candidate_id"] for a in data["assignments"]]
    for i, ia in enumerate(ids):
        for ib in ids[i + 1:]:
            r = c.post("/api/seating/swap?hall_id=1",
                       json={"candidate_a_id": ia, "candidate_b_id": ib, "hall_id": 1})
            if r.status_code == 200:
                return ia, ib, r
            # 失败必须没有产生新版本
            assert c.get("/api/seating/latest?hall_id=1").json()["id"] == v1
    raise AssertionError("应存在一对可成功对调的考生")


def test_swap_creates_new_version_and_advances(client):
    c, Session = client
    v1 = c.post("/api/seating/run?hall_id=1").json()
    assert v1["id"] == 1
    id_a, id_b, resp = find_legal_pair(c, 1)
    v2 = resp.json()

    # 版本前进
    assert v2["id"] == 2
    assert v2["version"] == 2
    assert v2["based_on_id"] == 1

    # 行列互换、第三人座位不变
    p1 = {a["candidate_id"]: (a["row"], a["col"]) for a in v1["assignments"]}
    p2 = {a["candidate_id"]: (a["row"], a["col"]) for a in v2["assignments"]}
    assert p2[id_a] == p1[id_b] and p2[id_b] == p1[id_a]
    for cid, pos in p1.items():
        if cid not in (id_a, id_b):
            assert p2[cid] == pos

    # 未排集合不变
    assert v1["unplaced"] == v2["unplaced"]

    # 违规与统计跟随新版本
    latest = c.get("/api/seating/latest?hall_id=1").json()
    assert latest["id"] == 2
    assert c.get("/api/seating/violations?hall_id=1").json()["version"] == 2
    assert c.get("/api/seating/stats?hall_id=1").json()["version"] == 2


def test_history_versions_are_never_rewritten(client):
    c, Session = client
    v1 = c.post("/api/seating/run?hall_id=1").json()
    id_a, id_b, resp = find_legal_pair(c, 1)
    v2 = resp.json()

    db = Session()
    row1 = db.get(SeatPlan, 1)
    snapshot = json.loads(row1.result_json)
    # 历史版本行内容与对调前完全一致（只追加，不改字）
    assert {a["candidate_id"]: (a["row"], a["col"]) for a in snapshot["assignments"]} == \
        {a["candidate_id"]: (a["row"], a["col"]) for a in v1["assignments"]}
    assert snapshot["version"] == 1
    assert row1.based_on_id is None
    assert row1.voided_at is None

    row2 = db.get(SeatPlan, v2["id"])
    assert row2.based_on_id == row1.id
    assert json.loads(row2.result_json)["version"] == 2
    assert db.query(SeatPlan).count() == 2
    db.close()


def test_failed_swap_rolls_back_entire_state(client):
    c, Session = client
    v1 = c.post("/api/seating/run?hall_id=1").json()

    # 找不到人：拒绝且无新版本
    r = c.post("/api/seating/swap?hall_id=1",
               json={"candidate_a_id": 1, "candidate_b_id": 99999, "hall_id": 1})
    assert r.status_code == 404

    before = c.get("/api/seating/latest?hall_id=1").json()
    # 当前方案版本、两人座位全部停留在操作前
    assert before["id"] == v1["id"]
    assert {a["candidate_id"]: (a["row"], a["col"]) for a in before["assignments"]} == \
        {a["candidate_id"]: (a["row"], a["col"]) for a in v1["assignments"]}
    # 违规列表、统计也跟随旧版本（未变）
    assert [(v["kind"], v["a_id"], v["b_id"]) for v in before["violations"]] == \
        [(v["kind"], v["a_id"], v["b_id"]) for v in v1["violations"]]
    assert before["stats"] == v1["stats"]

    db = Session()
    assert db.query(SeatPlan).count() == 1  # 失败没有写入任何版本行
    db.close()


def test_unseated_candidate_swap_rejected_via_api(client):
    c, Session = client
    c.post("/api/seating/run?hall_id=1")
    db = Session()
    # 新增一名属于本考室的考生，但不重新排座 → 该考生不在当前方案座位中
    extra = Candidate(hall_id=1, name="未排考生", ticket_no="T9999", paper_id=1)
    db.add(extra); db.commit()
    extra_id = extra.id
    db.close()

    r = c.post("/api/seating/swap?hall_id=1",
               json={"candidate_a_id": 1, "candidate_b_id": extra_id, "hall_id": 1})
    assert r.status_code == 409
    assert "不存在或未在座" in r.json()["detail"]
    db = Session()
    assert db.query(SeatPlan).count() == 1
    db.close()


def test_voided_plan_cannot_be_swapped(client):
    c, Session = client
    v1 = c.post("/api/seating/run?hall_id=1").json()
    # 作废当前版本
    r = c.post("/api/seating/void?hall_id=1", json={"hall_id": 1})
    assert r.status_code == 200 and r.json()["voided"] is True

    # 已作废方案禁止对调
    r = c.post("/api/seating/swap?hall_id=1",
               json={"candidate_a_id": 1, "candidate_b_id": 2, "hall_id": 1})
    assert r.status_code == 409
    assert "已作废" in r.json()["detail"]

    db = Session()
    # 没有新增版本；被作废的 v1 仍只有一行、内容未被改字
    assert db.query(SeatPlan).count() == 1
    row = db.get(SeatPlan, v1["id"])
    assert row.voided_at is not None
    assert positions(row)[0] == \
        {a["candidate_id"]: (a["row"], a["col"]) for a in v1["assignments"]}
    db.close()

    # 重新排座开新版本后，新版本可继续对调
    v3 = c.post("/api/seating/run?hall_id=1").json()
    assert v3["id"] == 2
    assert c.get("/api/seating/latest?hall_id=1").json()["id"] == 2


def test_voiding_latest_swap_falls_back_to_previous_version(client):
    c, Session = client
    v1 = c.post("/api/seating/run?hall_id=1").json()
    id_a, id_b, resp = find_legal_pair(c, 1)
    v2 = resp.json()
    assert v2["id"] == 2 and c.get("/api/seating/latest?hall_id=1").json()["id"] == 2

    # 作废 v2：最新版本被禁，当前生效版本回退到 v1；v1 内容一字未改
    r = c.post("/api/seating/void?hall_id=1", json={"hall_id": 1})
    assert r.status_code == 200 and r.json()["version"] == 2

    cur = c.get("/api/seating/latest?hall_id=1").json()
    assert cur["id"] == 1
    assert {a["candidate_id"]: (a["row"], a["col"]) for a in cur["assignments"]} == \
        {a["candidate_id"]: (a["row"], a["col"]) for a in v1["assignments"]}

    # 历史非当前版本 v2 未被这次操作改字；它仍是 v1 的派生物
    db = Session()
    row2 = db.get(SeatPlan, 2)
    assert row2.voided_at is not None
    assert row2.based_on_id == 1
    assert {a["candidate_id"]: (a["row"], a["col"])
            for a in json.loads(row2.result_json)["assignments"]} == \
        {a["candidate_id"]: (a["row"], a["col"]) for a in v2["assignments"]}
    db.close()

    # 作废的是 v2（历史版本），当前 v1 仍允许对调，成功后版本继续前进到 v3
    id_a2, id_b2, resp2 = find_legal_pair(c, 1)
    v3 = resp2.json()
    assert v3["id"] == 3 and v3["based_on_id"] == 1
    assert c.get("/api/seating/latest?hall_id=1").json()["id"] == 3
    # 已作废的 v2 依旧作废、内容不变
    db = Session()
    row2b = db.get(SeatPlan, 2)
    assert row2b.voided_at is not None
    db.close()


def test_swap_same_paper_diagonal_fails_and_rolls_back_version(client):
    c, Session = client
    db = Session()
    # 手工写入当前方案：1(卷1,0,0) 2(卷1,2,2) 3(卷2,1,1)，无任何违规
    from app.services.seat_engine import SeatAssign, plan_to_dict
    assigns = [
        SeatAssign(1, "考生1", "T1", 1, 0, 0),
        SeatAssign(2, "考生2", "T2", 1, 2, 2),
        SeatAssign(3, "考生3", "T3", 2, 1, 1),
    ]
    result = plan_to_dict(assigns, [], [], 5, 6)
    result["version"] = 1
    result["based_on_id"] = None
    result["voided"] = False
    db.add(SeatPlan(hall_id=1, created_at=datetime.utcnow(),
                    result_json=json.dumps(result, ensure_ascii=False)))
    db.commit(); db.close()

    # 对调 2、3：2(卷1) 落到 (1,1)，与 (0,0) 的 1(卷1) 对角相邻 → 整次失败
    r = c.post("/api/seating/swap?hall_id=1",
               json={"candidate_a_id": 2, "candidate_b_id": 3, "hall_id": 1})
    assert r.status_code == 409
    detail = r.json()["detail"]
    kinds = [v["kind"] for v in detail["violations"]]
    assert "same_paper_diagonal" in kinds
    assert detail["current_version"] == 1

    # 版本未前进；座位/违规/统计全部停留在操作前的 v1
    latest = c.get("/api/seating/latest?hall_id=1").json()
    assert latest["id"] == 1
    assert {a["candidate_id"]: (a["row"], a["col"]) for a in latest["assignments"]} == \
        {1: (0, 0), 2: (2, 2), 3: (1, 1)}
    assert latest["violations"] == []
    db = Session()
    assert db.query(SeatPlan).count() == 1
    db.close()
