"""Computer use: let JoltIQ see and operate the customer's screen toward a goal (desktop sessions only)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import Conflict, Forbidden, NotFound
from ..deps import Ctx, get_ctx
from ..models import ComputerUseRun, ComputerUseStep
from ..services import computer_use as svc
from ..services import sessions as session_svc
from ..services import storage
from .sessions import session_for

router = APIRouter(tags=["computer-use"])


def run_for(ctx: Ctx, rid: uuid.UUID) -> ComputerUseRun:
    run = ctx.db.get(ComputerUseRun, rid)
    if not run:
        raise NotFound("Run not found")
    session_for(ctx, run.session_id)  # same visibility rules as the session
    return run


def _is_customer_side(ctx: Ctx, run: ComputerUseRun) -> bool:
    # Only the person who consented to this session (the one at / owning the computer) answers for it.
    s = session_svc.get(ctx.db, run.session_id)
    return s.consented_by is not None and s.consented_by == ctx.p.user_id


class StartIn(BaseModel):
    goal: str = Field(min_length=5, max_length=2000)
    max_steps: int = Field(default=60, ge=5, le=200)
    mode: Literal["guide", "fix"] = "fix"


@router.post("/sessions/{sid}/computer-use", status_code=201)
def start(sid: uuid.UUID, body: StartIn, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    if not (ctx.p.can("sessions.start") or s.owner_id == ctx.p.user_id):
        raise Forbidden("You can't start screen control in this session")
    # The person whose computer it is (the one who consented) approves the goal by starting it themselves;
    # anyone else's request waits for their approval.
    own = s.consented_by == ctx.p.user_id
    run = svc.start(
        ctx.db,
        s,
        goal=body.goal,
        user_id=ctx.p.user_id,
        approved_by_customer=own,
        max_steps=body.max_steps,
        mode=body.mode,
    )
    return svc.ser(run, [])


@router.get("/sessions/{sid}/computer-use")
def list_runs(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    runs = ctx.db.execute(select(ComputerUseRun).where(ComputerUseRun.session_id == s.id).order_by(ComputerUseRun.created_at.desc()).limit(20)).scalars()
    return [svc.ser(r) for r in runs]


@router.get("/computer-use/{rid}")
def get(rid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    run = run_for(ctx, rid)
    steps = ctx.db.execute(select(ComputerUseStep).where(ComputerUseStep.run_id == run.id).order_by(ComputerUseStep.idx)).scalars().all()
    out = svc.ser(run, list(steps))
    out["you_can_answer"] = _is_customer_side(ctx, run)
    return out


class ApproveIn(BaseModel):
    approved: bool


@router.post("/computer-use/{rid}/approve")
def approve(rid: uuid.UUID, body: ApproveIn, ctx: Ctx = Depends(get_ctx)):
    run = run_for(ctx, rid)
    if not _is_customer_side(ctx, run):
        raise Forbidden("Only the person whose computer this is can approve")
    svc.approve_goal(ctx.db, run, user_id=ctx.p.user_id, approved=body.approved)
    return svc.ser(run)


class RespondIn(BaseModel):
    approved: bool | None = None
    answer: str | None = Field(default=None, max_length=1000)
    done: bool = False


@router.post("/computer-use/{rid}/respond")
def respond(rid: uuid.UUID, body: RespondIn, ctx: Ctx = Depends(get_ctx)):
    run = run_for(ctx, rid)
    if not _is_customer_side(ctx, run):
        raise Forbidden("Only the person whose computer this is can answer")
    svc.respond(ctx.db, run, user_id=ctx.p.user_id, approved=body.approved, answer=body.answer, done=body.done)
    return svc.ser(run)


@router.post("/computer-use/{rid}/stop")
def stop(rid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    run = run_for(ctx, rid)
    svc.stop(ctx.db, run, user_id=ctx.p.user_id, reason="Stopped by " + ("the customer" if _is_customer_side(ctx, run) else "a technician"))
    return svc.ser(run)


def _image(ctx: Ctx, key: str | None) -> Response:
    if not key:
        raise Conflict("No screenshot yet", code="no_view")
    data = storage.get(ctx.org_id, key)
    return Response(data, media_type="image/png" if data[:4] == b"\x89PNG" else "image/jpeg", headers={"cache-control": "no-store"})


@router.get("/computer-use/{rid}/screen")
def screen(rid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    return _image(ctx, run_for(ctx, rid).last_screenshot_key)


@router.get("/computer-use/{rid}/steps/{idx}/screen")
def step_screen(rid: uuid.UUID, idx: int, ctx: Ctx = Depends(get_ctx)):
    run = run_for(ctx, rid)
    st = ctx.db.execute(select(ComputerUseStep).where(ComputerUseStep.run_id == run.id, ComputerUseStep.idx == idx)).scalar_one_or_none()
    if not st:
        raise NotFound("Step not found")
    return _image(ctx, st.screenshot_key)
