import hashlib
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import store

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB_PATH",str(tmp_path/"accounts.sqlite3"))
    return TestClient(app),TestClient(app)

def register(client,email="alice@example.com",name="Alice"):
    result=client.post("/auth/register",json={"name":name,"email":email,"password":"Account-password-123"})
    assert result.status_code==201, result.text
    client.headers["X-XSRF-TOKEN"]=client.cookies["XSRF-TOKEN"]
    return result.json()

def test_register_me_logout_and_revocation(clients):
    client, stranger=clients
    assert client.get("/auth/me").status_code==401
    user=register(client)
    token=client.cookies["folio_session"]
    assert client.get("/auth/me").json()==user
    assert "password" not in str(user)
    with store.connect() as db:
        saved=db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert saved!="Account-password-123" and ":" in saved
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0]==hashlib.sha256(token.encode()).hexdigest()
    assert client.post("/auth/logout").status_code==200
    assert client.get("/documents").status_code==401
    stranger.cookies.set("folio_session",token)
    assert stranger.get("/auth/me").status_code==401

def test_login_and_duplicates(clients):
    client,_=clients
    register(client)
    client.post("/auth/logout")
    assert client.post("/auth/login",json={"email":"alice@example.com","password":"wrong"}).status_code==401
    result=client.post("/auth/login",json={"email":"ALICE@example.com","password":"Account-password-123"})
    assert result.status_code==200
    assert client.get("/auth/me").json()["name"]=="Alice"
    assert client.post("/auth/register",json={"name":"Again","email":"ALICE@example.com","password":"Account-password-123"}).status_code==409

def test_cross_account_documents_search_clear(clients):
    alice,bob=clients
    register(alice);register(bob,"bob@example.com","Bob")
    key=alice.post("/upload",files={"file":("private.txt",b"Private launch details")}).json()["document_ids"][0]
    assert bob.get("/documents").json()==[]
    assert bob.get("/documents/"+key).status_code==404
    assert bob.delete("/documents/"+key).status_code==404
    assert bob.post("/search",json={"question":"launch","document_ids":[key]}).json()["matches"]==[]
    assert bob.delete("/documents").status_code==200
    assert alice.get("/documents/"+key).status_code==200

def test_csrf_origin_and_expiration(clients):
    client,_=clients
    register(client)
    del client.headers["X-XSRF-TOKEN"]
    assert client.delete("/documents").status_code==403
    client.headers["X-XSRF-TOKEN"]=client.cookies["XSRF-TOKEN"]
    assert client.post("/auth/logout",headers={"Origin":"https://evil.example"}).status_code==403
    with store.connect() as db:
        db.execute("UPDATE sessions SET expires_at=?",(int(time.time())-1,))
    assert client.get("/documents").status_code==401

def test_registration_validation_and_unauthenticated_routes(clients):
    client,_=clients
    for path in ["/documents","/documents/not-found"]:
        assert client.get(path).status_code==401
    assert client.post("/upload",files={"file":("x.txt",b"x")}).status_code==401
    assert client.post("/search",json={"question":"test"}).status_code==401
    assert client.post("/auth/register",json={"name":"A","email":"bad","password":"password12345"}).status_code==400
    assert client.post("/auth/register",json={"name":"A","email":"a@b.com","password":"short"}).status_code==400
