import os
import sqlite3
import uuid
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("RAG_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "documents.sqlite3"))
_schema_lock = threading.Lock()

@contextmanager
def connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with _schema_lock:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
              csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
              owner_id TEXT REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS units (
              document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
              location TEXT NOT NULL, text TEXT NOT NULL);
            """)
            columns = {x["name"] for x in db.execute("PRAGMA table_info(documents)")}
            if "owner_id" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN owner_id TEXT REFERENCES users(id)")
            db.execute("CREATE INDEX IF NOT EXISTS documents_owner ON documents(owner_id)")
            db.commit()
        with db:
            yield db
    finally:
        db.close()

def add(units, owner_id):
    grouped = {}
    for unit in units:
        grouped.setdefault(unit["source"], []).append(unit)
    ids = []
    with connect() as db:
        for source, entries in grouped.items():
            key = str(uuid.uuid4())
            db.execute("INSERT INTO documents(id,source,owner_id) VALUES (?,?,?)", (key,source,owner_id))
            db.executemany("INSERT INTO units VALUES (?,?,?)",
                           [(key,x["location"],x["text"]) for x in entries])
            ids.append(key)
    return ids

def listing(owner_id):
    with connect() as db:
        return [dict(r) for r in db.execute("""
        SELECT d.id,d.source,d.uploaded_at, count(u.location) as units,
        coalesce(sum(length(u.text)),0) as characters
        FROM documents d LEFT JOIN units u ON u.document_id=d.id WHERE d.owner_id=?
        GROUP BY d.id ORDER BY d.uploaded_at DESC,d.rowid DESC""", (owner_id,))]

def get(key, owner_id):
    with connect() as db:
        document = db.execute("SELECT id,source,uploaded_at FROM documents WHERE id=? AND owner_id=?", (key,owner_id)).fetchone()
        if not document:
            return None
        return {**dict(document), "content": [dict(r) for r in db.execute(
            "SELECT location,text FROM units WHERE document_id=?", (key,))]}

def delete(key, owner_id):
    with connect() as db:
        return db.execute("DELETE FROM documents WHERE id=? AND owner_id=?", (key,owner_id)).rowcount > 0

def clear(owner_id):
    with connect() as db:
        db.execute("DELETE FROM documents WHERE owner_id=?", (owner_id,))
