import random
import re
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import pdfplumber
import plotly.express as px
import streamlit as st
from docx import Document
try:
    from moviepy import VideoFileClip
except ImportError:
    try:
        from moviepy.editor import VideoFileClip
    except ImportError:
        VideoFileClip = None
try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None
try:
    import speech_recognition as sr
except ImportError:
    sr = None
try:
    import pytesseract
except ImportError:
    pytesseract = None
try:
    from pdf2image import convert_from_bytes
except ImportError:
    convert_from_bytes = None

from utils.storage import (
    authenticate_user,
    init_db,
    load_attempts,
    load_questions,
    register_user,
    save_attempt,
    save_questions,
)
from analytics import render_dashboard

APP_TITLE = "SmartQuizzer Pro"
STOPWORDS = {
    "the", "is", "are", "was", "were", "this", "that", "these", "those", "from", "into",
    "with", "for", "and", "but", "about", "over", "under", "between", "during", "through",
    "have", "has", "had", "can", "could", "will", "would", "should", "may", "might", "must",
    "a", "an", "of", "to", "in", "on", "at", "by", "as", "it", "its", "be", "or", "if",
    "than", "then", "there", "their", "them", "they", "you", "your", "we", "our", "he", "she"
}

HAS_MOVIEPY = VideoFileClip is not None
HAS_PYDUB = AudioSegment is not None
HAS_SR = sr is not None
HAS_OCR = pytesseract is not None and convert_from_bytes is not None


def normalize_text(text):
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text):
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if len(p.split()) >= 6]


def extract_keywords(text, top_k=80):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower())
    words = [word for word in words if word not in STOPWORDS]
    return [item[0] for item in Counter(words).most_common(top_k)]


def text_from_pdf(file_bytes):
    result = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                result.append(page_text)
    extracted = "\n".join(result).strip()
    if extracted:
        return extracted

    # Fallback for scanned/image-only PDFs when OCR dependencies are present.
    if not HAS_OCR:
        return ""
    try:
        images = convert_from_bytes(file_bytes)
        ocr_pages = []
        for image in images:
            page_text = pytesseract.image_to_string(image) or ""
            if page_text.strip():
                ocr_pages.append(page_text)
        return "\n".join(ocr_pages).strip()
    except Exception:
        return ""


def text_from_docx(file_bytes):
    doc = Document(BytesIO(file_bytes))
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(lines)


def transcribe_audio(file_bytes, suffix):
    if sr is None:
        raise RuntimeError("Audio transcription needs SpeechRecognition. Install it with: pip install SpeechRecognition")
    recognizer = sr.Recognizer()
    with tempfile.TemporaryDirectory() as temp_dir:
        src = Path(temp_dir) / f"input{suffix}"
        wav = Path(temp_dir) / "audio.wav"
        src.write_bytes(file_bytes)

        if suffix.lower() != ".wav":
            if AudioSegment is None:
                raise RuntimeError("MP3 transcription needs pydub. Install it with: pip install pydub")
            audio = AudioSegment.from_file(src)
            audio.export(wav, format="wav")
            source_path = wav
        else:
            source_path = src

        with sr.AudioFile(str(source_path)) as source:
            audio_data = recognizer.record(source)
            return recognizer.recognize_google(audio_data)


def transcribe_video(file_bytes, suffix):
    if VideoFileClip is None:
        raise RuntimeError("Video transcription needs moviepy. Install it with: pip install moviepy")
    if sr is None:
        raise RuntimeError("Video transcription needs SpeechRecognition. Install it with: pip install SpeechRecognition")
    recognizer = sr.Recognizer()
    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = Path(temp_dir) / f"video{suffix}"
        audio_path = Path(temp_dir) / "video_audio.wav"
        video_path.write_bytes(file_bytes)

        clip = VideoFileClip(str(video_path))
        if clip.audio is None:
            clip.close()
            return ""
        clip.audio.write_audiofile(str(audio_path), logger=None)
        clip.close()

        with sr.AudioFile(str(audio_path)) as source:
            audio_data = recognizer.record(source)
            return recognizer.recognize_google(audio_data)


