"""History REST route: paginated personal game history."""

from fastapi import APIRouter, Depends, HTTPException

from app.security import get_current_user_id
from app.database import get_user_history, get_user_stats
from app import sharing

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/shared/{token}")
def shared_record(token: str):
    record = sharing.public_share(token)
    if record is None:
        raise HTTPException(status_code=404, detail="分享不存在或已撤销")
    return record


@router.post("/{record_id}/share")
def share_record(record_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        return sharing.create_share(record_id, user_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.delete("/{record_id}/share")
def unshare_record(record_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        return sharing.revoke_share(record_id, user_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.get("/stats")
def my_stats(user_id: int = Depends(get_current_user_id)):
    """Aggregated stats for the profile card + home "recent games"."""
    return get_user_stats(user_id)


@router.get("/mine")
def my_history(
    page: int = 1,
    size: int = 20,
    user_id: int = Depends(get_current_user_id),
):
    """Return paginated history records for the current user."""
    if page < 1:
        page = 1
    if size < 1:
        size = 1
    if size > 100:
        size = 100
    return get_user_history(user_id, page, size)
