from io import BytesIO
import zipfile
import pytest
from app.ingestion import ingest, MAX_UPLOAD

def archive(items):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, data in items:
            z.writestr(name, data)
    return stream.getvalue()

def test_text():
    result = ingest("notes.txt", b"Annual leave is 12 days.")
    assert result["documents"][0]["text"] == "Annual leave is 12 days."

def test_mixed_zip_preserves_sources():
    result = ingest("team.zip", archive([("hr/notes.txt", b"Leave policy"), ("photo.png", b"no")]))
    assert result["documents"][0]["source"] == "team.zip/hr/notes.txt"
    assert result["files"][1]["status"] == "skipped"

@pytest.mark.parametrize("name", ["../escape.txt", "/tmp/test.txt", "C:\\bad.txt", "..\\bad.txt"])
def test_rejects_paths(name):
    with pytest.raises(ValueError):
        ingest("bad.zip", archive([(name, b"test")]))

def test_corrupt_archive():
    with pytest.raises(ValueError):
        ingest("bad.zip", b"bad")

def test_count_limit():
    with pytest.raises(ValueError):
        ingest("many.zip", archive([(f"{n}.txt", b"x") for n in range(51)]))

def test_upload_limit():
    with pytest.raises(ValueError):
        ingest("big.txt", b"x" * (MAX_UPLOAD + 1))

def test_bad_text_is_reported():
    assert ingest("bad.txt", b"\xff")["files"][0]["status"] == "failed"

def test_api(tmp_path, monkeypatch):
    from app import store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.sqlite3"))
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    client.post("/auth/register", json={"name":"Tester","email":"tester@example.com","password":"Test-password-123"})
    client.headers["X-XSRF-TOKEN"] = client.cookies["XSRF-TOKEN"]
    response = client.post("/upload", files={"file": ("notes.txt", b"Hello")})
    assert response.status_code == 200
    assert len(response.json()["document_ids"]) == 1

def test_xlsx_sheet_and_row_metadata():
    from openpyxl import Workbook
    book=Workbook()
    book.active.title="Benefits"
    book.active.append(["Benefit","Days"])
    book.active.append(["Annual leave",12])
    data=BytesIO()
    book.save(data)
    result=ingest("benefits.xlsx",data.getvalue())
    assert result["documents"][0]["location"]=="sheet Benefits"
    assert "Row 2: Annual leave | 12" in result["documents"][0]["text"]

def test_pptx_slide_metadata():
    from pptx import Presentation
    deck=Presentation()
    slide=deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text="Leave policy"
    slide.placeholders[1].text="Manager approval required."
    data=BytesIO()
    deck.save(data)
    result=ingest("policy.pptx",data.getvalue())
    assert result["documents"][0]["location"]=="slide 1"
    assert "Manager approval required." in result["documents"][0]["text"]

def test_html_strips_script_without_executing_it():
    result=ingest("policy.html",b"<html><script>secret_code()</script><style>body{}</style><p>Leave requires approval.</p></html>")
    text=result["documents"][0]["text"]
    assert text=="Leave requires approval."
