"""Minimal demo interface and session routes.

The server binds every request to a demo session. Request bodies carry only text, ids, and
expected versions; they never choose a user, agent, role, team, or source kind. Routes are
plain functions, so each guarded dispatch runs synchronously in one worker thread. Run one
process. Real purchases are never placed; the purchasing service is the local mock.
"""

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Cookie, FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field, ValidationError

from memorybank.agents import InterpretationError, submit_stored_proposal
from memorybank.dispatch import (
    HttpPurchasingClient,
    PurchasingError,
    PurchasingUnavailable,
    execute_stored_purchase,
)
from memorybank.lifecycle import LifecycleConflict
from memorybank.llm import LlmError
from memorybank.memory_gateway import AccessDenied, MemoryGateway, ServerIdentity
from memorybank.proposals import StaleProposal

TENANT = "demo_company"
SUPPLIER = "northstar"
FIRST_MEMORY = "mem_northstar_v1"
STATUS_QUERY = "Northstar purchasing policy"
DEMO_DB_PREFIX = "memorybank_ui_demo_"
SESSION_COOKIE = "demo_session"
PREPARE_TIMEOUT_SECONDS = 300
PAGE = Path(__file__).with_name("page.html")

SESSIONS = {
    "employee": ("employee", "procurement"),
    "finance_admin": ("finance_admin", "procurement"),
    "other_team_user": ("other_team_user", "marketing"),
}
RUN_FIELDS = (
    "_id",
    "user_id",
    "agent_id",
    "tenant_id",
    "team_id",
    "status",
    "reason",
    "memory_id",
    "memory_version",
    "validation_event_id",
    "purchasing_record_id",
    "idempotency_key",
    "redacted",
    "created_at",
)
PROPOSAL_FIELDS = (
    "_id",
    "status",
    "reason",
    "policy_key",
    "expected_version",
    "content",
    "effective_content",
    "terms",
    "source_kind",
    "requested_by_user_id",
    "submitted_by_agent_id",
)
RECORD_FIELDS = (
    "supplier_id",
    "currency",
    "amount",
    "memory_id",
    "memory_version",
    "idempotency_key",
    "requested_by_user_id",
    "submitted_by_agent_id",
    "tenant_id",
    "team_id",
)
RECORD_KINDS = {"apr_": "approval_request", "ord_": "draft_order"}


class NotReady(Exception):
    """The disposable demo database is still being prepared."""


class NoSession(Exception):
    """No demo session has been chosen."""


