"""Reproducible, owner-scoped RAG evaluation with explicit metric definitions."""
import json
import math
import re
import time
import uuid
from collections import Counter
from types import SimpleNamespace
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import store, rag, connections
from .auth import current_user

router=APIRouter(prefix='/evaluations',tags=['Evaluations'])
class CaseInput(BaseModel):
    question:str=Field(min_length=1,max_length=2000,pattern=r'.*\S.*')
    expected_answer:str=Field(default='',max_length=8000)
    expected_document_ids:list[str]=Field(default_factory=list,max_length=20)
class RunInput(BaseModel):
    title:str=Field(min_length=1,max_length=100,pattern=r'.*\S.*')
    provider:str=Field(min_length=1,max_length=80)
    answer_mode:Literal['documents','auto','general']='documents'
    cases:list[CaseInput]=Field(min_length=1,max_length=30)
class ReviewInput(BaseModel):
    verdict:Literal['correct','partial','incorrect','unreviewed']
    note:str=Field(default='',max_length=2000)

def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS evaluation_runs (
       id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       title TEXT NOT NULL, provider TEXT NOT NULL, answer_mode TEXT NOT NULL,
       snapshot TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS evaluation_cases (
       id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
       position INTEGER NOT NULL, input_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
       result_json TEXT, verdict TEXT NOT NULL DEFAULT 'unreviewed', note TEXT NOT NULL DEFAULT '');''')
def owned(db,run_id,user_id):
    row=db.execute('SELECT * FROM evaluation_runs WHERE id=? AND owner_id=?',(run_id,user_id)).fetchone()
    if row is None:raise HTTPException(404,'Evaluation run not found')
    return dict(row)
def config_snapshot(provider,user_id):
    if provider=='sources':return {'name':'Semantic sources','provider':'sources','model_id':'all-MiniLM-L6-v2'}
    c=connections.for_chat(provider,user_id)
    return {key:c.get(key,'') for key in ['name','provider','model_id','base_url']}
def reference_f1(answer,expected):
    """Token overlap, not factual correctness; paraphrases can score poorly."""
    if not expected.strip():return None
    def tokens(text):
        text=re.sub(r'^\*\*General knowledge\*\*\s*','',text)
        return re.findall(r'\w+',re.sub(r'\[S\d+\]','',text).casefold())
    a,b=tokens(answer),tokens(expected)
    if not a or not b:return 0.0
    overlap=sum((Counter(a)&Counter(b)).values())
    precision,recall=overlap/len(a),overlap/len(b)
    return round(2*precision*recall/(precision+recall),4) if overlap else 0.0

def retrieval_metrics(retrieved,expected):
    if not expected:return {'hit_at_k':None,'document_recall_at_k':None,'reciprocal_rank':None}
    ranked=retrieved[:6]; target=set(expected)
    ranks=[i+1 for i,key in enumerate(ranked) if key in target]
    return {'hit_at_k':int(bool(ranks)),'document_recall_at_k':len(set(ranked)&target)/len(target),
            'reciprocal_rank':1/min(ranks) if ranks else 0.0}
def case_public(row):
    return {'id':row['id'],'position':row['position'],**json.loads(row['input_json']),
            'state':row['state'],'result':json.loads(row['result_json']) if row['result_json'] else None,
            'verdict':row['verdict'],'note':row['note']}
def aggregate(cases):
    complete=[c for c in cases if c['state']=='complete']
    failed=[c for c in cases if c['state']=='failed']
    reviewed=[c for c in complete if c['verdict']!='unreviewed']
    def average(key):
        vals=[c['result'][key] for c in complete if c['result'].get(key) is not None]
        return round(sum(vals)/len(vals),4) if vals else None
    lat=sorted(c['result']['latency_ms'] for c in complete)
    return {'total':len(cases),'completed':len(complete),'failed':len(failed),
            'pending':sum(c['state']=='pending' for c in cases),'running':sum(c['state']=='running' for c in cases),
            'reviewed':len(reviewed),'correct':sum(c['verdict']=='correct' for c in reviewed),
            'partial':sum(c['verdict']=='partial' for c in reviewed),
            'accuracy':sum(c['verdict']=='correct' for c in reviewed)/len(reviewed) if reviewed else None,
            'review_coverage':len(reviewed)/len(complete) if complete else None,
            'success_rate':len(complete)/(len(complete)+len(failed)) if complete or failed else None,
            'mean_latency_ms':round(sum(lat)/len(lat)) if lat else None,
            'p95_latency_ms':lat[math.ceil(.95*len(lat))-1] if lat else None,
            'hit_at_k':average('hit_at_k'),'document_recall_at_k':average('document_recall_at_k'),
            'mrr':average('reciprocal_rank'),'reference_f1':average('reference_f1'),
            'retrieval_scored':sum(c['result'].get('hit_at_k') is not None for c in complete),
            'reference_scored':sum(c['result'].get('reference_f1') is not None for c in complete),
            'tokens':sum(sum(c['result'].get('usage',{}).get(k,0) or 0 for k in ['input_tokens','output_tokens']) for c in complete)}
@router.get('')
def listing(user=Depends(current_user)):
    with store.connect() as db:
        schema(db)
        return [dict(r) for r in db.execute('SELECT id,title,created_at,provider,answer_mode FROM evaluation_runs WHERE owner_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50',(user['id'],))]
@router.post('',status_code=201)
def create(payload:RunInput,user=Depends(current_user)):
    snapshot=config_snapshot(payload.provider,user['id'])
    if payload.answer_mode=='general' and payload.provider=='sources':raise HTTPException(400,'General chat needs an AI connection')
    document_map={d['id']:d['source'] for d in store.listing(user['id'])}
    for c in payload.cases:
        if any(key not in document_map for key in c.expected_document_ids):raise HTTPException(400,'Expected sources must be documents in your account')
        if payload.answer_mode=='general' and c.expected_document_ids:raise HTTPException(400,'General chat does not retrieve documents. Remove expected sources.')
    snapshot['documents']=document_map
    run_id=str(uuid.uuid4())
    with store.connect() as db:
        schema(db)
        if db.execute('SELECT COUNT(*) FROM evaluation_runs WHERE owner_id=?',(user['id'],)).fetchone()[0]>=50:raise HTTPException(400,'Evaluation run limit reached (50)')
        db.execute('INSERT INTO evaluation_runs(id,owner_id,title,provider,answer_mode,snapshot) VALUES(?,?,?,?,?,?)',(run_id,user['id'],payload.title,payload.provider,payload.answer_mode,json.dumps(snapshot)))
        db.executemany('INSERT INTO evaluation_cases(id,run_id,position,input_json) VALUES(?,?,?,?)',[(str(uuid.uuid4()),run_id,i,json.dumps(c.model_dump())) for i,c in enumerate(payload.cases)])
    return {'id':run_id}
@router.get('/{run_id}')
def get(run_id:str,user=Depends(current_user)):
    with store.connect() as db:
        schema(db);run=owned(db,run_id,user['id'])
        cases=[case_public(r) for r in db.execute('SELECT * FROM evaluation_cases WHERE run_id=? ORDER BY position',(run_id,))]
    run.pop('owner_id');run['snapshot']=json.loads(run['snapshot']);run['cases']=cases;run['metrics']=aggregate(cases)
    return run
@router.post('/{run_id}/cases/{case_id}/execute')
def execute(run_id:str,case_id:str,user=Depends(current_user)):
    # Commit the claim before calling a provider: repeated clicks cannot bill twice.
    with store.connect() as db:
        schema(db);run=owned(db,run_id,user['id'])
        row=db.execute('SELECT * FROM evaluation_cases WHERE id=? AND run_id=?',(case_id,run_id)).fetchone()
        if row is None:raise HTTPException(404,'Evaluation case not found')
        claimed=db.execute("UPDATE evaluation_cases SET state='running' WHERE id=? AND state='pending'",(case_id,)).rowcount
        if not claimed:raise HTTPException(409,'This case has already started. Refresh the run; it will not execute twice.')
        case=json.loads(row['input_json'])
    started=time.perf_counter();diagnostics={}
    try:
        old=json.loads(run['snapshot']);current=config_snapshot(run['provider'],user['id'])
        if any(old.get(k)!=current.get(k) for k in current):raise HTTPException(400,'The connection changed. Create a new run to preserve model provenance.')
        existing={d['id'] for d in store.listing(user['id'])}
        if run['answer_mode']!='general' and any(key not in existing for key in old['documents']):raise HTTPException(400,'An expected document was deleted. Create a new run with current sources.')
        # Same limiter and answer pipeline as ordinary chat; no history contaminates cases.
        from .main import check_ask_limit
        check_ask_limit(user['id'])
        result=rag.answer(user['id'],SimpleNamespace(provider=run['provider'],answer_mode=run['answer_mode'],question=case['question'],history=[],document_ids=list(old['documents'])),diagnostics=diagnostics)
        answer=result['answer']
        retrieved=diagnostics.get('retrieved_document_ids',[])
        metrics=retrieval_metrics(retrieved,case['expected_document_ids'])
        result={**result,**metrics,'retrieved_document_ids':list(dict.fromkeys(retrieved)),
                'retrieved_passages':diagnostics.get('retrieved_passages',0),
                'reference_f1':reference_f1(answer,case['expected_answer']) if run['provider']!='sources' else None,
                'k':6}
        state='complete'
    except Exception as exc:
        result={'error':exc.detail if isinstance(exc,HTTPException) and isinstance(exc.detail,str) else 'Evaluation failed. Check the connection and document preparation.',
                'latency_ms':round((time.perf_counter()-started)*1000)}
        state='failed'
    with store.connect() as db:
        db.execute('UPDATE evaluation_cases SET state=?,result_json=? WHERE id=? AND run_id=?',(state,json.dumps(result),case_id,run_id))
    return {'state':state}
@router.put('/{run_id}/cases/{case_id}/review')
def review(run_id:str,case_id:str,payload:ReviewInput,user=Depends(current_user)):
    with store.connect() as db:
        schema(db);run=owned(db,run_id,user['id'])
        row=db.execute('SELECT state FROM evaluation_cases WHERE id=? AND run_id=?',(case_id,run_id)).fetchone()
        if row is None:raise HTTPException(404,'Evaluation case not found')
        if row['state']!='complete' or run['provider']=='sources':raise HTTPException(400,'Only completed AI answers can be reviewed')
        db.execute('UPDATE evaluation_cases SET verdict=?,note=? WHERE id=? AND run_id=?',(payload.verdict,payload.note,case_id,run_id))
    return {'saved':True}
