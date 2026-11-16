from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import rag

@pytest.mark.parametrize('symbol,term',[('==','equality'),('+=','augmented_addition'),('**','exponentiation'),('//','floor_division'),(':=','assignment_expression')])
def test_python_symbols_preserved(symbol,term):
    assert 'python_operator_'+term in rag.terms('Explain '+symbol)
    assert rag.semantic_query('Explain '+symbol)!='Explain '+symbol

def test_identity_not_english_stopword():
    assert 'python_operator_identity' in rag.terms('What is the difference between == and is?')
    assert 'python_operator_identity' in rag.terms('The is operator checks whether references point to the same object.')
    assert 'python_operator_identity' not in rag.terms('What is annual leave?')
    assert rag.semantic_query('What is annual leave?')=='What is annual leave?'

def test_explanation_ranks_above_suggested_question_list():
    question='What is the difference between == and is?'
    by_id={'questions':('Questions to ask your document assistant\n- What is == and is?\n- What is a function?',{}),
           'explanation':('Equality and identity. The == operator compares values. The is operator checks whether two references point to the same object.',{})}
    assert rag.rerank_candidates(question,{'questions':.033,'explanation':.031},by_id)[0]=='explanation'
    assert rag.passage_quality('Practice questions\n- What is Python?\n- What is a function?\nAnswer: A function is reusable behavior.')==1

@pytest.fixture
def evidence():return [{'citation_id':'S1','source':'python.txt','location':'page 1','excerpt':'The == operator compares values. The is operator checks whether references refer to the same object.'}]

@pytest.mark.parametrize('first',['Equality compares values; identity compares objects.','An invented reference [S99].',''])
def test_evidence_retry_is_bounded_and_accounts_for_usage(monkeypatch,evidence,first):
    monkeypatch.setattr(rag.connections,'for_chat',lambda *a:{'provider':'groq','api_key':'fake','model_id':'test'})
    calls=[]
    def complete(config,system,user):
        calls.append((system,user))
        return (first if len(calls)==1 else 'Equality compares values; identity compares object references [S1].',{'input_tokens':10,'output_tokens':5})
    monkeypatch.setattr(rag.llm_gateway,'complete',complete)
    text,usage,cited=rag.generate('groq','Difference between == and is?',evidence,[])
    assert cited and '[S1]' in text and len(calls)==2
    assert usage=={'input_tokens':20,'output_tokens':10}
    assert 'do not add a citation' in calls[1][0]
    assert calls[1][1]==calls[0][1]

def test_unsupported_response_is_never_given_an_automatic_citation(monkeypatch,evidence):
    monkeypatch.setattr(rag.connections,'for_chat',lambda *a:{})
    calls=[]
    def refuse(*a):calls.append(1);return 'The sources do not establish that.',{}
    monkeypatch.setattr(rag.llm_gateway,'complete',refuse)
    text,_,cited=rag.generate('groq','What is the company salary?',evidence,[])
    assert len(calls)==2 and not cited and '[S1]' not in text

def test_invalid_citations_still_rejected_after_retry(monkeypatch,evidence):
    monkeypatch.setattr(rag.connections,'for_chat',lambda *a:{})
    calls=[]
    def wrong(*a):calls.append(1);return 'Wrong [S99].',{}
    monkeypatch.setattr(rag.llm_gateway,'complete',wrong)
    with pytest.raises(HTTPException) as exc:rag.generate('groq','Question',evidence,[])
    assert exc.value.status_code==502 and len(calls)==2

def test_good_first_answer_and_general_chat_do_not_retry(monkeypatch,evidence):
    monkeypatch.setattr(rag.connections,'for_chat',lambda *a:{})
    calls=[]
    def complete(*a):calls.append(1);return ('A supported answer [S1].' if len(calls)==1 else 'A general answer.'),{}
    monkeypatch.setattr(rag.llm_gateway,'complete',complete)
    assert rag.generate('groq','Question',evidence,[])[2]
    assert not rag.generate('groq','Hello',[],[])[2]
    assert len(calls)==2

def test_unchanged_index_skips_resplitting_and_upload_invalidates_cache(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    monkeypatch.setattr(store,'DB_PATH',str(tmp_path/'quality.db'))
    c=TestClient(app)
    c.post('/auth/register',json={'name':'Tester','email':'index@quality.test','password':'Password-12345'})
    c.headers['X-XSRF-TOKEN']=c.cookies['XSRF-TOKEN']
    c.post('/upload',files={'file':('one.txt',b'The == operator compares values. The is operator checks identity.')})
    user=c.get('/auth/me').json()['id'];engine=rag.engine()
    first=engine.index(user)
    original=engine.chunks;calls=[]
    def chunks(owner):calls.append(owner);return original(owner)
    monkeypatch.setattr(engine,'chunks',chunks)
    assert engine.index(user)['added']==0 and not calls
    c.post('/upload',files={'file':('two.txt',b'Generators yield lazily.')})
    assert engine.index(user)['chunks']>first['chunks'] and calls==[user]
    engine.remove(user)
    assert engine.index(user)['added']>0

def test_end_to_end_operator_retrieval_prefers_explanation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    monkeypatch.setattr(store,'DB_PATH',str(tmp_path/'operators.db'))
    c=TestClient(app)
    c.post('/auth/register',json={'name':'Tester','email':'operators@quality.test','password':'Password-12345'})
    c.headers['X-XSRF-TOKEN']=c.cookies['XSRF-TOKEN']
    c.post('/upload',files={'file':('questions.txt',b'Questions to ask your document assistant\n- What is the difference between == and is?\n- How do functions work?')})
    response=c.post('/upload',files={'file':('explanation.txt',b'Equality and identity. The == operator compares values using equality rules. The is operator checks whether references point to the same object.')})
    key=response.json()['document_ids'][0]
    result=c.post('/ask',json={'question':'What is the difference between == and is?','provider':'sources','answer_mode':'documents'}).json()
    assert result['sources'][0]['document_id']==key
    assert 'compares values' in result['sources'][0]['excerpt']