def pick_answer_token(sentence):
    tokens = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", sentence)
    filtered = [t for t in tokens if t.lower() not in STOPWORDS]
    if not filtered:
        return None
    filtered.sort(key=len, reverse=True)
    return filtered[0]


def sentence_pool(sentences, difficulty):
    if difficulty == "Easy":
        return sentences[:]
    if difficulty == "Medium":
        return [s for s in sentences if 10 <= len(s.split()) <= 26] or sentences
    return [s for s in sentences if len(s.split()) >= 14] or sentences


def build_mcq(sentence, keyword_bank, difficulty):
    answer = pick_answer_token(sentence)
    if not answer:
        return None

    prompt = re.sub(
        rf"\b{re.escape(answer)}\b",
        "_____",
        sentence,
        count=1,
        flags=re.IGNORECASE
    )

    distractors = [w.title() for w in keyword_bank if w.lower() != answer.lower()]
    random.shuffle(distractors)

    options = [answer] + distractors[:3]

    while len(options) < 4:
        options.append(f"Option {len(options) + 1}")

    random.shuffle(options)

    return {
        "question": f"Fill in the blank: {prompt}",
        "options": options[:4],
        "answer": answer,
        "type": "MCQ",
        "difficulty": difficulty.lower(),
    }

def build_true_false(sentence, keyword_bank, difficulty):
    answer = "True"
    statement = sentence
    flip = random.choice([True, False])
    if flip:
        token = pick_answer_token(sentence)
        replacement = next((w for w in keyword_bank if w.lower() != (token or "").lower()), None)
        if token and replacement:
            statement = re.sub(rf"\b{re.escape(token)}\b", replacement, sentence, count=1, flags=re.IGNORECASE)
            answer = "False"
    return {
        "question": f"True or False: {statement}",
        "options": ["True", "False"],
        "answer": answer,
        "type": "True/False",
        "difficulty": difficulty.lower(),
    }


def build_short_answer(sentence, difficulty):
    return {
        "question": f"Explain briefly: {sentence}",
        "options": [],
        "answer": sentence,
        "type": "Short Answer",
        "difficulty": difficulty.lower(),
    }


def generate_quiz(text, question_count, difficulty, question_type):
    sentences = split_sentences(text)
    if not sentences:
        return []
    pool = sentence_pool(sentences, difficulty)
    random.shuffle(pool)
    keyword_bank = extract_keywords(text)

    builders = {
        "Multiple Choice Questions (MCQ)": build_mcq,
        "True/False": build_true_false,
        "Short Answer": build_short_answer,
    }

    questions = []
    builder = builders[question_type]
    for sentence in pool:
        question = builder(sentence, keyword_bank, difficulty) if question_type != "Short Answer" else builder(sentence, difficulty)
        if question:
            questions.append(question)
        if len(questions) >= question_count:
            break
    return questions


def evaluate_answer(question, user_answer):
    if user_answer is None:
        return False
    if question["type"] in {"MCQ", "True/False"}:
        return str(user_answer).strip().lower() == str(question["answer"]).strip().lower()

    expected = normalize_text(question["answer"]).lower()
    response = normalize_text(str(user_answer)).lower()
    expected_tokens = [w for w in expected.split() if w not in STOPWORDS]
    if not expected_tokens:
        return False
    overlap = sum(1 for token in expected_tokens if token in response)
    return overlap / max(1, len(expected_tokens)) >= 0.35


def extract_input_text(input_mode, typed_text, uploaded_file):
    if input_mode == "Paste Text":
        return normalize_text(typed_text), "Pasted Text"

    if uploaded_file is None:
        return "", ""

    file_bytes = uploaded_file.read()
    suffix = Path(uploaded_file.name).suffix.lower()
    source_name = uploaded_file.name

    if suffix == ".pdf":
        return normalize_text(text_from_pdf(file_bytes)), source_name
    if suffix == ".docx":
        return normalize_text(text_from_docx(file_bytes)), source_name
    if suffix in {".wav", ".mp3"}:
        return normalize_text(transcribe_audio(file_bytes, suffix)), source_name
    if suffix == ".mp4":
        return normalize_text(transcribe_video(file_bytes, suffix)), source_name
    return "", source_name


st.set_page_config(page_title=APP_TITLE, page_icon="🧠", layout="wide")
init_db()

