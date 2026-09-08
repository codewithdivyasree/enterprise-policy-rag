import streamlit as st

from config import DISPLAY_COMPANY_NAME, DISPLAY_PRODUCT_NAME
from rag_chain import answer_question


st.set_page_config(
    page_title=f"{DISPLAY_COMPANY_NAME} Policy Assistant",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(135deg, #020817 0%, #061b38 45%, #031227 100%);
        color: white;
    }
    .block-container {
        max-width: 1180px;
        padding-top: 1rem;
        padding-bottom: 1rem;
    }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(4,20,44,.98), rgba(6,31,69,.98));
        border-right: 1px solid rgba(59,130,246,.24);
    }
    section[data-testid="stSidebar"] * {
        color: white !important;
    }
    .hero {
        background: rgba(8,22,45,.88);
        border: 1px solid rgba(88,166,255,.24);
        border-radius: 22px;
        padding: 24px 28px;
        margin-bottom: 14px;
        box-shadow: 0 24px 60px rgba(0,0,0,.35);
    }
    .hero h1 {
        margin: 0;
        font-size: 34px;
    }
    .hero p {
        color: #bdd0ea;
        margin: 10px 0 0;
    }
    .stats {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 10px;
        margin-top: 18px;
    }
    .stat {
        background: rgba(10,26,55,.92);
        border: 1px solid rgba(88,166,255,.22);
        border-radius: 12px;
        padding: 12px;
    }
    div[data-testid="stChatMessage"] {
        background: rgba(8,22,45,.88);
        border: 1px solid rgba(88,166,255,.20);
        border-radius: 16px;
        padding: 12px 14px;
        margin-bottom: 10px;
    }
    div[data-testid="stChatInput"] {
        background: rgba(8,22,45,.96);
        border: 1px solid rgba(88,166,255,.32);
        border-radius: 14px;
    }
    .footer-note {
        text-align: center;
        color: #7f9abb;
        font-size: 12px;
        margin-top: 8px;
    }
    #MainMenu, footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)

sample_questions = [
    "How many earned leaves are allowed?",
    "Can sick leave be carried forward?",
    "What is the notice period after probation?",
    "What is the team outing budget per employee?",
    "Who is eligible for internal movement?",
    "What is the purpose of the POSH policy?",
]

selected_question = None

with st.sidebar:
    st.title(f"📘 {DISPLAY_PRODUCT_NAME}")
    st.caption("Internal Policy Assistant")
    st.markdown("### Sample Questions")

    for index, question in enumerate(sample_questions):
        if st.button(question, key=f"sample_{index}", use_container_width=True):
            selected_question = question

    st.markdown("---")
    st.caption("RAG Stack: HuggingFace • FAISS • Groq • Streamlit")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hi. Ask me a question about the uploaded policy documents.",
        }
    ]

if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


def process_question(question: str):
    try:
        response = answer_question(question)
        st.session_state.messages.append({"role": "assistant", "content": response})
    except Exception as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Error: {exc}"}
        )


left, center, right = st.columns([0.5, 1.7, 0.5])

with center:
    st.markdown(
        f"""
        <div class="hero">
            <h1>📘 {DISPLAY_COMPANY_NAME} Policy Assistant</h1>
            <p>Ask questions from uploaded policy documents and get source-grounded answers with RAG.</p>
            <div class="stats">
                <div class="stat">📄 Documents</div>
                <div class="stat">🔎 FAISS Search</div>
                <div class="stat">🔵 HuggingFace</div>
                <div class="stat">⚡ Groq LLM</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    chat_container = st.container(height=300, border=False)

    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if st.session_state.pending_question:
            with st.chat_message("assistant"):
                with st.spinner("Searching policy documents..."):
                    question = st.session_state.pending_question
                    st.session_state.pending_question = None
                    process_question(question)
                    st.rerun()

    if selected_question:
        st.session_state.messages.append({"role": "user", "content": selected_question})
        st.session_state.pending_question = selected_question
        st.rerun()

    user_question = st.chat_input("Ask an enterprise policy question...")

    if user_question:
        st.session_state.messages.append({"role": "user", "content": user_question})
        st.session_state.pending_question = user_question
        st.rerun()

    st.markdown(
        '<div class="footer-note">Enterprise Policy Assistant • RAG Demo</div>',
        unsafe_allow_html=True,
    )
