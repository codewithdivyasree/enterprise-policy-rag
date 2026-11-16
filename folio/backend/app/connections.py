"""Dynamic, owner-scoped provider connection CRUD and encrypted credential storage."""
import json
import re
import uuid
from datetime import datetime,timezone
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import BaseModel,Field,SecretStr
from . import store,provider_config,llm_gateway
from .auth import current_user

router=APIRouter(prefix="/connections",tags=["API Key Configuration"])
PUBLIC_FIELDS=("id","name","provider","custom_name","model_id","base_url","is_default","created_at","updated_at","last_tested_at","last_test_success","last_test_message")

def now():return datetime.now(timezone.utc).isoformat()

def table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS api_connections (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, provider TEXT NOT NULL, custom_name TEXT NOT NULL DEFAULT '',
        encrypted_key TEXT NOT NULL, model_id TEXT NOT NULL, base_url TEXT NOT NULL DEFAULT '',
        is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        last_tested_at TEXT, last_test_success INTEGER, last_test_message TEXT,
        UNIQUE(owner_id,name))""")
    db.execute("CREATE INDEX IF NOT EXISTS api_connections_owner ON api_connections(owner_id)")

def migrate(user_id):
    if not user_id:return
    legacy=provider_config._saved(user_id)
    if legacy.get("connections_migrated"):return
    config=provider_config.resolve(user_id)
    with store.connect() as db:
        table(db)
        db.execute("BEGIN IMMEDIATE")
        # Check the marker again inside the serialized migration transaction.
        row=db.execute("SELECT settings_json FROM provider_settings WHERE user_id=?",(user_id,)).fetchone()
        prefs=json.loads(row[0]) if row else {}
        if prefs.get("connections_migrated"):return
        existing=db.execute("SELECT count(*) FROM api_connections WHERE owner_id=?",(user_id,)).fetchone()[0]
        if not existing:
            imported=[]
            for provider,label in (("groq","Groq"),("gemini","Google Gemini")):
                key=config[provider+"_api_key"]
                if not key:continue
                connection_id=str(uuid.uuid4());stamp=now()
                encrypted=provider_config._cipher().encrypt(key.encode()).decode()
                db.execute("""INSERT INTO api_connections
                    (id,owner_id,name,provider,encrypted_key,model_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (connection_id,user_id,label+" connection",provider,encrypted,config[provider+"_model"],stamp,stamp))
                imported.append((connection_id,provider))
            preferred=next((key for key,p in imported if p==config["default_provider"]),imported[0][0] if imported else None)
            if preferred:db.execute("UPDATE api_connections SET is_default=1 WHERE id=? AND owner_id=?",(preferred,user_id))
        # Move legacy account credentials into the connection records. Explicit empty
        # values also disable old per-provider .env fallbacks after a record is deleted.
        for provider in ("groq","gemini"):
            prefs[provider+"_api_key"]=""
        prefs["connections_migrated"]=True
        db.execute("INSERT INTO provider_settings VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json",
                   (user_id,json.dumps(prefs)))

def rows(user_id):
    migrate(user_id)
    with store.connect() as db:
        table(db)
        return [dict(row) for row in db.execute("SELECT * FROM api_connections WHERE owner_id=? ORDER BY is_default DESC,created_at,id",(user_id,))]

def public(row):
    data={key:row[key] for key in PUBLIC_FIELDS}
    data["is_default"]=bool(data["is_default"])
    data["last_test_success"]=None if data["last_test_success"] is None else bool(data["last_test_success"])
    data["provider_name"]=row["custom_name"] if row["provider"]=="custom" else llm_gateway.PROVIDERS[row["provider"]]["name"]
    data["key_configured"]=True
    return data

def get_row(user_id,key):
    migrate(user_id)
    with store.connect() as db:
        table(db)
        row=db.execute("SELECT * FROM api_connections WHERE id=? AND owner_id=?",(str(key),user_id)).fetchone()
    if not row:raise HTTPException(404,"Connection not found.")
    return dict(row)

