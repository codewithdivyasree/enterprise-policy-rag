from types import SimpleNamespace
from app import rag,llm_gateway
from app.embeddings import LocalCPUEmbedding

def test_cpu_embedding_uses_bounded_nonspinning_session(monkeypatch):
    embedding=LocalCPUEmbedding();captured={}
    class Options:
        def __init__(self):self.entries={}
        def add_session_config_entry(self,key,value):self.entries[key]=value
    def session(path,**kw):captured.update(kw);return object()
    embedding.ort=SimpleNamespace(SessionOptions=Options,ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'),GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL='optimized'),InferenceSession=session)
    assert embedding.model is embedding.model
    o=captured['sess_options']
    assert captured['providers']==['CPUExecutionProvider']
    assert o.intra_op_num_threads==o.inter_op_num_threads==1
    assert o.execution_mode=='sequential'
    assert o.entries=={'session.intra_op.allow_spinning':'0','session.inter_op.allow_spinning':'0'}

def test_provider_timing_is_request_scoped_and_counts_actual_calls(monkeypatch):
    monkeypatch.setattr(llm_gateway,'_complete',lambda *a:('OK',{}))
    trace=[];token=llm_gateway.CALL_TIMINGS.set(trace)
    try:
        assert llm_gateway.complete({},'system','user')==('OK',{})
        assert len(trace)==1 and trace[0]>=0
    finally:llm_gateway.CALL_TIMINGS.reset(token)
    llm_gateway.complete({},'system','user')
    assert len(trace)==1

def test_query_vector_cache_is_bounded_and_account_scoped():
    engine=rag.engine();calls=[]
    engine.embed=lambda texts:(calls.append(texts) or [[1.0]*384])
    one=engine.query_vector('a','question')
    assert engine.query_vector('a','question')==one and len(calls)==1
    engine.query_vector('b','question');assert len(calls)==2
    assert engine.query_vector.cache_info().maxsize==128

def test_answer_timing_context_is_reset_even_on_failure(monkeypatch):
    seen=[]
    def answer(*args):
        seen.append(rag.REQUEST_TIMINGS.get())
        rag.timed('retrieval_ms',__import__('time').perf_counter())
        return {'answer':'OK'}
    monkeypatch.setattr(rag,'_answer',answer)
    result=rag.answer('one',None)
    assert result['provider_calls']==0 and result['timings_ms']['retrieval_ms']>=0
    assert rag.REQUEST_TIMINGS.get() is None
    def fail(*args):raise ValueError('test')
    monkeypatch.setattr(rag,'_answer',fail)
    try:rag.answer('one',None)
    except ValueError:pass
    assert rag.REQUEST_TIMINGS.get() is None and llm_gateway.CALL_TIMINGS.get() is None
