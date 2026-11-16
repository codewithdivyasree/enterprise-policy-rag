"""Bounded, in-memory document extraction. Never extracts archive paths to disk."""
from io import BytesIO
from pathlib import PurePosixPath
import stat
import zipfile

MAX_UPLOAD = 10 * 1024 * 1024
MAX_MEMBER = 5 * 1024 * 1024
MAX_EXPANDED = 25 * 1024 * 1024
MAX_FILES = 50
SUPPORTED = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".json", ".log"}

def safe_name(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or ":" in normalized or "\x00" in normalized:
        raise ValueError("Unsafe filename")
    return str(path)

def validate_container(data):
    # DOCX is also ZIP: bound its internal expansion before parsing.
    with zipfile.ZipFile(BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(x.file_size for x in members) > MAX_EXPANDED:
            raise ValueError("Document container exceeds limits")

def extract(name, data):
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".log"}:
        text = data.decode("utf-8-sig")
        return [{"source": name, "location": "text", "text": text}] if text.strip() else []
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is unsupported")
        if len(reader.pages) > 300:
            raise ValueError("PDF exceeds 300 pages")
        result = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                result.append({"source": name, "location": f"page {number}", "text": text})
        return result
    if suffix == ".docx":
        validate_container(data)
        from docx import Document
        doc = Document(BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        lines.extend(" | ".join(c.text for c in row.cells) for table in doc.tables for row in table.rows)
        text = "\n".join(lines)
        return [{"source": name, "location": "document", "text": text}] if text.strip() else []
    if suffix == ".xlsx":
        validate_container(data)
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(data),read_only=True,data_only=True)
        result=[]
        cell_count=0
        try:
            if len(book.worksheets)>100:
                raise ValueError("Workbook exceeds 100 sheets")
            for sheet in book.worksheets:
                lines=[]
                for number,row in enumerate(sheet.iter_rows(values_only=True),1):
                    cell_count+=len(row)
                    if cell_count>50000:
                        raise ValueError("Workbook exceeds 50,000 cells")
                    if any(x is not None for x in row):
                        lines.append(f"Row {number}: "+" | ".join("" if x is None else str(x) for x in row))
                text="\n".join(lines)
                if text.strip():
                    result.append({"source":name,"location":f"sheet {sheet.title}","text":text})
        finally:
            book.close()
        return result
    if suffix == ".pptx":
        validate_container(data)
        from pptx import Presentation
        deck=Presentation(BytesIO(data))
        if len(deck.slides)>300:
            raise ValueError("Presentation exceeds 300 slides")
        result=[]
        for number,slide in enumerate(deck.slides,1):
            lines=[]
            for shape in slide.shapes:
                if shape.has_text_frame:
                    lines.append(shape.text)
                if shape.has_table:
                    lines.extend(" | ".join(c.text for c in row.cells) for row in shape.table.rows)
            text="\n".join(lines)
            if text.strip():
                result.append({"source":name,"location":f"slide {number}","text":text})
        return result
    if suffix in {".html",".htm"}:
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(data.decode("utf-8-sig"),"html.parser")
        for tag in soup(["script","style","noscript"]):
            tag.decompose()
        text=soup.get_text(separator="\n",strip=True)
        return [{"source":name,"location":"HTML text","text":text}] if text else []
    raise ValueError("Unsupported format")

def ingest(filename, data):
    if len(data) > MAX_UPLOAD:
        raise ValueError("Upload exceeds 10 MiB")
    filename = safe_name(filename)
    documents, reports = [], []

    def process(name, content):
        if PurePosixPath(name).suffix.lower() not in SUPPORTED:
            reports.append({"source": name, "status": "skipped", "reason": "Unsupported format (nested ZIPs are skipped)"})
            return
        try:
            units = extract(name, content)
            documents.extend(units)
            reports.append({"source": name, "status": "processed" if units else "empty", "units": len(units)})
        except Exception:
            reports.append({"source": name, "status": "failed", "reason": "Unreadable, encrypted, invalid encoding, or document limits exceeded"})

    if PurePosixPath(filename).suffix.lower() != ".zip":
        process(filename, data)
    else:
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > MAX_FILES or sum(x.file_size for x in members) > MAX_EXPANDED:
                    raise ValueError("Archive exceeds 50 entries or 25 MiB expanded")
                for member in members:
                    safe_name(member.filename)
                    if stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError("Archive symlinks are rejected")
                    if member.flag_bits & 1:
                        raise ValueError("Password-protected ZIPs are unsupported")
                    if member.file_size > MAX_MEMBER or member.file_size / max(1, member.compress_size) > 200:
                        raise ValueError("Archive member exceeds size or compression limits")
                for member in members:
                    if member.is_dir():
                        continue
                    with archive.open(member) as stream:
                        content = stream.read(MAX_MEMBER + 1)
                    if len(content) > MAX_MEMBER:
                        raise ValueError("Expanded member exceeds limit")
                    process(f"{filename}/{member.filename}", content)
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid ZIP archive") from exc
    return {"documents": documents, "files": reports, "document_units": len(documents)}
