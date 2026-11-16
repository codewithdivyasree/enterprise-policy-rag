import re
import time
import threading
from collections import defaultdict, deque
import os
from dotenv import load_dotenv
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from starlette.concurrency import run_in_threadpool
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from .ingestion import MAX_UPLOAD, ingest
from . import store, rag
from .auth import router as auth_router, current_user
from .provider_config import router as settings_router
from .connections import router as connections_router
from .conversations import router as conversations_router

app = FastAPI(title="RAG Assistant", version="1.1.0")
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(connections_router)
app.include_router(conversations_router)

@app.exception_handler(RequestValidationError)
async def safe_validation_errors(request,exc):
    # Do not reflect secret input values in validation responses.
    return JSONResponse(status_code=422,content={"detail":[
        {"loc":list(item["loc"]),"msg":item["msg"],"type":item["type"]} for item in exc.errors()
    ]})
app.add_middleware(CORSMiddleware, allow_origins=[
    "http://localhost:4200", "http://127.0.0.1:4200"
], allow_credentials=True, allow_methods=["GET","POST","PUT","DELETE"], allow_headers=["Content-Type","X-XSRF-TOKEN"])

@app.get("/health")
def health():
    return {"status": "ok", "stage": "rag", "answer_mode": "semantic-retrieval-and-llm"}

def save_upload(name, data, owner_id):
    result = ingest(name, data)
    result["document_ids"] = store.add(result["documents"], owner_id)
    # Text is fetched on demand through /documents/{id}.
    del result["documents"]
    return result

@app.post("/upload")
async def upload(file: UploadFile, user=Depends(current_user)):
    try:
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Upload exceeds 10 MiB")
        return await run_in_threadpool(save_upload, file.filename or "unnamed", data, user["id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()

@app.get("/documents")
def documents(user=Depends(current_user)):
    return store.listing(user["id"])

@app.get("/documents/{key}")
def document(key: str, user=Depends(current_user)):
    result = store.get(key, user["id"])
    if result is None:
        raise HTTPException(404, "Document not found")
    return result

@app.delete("/documents/{key}")
def delete_document(key: str, user=Depends(current_user)):
    if not store.delete(key, user["id"]):
        raise HTTPException(404, "Document not found")
    try:
        rag.engine().remove(user["id"],key)
    except Exception:
        pass  # Deleted SQLite records cannot be retrieved; next index sync removes stale vectors.
    return {"deleted": key}

@app.delete("/documents")
def clear_documents(user=Depends(current_user)):
    store.clear(user["id"])
    try:
        rag.engine().remove(user["id"])
    except Exception:
        pass
    return {"cleared": True}

class Search(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] = Field(default_factory=list, max_length=500)

@app.post("/search")
def search(payload: Search, user=Depends(current_user)):
    terms = set(re.findall(r"\w+", payload.question.casefold())) - {
        "a","an","the","is","are","what","how","to","of","in","and","does","do","can","i","my"}
    if not terms:
        return {"mode":"keyword-excerpts", "matches":[]}
    matches = []
    for doc in store.listing(user["id"]):
        if payload.document_ids and doc["id"] not in payload.document_ids:
            continue
        detail = store.get(doc["id"], user["id"])
        if detail is None:
            continue
        for unit in detail["content"]:
            text = unit["text"]
            # Small overlapping windows produce readable, exact source excerpts.
            for offset in range(0, len(text), 650):
                excerpt = text[offset:offset+900]
                words = set(re.findall(r"\w+", excerpt.casefold()))
                score = len(terms & words)
                if score:
                    matches.append({"document_id":doc["id"], "source":doc["source"],
                                    "location":unit["location"], "excerpt":excerpt, "score":score})
    matches.sort(key=lambda x:x["score"], reverse=True)
    return {"mode":"keyword-excerpts", "matches":matches[:5]}

class HistoryTurn(BaseModel):
    role: Literal["user","assistant"]
    text: str = Field(max_length=4000)

class Ask(BaseModel):
    answer_mode: Literal["auto","documents","general"] = "auto"
    question: str = Field(min_length=1,max_length=2000,pattern=r".*\S.*")
    provider: str = Field(default="sources",max_length=80,pattern=r"^(?:sources|groq|gemini|openai|anthropic|custom|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
    document_ids: list[str] = Field(default_factory=list,max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list,max_length=6)

@app.get("/rag/status")
def rag_status(user=Depends(current_user)):
    return rag.status(user["id"])

@app.post("/rag/index")
def index_documents(user=Depends(current_user)):
    try:
        return rag.engine().index(user["id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,"Document indexing failed. Check storage permissions and embedding dependencies, then retry.") from exc

_ask_lock=threading.Lock()
_ask_windows=defaultdict(deque)
def check_ask_limit(user_id):
    now=time.monotonic()
    with _ask_lock:
        for key in list(_ask_windows):
            while _ask_windows[key] and _ask_windows[key][0]<now-60:_ask_windows[key].popleft()
            if not _ask_windows[key]:del _ask_windows[key]
        window=_ask_windows[user_id]
        if len(window)>=20:
            raise HTTPException(429,"Too many questions. Wait a minute and retry.",headers={"Retry-After":"60"})
        window.append(now)

@app.post("/ask")
def ask(payload: Ask,user=Depends(current_user)):
    check_ask_limit(user["id"])
    try:
        return rag.answer(user["id"],payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,"Semantic retrieval is unavailable. Prepare documents and retry.") from exc

# npm run build places the Angular app here. API routes must be declared first.
frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "browser"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