if "quiz_submitted" not in st.session_state:
    st.session_state.quiz_submitted = False

if "answers" not in st.session_state:
    st.session_state.answers = {}

if "current_q" not in st.session_state:
    st.session_state.current_q = 0

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');

        :root {
            --bg-a: #f4f6f8;
            --bg-b: #e8eef3;
            --ink: #10202a;
            --text: #1a2b35;
            --muted: #4a5f6b;
            --panel: #ffffff;
            --panel-soft: #f9fbfc;
            --line: #d7e0e6;
            --brand: #006d77;
            --brand-2: #0a9396;
            --brand-soft: rgba(0, 109, 119, 0.14);
            --success: #157347;
            --warn: #9a6700;
            --danger: #b42318;
            --radius-xl: 20px;
            --radius-lg: 14px;
            --radius-md: 10px;
            --shadow-sm: 0 8px 24px rgba(9, 30, 66, 0.08);
            --shadow-lg: 0 20px 45px rgba(9, 30, 66, 0.14);
        }

        html, body, [class*="css"] {
            font-family: 'Plus Jakarta Sans', sans-serif;
            color: var(--text);
        }

        .stApp {
            background: 
                linear-gradient(120deg, rgba(0, 109, 119, 0.05) 0%, rgba(10, 147, 150, 0.05) 100%),
                radial-gradient(at 0% 0%, rgba(0, 109, 119, 0.1) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(10, 147, 150, 0.1) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(0, 109, 119, 0.1) 0px, transparent 50%),
                radial-gradient(at 0% 100%, rgba(10, 147, 150, 0.1) 0px, transparent 50%),
                var(--bg-a);
            background-attachment: fixed;
            min-height: 100vh;
        }

        .main .block-container {
            max-width: 1180px;
            background: rgba(255, 255, 255, 0.7);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.7);
            border-radius: var(--radius-xl);
            box-shadow: var(--shadow-sm);
            padding: clamp(1rem, 2vw, 2.2rem) clamp(1rem, 2.3vw, 2.4rem) clamp(1.2rem, 2.6vw, 2.6rem) !important;
            margin-top: clamp(0.6rem, 1.1vw, 1rem);
            margin-bottom: clamp(0.7rem, 1.1vw, 1rem);
            animation: fadeInScale 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        }

        @keyframes fadeInScale {
            from { opacity: 0; transform: scale(0.98) translateY(10px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }

        .card {
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.6);
            border-radius: var(--radius-lg);
            padding: 1.5rem;
            box-shadow: var(--shadow-sm);
            margin-bottom: 1.5rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .card:hover {
            transform: translateY(-4px);
            box-shadow: var(--shadow-lg);
            border-color: var(--brand-2);
            background: rgba(255, 255, 255, 0.95);
        }

        h1, h2, h3, h4, h5, h6,
        [data-testid="stHeading"] {
            font-family: 'Fraunces', serif;
            color: var(--ink) !important;
            letter-spacing: 0.1px;
            line-height: 1.2;
        }

        h1 { font-size: clamp(1.85rem, 3vw, 2.7rem); }
        h2 { font-size: clamp(1.4rem, 2.25vw, 2.05rem); }
        h3 { font-size: clamp(1.2rem, 1.75vw, 1.55rem); }

        p, li, label, .stCaption, .stMarkdown, .stText {
            color: var(--text) !important;
            line-height: 1.55;
        }

        .stDivider {
            border-top: 1px solid var(--line);
            margin: 1rem 0 1.2rem;
        }

        div.stButton > button,
        div.stDownloadButton > button,
        div[data-testid="stFormSubmitButton"] > button {
            width: 100%;
            min-height: 46px;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.18);
            background: linear-gradient(135deg, var(--brand), var(--brand-2));
            color: #f5ffff !important;
            font-size: 0.98rem;
            font-weight: 700;
            letter-spacing: 0.2px;
            box-shadow: 0 10px 22px rgba(0, 109, 119, 0.22);
            transition: transform .16s ease, box-shadow .16s ease, filter .16s ease;
        }

        div.stButton > button:hover,
        div.stDownloadButton > button:hover,
        div[data-testid="stFormSubmitButton"] > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 13px 24px rgba(0, 109, 119, 0.28);
            filter: saturate(110%);
        }

        div.stButton > button:focus-visible,
        div.stDownloadButton > button:focus-visible,
        div[data-testid="stFormSubmitButton"] > button:focus-visible,
        .stTextInput > div > div > input:focus-visible,
        .stTextArea textarea:focus-visible {
            outline: 3px solid var(--brand-soft);
            outline-offset: 2px;
        }

        .stTextInput > div > div > input,
        .stTextArea textarea,
        .stNumberInput input,
        .stSelectbox > div > div,
        .stMultiSelect > div > div,
        [data-baseweb="select"] > div {
            background: #ffffff !important;
            border: 1px solid #c7d4dd !important;
            border-radius: var(--radius-md) !important;
            min-height: 44px;
            color: var(--ink) !important;
            -webkit-text-fill-color: var(--ink) !important;
        }

        .stTextArea textarea {
            min-height: 130px;
            background: #fcfeff !important;
        }

        .stTextInput input::placeholder,
        .stTextArea textarea::placeholder {
            color: #6f7f8a !important;
            opacity: 1;
        }

        .stTextInput > div > div > input:focus,
        .stTextArea textarea:focus,
        .stNumberInput input:focus,
        [data-baseweb="select"] > div:focus-within {
            border-color: #14919b !important;
            box-shadow: 0 0 0 4px var(--brand-soft) !important;
        }

        [data-testid="stWidgetLabel"],
        [data-testid="stRadio"] p,
        [role="radiogroup"] label,
        .stSlider label,
        .stFileUploader label,
        .stSelectbox label,
        .stTextInput label,
        .stTextArea label {
            color: var(--ink) !important;
            font-weight: 650;
        }

        [data-baseweb="select"] *,
        [data-baseweb="tag"] * {
            color: var(--ink) !important;
        }

        [data-baseweb="select"] [data-baseweb="menu"] li {
            color: var(--ink) !important;
            background: #ffffff !important;
        }

        [data-baseweb="select"] [data-baseweb="menu"] li:hover {
            background: #f0f4f7 !important;
        }

        [data-baseweb="select"] [data-baseweb="menu"] [data-baseweb="option"] {
            color: var(--ink) !important;
            background: #ffffff !important;
        }

        [data-baseweb="select"] [data-baseweb="menu"] [data-baseweb="option"]:hover {
            background: #f0f4f7 !important;
        }

        [data-baseweb="select"] [data-baseweb="menu"] {
            background: #ffffff !important;
            border: 1px solid #c7d4dd !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1) !important;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #ffffff, #f5f9fc);
            border-right: 1px solid var(--line);
        }

        [data-testid="stSidebar"] .block-container {
            background: transparent;
            border: none;
            box-shadow: none;
            padding-top: 1rem !important;
        }

        [data-testid="stSidebar"] * {
            color: var(--text) !important;
        }

        .user-pill {
            display: inline-block;
            padding: 0.14rem 0.5rem;
            border-radius: 999px;
            background: #dff3f4;
            border: 1px solid #8ac8cc;
            color: #005c64 !important;
            font-weight: 700;
            font-size: 0.82rem;
            line-height: 1.2;
        }

        [data-testid="stMetric"] {
            background: var(--panel-soft);
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            padding: 0.65rem 0.8rem;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
        }

        [data-testid="stMetricLabel"],
        [data-testid="stMetricValue"] {
            color: var(--ink) !important;
        }

        [data-testid="stExpander"] {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            overflow: hidden;
        }

        .stProgress > div > div > div > div {
            background: linear-gradient(90deg, #36b3a8, var(--brand));
        }

        .stAlert {
            border-radius: var(--radius-md);
            border: 1px solid var(--line);
        }

        .stSuccess { color: var(--success) !important; }
        .stWarning { color: var(--warn) !important; }
        .stError { color: var(--danger) !important; }

        [data-testid="stHorizontalBlock"] {
            gap: clamp(0.6rem, 1.4vw, 1rem);
        }

        [data-testid="stRadio"] [role="radiogroup"] {
            display: flex;
            gap: 0.45rem;
            flex-wrap: wrap;
        }

        [data-testid="stRadio"] [role="radiogroup"] > label {
            background: #f6fafb;
            border: 1px solid #d1dde4;
            border-radius: 999px;
            padding: 0.2rem 0.65rem;
        }

        @media (max-width: 1080px) {
            .main .block-container {
                border-radius: 14px;
                padding: 1rem 1rem 1.25rem !important;
            }
        }

        @media (max-width: 860px) {
            .main .block-container {
                max-width: 100%;
                border-radius: 12px;
            }

            [data-testid="stHorizontalBlock"] {
                display: flex;
                flex-direction: column;
                gap: 0.7rem;
            }

            [data-testid="column"] {
                width: 100% !important;
                flex: 1 1 100% !important;
                min-width: 100% !important;
            }

            div.stButton > button,
            div.stDownloadButton > button,
            div[data-testid="stFormSubmitButton"] > button {
                min-height: 44px;
                font-size: 0.95rem;
            }
        }

        @media (max-width: 640px) {
            .main .block-container {
                padding: 0.85rem 0.7rem 1rem !important;
                border-left: none;
                border-right: none;
                box-shadow: none;
            }

            h1 { font-size: 1.55rem; }
            h2 { font-size: 1.25rem; }
            h3 { font-size: 1.08rem; }
        }
    </style>
    """,
    unsafe_allow_html=True,
)



if st.session_state.auth_user is None:
    auth_mode = st.radio("SmartQuizzer", ["Login", "Register"], horizontal=True)
    if auth_mode == "Login":
        with st.form("login_form", clear_on_submit=False):
            login_username = st.text_input("Username", key="login_username")
            login_password = st.text_input("Password", type="password", key="login_password")
            login_submit = st.form_submit_button("Login", use_container_width=True)
            if login_submit:
                ok, db_username = authenticate_user(login_username, login_password)
                if ok:
                    st.session_state.auth_user = db_username
                    st.success("Login successful.")
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
    else:
        with st.form("register_form", clear_on_submit=False):
            reg_username = st.text_input("Create Username", key="register_username")
            reg_password = st.text_input("Create Password", type="password", key="register_password")
            reg_confirm = st.text_input("Confirm Password", type="password", key="register_confirm")
            register_submit = st.form_submit_button("Register", use_container_width=True)
            if register_submit:
                if reg_password != reg_confirm:
                    st.error("Passwords do not match.")
                else:
                    ok, message = register_user(reg_username, reg_password)
                    if ok:
                        st.success("Registration successful. Please login.")
                    else:
                        st.error(message)
    st.stop()

if "menu_selection" not in st.session_state:
    st.session_state.menu_selection = "Generate Quiz"

def update_menu():
    st.session_state.menu_selection = st.session_state.menu_radio

menu_opts = ["Generate Quiz", "Take Quiz", "Analytics Dashboard"]
curr_idx = menu_opts.index(st.session_state.menu_selection) if st.session_state.menu_selection in menu_opts else 0

menu = st.sidebar.radio("Navigate", menu_opts, index=curr_idx, key="menu_radio", on_change=update_menu)
candidate = st.session_state.auth_user
history = load_attempts(limit=200)

with st.sidebar:
    st.markdown(f"**Logged in as:** <span class='user-pill'>{candidate}</span>", unsafe_allow_html=True)
    if st.button("Logout", use_container_width=True):
        st.session_state.auth_user = None
        st.session_state.answers = {}
        st.session_state.quiz_submitted = False
        st.rerun()
    st.markdown("### Performance Snapshot")
    tests_taken = history["tests_taken"]
    avg_accuracy = round(sum(history["percentages"]) / len(history["percentages"]), 2) if history["percentages"] else 0.0
    st.metric("Attempts", tests_taken)
    st.metric("Average Accuracy", f"{avg_accuracy}%")

if menu == "Generate Quiz":
    st.header("📄 Upload Study Material")
    st.write("Upload your notes or paste text to generate a quiz easily.")
    st.divider()
    st.subheader("📥 Input Content")
    input_mode = st.radio("Select Input Type", ["Paste Text", "Upload File"], horizontal=True)
    typed_text = ""
    uploaded = None

    if input_mode == "Paste Text":
        typed_text = st.text_area(
            "Paste learning content",
            height=220,
            placeholder="Paste chapters, notes, or transcript text here...",
        )
    else:
        upload_types = ["pdf", "docx", "wav"]
        if HAS_PYDUB and HAS_SR:
            upload_types.append("mp3")
        if HAS_MOVIEPY and HAS_SR:
            upload_types.append("mp4")

        uploaded = st.file_uploader(
            "Upload source file",
            type=upload_types,
            help=f"Supported formats now: {', '.join(ext.upper() for ext in upload_types)}",
        )
        missing_features = []
        if not HAS_PYDUB:
            missing_features.append("MP3 support needs pydub")
        if not HAS_MOVIEPY:
            missing_features.append("MP4 support needs moviepy")
        if not HAS_SR:
            missing_features.append("Audio/Video transcription needs SpeechRecognition")
        if missing_features:
            st.info("Optional features disabled: " + " | ".join(missing_features))
        if not HAS_OCR:
            st.caption("Scanned PDF OCR is disabled. Install `pytesseract` and `pdf2image` to enable it.")

    st.subheader("⚙️ Quiz Settings")
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)
    with ctrl_col1:
        question_count = st.slider("Number of questions", min_value=3, max_value=25, value=8)
    with ctrl_col2:
        difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"])
    with ctrl_col3:
        question_type = st.selectbox(
            "Question type",
            ["Multiple Choice Questions (MCQ)", "True/False", "Short Answer"],
        )

    if st.button("🚀 Generate Quiz", type="primary", use_container_width=True):
        with st.spinner("Extracting and processing content..."):
            processing_failed = False
            try:
                extracted_text, source_name = extract_input_text(input_mode, typed_text, uploaded)
            except Exception as exc:
                detail = str(exc).strip() or f"{exc.__class__.__name__} occurred while processing the input."
                st.error(f"Input processing failed: {detail}")
                extracted_text, source_name = "", ""
                processing_failed = True

            if processing_failed:
                st.stop()
            if not extracted_text:
                if input_mode == "Paste Text":
                    st.warning("No text found. Paste some content and retry.")
                else:
                    file_name = uploaded.name if uploaded else "the selected file"
                    st.warning(
                        f"No valid text could be extracted from {file_name}. "
                        "For PDFs, ensure the file contains selectable text (not scanned images)."
                    )
            else:
                progress = st.progress(0)
                progress.progress(35)
                questions = generate_quiz(extracted_text, question_count, difficulty, question_type)
                progress.progress(85)
                if not questions:
                    st.warning("Not enough content to build a quiz. Provide richer material.")
                else:
                    quiz_id = save_questions(
                        questions=questions,
                        source_name=source_name or "Typed Text",
                        metadata={
                            "difficulty": difficulty,
                            "question_type": question_type,
                            "question_count": len(questions),
                            "generated_at": datetime.utcnow().isoformat(),
                        },
                    )
                    st.session_state.answers = {}
                    st.session_state.quiz_submitted = False
                    progress.progress(100)
                    st.success(f"Quiz generated successfully. Quiz ID: {quiz_id}")
                    with st.expander("Extracted Content Preview"):
                        st.write(extracted_text[:2000] + ("..." if len(extracted_text) > 2000 else ""))
                    st.success("Quiz ready! Go to 'Take Quiz' from the sidebar.")

elif menu == "Take Quiz":
    st.header("🧠 Take Your Quiz")
    st.write("Answer step-by-step and submit at the end.")

    quiz = load_questions()
    questions = quiz["questions"] if isinstance(quiz, dict) else quiz
    quiz_meta = quiz.get("metadata", {}) if isinstance(quiz, dict) else {}

    if not questions:
        st.info("No quiz available. Generate one first.")
    else:
        st.caption(
            f"Questions: {len(questions)} | Type: {quiz_meta.get('question_type', 'N/A')} | Difficulty: {quiz_meta.get('difficulty', 'N/A')}"
        )

        # Ensure state exists
        if "current_q" not in st.session_state:
            st.session_state.current_q = 0

        if "answers" not in st.session_state:
            st.session_state.answers = {}

        idx = st.session_state.current_q
        question = questions[idx]

        # Progress
        st.markdown(f"### 📝 Question {idx + 1} / {len(questions)}")
        st.progress((idx + 1) / len(questions))

        st.divider()
        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"**{question['question']}**")
            
            # Question Input logic based on type
            q_type = question["type"]
            if q_type in ["Multiple Choice Questions (MCQ)", "MCQ"]:
                current_answer = st.session_state.answers.get(idx)
                try:
                    index = question["options"].index(current_answer) if current_answer is not None else None
                except ValueError:
                    index = None
                
                choice = st.radio(
                    "Choose one:",
                    question["options"],
                    index=index,
                    key=f"q_radio_{idx}"
                )
                st.session_state.answers[idx] = choice
            
            elif q_type == "True/False":
                current_answer = st.session_state.answers.get(idx)
                index = None
                if current_answer == "True":
                    index = 0
                elif current_answer == "False":
                    index = 1
                
                choice = st.radio(
                    "True or False?",
                    ["True", "False"],
                    index=index,
                    key=f"q_tf_{idx}"
                )
                st.session_state.answers[idx] = choice
            
            elif q_type == "Short Answer":
                ans = st.text_area(
                    "Your Answer:",
                    value=st.session_state.answers.get(idx, ""),
                    key=f"q_sa_{idx}"
                )
                st.session_state.answers[idx] = ans
            st.markdown('</div>', unsafe_allow_html=True)

        # Navigation
        col1, col2 = st.columns(2)

        with col1:
            if st.button("⬅️ Previous"):
                if idx > 0:
                    st.session_state.current_q -= 1
                    st.rerun()

        with col2:
            if st.button("Next ➡️"):
                if idx < len(questions) - 1:
                    st.session_state.current_q += 1
                    st.rerun()

        # Submit only on last question
        if idx == len(questions) - 1:
            if st.button("🚀 Submit Quiz", type="primary"):

                score = 0
                details = []
                difficulty_totals = defaultdict(lambda: {"correct": 0, "total": 0})

                for i, q in enumerate(questions):
                    user_answer = st.session_state.answers.get(i)
                    correct = evaluate_answer(q, user_answer)
                    score += int(correct)

                    diff = q.get("difficulty", "unknown")
                    difficulty_totals[diff]["total"] += 1
                    difficulty_totals[diff]["correct"] += int(correct)

                    details.append({
                        "index": i + 1,
                        "question": q["question"],
                        "user_answer": user_answer,
                        "correct_answer": q["answer"],
                        "is_correct": correct,
                    })

                percent = round((score / len(questions)) * 100, 2)

                save_attempt(
                    score=score,
                    total=len(questions),
                    user_name=candidate.strip() or "Guest",
                    details=details,
                    difficulty_breakdown=dict(difficulty_totals),
                )

                # Reset
                st.session_state.current_q = 0

                st.success("Quiz Submitted!")
                st.balloons()

                # Metrics
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Score", f"{score}/{len(questions)}")
                with col2:
                    st.metric("Accuracy", f"{percent}%")
                with col3:
                    st.metric("Wrong", len(questions) - score)

                # Feedback
                if percent >= 80:
                    st.balloons()
                    st.success("🔥 Excellent performance!")
                elif percent >= 50:
                    st.info("👍 Good, but can improve.")
                else:
                    st.warning("⚠️ Needs improvement.")

                # Weak Areas
                weak_areas = []
                for diff, stats in difficulty_totals.items():
                    if stats["correct"] / stats["total"] < 0.5:
                        weak_areas.append(diff)

                if weak_areas:
                    st.warning(f"⚠️ Weak in: {', '.join(weak_areas)}")

                # Review Answers
                with st.expander("📊 Review Answers", expanded=True):
                    for item in details:
                        if item["is_correct"]:
                            st.success(f"Q{item['index']} - Correct ✅")
                        else:
                            st.error(f"Q{item['index']} - Incorrect ❌")

                        st.write(f"Your answer: {item['user_answer']}")
                        if not item["is_correct"]:
                            st.write(f"Correct answer: {item['correct_answer']}")

                        st.divider()

elif menu == "Analytics Dashboard":
    st.subheader("Quiz Analytics Dashboard")
    dataset = load_attempts(limit=200)
    render_dashboard(dataset)
