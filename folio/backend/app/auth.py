"""Local account authentication: scrypt passwords and revocable cookie sessions."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
import threading
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from . import store

router = APIRouter(prefix="/auth", tags=["Accounts"])
COOKIE = "folio_session"
MAX_AGE = 7 * 24 * 3600
SECURE = os.getenv("RAG_SECURE_COOKIES", "false").lower() == "true"
_attempts = {}
_attempt_lock = threading.Lock()

class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

class Registration(Credentials):
    name: str = Field(min_length=1, max_length=80)

def origin_check(request: Request):
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), "http://localhost:4200", "http://127.0.0.1:4200"}
    if origin and origin not in allowed:
        raise HTTPException(403, "Untrusted request origin")

def throttle(request):
    key = request.client.host if request.client else "local"
    now = time.monotonic()
    with _attempt_lock:
        for stale in [k for k,v in _attempts.items() if now-v[0]>60]:
            del _attempts[stale]
        start, count = _attempts.get(key,(now,0))
        if count >= 20 or len(_attempts) > 10000:
            raise HTTPException(429, "Too many attempts. Try again in a minute.")
        _attempts[key]=(start,count+1)

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ":" + key.hex()

def public(user):
    return {key:user[key] for key in ("id","name","email")}

def issue_session(user, request, response):
    old = request.cookies.get(COOKIE)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        if old:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(old),))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                   (digest(token),user["id"],csrf,int(time.time())+MAX_AGE))
    response.set_cookie(COOKIE,token,max_age=MAX_AGE,httponly=True,secure=SECURE,samesite="strict",path="/")
    response.set_cookie("XSRF-TOKEN",csrf,max_age=MAX_AGE,httponly=False,secure=SECURE,samesite="strict",path="/")
    return public(user)

def current_user(request: Request):
    token = request.cookies.get(COOKIE,"")
    with store.connect() as db:
        row = db.execute("""SELECT u.id,u.name,u.email,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id
                          WHERE s.token_hash=? AND s.expires_at>?""",(digest(token),int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401,"Please sign in to continue.")
    if request.method not in {"GET","HEAD","OPTIONS"}:
        origin_check(request)
        if not hmac.compare_digest(request.headers.get("x-xsrf-token",""),row["csrf"]):
            raise HTTPException(403,"Session verification failed. Refresh and try again.")
    return public(row)

@router.post("/register",status_code=201)
def register(payload: Registration, request: Request, response: Response):
    origin_check(request); throttle(request)
    email, name = payload.email.strip().casefold(), payload.name.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email) or not name:
        raise HTTPException(400,"Enter a valid name and email address.")
    if len(payload.password)<10:
        raise HTTPException(400,"Use a password with at least 10 characters.")
    user={"id":str(uuid.uuid4()),"name":name,"email":email}
    password_hash = hash_password(payload.password)
    try:
        with store.connect() as db:
            db.execute("INSERT INTO users(id,name,email,password_hash) VALUES (?,?,?,?)",
                       (user["id"],name,email,password_hash))
    except sqlite3.IntegrityError:
        raise HTTPException(409,"An account with this email already exists. Please sign in.")
    return issue_session(user,request,response)

@router.post("/login")
def login(payload: Credentials, request: Request, response: Response):
    origin_check(request); throttle(request)
    with store.connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=?",(payload.email.strip().casefold(),)).fetchone()
    # Dummy work keeps unknown-account failures comparable to password failures.
    stored = row["password_hash"] if row else hash_password("dummy-comparison-password", "00"*16)
    candidate = hash_password(payload.password,stored.split(":")[0])
    if not row or not hmac.compare_digest(candidate,stored):
        raise HTTPException(401,"Email or password is incorrect.")
    return issue_session(row,request,response)

@router.get("/me")
def me(user=Depends(current_user)):
    return user

@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?",(digest(request.cookies.get(COOKIE,"")),))
    response.delete_cookie(COOKIE,path="/",secure=SECURE,httponly=True,samesite="strict")
    response.delete_cookie("XSRF-TOKEN",path="/",secure=SECURE,samesite="strict")
    return {"logged_out":True}
