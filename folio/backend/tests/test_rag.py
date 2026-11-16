import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import store,rag

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB_PATH",str(tmp_path/"rag.sqlite3"))
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    c=TestClient(app)
    result=c.post("/auth/register",json={"name":"RAG tester","email":"rag@example.com","password":"Test-password-123"})
    assert result.status_code==201
    c.headers["X-XSRF-TOKEN"]=c.cookies["XSRF-TOKEN"]
    return c

def upload(client,name,text):
    response=client.post("/upload",files={"file":(name,text.encode())})
    assert response.status_code==200
    return response.json()["document_ids"][0]

def test_index_idempotency_semantic_sources_and_delete(client):
    key=upload(client,"leave.txt","Annual leave policy: employees receive 12 days leave. Leave requires manager approval.")
    first=client.post("/rag/index").json()
    assert first["chunks"]==1 and first["added"]==1
    assert client.post("/rag/index").json()["added"]==0
    response=client.post("/ask",json={"question":"vacation leave policy","provider":"sources"}).json()
    assert response["mode"]=="semantic_sources"
    assert response["sources"][0]["document_id"]==key
    assert response["sources"][0]["citation_id"]=="S1"
    assert client.delete("/documents/"+key).status_code==200
    assert client.get("/rag/status").json()["chunks"]==0
    assert client.post("/ask",json={"question":"leave","provider":"sources"}).json()["sources"]==[]

def test_scope_and_no_context(client):
    leave=upload(client,"leave.txt","leave policy leave approval")
    salary=upload(client,"salary.txt","salary payroll monthly salary")
    result=client.post("/ask",json={"question":"salary","provider":"sources","document_ids":[leave]}).json()
    assert result["mode"]=="no_context"
    result=client.post("/ask",json={"question":"salary payroll","provider":"sources","document_ids":[salary]}).json()
    assert {s["document_id"] for s in result["sources"]}=={salary}

def test_missing_key_and_validated_grounding(client,monkeypatch):
    upload(client,"policy.txt","leave policy leave approval")
    missing=client.post("/ask",json={"question":"leave","provider":"groq"})
    assert missing.status_code==400 and "API Key Configuration" in missing.json()["detail"]
    monkeypatch.setenv("GROQ_API_KEY","test-not-a-real-key")
    client.post("/connections",json={"name":"Test connection","provider":"groq","api_key":"test-not-a-real-key","model_id":"test-model"})
    monkeypatch.setattr(rag,"generate",lambda *a:("Leave requires approval [S1].",{"input_tokens":10,"output_tokens":7},True))
    result=client.post("/ask",json={"question":"leave policy","provider":"groq"}).json()
    assert result["mode"]=="rag_answer"
    assert result["citation_check"]=="valid_ids"
    assert result["usage"]["output_tokens"]==7

def test_reject_invalid_provider_and_auth(client):
    assert client.post("/ask",json={"question":"test","provider":"unknown"}).status_code==422
    assert client.post("/ask",json={"question":" ","provider":"sources"}).status_code==422
    anon=TestClient(app)
    assert anon.post("/ask",json={"question":"test"}).status_code==401
    assert anon.get("/rag/status").status_code==401

def test_long_documents_have_overlapping_chunks(client):
    upload(client,"long.txt","Policy leave approval and yearly review. "*100)
    result=client.post("/rag/index").json()
    assert result["chunks"]>1
    assert client.post("/rag/index").json()["added"]==0

def test_other_account_never_retrieves_vectors(client):
    key=upload(client,"private.txt","private salary payroll")
    client.post("/rag/index")
    other=TestClient(app)
    other.post("/auth/register",json={"name":"Other","email":"other@example.com","password":"Test-password-123"})
    other.headers["X-XSRF-TOKEN"]=other.cookies["XSRF-TOKEN"]
    result=other.post("/ask",json={"question":"salary payroll","provider":"sources","document_ids":[key]}).json()
    assert result["sources"]==[]
    assert other.get("/rag/status").json()["chunks"]==0

def test_index_failure_preserves_sqlite_upload(client,monkeypatch):
    key=upload(client,"notes.txt","leave policy")
    def fail(texts):
        raise RuntimeError("model download blocked")
    monkeypatch.setattr(rag.engine(),"embed",fail)
    assert client.post("/rag/index").status_code==503
    assert client.get("/documents/"+key).status_code==200

