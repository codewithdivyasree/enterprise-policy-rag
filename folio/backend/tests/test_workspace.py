import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import store

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.sqlite3"))
    client = TestClient(app)
    client.post("/auth/register", json={"name":"Tester","email":"tester@example.com","password":"Test-password-123"})
    client.headers["X-XSRF-TOKEN"] = client.cookies["XSRF-TOKEN"]
    return client

def upload(client, name, text):
    result = client.post("/upload", files={"file": (name, text.encode())})
    assert result.status_code == 200
    return result.json()["document_ids"][0]

def test_upload_preview_search_delete(client):
    key = upload(client, "policy.txt", "Leave requires manager approval.")
    assert client.get("/documents").json()[0]["id"] == key
    assert client.get("/documents/"+key).json()["content"][0]["text"] == "Leave requires manager approval."
    response = client.post("/search", json={"question":"leave policy"})
    assert response.json()["mode"] == "keyword-excerpts"
    assert response.json()["matches"][0]["document_id"] == key
    assert client.delete("/documents/"+key).status_code == 200
    assert client.get("/documents").json() == []
    assert client.get("/documents/"+key).status_code == 404

def test_scope_and_clear(client):
    key = upload(client, "a.txt", "approval by manager")
    other = upload(client, "b.txt", "approval by director")
    matches = client.post("/search", json={"question":"approval","document_ids":[key]}).json()["matches"]
    assert {m["document_id"] for m in matches} == {key}
    assert client.post("/search", json={"question":"bananas"}).json()["matches"] == []
    assert client.post("/search", json={"question":" "}).json()["matches"] == []
    assert client.delete("/documents").status_code == 200
    assert client.get("/documents/"+other).status_code == 404

def test_invalid_zip_and_cors(client):
    assert client.post("/upload", files={"file":("bad.zip",b"bad")}).status_code == 400
    response = client.options("/upload",headers={"Origin":"http://localhost:4200","Access-Control-Request-Method":"POST"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"

def test_sqlite_survives_new_connection(client):
    upload(client, "notes.txt", "persistent content")
    assert store.listing(client.get("/auth/me").json()["id"])[0]["source"] == "notes.txt"
