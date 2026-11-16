import json
import socket
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app import connections,llm_gateway,store,provider_config

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB_PATH",str(tmp_path/"connections.sqlite3"))
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    c=TestClient(app)
    c.post("/auth/register",json={"name":"API owner","email":"owner@example.com","password":"Test-password-123"})
    c.headers["X-XSRF-TOKEN"]=c.cookies["XSRF-TOKEN"]
    return c

def payload(name="My connection",provider="groq",**changes):
    data={"name":name,"provider":provider,"model_id":"test-model","api_key":"test-secret-api-key"}
    data.update(changes)
    return data

def add(client,**changes):
    response=client.post("/connections",json=payload(**changes))
    assert response.status_code==201,response.text
    return response.json()

def test_empty_catalog_and_multiple_records(client):
    config=client.get("/connections").json()
    assert config["connections"]==[] and config["default_id"]=="sources"
    assert {p["id"] for p in config["provider_options"]}=={"groq","gemini","openai","anthropic","custom"}
    one=add(client,name="Work Groq")
    two=add(client,name="Personal Groq",model_id="another-model")
    assert one["is_default"] and not two["is_default"]
    assert one["id"]!=two["id"]
    listing=client.get("/connections")
    assert listing.headers["cache-control"]=="no-store"
    assert len(listing.json()["connections"])==2
    assert "test-secret-api-key" not in listing.text and "encrypted_key" not in listing.text
    assert len(client.get("/rag/status").json()["providers"])==2
    duplicate=client.post("/connections",json=payload(name="Work Groq",is_default=True))
    assert duplicate.status_code==409
    assert client.get("/connections").json()["default_id"]==one["id"]

def test_edit_keeps_key_resets_test_and_default_delete(client,monkeypatch):
    one=add(client,name="First");two=add(client,name="Second")
    monkeypatch.setattr(llm_gateway,"complete",lambda *args,**kwargs:("OK",{}))
    assert client.post("/connections/"+one["id"]+"/test",json={}).json()["success"]
    updated=client.put("/connections/"+one["id"],json=payload(name="First edited",api_key="",model_id="new-model"))
    assert updated.status_code==200
    assert updated.json()["last_test_success"] is None
    user=client.get("/auth/me").json()["id"]
    assert connections.for_chat(one["id"],user)["api_key"]=="test-secret-api-key"
    assert client.post("/connections/"+two["id"]+"/default",json={}).json()["default_id"]==two["id"]
    assert client.delete("/connections/"+two["id"]).status_code==200
    assert client.get("/connections").json()["default_id"]==one["id"]
    client.delete("/connections/"+one["id"])
    assert client.get("/connections").json()["connections"]==[]
    assert client.post("/ask",json={"question":"Hello","provider":one["id"]}).status_code==400

def test_provider_change_requires_key_and_new_protocol(client):
    row=add(client)
    assert client.put("/connections/"+row["id"],json=payload(provider="openai",api_key="")).status_code==400
    result=client.put("/connections/"+row["id"],json=payload(provider="openai",api_key="new-openai-key"))
    assert result.status_code==200 and result.json()["provider"]=="openai"
    user=client.get("/auth/me").json()["id"]
    assert connections.for_chat(row["id"],user)["api_key"]=="new-openai-key"

def test_owner_isolation_and_csrf(client):
    row=add(client)
    other=TestClient(app)
    other.post("/auth/register",json={"name":"Other","email":"other@example.com","password":"Test-password-123"})
    other.headers["X-XSRF-TOKEN"]=other.cookies["XSRF-TOKEN"]
    assert other.get("/connections").json()["connections"]==[]
    assert other.put("/connections/"+row["id"],json=payload()).status_code==404
    assert other.delete("/connections/"+row["id"]).status_code==404
    for suffix in ("test","models","default"):
        assert other.post("/connections/"+row["id"]+"/"+suffix,json={}).status_code==404
    assert TestClient(app).get("/connections").status_code==401
    del client.headers["X-XSRF-TOKEN"]
    assert client.delete("/connections/"+row["id"]).status_code==403

