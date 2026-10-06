import json
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Candidate, Hall, SeatPlan
from app.services.seat_engine import (
    SwapError,
    assigns_from_dicts,
    find_violations,
    place_candidates,
    plan_to_dict,
    swap_candidates,
    validate_swapped,
)

router = APIRouter(prefix="/seating", tags=["seating"])


class SwapRequest(BaseModel):
    candidate_a_id: int
    candidate_b_id: int
    hall_id: int = 1


class VoidRequest(BaseModel):
    hall_id: int = 1


def _latest_tip(db: Session, hall_id: int) -> SeatPlan | None:
    """当前考室最新版本行（可能已作废）。历史版本只追加、永不改字。"""
    return db.scalars(
        select(SeatPlan).where(SeatPlan.hall_id == hall_id).order_by(SeatPlan.id.desc())
    ).first()


def _latest_live(db: Session, hall_id: int) -> SeatPlan | None:
    """当前生效版本：最新的未作废方案。"""
    return db.scalars(
        select(SeatPlan)
        .where(SeatPlan.hall_id == hall_id, SeatPlan.voided_at.is_(None))
        .order_by(SeatPlan.id.desc())
    ).first()


def _store_plan(db: Session, hall_id: int, result: dict, based_on_id: int | None = None) -> SeatPlan:
    """只追加新版本行；flush 拿到版本号后写回 version 字段，再提交。"""
    result.setdefault("version", 0)
    result["based_on_id"] = based_on_id
    result["voided"] = False
    plan = SeatPlan(hall_id=hall_id, based_on_id=based_on_id,
                    created_at=datetime.utcnow(), result_json=json.dumps(result, ensure_ascii=False))
    db.add(plan)
    db.flush()
    result["version"] = plan.id
    plan.result_json = json.dumps(result, ensure_ascii=False)
    db.commit()
    db.refresh(plan)
    return plan


@router.post("/run")
def run_seating(hall_id: int = 1, db: Session = Depends(get_db)):
    hall = db.get(Hall, hall_id)
    if not hall:
        raise HTTPException(404, "考室不存在")
    cands = [{"id": c.id, "name": c.name, "ticket_no": c.ticket_no, "paper_id": c.paper_id}
             for c in db.scalars(select(Candidate).where(Candidate.hall_id == hall_id)).all()]
    assigns, unplaced = place_candidates(hall.rows, hall.cols, hall.min_manhattan, cands)
    viols = find_violations(hall.rows, hall.cols, hall.min_manhattan, assigns)
    result = plan_to_dict(assigns, unplaced, viols, hall.rows, hall.cols)
    result["hall"] = {"id": hall.id, "name": hall.name, "min_manhattan": hall.min_manhattan}
    plan = _store_plan(db, hall_id, result)
    return {"id": plan.id, **json.loads(plan.result_json)}


@router.post("/swap")
def swap_seats(body: SwapRequest, db: Session = Depends(get_db)):
    """对调 = 一次方案版本切换。

    成功：新写一版方案，图/违规/统计跟随新版本；第三人座位与未排集合不变。
    失败：不插入任何版本行，两人座位、违规、统计、当前版本全部保持操作前。
    """
    hall = db.get(Hall, body.hall_id)
    if not hall:
        raise HTTPException(404, "考室不存在")

    # 对调只作用于「当前生效版本」（最新未作废方案）；已作废版本永不成为操作目标
    plan = _latest_live(db, body.hall_id)
    tip = _latest_tip(db, body.hall_id)
    if plan is None:
        if tip is not None and tip.voided_at is not None:
            raise HTTPException(409, f"方案版本 v{tip.id} 已作废，禁止对调，请重新排座")
        raise HTTPException(409, "尚无排座方案，请先执行排座")

    # 找不到人（不存在或不属于本考室）→ 在任何写入之前拒绝
    cand_rows = db.scalars(
        select(Candidate).where(
            Candidate.hall_id == body.hall_id,
            Candidate.id.in_([body.candidate_a_id, body.candidate_b_id]),
        )
    ).all()
    found = {c.id for c in cand_rows}
    for cid in (body.candidate_a_id, body.candidate_b_id):
        if cid not in found:
            raise HTTPException(404, f"考生 {cid} 不存在")

    data = json.loads(plan.result_json)
    try:
        # 纯函数：在深拷贝上对调，原方案数据一格不动（未在座/自身对调在此被拒绝）
        next_assigns = swap_candidates(
            assigns_from_dicts(data.get("assignments", [])),
            body.candidate_a_id,
            body.candidate_b_id,
        )
        # 按现网约束对新方案重验；blocking 非空即整次失败
        all_viols, blocking = validate_swapped(
            hall.rows, hall.cols, hall.min_manhattan, next_assigns,
            body.candidate_a_id, body.candidate_b_id,
        )
    except SwapError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))

    if blocking:
        # 整次失败：不插入新版本行，座位/违规/统计/当前版本全部保持操作前
        db.rollback()
        raise HTTPException(409, {
            "message": "对调后违反现网约束，整次对调失败并回滚",
            "current_version": plan.id,
            "violations": [asdict(v) for v in blocking],
        })

    # 成功：图/违规/统计全部来自同一份新方案；未排集合沿用旧版（对调不改未排）
    result = plan_to_dict(next_assigns, data.get("unplaced", []), all_viols, hall.rows, hall.cols)
    result["hall"] = data.get("hall") or {"id": hall.id, "name": hall.name,
                                          "min_manhattan": hall.min_manhattan}
    # 只追加新版本行，旧版本行一个字都不改
    new_plan = _store_plan(db, body.hall_id, result, based_on_id=plan.id)
    return {"id": new_plan.id, **json.loads(new_plan.result_json)}


@router.post("/void")
def void_plan(body: VoidRequest, db: Session = Depends(get_db)):
    """作废当前最新版本（只置作废状态位，方案内容本身不改字）。"""
    plan = _latest_tip(db, body.hall_id)
    if plan is None:
        raise HTTPException(404, "尚无排座方案")
    if plan.voided_at is not None:
        raise HTTPException(409, f"方案版本 v{plan.id} 已作废")
    plan.voided_at = datetime.utcnow()
    db.commit()
    return {"id": plan.id, "version": plan.id, "voided": True}


@router.get("/latest")
def latest(hall_id: int = 1, db: Session = Depends(get_db)):
    plan = _latest_live(db, hall_id)
    if not plan:
        return run_seating(hall_id=hall_id, db=db)
    data = json.loads(plan.result_json)
    return {"id": plan.id, **data}


@router.get("/violations")
def violations(hall_id: int = 1, db: Session = Depends(get_db)):
    data = latest(hall_id=hall_id, db=db)
    return {"hall_id": hall_id, "version": data.get("id"),
            "violations": data.get("violations", []), "unplaced": data.get("unplaced", [])}


@router.get("/stats")
def stats(hall_id: int = 1, db: Session = Depends(get_db)):
    data = latest(hall_id=hall_id, db=db)
    return {"hall_id": hall_id, "version": data.get("id"), **data.get("stats", {})}