@pytest.mark.parametrize("provider",["groq","gemini"])
def test_provider_sdk_payloads_and_citation_ids(monkeypatch,provider):
    from types import SimpleNamespace
    from app.main import HistoryTurn
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setenv("GEMINI_API_KEY","test-key")
    captured={}
    def complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Twelve leave days [S1]."))],
                               usage=SimpleNamespace(prompt_tokens=20,completion_tokens=8))
    def generate_content(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="Twelve leave days [S1].",usage_metadata=SimpleNamespace(prompt_token_count=20,candidates_token_count=8))
    class FakeClient:
        def __init__(self,**kwargs):
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
            self.models=SimpleNamespace(generate_content=generate_content)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    if provider=="groq":
        import groq
        monkeypatch.setattr(groq,"Groq",FakeClient)
    else:
        from google import genai
        monkeypatch.setattr(genai,"Client",FakeClient)
    answer,usage,cited=rag.generate(provider,"How much leave?",
        [{"citation_id":"S1","source":"policy.txt","location":"page 2","excerpt":"Twelve leave days."}],
        [HistoryTurn(role="user",text="What is the policy?")])
    assert answer.endswith("[S1].") and cited and usage["input_tokens"]==20
    content=str(captured)
    assert "Twelve leave days." in content and "page 2" in content
    assert "What is the policy?" in content

def test_invalid_model_citation_rejected(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    import groq
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    class Fake:
        def __init__(self,**kwargs):
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:
                SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Invented [S99]."))],usage=None)))
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(groq,"Groq",Fake)
    with pytest.raises(HTTPException) as caught:
        rag.generate("groq","Question",[{"citation_id":"S1","source":"a","location":"text","excerpt":"Evidence"}],[])
    assert caught.value.status_code==502

def test_uncited_response_is_not_presented_as_grounded(client,monkeypatch):
    upload(client,"leave.txt","leave policy leave approval")
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    def reply(provider,question,matches,history,user_id=None):
        return ("An unsupported document answer.",{},False) if matches else ("I cannot determine your company's policy without supporting information.",{},False)
    monkeypatch.setattr(rag,"generate",reply)
    result=client.post("/ask",json={"question":"leave policy","provider":"groq"}).json()
    assert result["mode"]=="general_answer"
    assert "unsupported document answer" not in result["answer"]
    assert result["sources"]==[]
    assert result["answer"].startswith("**General knowledge**")

def test_legacy_document_migration_does_not_assign_owner(tmp_path,monkeypatch):
    import sqlite3
    db_path=tmp_path/"legacy.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE documents(id TEXT PRIMARY KEY,source TEXT NOT NULL,uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO documents(id,source) VALUES ('old','old.txt')")
    monkeypatch.setattr(store,"DB_PATH",str(db_path))
    assert store.listing("new-user")==[]

def test_short_functions_question_survives_strict_cosine_cutoff(client,monkeypatch):
    key=upload(client,"python.txt",
        "Functions and safe defaults. A function packages reusable behavior. "
        "Parameters are names in the function definition; arguments are values supplied "
        "by the caller. A function without an explicit return value returns None.")
    index=rag.engine()
    index.index(client.get("/auth/me").json()["id"])
    query=index.collection.query
    def distant_results(self,**kwargs):
        result=query(**kwargs)
        result["distances"]=[[1.4 for _ in result["ids"][0]]]
        return result
    monkeypatch.setattr(type(index.collection),"query",distant_results)
    monkeypatch.setenv("RAG_MAX_DISTANCE","0.1")
    response=client.post("/ask",json={"question":"what is functions?","provider":"sources"}).json()
    assert response["mode"]=="semantic_sources"
    assert response["sources"][0]["document_id"]==key
    assert "reusable behavior" in response["sources"][0]["excerpt"]

@pytest.mark.parametrize("question",["what is variables?","what are variables?","Explain a variable"])
def test_plural_and_singular_topic_matching(client,monkeypatch,question):
    upload(client,"python.txt","A variable name refers to an object. Variables do not copy the object.")
    monkeypatch.setenv("RAG_MAX_DISTANCE","0")
    result=client.post("/ask",json={"question":question,"provider":"sources"}).json()
    assert result["sources"]
    assert result["mode"]=="semantic_sources"

