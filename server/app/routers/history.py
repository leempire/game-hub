"""History REST route: paginated personal game history."""

from fastapi import APIRouter, Depends

from app.security import get_current_user_id
from app.database import get_user_history, get_user_stats

router = APIRouter(prefix="/api/history", tags=["history"])


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
