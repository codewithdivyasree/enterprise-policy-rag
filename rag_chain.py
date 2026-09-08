from pathlib import Path

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq

from config import GROQ_API_KEY, GROQ_MODEL, VECTORSTORE_DIR, EMBEDDING_MODEL


PROMPT = PromptTemplate.from_template(
    """
You are an enterprise policy assistant.
Answer the user's question using only the provided context.
If the answer is not available in the context, say that the information is not available in the uploaded documents.
Do not invent policy details.
Keep the answer clear and concise.
When possible, mention the source document and page number.

Context:
{context}

Question:
{question}

Answer:
"""
)


def get_vectorstore():
    if not Path(VECTORSTORE_DIR).exists():
        raise FileNotFoundError(
            "Vector store not found. Run 'python ingest.py' before starting the app."
        )

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    return FAISS.load_local(
        VECTORSTORE_DIR,
        embeddings,
        allow_dangerous_deserialization=True,
    )


def format_documents(documents):
    parts = []

    for doc in documents:
        source = doc.metadata.get("source", "Unknown document")
        page = doc.metadata.get("page", "Unknown page")
        parts.append(
            f"Source: {source}\nPage: {page}\nContent:\n{doc.page_content}"
        )

    return "\n\n---\n\n".join(parts)


def answer_question(question: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is missing. Add it to your local .env file.")

    vectorstore = get_vectorstore()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
    documents = retriever.invoke(question)

    if not documents:
        return "The information is not available in the uploaded documents."

    context = format_documents(documents)

    llm = ChatGroq(
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL,
        temperature=0,
    )

    chain = PROMPT | llm
    response = chain.invoke({"context": context, "question": question})

    return response.content
