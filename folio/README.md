# Folio — Dynamic API Connections (v1.1)
Angular + FastAPI + LangChain text splitting/prompt construction + persistent
ChromaDB + local MiniLM embeddings + Groq / Gemini / OpenAI / Claude / custom OpenAI-compatible generation.

## Windows quick start - dynamic API connection manager
Use 64-bit Python 3.11 or 3.12. Extract the ZIP completely.
Open PowerShell inside rag-assistant and run:
```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```
Open http://127.0.0.1:8000 and sign in or create an account.
The compiled Angular UI is included; Node.js is not needed for testing.

If port 8000 is occupied, start from an existing backend CMD window:
```cmd
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```
Open http://127.0.0.1:8002.

## Add and manage API connections
Open API Key Configuration in the left sidebar, or Add API key in chat.
The empty page has a centered Add API key button. Saved records appear in a grid
with an Add API key tile for creating more records.

The form contains:
- Provider: Groq, Google Gemini, OpenAI, Claude (Anthropic), or Other.
- Custom provider name and API base URL when Other is selected.
- Optional connection name for distinguishing work/personal connections.
- API key (required for new records; leave blank when editing to retain it).
- Model ID.
- Use as default option and Save connection button.

Every saved record has Test connection, Edit and Delete buttons, test status,
and a default-connection action. Deletion asks for confirmation. Key values are
never returned by the backend; the grid shows a fixed mask, not the saved secret.
Changing provider or endpoint while editing requires re-entering the API key.
You can save up to 25 records per account, including several for one provider.
Connection names are unique per account.

Chat's provider selector is populated from saved connection IDs and labels.
Saving a connection from chat makes it available immediately. Test connection
makes a short real generation request and may consume provider quota.
Editing a record resets its previous test result to Not tested.
Model discovery is available from the Edit form. Some providers include audio,
guard or other non-chat models in their list; choose a text-generation model.
No .env changes or restart are needed after saving provider records.

