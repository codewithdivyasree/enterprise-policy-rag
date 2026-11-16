"""Account-scoped provider settings. Secrets are encrypted, never returned by the API."""
import json
import os
import re
import threading
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, SecretStr, Field
from typing import Literal
from cryptography.fernet import Fernet, InvalidToken
from . import store
from .auth import current_user

router=APIRouter(prefix="/settings",tags=["Provider settings"])
_key_lock=threading.Lock()

def _saved(user_id):
    if not user_id:
        return {}
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS provider_settings (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            settings_json TEXT NOT NULL)""")
        row=db.execute("SELECT settings_json FROM provider_settings WHERE user_id=?",(user_id,)).fetchone()
    return json.loads(row[0]) if row else {}

def _cipher():
    key_path=Path(store.DB_PATH).parent/"provider_settings.key"
    key_path.parent.mkdir(parents=True,exist_ok=True)
    with _key_lock:
        if not key_path.exists():
            # This is a local server encryption key, never a browser-readable setting.
            descriptor=os.open(key_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,"wb") as target:
                target.write(Fernet.generate_key())
        return Fernet(key_path.read_bytes())

def resolve(user_id=None):
    saved=_saved(user_id)
    result={
        "default_provider":saved.get("default_provider",os.getenv("DEFAULT_LLM_PROVIDER","groq")),
        "general_chat_enabled":saved.get("general_chat_enabled",os.getenv("GENERAL_CHAT_ENABLED","true").strip().lower() in {"true","1","yes"}),
    }
    for provider,default in (("groq","openai/gpt-oss-20b"),("gemini","gemini-3.8-flash")):
        field=provider+"_api_key"
        encrypted=saved.get(field)
        if field in saved:
            try:
                key=_cipher().decrypt(encrypted.encode()).decode() if encrypted else ""
            except (InvalidToken,ValueError,OSError) as exc:
                raise HTTPException(503,"Saved API keys could not be read. Restore the provider_settings.key backup or save a replacement key in AI settings.") from exc
            source="account" if key else "none"
        else:
            key=os.getenv(provider.upper()+"_API_KEY","").strip()
            source="server" if key else "none"
        result[field]=key
        result[provider+"_model"]=saved.get(provider+"_model",os.getenv(provider.upper()+"_MODEL",default))
        result[provider+"_key_source"]=source
    return result

def public_providers(user_id=None):
    config=resolve(user_id)
    return [{"id":p,"name":name,"model":config[p+"_model"],
             "configured":bool(config[p+"_api_key"]),"key_source":config[p+"_key_source"]}
            for p,name in (("groq","Groq"),("gemini","Google Gemini"))]

def public_config(user_id):
    config=resolve(user_id)
    return {"default_provider":config["default_provider"],
            "general_chat_enabled":config["general_chat_enabled"],
            "providers":public_providers(user_id)}

class UpdateSettings(BaseModel):
    groq_api_key: SecretStr|None=None
    gemini_api_key: SecretStr|None=None
    remove_groq_key: bool=False
    remove_gemini_key: bool=False
    groq_model: str|None=Field(default=None,max_length=160)
    gemini_model: str|None=Field(default=None,max_length=160)
    default_provider: Literal["groq","gemini"]|None=None
    general_chat_enabled: bool|None=None

class ProviderAction(BaseModel):
    provider: Literal["groq","gemini"]

def provider_error(provider,exc):
    code=getattr(exc,"status_code",None) or getattr(exc,"code",None)
    name="Groq" if provider=="groq" else "Google Gemini"
    reasons={
        400:"rejected this request. Check the API key and the selected model.",
        401:"did not accept the API key. Save a valid key in AI settings.",
        403:"denied access. Check the API key restrictions and project permissions.",
        404:"could not find or access the selected model. Load available models and choose a supported text model.",
        429:"quota or rate limit was reached. Check your provider usage and billing, then retry.",
        500:"reported a server error. Try again shortly.",
        502:"reported a gateway error. Try again shortly.",
        503:"is temporarily unavailable. Try again shortly.",
        504:"timed out before completing the request. Retry or select another available model.",
    }
    message=reasons.get(code,"could not complete the connection. Check internet/proxy access and retry.")
    # Provider bodies may contain credentials or request content; never echo them.
    return HTTPException(502,f"{name} {message}"+(f" (HTTP {code})" if isinstance(code,int) else ""))

@router.get("")
def get_settings(response:Response,user=Depends(current_user)):
    response.headers["Cache-Control"]="no-store"
    return public_config(user["id"])

@router.post("")
def save_settings(payload:UpdateSettings,response:Response,user=Depends(current_user)):
    # Validate all fields before changing anything.
    changes={}
    for provider in ("groq","gemini"):
        secret=getattr(payload,provider+"_api_key")
        value=secret.get_secret_value().strip() if secret else ""
        remove=getattr(payload,"remove_"+provider+"_key")
        if remove and value:
            raise HTTPException(400,"Choose either a replacement key or Remove saved key.")
        if value and (len(value)<8 or len(value)>2048 or any(c.isspace() for c in value)):
            raise HTTPException(400,"API keys must be 8-2,048 characters without spaces or line breaks.")
        if remove:
            changes[provider+"_api_key"]=""
        elif value:
            changes[provider+"_api_key"]=_cipher().encrypt(value.encode()).decode()
        model=getattr(payload,provider+"_model")
        if model is not None:
            model=model.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,159}",model):
                raise HTTPException(400,"Enter a valid non-empty model ID.")
            changes[provider+"_model"]=model
    for field in ("default_provider","general_chat_enabled"):
        value=getattr(payload,field)
        if value is not None:
            changes[field]=value
    # Merge under one SQLite write transaction to avoid lost simultaneous updates.
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS provider_settings (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            settings_json TEXT NOT NULL)""")
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT settings_json FROM provider_settings WHERE user_id=?",(user["id"],)).fetchone()
        saved=json.loads(row[0]) if row else {}
        saved.update(changes)
        db.execute("INSERT INTO provider_settings VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json",
                   (user["id"],json.dumps(saved)))
    response.headers["Cache-Control"]="no-store"
    return public_config(user["id"])

