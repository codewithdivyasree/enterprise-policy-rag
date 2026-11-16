"""Account-scoped conversation storage. Uses the same database as documents."""
import json
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import store
from .auth import current_user

router=APIRouter(prefix="/conversations",tags=["Conversations"])
class Source(BaseModel):
    document_id:str=Field(max_length=80)
    source:str=Field(max_length=1024)
    location:str=Field(max_length=256)
    excerpt:str=Field(max_length=12000)
    citation_id:str|None=Field(default=None,max_length=20)
    distance:float|None=None
class Message(BaseModel):
    role:Literal["user","assistant"]
    text:str=Field(max_length=16000)
    matches:list[Source]=Field(default_factory=list,max_length=12)
    provider:str|None=Field(default=None,max_length=80)
    providerLabel:str|None=Field(default=None,max_length=160)
    latency:float|None=None
    tokens:int|None=None
class Conversation(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    messages:list[Message]=Field(max_length=100)

def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,messages TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
def owned(db,key,user_id):
    row=db.execute("SELECT * FROM conversations WHERE id=? AND owner_id=?",(key,user_id)).fetchone()
    if row is None:raise HTTPException(404,"Conversation not found")
    return row
@router.get("")
def listing(user=Depends(current_user)):
    with store.connect() as db:
        schema(db)
        return [dict(r) for r in db.execute("SELECT id,title,updated_at FROM conversations WHERE owner_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 100",(user["id"],))]
@router.post("",status_code=201)
def create(payload:Conversation,user=Depends(current_user)):
    key=str(uuid.uuid4())
    with store.connect() as db:
        schema(db)
        if db.execute("SELECT COUNT(*) FROM conversations WHERE owner_id=?",(user["id"],)).fetchone()[0]>=100:
            raise HTTPException(400,"Conversation limit reached. Delete an old conversation first.")
        db.execute("INSERT INTO conversations(id,owner_id,title,messages) VALUES(?,?,?,?)",(key,user["id"],payload.title,json.dumps([m.model_dump() for m in payload.messages])))
    return {"id":key}
@router.get("/{key}")
def get(key:str,user=Depends(current_user)):
    with store.connect() as db:
        schema(db); row=dict(owned(db,key,user["id"]))
    return {"id":row["id"],"title":row["title"],"messages":json.loads(row["messages"])}
@router.put("/{key}")
def update(key:str,payload:Conversation,user=Depends(current_user)):
    with store.connect() as db:
        schema(db);owned(db,key,user["id"])
        db.execute("UPDATE conversations SET title=?,messages=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner_id=?",(payload.title,json.dumps([m.model_dump() for m in payload.messages]),key,user["id"]))
    return {"id":key}
@router.delete("/{key}")
def delete(key:str,user=Depends(current_user)):
    with store.connect() as db:
        schema(db);owned(db,key,user["id"])
        db.execute("DELETE FROM conversations WHERE id=? AND owner_id=?",(key,user["id"]))
    return {"deleted":key}