def for_chat(selection,user_id=None):
    selection=str(selection)
    if user_id:
        choices=rows(user_id)
        row=next((r for r in choices if r["id"]==selection),None)
        if row is None and selection in llm_gateway.PROVIDERS:
            row=next((r for r in choices if r["provider"]==selection),None)
        if row is None:raise HTTPException(400,"Add an API key in API Key Configuration or select an available connection.")
        try:key=provider_config._cipher().decrypt(row["encrypted_key"].encode()).decode()
        except Exception as exc:
            raise HTTPException(503,"This saved key could not be read. Edit the connection and save a replacement API key.") from exc
        return {"id":row["id"],"name":row["name"],"provider":row["provider"],"api_key":key,
                "model_id":row["model_id"],"base_url":row["base_url"]}
    # Compatibility for server-side callers without an account context.
    if selection in {"groq","gemini"}:
        config=provider_config.resolve()
        key=config[selection+"_api_key"]
        if not key:raise HTTPException(400,"Add an API key in AI settings.")
        return {"id":selection,"name":llm_gateway.PROVIDERS[selection]["name"],"provider":selection,
                "api_key":key,"model_id":config[selection+"_model"],"base_url":""}
    raise HTTPException(400,"Select a saved API connection.")

def general_enabled(user_id=None):
    prefs=provider_config._saved(user_id)
    import os
    return prefs.get("general_chat_enabled",os.getenv("GENERAL_CHAT_ENABLED","true").strip().lower() in {"true","1","yes"})

def catalog():
    return [{"id":key,**value} for key,value in llm_gateway.PROVIDERS.items()]

def configuration(user_id):
    data=rows(user_id)
    return {"connections":[public(row) for row in data],
            "default_id":next((r["id"] for r in data if r["is_default"]),data[0]["id"] if data else "sources"),
            "general_chat_enabled":general_enabled(user_id),"provider_options":catalog()}

class ConnectionInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    provider:Literal["groq","gemini","openai","anthropic","custom"]
    custom_name:str=Field(default="",max_length=60)
    api_key:SecretStr|None=None
    model_id:str=Field(min_length=1,max_length=160)
    base_url:str=Field(default="",max_length=2048)
    is_default:bool=False

class Preferences(BaseModel):
    general_chat_enabled:bool

def validate(payload,old=None):
    name=payload.name.strip();model=payload.model_id.strip();custom=payload.custom_name.strip()
    if not name:raise HTTPException(400,"Enter a connection name.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,159}",model):
        raise HTTPException(400,"Enter a valid model ID.")
    base=llm_gateway.validate_endpoint(payload.base_url,resolve_dns=True) if payload.provider=="custom" else ""
    if payload.provider=="custom" and not custom:raise HTTPException(400,"Enter the custom provider name.")
    key=payload.api_key.get_secret_value().strip() if payload.api_key else ""
    if key and (len(key)<8 or len(key)>2048 or any(c.isspace() for c in key)):
        raise HTTPException(400,"API keys must be 8-2,048 characters without spaces or line breaks.")
    destination_changed=old and (old["provider"]!=payload.provider or old["base_url"]!=base)
    if not key and (not old or destination_changed):
        raise HTTPException(400,"Enter an API key. Changing provider or endpoint requires entering the key again.")
    encrypted=provider_config._cipher().encrypt(key.encode()).decode() if key else old["encrypted_key"]
    return name,model,custom if payload.provider=="custom" else "",base,encrypted

@router.get("")
def list_connections(response:Response,user=Depends(current_user)):
    response.headers["Cache-Control"]="no-store"
    return configuration(user["id"])

@router.post("/preferences")
def save_preferences(payload:Preferences,response:Response,user=Depends(current_user)):
    migrate(user["id"])
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT settings_json FROM provider_settings WHERE user_id=?",(user["id"],)).fetchone()
        data=json.loads(row[0]) if row else {}
        data["general_chat_enabled"]=payload.general_chat_enabled
        db.execute("INSERT INTO provider_settings VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json",
                   (user["id"],json.dumps(data)))
    response.headers["Cache-Control"]="no-store"
    return configuration(user["id"])

