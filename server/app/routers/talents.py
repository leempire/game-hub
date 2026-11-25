"""深渊回声局外天赋树：查询、加点、免费重置。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import talents
from app.security import get_current_user_id

router = APIRouter(prefix="/api/talents", tags=["talents"])


class AllocateRequest(BaseModel):
    node: str = Field(min_length=1, max_length=40)


@router.get("")
def my_talents(user_id: int = Depends(get_current_user_id)):
    return talents.profile(user_id)


@router.post("/allocate")
def allocate_talent(body: AllocateRequest,
                    user_id: int = Depends(get_current_user_id)):
    try:
        return talents.allocate(user_id, body.node)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/reset")
def reset_talents(user_id: int = Depends(get_current_user_id)):
    return talents.reset(user_id)