## Other provider compatibility
Other means an OpenAI-compatible Chat Completions API, not an arbitrary API
protocol. Enter the provider name, key, model ID and public HTTPS API base URL
(such as the provider's /v1 endpoint). Custom endpoints must use port 443,
must resolve to public IP addresses, and may not contain credentials, query
parameters or fragments. Private/local endpoints are not supported.
The endpoint is revalidated before each outbound call, and redirects are disabled.
The UI explicitly identifies the destination that receives keys and prompts.
For a hosted production service, use a vetted endpoint allowlist and outbound
network controls as well as these application checks.

## Update an existing installation
Stop the server, then replace ONLY these items from the ZIP:
- frontend/ (the whole folder, including dist/)
- backend/app/ (the whole folder, including the new provider_config.py)
- backend/requirements.txt

Keep backend/.env, backend/.venv and backend/data unchanged.
From your existing backend CMD window:
```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```
Press Ctrl+F5 in the browser. Open profile -> AI settings.
No document re-upload or account recreation is required.
Copying rag.py alone is insufficient for this update because settings add a new API module.

## First end-to-end test
1. Create an account or sign in. No email verification is implemented.
2. Upload samples/policy.txt or samples/documents.zip.
3. Open the document preview and confirm extraction.
4. Click Prepare documents. This chunks text and embeds it into ChromaDB.
5. Select Semantic sources in the composer. Ask "How many leave days are available?"
   This mode returns retrieved passages and does not call an LLM.
6. Select Groq or Google Gemini. Ask the same question.
7. Check answer references such as [S1], expand Read source passage, and click
   the source filename to open the extracted document.
8. Check provider, latency and token usage beneath the answer.
9. Test selected-document scope, New chat, export, deletion, and logout.

Questions also synchronize the index automatically, so an explicit Prepare
step is optional once the model is available. Index failure leaves extracted
documents saved. Retry Prepare documents after fixing download/storage issues.
Provider errors do not silently switch providers or fabricate an answer.

## Connection security, migration and persistence
API keys are encrypted with Fernet and stored in the api_connections SQLite
table with owner IDs. All CRUD, model discovery, default selection, connection
tests and chat selection enforce account ownership. Mutations require a session
and CSRF checks. Secrets are never placed in localStorage or returned by GET APIs.
Generation errors expose a safe status/category rather than raw provider bodies.

Existing Groq/Gemini keys and model IDs from the previous account settings or
server defaults are migrated once into connection records for each account.
The migration marker prevents deleted records from being recreated on reload.
Legacy account key fields are cleared/disabled after migration; future changes
should use API Key Configuration rather than .env or the old settings panel.
Documents, users and vector indexes are not recreated.

Back up both backend/data/documents.sqlite3 and backend/data/provider_settings.key.
The encryption key is created automatically and is never sent to the browser.
Encryption does not protect against a person who can read both files on the host.
Keep that directory out of source control and archives.
Internal paths, cookie security and the retrieval threshold remain server settings.

New API:
- GET /connections: safe records, provider catalog, default ID and general-chat preference.
- POST /connections: create a record.
- PUT /connections/{id}: edit a record.
- DELETE /connections/{id}: delete one record.
- POST /connections/{id}/default: choose default.
- POST /connections/{id}/test: generation test with saved configuration.
- POST /connections/{id}/models: available model IDs.
- POST /connections/preferences: general-question preference.

## Document answers and general chat
Groq/Gemini now answer general questions when files provide no relevant evidence.
These responses start with General knowledge and never carry document references.
A question with no uploaded files can also use the selected LLM.
If a retrieved passage does not yield a cited document answer, the assistant can
make a second, general-knowledge call; token usage sums both calls.
Semantic sources mode stays document-only and never calls an LLM.
Set GENERAL_CHAT_ENABLED=false in backend/.env and restart to disable fallback.
The default is true even when that setting is absent from an existing .env.
General mode is not live web search and cannot establish missing private policy facts.

For this release follow the Update an existing installation section above.

## Short-question retrieval fix
Search now combines local semantic retrieval with keyword topic matching and
reciprocal-rank fusion. Simple singular/plural variants such as function/functions
and variable/variables are matched. A strong exact-topic match is not discarded
only because it missed the cosine-distance cutoff. Standalone questions no longer
inherit unrelated previous questions; referential follow-ups can use history.

Existing indexes are preserved; document re-upload is not required.
Regression tests use real Chroma with deterministic test embeddings, including
deliberately poor semantic scores for a relevant exact-topic question.

## How retrieval works
Extraction -> LangChain recursive splitter (900 characters / 150 overlap) ->
local all-MiniLM-L6-v2 embeddings -> persistent Chroma cosine search ->
semantic + keyword rank fusion -> top six source chunks -> selected provider -> answer with [S1] citations.

Source metadata preserves filename plus page / sheet / slide / text location.
Chroma records carry owner and document IDs. The server filters both, checks
SQLite ownership again before returning matches, and removes stale vectors on
index synchronization. Deleting a document removes its vector records when
available; ownership/source checks prevent retrieval even if cleanup fails.

Only the question, bounded recent conversation and retrieved excerpts go to
the selected provider. Full original file bytes are not sent. Semantic sources
runs without provider requests. MiniLM's first download still requires internet.
Recent chat context helps follow-up retrieval; conversation history itself is
held in this browser session and is not persisted as chat records.

## Grounding and practical limits
The model is instructed to use only supplied passages and cite them.
Unknown citation IDs are rejected. Uncited affirmative responses are replaced
with a retry message. With no sufficiently close chunks, enabled general chat calls the LLM without document evidence and labels the reply General knowledge.
For document answers these checks validate citation references, not factual entailment or answer
completeness. Prompt injection defenses and retrieval thresholds are not a
guarantee; inspect sources and evaluate with your own documents.
Spreadsheet extraction is text-based and is not a calculation engine.

## Supported files
- PDF with an existing text layer (up to 300 pages)
- DOCX paragraphs and tables
- XLSX cached cell values, with sheet and row labels (100 sheets / 50,000 cells)
- PPTX slide text and tables (up to 300 slides)
- UTF-8 TXT, Markdown, CSV, JSON and log files
- HTML/HTM text, with script/style content removed; no external content fetched
- ZIP archives containing supported formats

No OCR for scanned PDFs or images, legacy DOC/XLS/PPT, nested ZIPs, or encrypted
archives. Charts/images/grouped slide graphics are not interpreted. XLSX formulas
are not recalculated; formula values require a saved calculation cache.
Empty/scanned files report empty; unsupported or invalid members are reported.
10 MiB/upload, 50 archive entries, 5 MiB/archive member, 25 MiB expanded archive,
200:1 compression ratio. No archive path is extracted to disk.
Index quotas: 1,000 chunks per document and 5,000 chunks per account.

## Accounts
Create account / login / logout, top-right initials avatar and profile menu,
salted scrypt password hashes, hashed opaque session tokens, seven-day expiry,
HttpOnly SameSite=Strict session cookies, Angular XSRF checks and origin checks.
Every document, index and question operation requires authentication.
Signup/login have a per-process attempt limit of 20/minute per client address.
Documents survive logout; each account has its own document library.
The build includes no accounts, API keys or user databases.
Old pre-account documents remain unassigned; upload them again under your account.
Password reset, email verification and third-party sign-in are not implemented.

## Natural answers
Normal Groq/Gemini chat now displays a direct, concise explanation in Markdown,
with examples when useful. Internal source markers are removed from the displayed
reply. No chunk cards, reference list or page numbers appear in normal chat.
The explicit Semantic sources inspection mode still offers retrieved passages.
The backend still retrieves evidence and checks citation IDs before display.

For installation changes, follow Update an existing installation above.

## Source inspection mode
Only the explicitly selected Semantic sources mode shows a collapsed View
sources control for inspecting retrieval. Generated Groq/Gemini replies do not
display source cards, source markers, filenames, or page numbers.

## Interface
Fixed workspace with no full-page scrolling; the empty conversation is static
and only overflowing chat messages scroll. The composer remains visible.
Login/signup fit the viewport using compact layouts on shorter screens.
Document library pagination, document and report dialogs, mobile Documents/Chat
tabs, collapsible sidebar, full gradient upload outline, coordinated light/dark
themes, animated aurora chat background and Animation on/off control are retained.
The sidebar, theme and motion preferences persist locally.

## Developer setup
Backend:
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Linux/macOS equivalents: python3 -m venv .venv, .venv/bin/python -m pip install
-r requirements.txt, then .venv/bin/python -m uvicorn app.main:app.

Angular source development (Node 22.12+ or Node 24+):
```bash
cd frontend
npm ci
npm start
```
Open http://127.0.0.1:4200 with the backend on port 8000. The development proxy
forwards /auth, /upload, /documents, /health, /rag, /ask and /search.
Run npm run build after UI edits. Restart FastAPI after the first build.
API docs: http://127.0.0.1:8000/docs.

Tests, from backend:
```bash
python -m pytest -q
```
Tests exercise real Chroma persistence and LangChain splitting with test-only
deterministic embeddings and mocked provider calls. Production uses MiniLM;
there is no fake embedding/provider fallback in the app.
Live provider and model-download checks require connectivity and your keys.
A first real embedding download timed out in the build environment; no live
Groq/Gemini account was available for paid API verification.

## Persistence and local-use scope
SQLite users/sessions/text: backend/data/documents.sqlite3.
Chroma vectors: backend/data/chroma (or CHROMA_PATH).
RAG_DB_PATH may override the SQLite path. Both are excluded from the ZIP.
Back up the data directory for persistent local accounts/documents.

This is a functional local RAG implementation, not a hardened hosted service.
Production needs HTTPS with secure cookies, email recovery/verification,
distributed rate limits and quotas, parser process CPU/memory/time limits,
malware checks, proxy upload limits, monitoring and evaluation. Provider calls
may incur charges. Python requirements specify compatible ranges; Angular has
a package lock.

References:
https://reference.langchain.com/python/langchain-text-splitters/character/RecursiveCharacterTextSplitter
https://cookbook.chromadb.dev/core/collections/
https://console.groq.com/docs/text-chat
https://googleapis.github.io/python-genai/


## November 16 usability update

- Answer modes: Auto (retrieval with permitted general fallback), Documents only (no general fallback), General chat (skips retrieval; requires a configured provider and enabled general questions).
- Saved conversations: account-scoped database history, search by title, reopen, rename and delete. Up to 100 conversations per account and the most recent 100 messages per conversation are saved.
- Copy answer, retry a failed question, collapsible supporting sources, and real upload progress followed by extraction status. Indexing remains an explicit Prepare documents step.
- Per-account /ask limit: 20 requests per rolling minute on the current single-instance deployment; HTTP 429 includes Retry-After. Limits reset on restart and are not a distributed quota.
- Chat content is stored in the account database; it is not end-to-end encrypted. API keys retain their separate encryption.

### Deployment limits and remaining work
The free Render deployment uses an ephemeral SQLite database and Chroma index. This update does not make them durable. A restart/redeployment may erase accounts, saved chats, documents and configured keys. Do not treat it as permanent storage. Durable storage needs an external database/storage service or a paid persistent disk. Existing local installations retain data when backend/data is preserved.

Native provider streaming/Stop, a measured answer-quality evaluation dashboard, and durable cloud storage remain future work. The UI does not simulate streaming or claim an unmeasured accuracy score.
