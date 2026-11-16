import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import provider_config,store,rag

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB_PATH",str(tmp_path/"settings.sqlite3"))
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    monkeypatch.delenv("GROQ_MODEL",raising=False)
    c=TestClient(app)
    c.post("/auth/register",json={"name":"Settings user","email":"settings@example.com","password":"Test-password-123"})
    c.headers["X-XSRF-TOKEN"]=c.cookies["XSRF-TOKEN"]
    return c

def test_key_encryption_and_no_secret_echo(client):
    secret="gsk_test_secret_that_must_not_be_echoed"
    response=client.post("/settings",json={"groq_api_key":secret,"groq_model":"openai/gpt-oss-20b","default_provider":"groq"})
    assert response.status_code==200,response.text
    assert secret not in response.text
    loaded=client.get("/settings")
    assert loaded.headers["cache-control"]=="no-store"
    assert secret not in loaded.text and "groq_api_key" not in loaded.text
    user=client.get("/auth/me").json()["id"]
    with store.connect() as db:
        stored=db.execute("SELECT settings_json FROM provider_settings WHERE user_id=?",(user,)).fetchone()[0]
    assert secret not in stored
    assert json.loads(stored)["groq_api_key"].startswith("gAAAA")
    assert provider_config.resolve(user)["groq_api_key"]==secret
    assert client.get("/rag/status").json()["providers"][0]["configured"]

def test_blank_keeps_key_and_remove_disables_it(client,monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","server-default-secret")
    client.post("/settings",json={"groq_api_key":"my-account-secret"})
    client.post("/settings",json={"groq_api_key":"","groq_model":"openai/gpt-oss-120b"})
    user=client.get("/auth/me").json()["id"]
    assert provider_config.resolve(user)["groq_api_key"]=="my-account-secret"
    client.post("/settings",json={"remove_groq_key":True})
    assert provider_config.resolve(user)["groq_api_key"]==""
    assert provider_config.resolve(user)["groq_key_source"]=="none"
    assert provider_config.resolve()["groq_api_key"]=="server-default-secret"

def test_preferences_and_keys_are_account_scoped(client):
    client.post("/settings",json={"groq_api_key":"alice-private-key","general_chat_enabled":False})
    other=TestClient(app)
    other.post("/auth/register",json={"name":"Other","email":"other-settings@example.com","password":"Test-password-123"})
    other.headers["X-XSRF-TOKEN"]=other.cookies["XSRF-TOKEN"]
    data=other.get("/settings").json()
    assert not data["providers"][0]["configured"]
    assert data["general_chat_enabled"]
    other.post("/settings",json={"groq_api_key":"bob-private-key"})
    user=client.get("/auth/me").json()["id"]
    assert provider_config.resolve(user)["groq_api_key"]=="alice-private-key"

def test_csrf_auth_and_validation_do_not_echo_keys(client):
    anon=TestClient(app)
    assert anon.get("/settings").status_code==401
    assert anon.post("/settings",json={}).status_code==401
    csrf=client.headers.pop("X-XSRF-TOKEN")
    assert client.post("/settings",json={"groq_api_key":"secret-value"}).status_code==403
    client.headers["X-XSRF-TOKEN"]=csrf
    secret="secret-with-newline\nprivate"
    response=client.post("/settings",json={"groq_api_key":secret})
    assert response.status_code==400 and secret not in response.text
    bad=client.post("/settings",json={"groq_api_key":{"bad":"sensitive"},"default_provider":"invalid"})
    assert bad.status_code==422 and "sensitive" not in bad.text
    assert client.post("/settings",json={"groq_model":""}).status_code==400

def test_runtime_generation_uses_saved_key_without_restart(client,monkeypatch):
    from types import SimpleNamespace
    import groq
    secret="gsk_saved_live_key_for_test"
    client.post("/settings",json={"groq_api_key":secret,"groq_model":"openai/gpt-oss-20b"})
    captured={}
    class Fake:
        def __init__(self,**kwargs):
            captured.update(kwargs)
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="New Delhi."))],usage=None)))
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(groq,"Groq",Fake)
    response=client.post("/ask",json={"question":"What is the capital of India?","provider":"groq"})
    assert response.status_code==200,response.text
    assert response.json()["mode"]=="general_answer"
    assert captured["api_key"]==secret
    assert secret not in response.text

@pytest.mark.parametrize("provider",["groq","gemini"])
def test_models_and_connection_action_use_saved_configuration(client,monkeypatch,provider):
    from types import SimpleNamespace
    secret=provider+"-private-key"
    client.post("/settings",json={provider+"_api_key":secret})
    captured={}
    class Fake:
        def __init__(self,**kwargs):
            captured.update(kwargs)
            self.models=SimpleNamespace(
                list=lambda:[SimpleNamespace(name="models/gemini-text-test",supported_actions=["generateContent"])],
                generate_content=lambda **kw:SimpleNamespace(text="OK"))
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:SimpleNamespace()))
            if provider=="groq":
                self.models.list=lambda:SimpleNamespace(data=[SimpleNamespace(id="openai/gpt-oss-20b")])
        def __enter__(self):return self
        def __exit__(self,*args):pass
    if provider=="groq":
        import groq
        monkeypatch.setattr(groq,"Groq",Fake)
    else:
        from google import genai
        monkeypatch.setattr(genai,"Client",Fake)
    assert client.post("/settings/models",json={"provider":provider}).json()["models"]
    tested=client.post("/settings/test",json={"provider":provider})
    assert tested.status_code==200 and tested.json()["success"]
    assert captured["api_key"]==secret
    assert secret not in tested.text

def test_provider_errors_are_actionable_and_redacted():
    class Error(Exception):
        status_code=404
    error=provider_config.provider_error("groq",Error("super-secret-provider-response"))
    assert "selected model" in error.detail and "HTTP 404" in error.detail
    assert "super-secret" not in error.detail
