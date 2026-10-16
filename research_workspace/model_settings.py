"""Ephemeral DeepSeek configuration for the default local workspace only.

The application owns process-environment changes and worker replacement through
app.state.configure_model. This router neither persists nor tests credentials.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Literal
import unicodedata

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .accounts import Accounts, AuthenticationError


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
ALLOWED_MODELS = (DEFAULT_MODEL, "deepseek-v4-pro")
router = APIRouter(prefix="/api/model", tags=["local-model-settings"])


class ModelSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    api_key: SecretStr = Field(min_length=8, max_length=300)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = DEFAULT_MODEL

    @field_validator("api_key")
    @classmethod
    def valid_key_characters(cls, value: SecretStr) -> SecretStr:
        if any(character.isspace() or unicodedata.category(character).startswith("C")
               for character in value.get_secret_value()):
            raise ValueError("Invalid key characters")
        return value


def require_default_workspace(request: Request) -> None:
    accounts = getattr(request.app.state, "accounts", None)
    if accounts is None:
        raise HTTPException(503, "本机工作空间服务尚未就绪。")
    try:
        user = accounts.resolve(request.cookies.get(Accounts.COOKIE))
    except AuthenticationError:
        raise HTTPException(401, "登录状态无效，请返回本机默认空间后重试。") from None
    if user.get("id") is not None or user.get("mode") != "local":
        raise HTTPException(403, "模型配置仅允许在本机默认空间操作，请先退出当前账号。")


def safe_status() -> dict:
    configured = bool(
        os.environ.get("RESEARCH_MODEL_API_KEY", "").strip()
        and os.environ.get("RESEARCH_MODEL", "") in ALLOWED_MODELS
        and os.environ.get("RESEARCH_MODEL_BASE_URL", "").rstrip("/")
        in {DEEPSEEK_BASE_URL, DEEPSEEK_BASE_URL + "/v1"}
    )
    return {"configured": configured, "provider": "DeepSeek", "defaultModel": DEFAULT_MODEL,
            "model": os.environ.get("RESEARCH_MODEL") if configured else None}


@router.get("")
def get_settings(request: Request):
    require_default_workspace(request)
    return JSONResponse(safe_status(), headers={"Cache-Control": "no-store"})


@router.post("")
async def update_settings(request: Request):
    require_default_workspace(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请使用JSON提交模型设置。")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, "模型设置请求过大。")
        body.extend(chunk)
    try:
        raw = json.loads(body)
        settings = ModelSettingsInput.model_validate(raw)
    except (ValueError, TypeError, ValidationError):
        # Default request-validation responses can contain the invalid input.
        # Never include errors(), the submitted body, or exception text here.
        raise HTTPException(400, "设置无效：API密钥须为8至300个字符，不能包含空白或控制字符；请选择支持的模型。") from None
    finally:
        body.clear()
    callback = getattr(request.app.state, "configure_model", None)
    if not callable(callback):
        raise HTTPException(503, "当前本机服务尚未提供模型设置功能。")
    try:
        if inspect.iscoroutinefunction(callback):
            value = callback(settings.api_key.get_secret_value(), settings.model)
        else:
            # The application callback may wait for SQLite, a configuration lock,
            # or worker termination; none of those may block the ASGI event loop.
            value = await run_in_threadpool(callback,settings.api_key.get_secret_value(),settings.model)
        if inspect.isawaitable(value):
            await value
    except Exception:
        # Callback failures may contain subprocess/configuration information;
        # return a fixed message rather than forwarding their text or payloads.
        raise HTTPException(503, "模型配置未能更新。请确认没有运行中的模型任务，再重试保存；若仍失败，请重启本机服务。") from None
    return JSONResponse({"configured": True, "provider": "DeepSeek", "defaultModel": DEFAULT_MODEL,
                         "model": settings.model}, headers={"Cache-Control": "no-store"})