def test_legacy_migration_is_once_and_deleted_credentials_do_not_return(client,monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","legacy-server-key")
    monkeypatch.setenv("GROQ_MODEL","openai/gpt-oss-20b")
    first=client.get("/connections").json()
    assert len(first["connections"])==1
    row=first["connections"][0]
    user=client.get("/auth/me").json()["id"]
    assert connections.for_chat(row["id"],user)["api_key"]=="legacy-server-key"
    assert client.get("/connections").json()["connections"][0]["id"]==row["id"]
    with store.connect() as db:
        encrypted=db.execute("SELECT encrypted_key FROM api_connections").fetchone()[0]
    assert "legacy-server-key" not in encrypted
    assert provider_config.resolve(user)["groq_api_key"]==""
    client.delete("/connections/"+row["id"])
    assert client.get("/connections").json()["connections"]==[]
    assert client.post("/ask",json={"question":"Hello","provider":"groq"}).status_code==400

def test_chat_uses_selected_record_and_returns_human_label(client,monkeypatch):
    one=add(client,name="Work Groq",model_id="work-model")
    two=add(client,name="My Claude",provider="anthropic",model_id="my-claude-model")
    captured=[]
    def complete(config,system,user,**kwargs):
        captured.append(config)
        return "Hello there.",{"input_tokens":3,"output_tokens":3}
    monkeypatch.setattr(llm_gateway,"complete",complete)
    result=client.post("/ask",json={"question":"Hello","provider":two["id"]}).json()
    assert result["mode"]=="general_answer"
    assert result["provider_label"]=="My Claude"
    assert captured[-1]["provider"]=="anthropic"
    assert captured[-1]["model_id"]=="my-claude-model"
    assert "test-secret-api-key" not in json.dumps(result)

def test_test_status_and_models_belong_to_record(client,monkeypatch):
    row=add(client)
    monkeypatch.setattr(llm_gateway,"available_models",lambda config:["model-one","model-two"])
    assert client.post("/connections/"+row["id"]+"/models",json={}).json()["models"]==["model-one","model-two"]
    def fail(*args,**kwargs):raise HTTPException(502,"Provider quota reached (HTTP 429)")
    monkeypatch.setattr(llm_gateway,"complete",fail)
    assert client.post("/connections/"+row["id"]+"/test",json={}).status_code==502
    state=client.get("/connections").json()["connections"][0]
    assert state["last_test_success"] is False and "429" in state["last_test_message"]
    monkeypatch.setattr(llm_gateway,"complete",lambda *args,**kwargs:("OK",{}))
    assert client.post("/connections/"+row["id"]+"/test",json={}).json()["success"]
    assert client.get("/connections").json()["connections"][0]["last_test_success"] is True

def test_preferences_apply_to_general_fallback(client,monkeypatch):
    row=add(client)
    assert client.post("/connections/preferences",json={"general_chat_enabled":False}).status_code==200
    monkeypatch.setattr(llm_gateway,"complete",lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError("No LLM call")))
    assert client.post("/ask",json={"question":"Hello","provider":row["id"]}).json()["mode"]=="no_context"

@pytest.mark.parametrize("url",["http://api.example.com/v1","https://127.0.0.1/v1","https://localhost/v1",
    "https://10.0.0.1/v1","https://[::1]/v1","https://api.example.com:8000/v1",
    "https://user:password@api.example.com/v1","https://api.example.com/v1?key=secret"])
def test_custom_rejects_unsafe_endpoints(client,url):
    result=client.post("/connections",json=payload(provider="custom",custom_name="Custom",base_url=url))
    assert result.status_code==400

def test_custom_provider_public_endpoint_and_endpoint_change(client,monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("1.1.1.1",443))])
    row=add(client,name="Custom provider",provider="custom",custom_name="Example AI",base_url="https://api.example.com/v1")
    assert row["provider_name"]=="Example AI"
    updated=client.put("/connections/"+row["id"],json=payload(name="Custom provider",provider="custom",custom_name="Example AI",base_url="https://other.example.com/v1",api_key=""))
    assert updated.status_code==400
    monkeypatch.setattr(socket,"getaddrinfo",lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("10.0.0.8",443))])
    assert client.post("/connections",json=payload(provider="custom",custom_name="Internal DNS",base_url="https://internal.example.com/v1")).status_code==400

@pytest.mark.parametrize("provider",["openai","anthropic","custom"])
def test_new_provider_adapter_payloads(monkeypatch,provider):
    captured={}
    config={"provider":provider,"api_key":"adapter-test-key","model_id":"test-chat-model","base_url":"https://api.example.com/v1"}
    if provider=="custom":
        monkeypatch.setattr(llm_gateway,"validate_endpoint",lambda value,resolve_dns=False:value)
    class Fake:
        def __init__(self,**kwargs):
            captured["constructor"]=kwargs
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.complete))
            self.messages=SimpleNamespace(create=self.anthropic)
        def complete(self,**kwargs):
            captured["request"]=kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer."))],usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5))
        def anthropic(self,**kwargs):
            captured["request"]=kwargs
            return SimpleNamespace(content=[SimpleNamespace(type="text",text="Answer.")],usage=SimpleNamespace(input_tokens=10,output_tokens=5))
        def __enter__(self):return self
        def __exit__(self,*args):pass
    if provider=="anthropic":
        import anthropic
        monkeypatch.setattr(anthropic,"Anthropic",Fake)
    else:
        import openai
        monkeypatch.setattr(openai,"OpenAI",Fake)
    answer,usage=llm_gateway.complete(config,"System prompt","User question")
    assert answer=="Answer." and usage["output_tokens"]==5
    assert captured["constructor"]["api_key"]=="adapter-test-key"
    assert captured["request"]["model"]=="test-chat-model"
    if provider=="anthropic":
        assert captured["request"]["system"]=="System prompt"
        assert captured["request"]["max_tokens"]==1200
    if provider=="custom":
        assert captured["constructor"]["base_url"]=="https://api.example.com/v1"
        assert captured["constructor"]["http_client"].follow_redirects is False
