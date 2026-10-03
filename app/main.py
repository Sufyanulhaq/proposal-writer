"""The web API. Start it with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .profile import Profile
from .repo import STATUSES, Repo, month_key
from .security import DUMMY, hash_password, new_token, token_hash, verify_password
from .writer_rules import write as write_offline

STATIC = Path(__file__).resolve().parent.parent / "static"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "proposal-writer"
COOKIE = "sid"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class GenerateIn(BaseModel):
    job_text: str = Field(min_length=50, max_length=6000)
    title: str | None = Field(default=None, max_length=120)


class ProposalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=6000)
    status: Literal["draft", "sent", "won", "lost"] | None = None


def default_title(job_text: str, limit: int = 60) -> str:
    """The start of the post, cut at a word boundary instead of in the middle of a word."""
    text = " ".join(job_text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def build_writer(settings: Settings):
    """Claude when an API key is set, the offline writer otherwise."""
    if not settings.anthropic_api_key:
        return write_offline, "offline"
    import anthropic

    from .writer_llm import ClaudeWriter

    return ClaudeWriter(anthropic.Anthropic(api_key=settings.anthropic_api_key), settings.model), "claude"


def create_app(settings: Settings | None = None, writer=None, mode: str | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    settings = settings or Settings.from_env()
    if writer is None:
        writer, detected = build_writer(settings)
        mode = mode or detected
    mode = mode or "custom"
    repo = Repo(settings.db_path, clock)
    app = FastAPI(title="Proposal Writer")
    app.state.repo = repo

    @app.middleware("http")
    async def csrf(request: Request, call_next):
        # A page on another site cannot add a custom header to a request, so requiring one stops cross site forgery.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403)
        return await call_next(request)

    def user(request: Request) -> dict:
        token = request.cookies.get(COOKIE)
        found = repo.user_for_token(token_hash(token)) if token else None
        if not found:
            raise HTTPException(401, "Please log in.")
        return found

    def start_session(response: Response, user_id: int) -> None:
        token = new_token()
        repo.create_session(user_id, token_hash(token), settings.session_days * 86400)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_days * 86400, path="/")

    def usage_of(user_id: int) -> dict:
        return {"used": repo.usage(user_id), "limit": settings.monthly_limit, "month": month_key(clock())}

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": mode}

    @app.post("/api/signup", status_code=201)
    def signup(body: Credentials, response: Response):
        email = body.email.strip().lower()
        if not EMAIL.match(email):
            raise HTTPException(422, "Enter a valid email address.")
        if len(body.password) < 10:
            raise HTTPException(422, "Use a password of at least 10 characters.")
        if body.password.lower() == email:
            raise HTTPException(422, "The password cannot be your email address.")
        user_id = repo.create_user(email, hash_password(body.password))
        if user_id is None:
            raise HTTPException(409, "An account with this email already exists.")
        start_session(response, user_id)
        return {"email": email}

    @app.post("/api/login")
    def login(body: Credentials, response: Response):
        email = body.email.strip().lower()
        if repo.failures(email, settings.login_window) >= settings.max_login_failures:
            raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
        found = repo.user_by_email(email)
        # An unknown email still costs one password check, so the response time does not reveal which emails have accounts.
        ok = verify_password(body.password, found["password_hash"] if found else DUMMY)
        if not (found and ok):
            repo.record_failure(email)
            raise HTTPException(401, "Wrong email or password.")
        repo.clear_failures(email)
        start_session(response, found["id"])
        return {"email": email}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        if token:
            repo.delete_session(token_hash(token))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(current: dict = Depends(user)):
        return {"email": current["email"], "usage": usage_of(current["id"]), "mode": mode}

    @app.get("/api/profile")
    def get_profile(current: dict = Depends(user)):
        return repo.get_profile(current["id"])

    @app.put("/api/profile")
    def put_profile(body: Profile, current: dict = Depends(user)):
        cleaned = body.cleaned()
        repo.set_profile(current["id"], cleaned.model_dump())
        return cleaned.model_dump()

    def view(p: dict) -> dict:
        return {k: p[k] for k in ("id", "title", "job_text", "body", "engine", "warnings", "status", "created_at", "updated_at")}

    @app.get("/api/proposals")
    def proposals(current: dict = Depends(user)):
        return [view(p) | {"job_text": ""} for p in repo.proposals(current["id"])]

    @app.get("/api/proposals/{proposal_id}")
    def proposal(proposal_id: int, current: dict = Depends(user)):
        found = repo.proposal(current["id"], proposal_id)
        if not found:
            raise HTTPException(404, "No such proposal.")
        return view(found)

    @app.post("/api/proposals/generate", status_code=201)
    def generate(body: GenerateIn, current: dict = Depends(user)):
        profile = Profile(**repo.get_profile(current["id"]))
        if not profile.name:
            raise HTTPException(409, "Add your name to your profile first.")
        if not repo.try_consume(current["id"], settings.monthly_limit):
            raise HTTPException(402, f"You have used all {settings.monthly_limit} proposals for this month.")
        try:
            draft = writer(body.job_text, profile)
        except Exception:
            repo.refund(current["id"])  # a failure on our side must not cost the customer a proposal
            raise HTTPException(502, "The draft could not be written. Nothing was used from your allowance.") from None
        title = (body.title or "").strip() or default_title(body.job_text)
        new_id = repo.add_proposal(current["id"], title, body.job_text, draft.body, draft.engine, draft.warnings)
        return {**view(repo.proposal(current["id"], new_id)), "usage": usage_of(current["id"])}

    @app.put("/api/proposals/{proposal_id}")
    def update(proposal_id: int, body: ProposalPatch, current: dict = Depends(user)):
        if not repo.update_proposal(current["id"], proposal_id, **body.model_dump()):
            raise HTTPException(404, "No such proposal.")
        return view(repo.proposal(current["id"], proposal_id))

    @app.delete("/api/proposals/{proposal_id}")
    def delete(proposal_id: int, current: dict = Depends(user)):
        if not repo.delete_proposal(current["id"], proposal_id):
            raise HTTPException(404, "No such proposal.")
        return {"deleted": proposal_id}

    @app.get("/api/stats")
    def stats(current: dict = Depends(user)):
        return repo.stats(current["id"])

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
