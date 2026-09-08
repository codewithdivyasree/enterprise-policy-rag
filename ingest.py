from pathlib import Path

import fitz
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import DOCUMENTS_DIR, VECTORSTORE_DIR, EMBEDDING_MODEL


def load_pdf_pages():
    documents = []
    pdf_paths = sorted(Path(DOCUMENTS_DIR).glob("*.pdf"))

    if not pdf_paths:
        raise FileNotFoundError(
            f"No PDF files found in '{DOCUMENTS_DIR}'. Add your policy PDFs and run again."
        )

    for pdf_path in pdf_paths:
        pdf = fitz.open(pdf_path)
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if text:
                documents.append(
                    {
                        "text": text,
                        "source": pdf_path.name,
                        "page": page_number,
                    }
                )
        pdf.close()

    return documents


def build_vectorstore():
    pages = load_pdf_pages()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
    )

    texts = []
    metadatas = []

    for page in pages:
        chunks = splitter.split_text(page["text"])
        for chunk in chunks:
            texts.append(chunk)
            metadatas.append(
                {
                    "source": page["source"],
                    "page": page["page"],
                }
            )

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    vectorstore = FAISS.from_texts(
        texts=texts,
        embedding=embeddings,
        metadatas=metadatas,
    )

    Path(VECTORSTORE_DIR).mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(VECTORSTORE_DIR)

    print(f"PDF pages loaded: {len(pages)}")
    print(f"Chunks created: {len(texts)}")
    print(f"Vector store saved to: {VECTORSTORE_DIR}")


if __name__ == "__main__":
    build_vectorstore()