@router.post("/models")
def available_models(payload:ProviderAction,response:Response,user=Depends(current_user)):
    config=resolve(user["id"])
    key=config[payload.provider+"_api_key"]
    if not key:
        raise HTTPException(400,"Save an API key for this provider first.")
    try:
        if payload.provider=="groq":
            from groq import Groq
            with Groq(api_key=key,timeout=20,max_retries=0) as client:
                models=sorted(m.id for m in client.models.list().data)
        else:
            from google import genai
            from google.genai import types
            with genai.Client(api_key=key,http_options=types.HttpOptions(timeout=20000)) as client:
                models=[]
                for model in client.models.list():
                    if "generateContent" in (model.supported_actions or []):
                        models.append(model.name.removeprefix("models/"))
                    if len(models)>=200:
                        break
                models.sort()
    except Exception as exc:
        raise provider_error(payload.provider,exc) from exc
    response.headers["Cache-Control"]="no-store"
    return {"provider":payload.provider,"models":models,
            "note":"Model availability does not guarantee chat support. Test the selected model before using it."}

@router.post("/test")
def test_provider(payload:ProviderAction,response:Response,user=Depends(current_user)):
    config=resolve(user["id"])
    key=config[payload.provider+"_api_key"]
    model=config[payload.provider+"_model"]
    if not key:
        raise HTTPException(400,"Save an API key for this provider first.")
    try:
        if payload.provider=="groq":
            from groq import Groq
            with Groq(api_key=key,timeout=30,max_retries=0) as client:
                client.chat.completions.create(model=model,
                    messages=[{"role":"user","content":"Reply with OK."}],max_completion_tokens=64)
        else:
            from google import genai
            from google.genai import types
            with genai.Client(api_key=key,http_options=types.HttpOptions(timeout=30000)) as client:
                client.models.generate_content(model=model,contents="Reply with OK.",
                    config=types.GenerateContentConfig(max_output_tokens=64))
    except Exception as exc:
        raise provider_error(payload.provider,exc) from exc
    response.headers["Cache-Control"]="no-store"
    return {"success":True,"message":"The provider accepted a generation request for this model.",
            "provider":payload.provider,"model":model}
