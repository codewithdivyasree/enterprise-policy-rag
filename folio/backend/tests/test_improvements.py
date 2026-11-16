import pytest
from fastapi.testclient import TestClient
from app.main import app, _ask_windows
from app import store,rag

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB_PATH",str(tmp_path/"test.db"))
    _ask_windows.clear()
    result=[]
    for email in ["one@test.com","two@test.com"]:
        c=TestClient(app)
        assert c.post("/auth/register",json={"name":"Tester","email":email,"password":"Password-12345"}).status_code==201
        c.headers["X-XSRF-TOKEN"]=c.cookies["XSRF-TOKEN"]
        result.append(c)
    return result

def test_conversation_ownership_crud_and_csrf(clients):
    a,b=clients
    payload={"title":"Private chat","messages":[{"role":"user","text":"hello"}]}
    made=a.post("/conversations",json=payload)
    assert made.status_code==201
    url="/conversations/"+made.json()["id"]
    assert a.get(url).json()["messages"][0]["text"]=="hello"
    assert a.get("/conversations").json()[0]["title"]=="Private chat"
    assert b.get("/conversations").json()==[]
    for action in [lambda:b.get(url),lambda:b.put(url,json=payload),lambda:b.delete(url)]:assert action().status_code==404
    payload["title"]="Renamed"
    assert a.put(url,json=payload).status_code==200
    assert a.get(url).json()["title"]=="Renamed"
    csrf=a.headers.pop("X-XSRF-TOKEN")
    assert a.delete(url).status_code==403
    a.headers["X-XSRF-TOKEN"]=csrf
    assert a.delete(url).status_code==200
    assert a.get(url).status_code==404

def test_general_mode_bypasses_retrieval_and_document_mode_blocks_fallback(clients,monkeypatch):
    a,_=clients
    connection=a.post("/connections",json={"name":"Mock","provider":"groq","api_key":"fake-test-key","model_id":"test"}).json()
    a.post("/upload",files={"file":("doc.txt",b"Leave approval policy")})
    def forbidden(*a):raise AssertionError("General mode must not retrieve documents")
    monkeypatch.setattr(rag.Engine,"retrieve",forbidden)
    monkeypatch.setattr(rag,"generate",lambda *args:("New Delhi",{},False))
    response=a.post("/ask",json={"question":"Capital of India?","provider":connection["id"],"answer_mode":"general"})
    assert response.status_code==200 and response.json()["mode"]=="general_answer"
    monkeypatch.setattr(rag.Engine,"retrieve",lambda *a:[])
    monkeypatch.setattr(rag,"generate",forbidden)
    response=a.post("/ask",json={"question":"Capital of India?","provider":connection["id"],"answer_mode":"documents"})
    assert response.status_code==200 and response.json()["mode"]=="no_context"
    assert a.post("/ask",json={"question":"Hi","answer_mode":"general"}).status_code==400
    assert a.post("/ask",json={"question":"Hi","answer_mode":"invalid"}).status_code==422

def test_ask_rate_limit_is_per_account(clients,monkeypatch):
    a,b=clients
    monkeypatch.setattr(rag,"answer",lambda *a:{"answer":"OK"})
    for _ in range(20):assert a.post("/ask",json={"question":"hi"}).status_code==200
    limited=a.post("/ask",json={"question":"hi"})
    assert limited.status_code==429 and limited.headers["Retry-After"]=="60"
    assert b.post("/ask",json={"question":"hi"}).status_code==200