@router.post("",status_code=201)
def create_connection(payload:ConnectionInput,response:Response,user=Depends(current_user)):
    migrate(user["id"]);name,model,custom,base,encrypted=validate(payload)
    import sqlite3
    key=str(uuid.uuid4());stamp=now()
    try:
        with store.connect() as db:
            table(db);db.execute("BEGIN IMMEDIATE")
            count=db.execute("SELECT count(*) FROM api_connections WHERE owner_id=?",(user["id"],)).fetchone()[0]
            if count>=25:raise HTTPException(400,"You can save up to 25 connections per account.")
            default=payload.is_default or count==0
            if default:db.execute("UPDATE api_connections SET is_default=0 WHERE owner_id=?",(user["id"],))
            db.execute("""INSERT INTO api_connections
                (id,owner_id,name,provider,custom_name,encrypted_key,model_id,base_url,is_default,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (key,user["id"],name,payload.provider,custom,encrypted,model,base,int(default),stamp,stamp))
    except sqlite3.IntegrityError:raise HTTPException(409,"That connection name is already used. Choose another name.")
    response.headers["Cache-Control"]="no-store"
    return public(get_row(user["id"],key))

@router.put("/{key}")
def update_connection(key:str,payload:ConnectionInput,response:Response,user=Depends(current_user)):
    old=get_row(user["id"],key)
    name,model,custom,base,encrypted=validate(payload,old)
    import sqlite3
    try:
        with store.connect() as db:
            table(db);db.execute("BEGIN IMMEDIATE")
            # Recheck ownership/existence after validation and before changing defaults.
            if not db.execute("SELECT id FROM api_connections WHERE id=? AND owner_id=?",(key,user["id"])).fetchone():
                raise HTTPException(404,"Connection not found.")
            default=payload.is_default or bool(old["is_default"])
            if default:db.execute("UPDATE api_connections SET is_default=0 WHERE owner_id=?",(user["id"],))
            db.execute("""UPDATE api_connections SET name=?,provider=?,custom_name=?,encrypted_key=?,model_id=?,
                base_url=?,is_default=?,updated_at=?,last_tested_at=NULL,last_test_success=NULL,last_test_message=NULL
                WHERE id=? AND owner_id=?""",
                (name,payload.provider,custom,encrypted,model,base,int(default),now(),key,user["id"]))
    except sqlite3.IntegrityError:raise HTTPException(409,"That connection name is already used. Choose another name.")
    response.headers["Cache-Control"]="no-store"
    return public(get_row(user["id"],key))

@router.delete("/{key}")
def delete_connection(key:str,user=Depends(current_user)):
    get_row(user["id"],key)
    with store.connect() as db:
        table(db);db.execute("BEGIN IMMEDIATE")
        result=db.execute("DELETE FROM api_connections WHERE id=? AND owner_id=?",(key,user["id"]))
        if not result.rowcount:raise HTTPException(404,"Connection not found.")
        if not db.execute("SELECT id FROM api_connections WHERE owner_id=? AND is_default=1",(user["id"],)).fetchone():
            replacement=db.execute("SELECT id FROM api_connections WHERE owner_id=? ORDER BY created_at,id LIMIT 1",(user["id"],)).fetchone()
            if replacement:db.execute("UPDATE api_connections SET is_default=1 WHERE id=? AND owner_id=?",(replacement[0],user["id"]))
    return {"deleted":key}

@router.post("/{key}/default")
def set_default(key:str,user=Depends(current_user)):
    get_row(user["id"],key)
    with store.connect() as db:
        table(db);db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT id FROM api_connections WHERE id=? AND owner_id=?",(key,user["id"])).fetchone():
            raise HTTPException(404,"Connection not found.")
        db.execute("UPDATE api_connections SET is_default=0 WHERE owner_id=?",(user["id"],))
        db.execute("UPDATE api_connections SET is_default=1 WHERE id=? AND owner_id=?",(key,user["id"]))
    return configuration(user["id"])

@router.post("/{key}/models")
def connection_models(key:str,response:Response,user=Depends(current_user)):
    get_row(user["id"],key);config=for_chat(key,user["id"])
    response.headers["Cache-Control"]="no-store"
    return {"models":llm_gateway.available_models(config)}

@router.post("/{key}/test")
def test_connection(key:str,response:Response,user=Depends(current_user)):
    get_row(user["id"],key);config=for_chat(key,user["id"])
    stamp=now()
    try:
        llm_gateway.complete(config,"You are a connection test assistant.","Reply with OK.",max_tokens=64)
        success=True;message="The provider accepted a generation request for this model."
    except HTTPException as exc:
        success=False;message=exc.detail
        with store.connect() as db:
            db.execute("UPDATE api_connections SET last_tested_at=?,last_test_success=0,last_test_message=? WHERE id=? AND owner_id=?",
                       (stamp,message,key,user["id"]))
        raise
    with store.connect() as db:
        db.execute("UPDATE api_connections SET last_tested_at=?,last_test_success=1,last_test_message=? WHERE id=? AND owner_id=?",
                   (stamp,message,key,user["id"]))
    response.headers["Cache-Control"]="no-store"
    return {"success":success,"message":message,"connection":public(get_row(user["id"],key))}
