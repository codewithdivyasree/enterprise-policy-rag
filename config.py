import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
DISPLAY_COMPANY_NAME = os.getenv("DISPLAY_COMPANY_NAME", "Enterprise")
DISPLAY_PRODUCT_NAME = os.getenv("DISPLAY_PRODUCT_NAME", "PolicyBot")

DOCUMENTS_DIR = "documents"
VECTORSTORE_DIR = "vectorstore"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
