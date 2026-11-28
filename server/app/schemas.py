"""Pydantic request/response models."""

from decimal import Decimal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class NicknameRequest(BaseModel):
    nickname: str | None = None


class PasswordRequest(BaseModel):
    old_password: str
    new_password: str


class BanRequest(BaseModel):
    banned: bool


class RoleRequest(BaseModel):
    role: str


class CreateRoomRequest(BaseModel):
    game: str = "gomoku"
    name: str | None = None
    password: str | None = None
    # 扫雷难度**不在创建时定**：与军棋的玩法、深空休眠的地图一样，由房主在等待
    # 大厅里选（`room_setting`）。创建接口只给一个缺省档，房主不选也能直接开局。


class JoinRoomRequest(BaseModel):
    password: str | None = None


class AICreditsRequest(BaseModel):
    ai_credits: Decimal = Field(ge=-40000000, le=40000000, allow_inf_nan=False)
