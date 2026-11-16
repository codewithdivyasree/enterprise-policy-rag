"""Provider adapters shared by chat and per-connection tests."""
import time
from contextvars import ContextVar
from urllib.parse import urlsplit
import ipaddress
import socket
import httpx
from fastapi import HTTPException

PROVIDERS={
    "groq":{"name":"Groq","protocol":"groq","model_hint":"openai/gpt-oss-20b"},
    "gemini":{"name":"Google Gemini","protocol":"gemini","model_hint":"Model ID from Google AI Studio"},
    "openai":{"name":"OpenAI","protocol":"openai","model_hint":"Chat model ID from your OpenAI account"},
    "anthropic":{"name":"Claude (Anthropic)","protocol":"anthropic","model_hint":"Claude model ID from your Anthropic account"},
    "custom":{"name":"Other / OpenAI-compatible","protocol":"openai","model_hint":"Model ID supported by your endpoint"},
}

def validate_endpoint(value,resolve_dns=False):
    try:
        url=urlsplit((value or "").strip())
        if url.scheme!="https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.port not in (None,443):
            raise ValueError()
        host=url.hostname.casefold()
        if host=="localhost" or host.endswith((".localhost",".local",".internal")):
            raise ValueError()
        try:
            address=ipaddress.ip_address(host)
        except ValueError:
            address=None
        if address is not None and not address.is_global:
            raise ValueError()
        if resolve_dns:
            addresses={item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
                raise ValueError()
        path=url.path.rstrip("/")
        return f"https://{url.netloc}{path}"
    except (ValueError,TypeError,OSError) as exc:
        raise HTTPException(400,"Use a public HTTPS API base URL on port 443, without credentials, query parameters or fragments. Local/private addresses are not supported.") from exc

def request_error(provider,exc):
    code=getattr(exc,"status_code",None) or getattr(exc,"code",None)
    name=PROVIDERS.get(provider,{}).get("name","Provider")
    reasons={
        400:"rejected the request. Check the API key, model ID and endpoint compatibility.",
        401:"did not accept this API key. Edit the connection and save a valid key.",
        403:"denied access. Check key restrictions and project permissions.",
        404:"could not find or access this model or endpoint. Edit the model ID or base URL.",
        429:"quota or rate limit was reached. Check provider usage and billing.",
        500:"reported a server error. Try again shortly.",
        502:"reported a gateway error. Try again shortly.",
        503:"is temporarily unavailable. Try again shortly.",
        504:"timed out. Retry or select another available model.",
    }
    message=reasons.get(code,"could not complete the connection. Check internet/proxy access and retry.")
    return HTTPException(502,f"{name} {message}"+(f" (HTTP {code})" if isinstance(code,int) else ""))

CALL_TIMINGS=ContextVar("folio_provider_timings",default=None)
def complete(config,system,user,max_tokens=1200):
    started=time.perf_counter()
    try:return _complete(config,system,user,max_tokens)
    finally:
        trace=CALL_TIMINGS.get()
        if trace is not None:trace.append((time.perf_counter()-started)*1000)

def _complete(config,system,user,max_tokens=1200):
    provider=config["provider"]
    key=config["api_key"];model=config["model_id"]
    try:
        if provider=="groq":
            from groq import Groq
            with Groq(api_key=key,timeout=45,max_retries=1) as client:
                result=client.chat.completions.create(model=model,
                    messages=[{"role":"system","content":system},{"role":"user","content":user}],
                    temperature=0.1,max_completion_tokens=max_tokens)
            text=result.choices[0].message.content or ""
            usage=result.usage
            tokens={"input_tokens":getattr(usage,"prompt_tokens",0),"output_tokens":getattr(usage,"completion_tokens",0)} if usage else {}
        elif provider=="gemini":
            from google import genai
            from google.genai import types
            with genai.Client(api_key=key,http_options=types.HttpOptions(timeout=45000)) as client:
                result=client.models.generate_content(model=model,contents=user,
                    config=types.GenerateContentConfig(system_instruction=system,temperature=0.1,max_output_tokens=max_tokens))
            text=result.text or "";usage=result.usage_metadata
            tokens={"input_tokens":getattr(usage,"prompt_token_count",0),"output_tokens":getattr(usage,"candidates_token_count",0)} if usage else {}
        elif provider=="anthropic":
            from anthropic import Anthropic
            with Anthropic(api_key=key,base_url="https://api.anthropic.com",timeout=45,max_retries=1) as client:
                result=client.messages.create(model=model,max_tokens=max_tokens,system=system,
                    messages=[{"role":"user","content":user}])
            text="".join(block.text for block in result.content if getattr(block,"type",None)=="text")
            usage=result.usage
            tokens={"input_tokens":getattr(usage,"input_tokens",0),"output_tokens":getattr(usage,"output_tokens",0)} if usage else {}
        elif provider in {"openai","custom"}:
            from openai import OpenAI
            base=validate_endpoint(config["base_url"],resolve_dns=True) if provider=="custom" else "https://api.openai.com/v1"
            # Custom credentials must never follow a redirect to a different destination.
            with httpx.Client(follow_redirects=False,timeout=45) as transport:
                with OpenAI(api_key=key,base_url=base,http_client=transport,timeout=45,max_retries=1) as client:
                    options={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}
                    options["max_tokens" if provider=="custom" else "max_completion_tokens"]=max_tokens
                    result=client.chat.completions.create(**options)
            text=result.choices[0].message.content or "";usage=result.usage
            tokens={"input_tokens":getattr(usage,"prompt_tokens",0),"output_tokens":getattr(usage,"completion_tokens",0)} if usage else {}
        else:
            raise HTTPException(400,"Unsupported provider protocol.")
    except HTTPException:
        raise
    except Exception as exc:
        raise request_error(provider,exc) from exc
    return text,tokens

def available_models(config):
    provider=config["provider"];key=config["api_key"]
    try:
        if provider=="groq":
            from groq import Groq
            with Groq(api_key=key,timeout=20,max_retries=0) as client:
                return sorted(m.id for m in client.models.list().data)[:200]
        if provider=="gemini":
            from google import genai
            from google.genai import types
            with genai.Client(api_key=key,http_options=types.HttpOptions(timeout=20000)) as client:
                result=[]
                for item in client.models.list():
                    if "generateContent" in (item.supported_actions or []):
                        result.append(item.name.removeprefix("models/"))
                    if len(result)>=200:break
                return sorted(result)
        if provider=="anthropic":
            from anthropic import Anthropic
            with Anthropic(api_key=key,base_url="https://api.anthropic.com",timeout=20,max_retries=0) as client:
                return sorted(m.id for m in client.models.list().data)[:200]
        from openai import OpenAI
        base=validate_endpoint(config["base_url"],resolve_dns=True) if provider=="custom" else "https://api.openai.com/v1"
        with httpx.Client(follow_redirects=False,timeout=20) as transport:
            with OpenAI(api_key=key,base_url=base,http_client=transport,timeout=20,max_retries=0) as client:
                return sorted(m.id for m in client.models.list().data)[:200]
    except HTTPException:raise
    except Exception as exc:
        code=getattr(exc,"status_code",None) or getattr(exc,"code",None)
        raise HTTPException(502,"Model listing failed. Enter the model ID manually and test the connection."
            +(f" (HTTP {code})" if isinstance(code,int) else "")) from exc