def test_unrelated_question_cannot_reuse_old_topic(client):
    from app.main import HistoryTurn
    standalone="What is the capital of India?"
    history=[HistoryTurn(role="user",text="Explain Python functions.")]
    assert rag.retrieval_query(standalone,history)==standalone
    assert "Explain Python functions." in rag.retrieval_query("How do they return values?",history)
    upload(client,"python.txt","A function packages reusable behavior. Functions return values.")
    result=client.post("/ask",json={"question":standalone,"provider":"sources",
        "history":[{"role":"user","text":"Explain Python functions."}]}).json()
    assert result["mode"]=="no_context"

def test_keyword_retrieval_keeps_document_selection_and_owner_filter(client,monkeypatch):
    function_id=upload(client,"functions.txt","A function is reusable behavior. Functions accept arguments.")
    list_id=upload(client,"lists.txt","Lists are ordered mutable collections.")
    monkeypatch.setenv("RAG_MAX_DISTANCE","0")
    response=client.post("/ask",json={"question":"what is functions?",
        "document_ids":[list_id],"provider":"sources"}).json()
    assert response["sources"]==[]
    other=TestClient(app)
    other.post("/auth/register",json={"name":"Other","email":"second@example.com","password":"Test-password-123"})
    other.headers["X-XSRF-TOKEN"]=other.cookies["XSRF-TOKEN"]
    result=other.post("/ask",json={"question":"what is functions?","provider":"sources",
        "document_ids":[function_id]}).json()
    assert result["sources"]==[]

def test_general_question_falls_back_without_false_sources(client,monkeypatch):
    upload(client,"python.txt","A function packages reusable behavior. Functions accept arguments.")
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setenv("RAG_MAX_DISTANCE","0")
    called=[]
    def reply(provider,question,matches,history,user_id=None):
        called.append((provider,question,matches))
        return "The capital of India is New Delhi.",{"input_tokens":8,"output_tokens":8},False
    monkeypatch.setattr(rag,"generate",reply)
    result=client.post("/ask",json={"question":"Which city is the capital of India?","provider":"groq"}).json()
    assert result["mode"]=="general_answer"
    assert result["answer_basis"]=="general_knowledge"
    assert result["sources"]==[]
    assert result["citation_check"]=="not_applicable"
    assert "New Delhi" in result["answer"]
    assert called[0][2]==[]

def test_general_chat_with_no_documents_skips_embeddings(client,monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setattr(rag,"engine",lambda: (_ for _ in ()).throw(AssertionError("No embeddings should be needed")))
    monkeypatch.setattr(rag,"generate",lambda *args:("Hello! How can I help?",{},False))
    result=client.post("/ask",json={"question":"Hello","provider":"groq"}).json()
    assert result["mode"]=="general_answer"
    assert result["answer"].startswith("**General knowledge**")
    assert client.post("/ask",json={"question":"Hello","provider":"sources"}).json()["mode"]=="no_context"

def test_fallback_can_be_disabled(client,monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setenv("GENERAL_CHAT_ENABLED","false")
    monkeypatch.setattr(rag,"generate",lambda *args: (_ for _ in ()).throw(AssertionError("No LLM call expected")))
    result=client.post("/ask",json={"question":"What is the capital of India?","provider":"groq"}).json()
    assert result["mode"]=="no_context"

def test_document_answers_keep_document_basis(client,monkeypatch):
    upload(client,"policy.txt","leave policy leave approval")
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setattr(rag,"generate",lambda *args:("Leave requires approval [S1].",{},True))
    result=client.post("/ask",json={"question":"leave policy","provider":"groq"}).json()
    assert result["mode"]=="rag_answer"
    assert result["answer_basis"]=="documents"
    assert not result["answer"].startswith("**General knowledge**")

@pytest.mark.parametrize("provider",["groq","gemini"])
def test_general_sdk_uses_general_prompt_without_passages(monkeypatch,provider):
    from types import SimpleNamespace
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    monkeypatch.setenv("GEMINI_API_KEY","test-key")
    captured={}
    def complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="New Delhi."))],usage=None)
    def generate_content(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="New Delhi.",usage_metadata=None)
    class FakeClient:
        def __init__(self,**kwargs):
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
            self.models=SimpleNamespace(generate_content=generate_content)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    if provider=="groq":
        import groq
        monkeypatch.setattr(groq,"Groq",FakeClient)
    else:
        from google import genai
        monkeypatch.setattr(genai,"Client",FakeClient)
    answer,usage,cited=rag.generate(provider,"Which city is the capital of India?",[],[])
    assert answer=="New Delhi." and not cited
    payload=str(captured)
    assert "GENERAL KNOWLEDGE mode" in payload
    assert "SOURCE PASSAGES:" not in payload
