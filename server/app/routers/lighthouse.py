"""Account-owned lighthouse saves and atomic action processing."""
import json
from time import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_conn
from app.game import lighthouse
from app.security import get_current_user_id

router = APIRouter(prefix="/api/lighthouse", tags=["lighthouse"])


class Action(BaseModel):
    action: Literal[
        "start", "light", "battery", "dock", "power_salvage", "power_signal",
        "upgrade_salvage", "upgrade_signal", "dispatch", "order_parts", "order_tickets",
        "mode_balanced", "mode_parts", "mode_tickets",
    ]
    operation_id: str = Field(min_length=8, max_length=80)
    revision: int = Field(ge=0)


def transact(user_id, command=None):
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = int(time())
        row = conn.execute("SELECT state FROM lighthouse_saves WHERE user_id = ?", (user_id,)).fetchone()
        state = json.loads(row["state"]) if row else lighthouse.new_save(now)
        summary = lighthouse.settle(state, now)
        if command:
            previous = conn.execute(
                "SELECT action FROM lighthouse_actions WHERE user_id = ? AND operation_id = ?",
                (user_id, command.operation_id),
            ).fetchone()
            if previous and previous["action"] != command.action:
                raise HTTPException(409, "操作编号已使用，请刷新进度")
            if not previous:
                if state["revision"] != command.revision:
                    raise HTTPException(409, "港口进度已变化，请刷新后重试")
                try:
                    lighthouse.apply_action(state, command.action)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
        conn.execute(
            """INSERT INTO lighthouse_saves(user_id, state) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET state = excluded.state""",
            (user_id, json.dumps(state)),
        )
        if command and not previous:
            conn.execute(
                "INSERT INTO lighthouse_actions(user_id, operation_id, action) VALUES (?, ?, ?)",
                (user_id, command.operation_id, command.action),
            )
        conn.commit()
        return lighthouse.view(state, summary, now)
    finally:
        conn.close()


@router.get("")
def read_save(user_id: int = Depends(get_current_user_id)):
    return transact(user_id)


@router.post("/actions")
def act(command: Action, user_id: int = Depends(get_current_user_id)):
    return transact(user_id, command)