class CachedQueries:
    """Reuse real Voyage query vectors for repeated identical text. Documents are not cached."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.model, self.dimensions = delegate.model, delegate.dimensions
        self._cache: dict[str, list[float]] = {}

    def documents(self, texts):
        return self.delegate.documents(texts)

    def query(self, text):
        if text not in self._cache:
            self._cache[text] = self.delegate.query(text)
        return self._cache[text]


class DemoState:
    """Everything one app process shares. Lists only ids this app has seen."""

    def __init__(self, *, gateway=None, settings=None, gemini=None, purchasing=None):
        self.gateway = gateway
        self.settings = settings
        self.gemini = gemini
        self.purchasing = purchasing
        self.ready = gateway is not None
        self.preparing_error: str | None = None
        self.lock = threading.Lock()
        self.proposals: list[str] = []
        self.memories: dict[str, int] = {FIRST_MEMORY: 1}
        self.current_memory = FIRST_MEMORY
        self.runs: list[str] = []
        self.db_name: str | None = None
        self.client = None

    def require_ready(self):
        if not self.ready:
            raise NotReady(self.preparing_error or "Still preparing the demo database.")


def identity(session: str | None, agent_id: str) -> ServerIdentity:
    if session not in SESSIONS:
        raise NoSession()
    user_id, team_id = SESSIONS[session]
    return ServerIdentity(user_id, agent_id, TENANT, team_id)


def source_kind(session: str) -> str:
    return "finance_admin" if session == "finance_admin" else "employee_message"


def llm_options(state: DemoState) -> dict[str, Any]:
    model = state.settings.llm_model if state.gemini is not None else None
    return {"client": state.gemini, "model": model, "settings": state.settings}


def pick(document: dict, fields) -> dict:
    return {field: document[field] for field in fields if field in document}


def prepare(state: DemoState):
    """Create, seed, embed, and index a uniquely named disposable demo database."""
    from memorybank.config import get_settings
    from memorybank.db import get_client
    from memorybank.embeddings import Embeddings, embed_demo_memories
    from memorybank.llm import build_client
    from memorybank.schema import setup_database
    from memorybank.search import setup_search_index
    from memorybank.seed import seed_database

    try:
        settings = get_settings()
        client = get_client()
        name = f"{DEMO_DB_PREFIX}{uuid4().hex}"
        state.client, state.db_name = client, name
        db = client[name]
        embedder = CachedQueries(
            Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
        )
        setup_database(db)
        seed_database(db)
        embed_demo_memories(db, embedder)
        setup_search_index(db.memories, embedder.dimensions)
        gateway = MemoryGateway(db, embedder)
        buyer = ServerIdentity("employee", "agent_a", TENANT, "procurement")
        deadline = time.monotonic() + PREPARE_TIMEOUT_SECONDS
        while True:
            try:
                found = gateway.retrieve_memories(buyer, STATUS_QUERY, SUPPLIER)
                if any(memory["memory_id"] == FIRST_MEMORY for memory in found):
                    break
            except RuntimeError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Search index was not ready in time. Restart the demo.")
            time.sleep(2)
        state.settings = settings
        state.gateway = gateway
        state.gemini = build_client(settings.llm_api_key) if settings.llm_api_key else None
        state.purchasing = HttpPurchasingClient(settings.purchasing_service_url)
        state.ready = True
    except Exception as exc:  # Shown on the page; keys and URIs never appear in these messages.
        state.preparing_error = f"Demo database could not be prepared: {type(exc).__name__}."


def cleanup(state: DemoState):
    if state.purchasing is not None and hasattr(state.purchasing, "close"):
        state.purchasing.close()
    if state.gemini is not None and hasattr(state.gemini, "close"):
        state.gemini.close()
    if state.client is not None and state.db_name and state.db_name.startswith(DEMO_DB_PREFIX):
        state.client.drop_database(state.db_name)


class SessionBody(BaseModel):
    session: Literal["employee", "finance_admin", "other_team_user"]


class MessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class VersionBody(BaseModel):
    expected_version: int = Field(ge=1)


def create_app(state: DemoState | None = None) -> FastAPI:
    injected = state is not None
    shared = state if state is not None else DemoState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not injected:
            threading.Thread(target=prepare, args=(shared,), daemon=True).start()
        yield
        if not injected:
            cleanup(shared)

    app = FastAPI(title="MemoryBank demo", lifespan=lifespan)
    app.state.demo = shared
    Session = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]

    def failure(status: int, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": message})

    @app.exception_handler(NoSession)
    def no_session(_request: Request, _exc: NoSession):
        return failure(401, "Choose a demo session first.")

    @app.exception_handler(NotReady)
    def not_ready(_request: Request, exc: NotReady):
        return failure(503, str(exc))

    @app.exception_handler(AccessDenied)
    def denied(_request: Request, _exc: AccessDenied):
        return failure(403, "This session is not allowed to do that.")

    @app.exception_handler(StaleProposal)
    def stale(_request: Request, _exc: StaleProposal):
        return failure(409, "The version moved. Refresh and retry.")

    @app.exception_handler(LifecycleConflict)
    def conflict(_request: Request, _exc: LifecycleConflict):
        return failure(409, "The version moved or the memory is unavailable. Refresh and retry.")

    @app.exception_handler(LlmError)
    def llm_error(_request: Request, exc: LlmError):
        return failure(503, str(exc))

    @app.exception_handler(genai_errors.APIError)
    def gemini_error(_request: Request, _exc: genai_errors.APIError):
        return failure(502, "Gemini request failed. Nothing changed. Try again.")

    @app.exception_handler(InterpretationError)
    @app.exception_handler(ValidationError)
    def unreadable(_request: Request, _exc: Exception):
        return failure(422, "The request could not be read safely. Nothing changed.")

    @app.exception_handler(PurchasingError)
    @app.exception_handler(PurchasingUnavailable)
    def purchasing_error(_request: Request, _exc: Exception):
        return failure(502, "The purchasing service did not confirm the request.")

    @app.exception_handler(ValueError)
    def invalid(_request: Request, _exc: ValueError):
        return failure(400, "That request is not valid.")

    @app.exception_handler(RuntimeError)
    def search_wait(_request: Request, _exc: RuntimeError):
        return failure(503, "Search is not ready yet. Wait a moment and retry.")

    @app.get("/", response_class=HTMLResponse)
    def page():
        return PAGE.read_text(encoding="utf-8")

    @app.post("/session")
    def choose_session(body: SessionBody, response: Response):
        response.set_cookie(SESSION_COOKIE, body.session, httponly=True, samesite="strict")
        user_id, team_id = SESSIONS[body.session]
        return {"session": body.session, "user_id": user_id, "team_id": team_id}

    @app.post("/purchase")
    def purchase(body: MessageBody, demo_session: Session = None):
        shared.require_ready()
        buyer = identity(demo_session, "agent_a")
        result = execute_stored_purchase(
            body.message,
            buyer,
            SUPPLIER,
            gateway=shared.gateway,
            purchasing=shared.purchasing,
            **llm_options(shared),
        )
        with shared.lock:
            shared.runs.append(result.run_id)
        return {
            "decision": "allowed" if result.allowed else "denied",
            "reason": result.reason,
            "proposed_action": result.proposed.action.value,
            "required_action": result.required_action,
            "supplier_id": result.proposed.supplier_id,
            "currency": result.proposed.currency,
            "amount": result.proposed.amount,
            "memory_id": result.proposed.memory_id,
            "memory_version": result.proposed.memory_version,
            "purchasing_record_id": result.purchasing_record_id,
            "run_id": result.run_id,
            "status": result.status,
        }

    @app.post("/proposal")
    def proposal(body: MessageBody, demo_session: Session = None):
        shared.require_ready()
        submitter = identity(demo_session, "agent_b")
        current = shared.gateway.get_memory(submitter, shared.current_memory)
        result = submit_stored_proposal(
            body.message,
            submitter,
            source_kind(demo_session),
            current_policy=current,
            gateway=shared.gateway,
            **llm_options(shared),
        )
        with shared.lock:
            shared.proposals.append(result.proposal_id)
        return result.model_dump()

    @app.get("/proposals")
    def proposals(demo_session: Session = None):
        shared.require_ready()
        reviewer = identity(demo_session, "agent_b")
        listed = []
        for proposal_id in list(shared.proposals):
            found = shared.gateway.get_proposal(reviewer, proposal_id)
            if found:
                item = pick(found, PROPOSAL_FIELDS)
                item["proposal_id"] = item.pop("_id")
                listed.append(item)
        return jsonable_encoder({"proposals": listed})

    def review(proposal_id: str, body: VersionBody, session: str | None, approve: bool):
        shared.require_ready()
        reviewer = identity(session, "agent_b")
        if approve:
            result = shared.gateway.approve_proposal(reviewer, proposal_id, body.expected_version)
        else:
            result = shared.gateway.reject_proposal(reviewer, proposal_id, body.expected_version)
        result = jsonable_encoder(result)
        if approve and result.get("memory_id") and result.get("version"):
            with shared.lock:
                result["superseded_memory_id"] = shared.current_memory
                shared.memories[result["memory_id"]] = result["version"]
                shared.current_memory = result["memory_id"]
            result["indexing"] = (
                f"Waiting for search to index version {result['version']}. A purchase may ask "
                "for clarification until then. It will not reuse the old version."
            )
        return result

    @app.post("/proposals/{proposal_id}/approve")
    def approve(proposal_id: str, body: VersionBody, demo_session: Session = None):
        return review(proposal_id, body, demo_session, True)

    @app.post("/proposals/{proposal_id}/reject")
    def reject(proposal_id: str, body: VersionBody, demo_session: Session = None):
        return review(proposal_id, body, demo_session, False)

    @app.post("/memories/{memory_id}/{operation}")
    def lifecycle(
        memory_id: str,
        operation: Literal["expire", "revoke", "forget"],
        body: VersionBody,
        demo_session: Session = None,
    ):
        shared.require_ready()
        admin = identity(demo_session, "agent_b")
        change = {
            "expire": shared.gateway.expire_memory,
            "revoke": shared.gateway.revoke_memory,
            "forget": shared.gateway.forget_memory,
        }[operation]
        return jsonable_encoder(change(admin, memory_id, body.expected_version))

    @app.get("/memories/{memory_id}")
    def memory(memory_id: str, demo_session: Session = None):
        shared.require_ready()
        reader = identity(demo_session, "agent_a")
        found = shared.gateway.get_memory(reader, memory_id)
        if found is None:
            return failure(404, "Memory not available.")
        return jsonable_encoder(found)

    @app.get("/runs/{run_id}")
    def run(run_id: str, demo_session: Session = None):
        shared.require_ready()
        viewer = identity(demo_session, "agent_a")
        found = shared.gateway.db.runs.find_one(
            {"_id": run_id, "tenant_id": viewer.tenant_id, "team_id": viewer.team_id}
        )
        if found is None:
            return failure(404, "Run not available.")
        item = pick(found, RUN_FIELDS)
        item["run_id"] = item.pop("_id")
        return jsonable_encoder(item)

    @app.post("/records/{record_id}/replay")
    def replay(record_id: str, demo_session: Session = None):
        shared.require_ready()
        admin = identity(demo_session, "agent_b")
        if demo_session != "finance_admin":
            raise AccessDenied("Only the finance administrator may replay a record")
        kind = next(
            (value for prefix, value in RECORD_KINDS.items() if record_id.startswith(prefix)),
            None,
        )
        if kind is None:
            return failure(404, "Purchasing record not available.")
        original = shared.purchasing.read(kind, record_id)
        if original is None or original.get("team_id") != admin.team_id:
            return failure(404, "Purchasing record not available.")
        replayed = shared.purchasing.create(kind, pick(original, RECORD_FIELDS))
        return {
            "original_id": record_id,
            "replayed_id": replayed["id"],
            "same_record": replayed["id"] == record_id,
            "note": "Service-level idempotency check, not a workflow retry.",
        }

    @app.get("/status")
    def status(demo_session: Session = None):
        if not shared.ready:
            return {
                "ready": False,
                "message": shared.preparing_error or "Preparing the demo database.",
            }
        result: dict[str, Any] = {
            "ready": True,
            "session": demo_session,
            "database": shared.db_name,
            "memories": dict(shared.memories),
            "proposals": list(shared.proposals),
            "runs": list(shared.runs[-10:]),
            "current_policy": None,
            "indexed": None,
        }
        if demo_session not in SESSIONS:
            return result
        reader = identity(demo_session, "agent_a")
        try:
            current = shared.gateway.get_memory(reader, shared.current_memory)
        except AccessDenied:
            current = None
        result["current_policy"] = current
        if current is not None:
            try:
                found = shared.gateway.retrieve_memories(reader, STATUS_QUERY, SUPPLIER)
                result["indexed"] = any(m["memory_id"] == current["memory_id"] for m in found)
            except RuntimeError:
                result["indexed"] = False
        return jsonable_encoder(result)

    return app


app = create_app()
