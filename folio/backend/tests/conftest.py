import pytest
from app import auth

@pytest.fixture(autouse=True)
def reset_auth_throttle():
    auth._attempts.clear()

@pytest.fixture(autouse=True)
def isolate_vectors(tmp_path,monkeypatch):
    import hashlib
    import math
    import re
    from app import rag
    cached=[]
    def test_embed(texts):
        # Deterministic test-only vectors. Production always uses ONNX MiniLM.
        result=[]
        for text in texts:
            vector=[0.0]*384
            for word in re.findall(r"\w+",text.lower()):
                if word in {"what","is","the","a","an","in","of","and","can","how"}:
                    continue
                word={"vacation":"leave","holidays":"leave","holiday":"leave"}.get(word,word)
                i=int(hashlib.sha256(word.encode()).hexdigest()[:8],16)%384
                vector[i]+=1
            norm=math.sqrt(sum(x*x for x in vector)) or 1
            result.append([x/norm for x in vector])
        return result
    def test_engine():
        if not cached:
            cached.append(rag.Engine(path=str(tmp_path/"chroma"),embed=test_embed))
        return cached[0]
    monkeypatch.setattr(rag,"engine",test_engine)
