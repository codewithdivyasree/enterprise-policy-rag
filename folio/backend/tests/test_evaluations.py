import pytest
from fastapi.testclient import TestClient
from app.main import app, _ask_windows
from app import store,rag
from app.evaluations import reference_f1,retrieval_metrics,aggregate

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB_PATH',str(tmp_path/'eval.db'))
    _ask_windows.clear()
    out=[]
    for email in ['a@eval.test','b@eval.test']:
        c=TestClient(app)
        assert c.post('/auth/register',json={'name':'Evaluator','email':email,'password':'Password-12345'}).status_code==201
        c.headers['X-XSRF-TOKEN']=c.cookies['XSRF-TOKEN'];out.append(c)
    return out

def doc(c):return c.post('/upload',files={'file':('policy.txt',b'Annual leave is twelve days. Approval by manager is required.')}).json()['document_ids'][0]
def connection(c):
    response=c.post('/connections',json={'name':'Evaluation model','provider':'groq','api_key':'test-secret-not-real','model_id':'test-model'})
    assert response.status_code==200 or response.status_code==201
    return response.json()['id']
def make(c,provider='sources',key=None,mode='documents'):
    response=c.post('/evaluations',json={'title':'Policy checks','provider':provider,'answer_mode':mode,'cases':[{'question':'Annual leave?','expected_answer':'Twelve days','expected_document_ids':[key] if key else []}]})
    assert response.status_code==201,response.text
    return c.get('/evaluations/'+response.json()['id']).json()
def url(run):return '/evaluations/'+run['id']
def execute_url(run):return url(run)+'/cases/'+run['cases'][0]['id']+'/execute'

def test_metric_definitions():
    assert reference_f1('Twelve days [S1].','twelve days')==1
    assert reference_f1('a a b','a b')==.8
    assert reference_f1('anything','') is None
    assert reference_f1('unrelated','twelve days')==0
    m=retrieval_metrics(['x','x','right'],['right','missing'])
    assert m=={'hit_at_k':1,'document_recall_at_k':.5,'reciprocal_rank':1/3}
    assert retrieval_metrics(['x']*6+['right'],['right'])['hit_at_k']==0
    assert retrieval_metrics([],[])['hit_at_k'] is None
    empty=aggregate([])
    assert empty['accuracy'] is None and empty['mean_latency_ms'] is None

def test_real_retrieval_source_mode_and_no_duplicate_execution(clients):
    a,_=clients; key=doc(a);run=make(a,key=key)
    assert a.post(execute_url(run),json={}).json()['state']=='complete'
    result=a.get(url(run)).json()
    assert result['metrics']['hit_at_k']==1
    assert result['metrics']['retrieval_scored']==1
    assert result['metrics']['accuracy'] is None
    assert result['metrics']['reference_f1'] is None
    assert result['metrics']['mean_latency_ms']>=0
    assert a.post(execute_url(run),json={}).status_code==409
    review=url(run)+'/cases/'+run['cases'][0]['id']+'/review'
    assert a.put(review,json={'verdict':'correct'}).status_code==400

def test_owner_isolation_validation_and_csrf(clients):
    a,b=clients;key=doc(a);run=make(a,key=key)
    assert b.get('/evaluations').json()==[]
    assert b.get(url(run)).status_code==404
    assert b.post(execute_url(run),json={}).status_code==404
    review=url(run)+'/cases/'+run['cases'][0]['id']+'/review'
    assert b.put(review,json={'verdict':'correct'}).status_code==404
    assert b.post('/evaluations',json={'title':'Invalid scope','cases':[{'question':'test','expected_document_ids':[key]}],'provider':'sources'}).status_code==400
    a.headers.pop('X-XSRF-TOKEN')
    assert a.post(execute_url(run),json={}).status_code==403
    assert TestClient(app).get('/evaluations').status_code==401

def test_review_drives_accuracy_not_reference_overlap(clients,monkeypatch):
    a,_=clients;provider=connection(a);run=make(a,provider=provider)
    def mock_answer(*args,diagnostics):
        diagnostics.update(retrieved_document_ids=[],retrieved_passages=0)
        return {'answer':'A paraphrase','latency_ms':120,'usage':{'input_tokens':10,'output_tokens':5},'mode':'no_context'}
    monkeypatch.setattr(rag,'answer',mock_answer)
    assert a.post(execute_url(run),json={}).json()['state']=='complete'
    assert a.get(url(run)).json()['metrics']['accuracy'] is None
    review=url(run)+'/cases/'+run['cases'][0]['id']+'/review'
    for verdict,expected in [('correct',1),('partial',0),('incorrect',0),('unreviewed',None)]:
        assert a.put(review,json={'verdict':verdict,'note':'Manual review'}).status_code==200
        result=a.get(url(run)).json()
        assert result['metrics']['accuracy']==expected
        assert result['metrics']['reference_f1']==0
        assert result['metrics']['tokens']==15

def test_failures_recorded_without_raw_exception_or_retry(clients,monkeypatch):
    a,_=clients;run=make(a)
    def fail(*a,**kw):raise RuntimeError('a secret provider response')
    monkeypatch.setattr(rag,'answer',fail)
    assert a.post(execute_url(run),json={}).json()['state']=='failed'
    result=a.get(url(run)).json()
    assert result['metrics']['failed']==1 and result['metrics']['success_rate']==0
    assert result['metrics']['hit_at_k'] is None
    assert 'secret provider' not in str(result)
    assert a.post(execute_url(run),json={}).status_code==409

def test_general_has_no_retrieval_score_and_invalid_provider_rejected(clients,monkeypatch):
    a,_=clients;provider=connection(a);run=make(a,provider=provider,mode='general')
    monkeypatch.setattr(rag,'generate',lambda *a:('Twelve days',{},False))
    assert a.post(execute_url(run),json={}).json()['state']=='complete'
    result=a.get(url(run)).json()
    assert result['metrics']['hit_at_k'] is None
    assert result['metrics']['reference_f1']==1
    assert a.post('/evaluations',json={'title':'Bad','provider':'invalid','cases':[{'question':'Hi'}]}).status_code==400

def test_document_scope_and_model_provenance(clients,monkeypatch):
    a,_=clients;key=doc(a);provider=connection(a);run=make(a,provider,key)
    another=doc(a)
    captured={}
    def mock(user,payload,diagnostics):
        captured['ids']=payload.document_ids
        diagnostics.update(retrieved_document_ids=[key],retrieved_passages=1)
        return {'answer':'Twelve days','latency_ms':3,'usage':{}}
    monkeypatch.setattr(rag,'answer',mock)
    assert a.post(execute_url(run),json={}).json()['state']=='complete'
    assert captured['ids']==[key] and another not in captured['ids']
    changed=make(a,provider,key)
    a.put('/connections/'+provider,json={'name':'Evaluation model','provider':'groq','api_key':'','model_id':'changed-model'})
    assert a.post(execute_url(changed),json={}).json()['state']=='failed'
    assert 'connection changed' in a.get(url(changed)).json()['cases'][0]['result']['error']

def test_cannot_execute_case_from_other_run(clients):
    a,_=clients;one=make(a);two=make(a)
    mixed=url(one)+'/cases/'+two['cases'][0]['id']+'/execute'
    assert a.post(mixed,json={}).status_code==404
    assert a.get(url(two)).json()['cases'][0]['state']=='pending'
