"""Run with: python run_sirius_server.py. Text API for the Sirius Telegram client."""
from contextlib import asynccontextmanager, suppress
import asyncio
from typing import Literal
import secrets
import hashlib
from fastapi import FastAPI, Header, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field
import uvicorn
from sirius_core import SiriusCore
from sirius_settings import get_secret, server_config
from router_memory import request_scope, record_turn

core = SiriusCore()

async def timer_worker():
    while True:
        await asyncio.to_thread(core.run_due_timers)
        await asyncio.sleep(0.5)

@asynccontextmanager
async def lifespan(app):
    core.init_with_plugins()
    core.start_background_services()
    worker = asyncio.create_task(timer_worker())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        await asyncio.to_thread(core.stop_background_services)

app = FastAPI(title="Сіріус", lifespan=lifespan)

def authorize(x_sirius_key: str = Header(default="")):
    expected = get_secret("server.api_key")
    if not expected or not secrets.compare_digest(x_sirius_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")

class Message(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    chat_id: int
    user_id: int
    user_name: str = ""
    home: str = "0"

class TimerDecision(BaseModel):
    chat_id: int
    user_id: int
    id: str = Field(min_length=16, max_length=16)
    decision: Literal["confirm", "cancel"]

@app.post("/timer/decision", dependencies=[Depends(authorize)])
def timer_decision(payload: TimerDecision):
    return core.decide_action(payload.chat_id, payload.user_id, payload.id, payload.decision)

@app.get("/health")
def health():
    return {"name": "Сіріус", "status": "ok" if not core.plugin_errors else "degraded",
            "plugins": list(core.plugin_manifests), "plugin_errors": core.plugin_errors}

@app.post("/message", dependencies=[Depends(authorize)])
def message(payload: Message):
    return core.handle_message(payload.text, payload.chat_id, payload.user_id,
                               user_name=payload.user_name, home=payload.home)

@app.get("/notifications", dependencies=[Depends(authorize)])
def notifications(chat_id: int, user_id: int):
    return {"messages": core.notifications(chat_id, user_id)}


def local_web_only(request: Request):
    # The existing voice daemon has a localhost API contract without API headers.
    # Keep that compatibility interface inaccessible to remote/proxied callers.
    if (not request.client or request.client.host not in {"127.0.0.1", "::1"}
            or request.headers.get("x-forwarded-for") or request.headers.get("forwarded")):
        raise HTTPException(status_code=403, detail="Local web daemon only")


def web_identity(chat_id, user_id):
    # Web session IDs are strings; Telegram IDs stay in a separate numeric space.
    def ident(value):
        value = value or secrets.token_hex(16)
        return -int.from_bytes(hashlib.sha256(("sirius-web:" + value).encode()).digest()[:8], "big") - 1
    return ident(chat_id), ident(user_id)


@app.get("/sendTxtCmd", dependencies=[Depends(local_web_only)])
def web_command(cmd: str = Query(min_length=1, max_length=4000), chat_id: str = "",
                user_id: str = "", source: str = "web", returnFormat: str = "saytxt"):
    chat, user = web_identity(chat_id, user_id)
    result = core.handle_message(cmd, chat, user, home="0", user_name="Веб")
    return {**result, "restxt": result["text"]}


class WebObservation(BaseModel):
    chat_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    source: str = "web"
    text: str = Field(max_length=4000)
    reply: str = Field(default="", max_length=4000)


@app.post("/router/context", dependencies=[Depends(local_web_only)])
def web_observation(payload: WebObservation):
    chat, user = web_identity(payload.chat_id, payload.user_id)
    with core._lock, request_scope(chat, user, f"guest:{id(core)}"):
        record_turn(payload.text, payload.reply)
    return {"ok": True}

if __name__ == "__main__":
    config = server_config()
    if not get_secret("server.api_key"):
        raise SystemExit("Set server.api_key in secrets.local.json")
    uvicorn.run(app, host=config.get("host", "127.0.0.1"), port=int(config.get("port", 5003)))
