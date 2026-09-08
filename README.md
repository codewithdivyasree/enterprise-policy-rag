# Enterprise Policy RAG Assistant

A Retrieval-Augmented Generation (RAG) application that helps employees ask natural-language questions across internal policy documents and receive source-grounded answers.

## Problem

Company policies are often scattered across multiple PDF files. Employees may spend time opening documents, searching pages, and still struggle to find the correct answer.

This project solves that problem by combining semantic retrieval with an LLM so users can ask a question once and get an answer grounded in the uploaded documents.

## Features

- PDF ingestion and text extraction
- Chunking for retrieval
- HuggingFace embeddings
- FAISS vector search
- Groq-powered LLM responses
- Source-aware answers
- Streamlit chat interface
- Local-first setup

## RAG Flow

`PDFs -> Text Extraction -> Chunking -> Embeddings -> FAISS -> Retrieval -> Groq LLM -> Answer`

## Tech Stack

- Python
- Streamlit
- LangChain
- FAISS
- HuggingFace sentence-transformers
- Groq API
- PyMuPDF

## Project Structure

```text
enterprise-policy-rag/
├── app.py
├── config.py
├── ingest.py
├── rag_chain.py
├── requirements.txt
├── .env.example
├── .gitignore
├── documents/
│   └── .gitkeep
└── vectorstore/         # generated locally, ignored by git
```

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and add your Groq API key.

```env
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b
DISPLAY_COMPANY_NAME=Enterprise
DISPLAY_PRODUCT_NAME=PolicyBot
```

### 4. Add your PDF files

Place your own policy PDFs inside the `documents/` directory.

Do not commit private or confidential documents to a public repository.

### 5. Build the vector store

```bash
python ingest.py
```

### 6. Run the app

```bash
streamlit run app.py --server.fileWatcherType none
```

Open `http://localhost:8501`.

## Security

- `.env` is ignored by Git.
- API keys should never be committed.
- Private company PDFs should remain local.
- The generated FAISS vector store is also ignored by default.

## Future Improvements

- Role-based access control
- Document uploads from the UI
- Better citation display
- Feedback tracking
- RAG evaluation metrics
- Admin analytics dashboard

## Use Cases

The same architecture can be adapted for:

- HR policy assistants
- IT support knowledge bases
- Product documentation assistants
- Compliance and SOP search
- Internal enterprise knowledge systems

---

Built as a practical RAG project for enterprise knowledge retrieval.
