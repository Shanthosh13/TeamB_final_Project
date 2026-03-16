import random
import re
import tempfile
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
    prompt = re.sub(rf"\b{re.escape(answer)}\b", "_____", sentence, count=1, flags=re.IGNORECASE)
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


st.set_page_config(page_title=APP_TITLE, page_icon="??", layout="wide")
init_db()

if "quiz_submitted" not in st.session_state:
    st.session_state.quiz_submitted = False

if "answers" not in st.session_state:
    st.session_state.answers = {}

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

st.markdown(
    """
    <style>
        :root {
            --surface: #f4f7fb;
            --card: #ffffff;
            --ink: #0f172a;
            --muted: #475467;
            --accent: #0e7490;
            --soft: #dbeafe;
        }
        .stApp {
            background: radial-gradient(circle at top right, #e0f2fe 0%, #f8fafc 45%, #eef2ff 100%);
        }
        .hero {
            background: linear-gradient(115deg, #312e81 0%, #b45309 55%, #dc2626 100%);
            color: white;
            border-radius: 16px;
            padding: 20px 24px;
            margin-bottom: 18px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.18);
        }
        .hero h1 {
            margin: 0;
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: 0.2px;
        }
        .hero p {
            margin: 8px 0 0;
            opacity: 0.92;
            font-size: 0.98rem;
        }
        .card {
            background: var(--card);
            border: 1px solid #dbe2ea;
            border-radius: 14px;
            padding: 14px;
            box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
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

menu = st.sidebar.radio("Navigate", ["Generate Quiz", "Take Quiz", "Analytics Dashboard"])
candidate = st.session_state.auth_user
history = load_attempts(limit=200)

with st.sidebar:
    st.markdown(f"**Logged in as:** `{candidate}`")
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
    st.subheader("Input Content")
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

    st.subheader("Quiz Generation Controls")
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

    if st.button("Generate Quiz", type="primary", use_container_width=True):
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

elif menu == "Take Quiz":
    st.subheader("Interactive Quiz")
    quiz = load_questions()
    questions = quiz["questions"] if isinstance(quiz, dict) else quiz
    quiz_meta = quiz.get("metadata", {}) if isinstance(quiz, dict) else {}

    if not questions:
        st.info("No quiz available. Generate one first.")
    else:
        st.caption(
            f"Questions: {len(questions)} | Type: {quiz_meta.get('question_type', 'N/A')} | Difficulty: {quiz_meta.get('difficulty', 'N/A')}"
        )
        for idx, question in enumerate(questions, start=1):
            with st.container():
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown(f"**Q{idx}. {question['question']}**")
                st.caption(f"Type: {question['type']} | Difficulty: {question.get('difficulty', 'unknown').title()}")

                if question["type"] in {"MCQ", "True/False"}:
                    answer = st.radio(
                        f"Answer {idx}",
                        question["options"],
                        index=None,
                        key=f"q_{idx}",
                        label_visibility="collapsed",
                    )
                else:
                    answer = st.text_input(
                        f"Answer {idx}",
                        key=f"q_{idx}",
                        placeholder="Write your short answer...",
                        label_visibility="collapsed",
                    )
                st.session_state.answers[idx] = answer
                st.markdown("</div>", unsafe_allow_html=True)
                st.write("")

        if st.button("Submit Quiz", type="primary", use_container_width=True):
            details = []
            difficulty_totals = defaultdict(lambda: {"correct": 0, "total": 0})
            score = 0
            for idx, question in enumerate(questions, start=1):
                user_answer = st.session_state.answers.get(idx)
                correct = evaluate_answer(question, user_answer)
                score += int(correct)
                diff = question.get("difficulty", "unknown")
                difficulty_totals[diff]["total"] += 1
                difficulty_totals[diff]["correct"] += int(correct)
                details.append(
                    {
                        "index": idx,
                        "question": question["question"],
                        "type": question["type"],
                        "difficulty": diff,
                        "user_answer": user_answer,
                        "correct_answer": question["answer"],
                        "is_correct": correct,
                    }
                )

            save_attempt(
                score=score,
                total=len(questions),
                user_name=candidate.strip() or "Guest",
                details=details,
                difficulty_breakdown=dict(difficulty_totals),
            )
            st.session_state.quiz_submitted = True
            percent = round((score / len(questions)) * 100, 2) if questions else 0.0
            st.success(f"Score: {score}/{len(questions)} ({percent}%)")
            st.progress(score / len(questions))

            with st.expander("Review Answers", expanded=True):
                for item in details:
                    verdict = "Correct" if item["is_correct"] else "Incorrect"
                    st.write(f"Q{item['index']}: {verdict}")
                    st.write(f"Your answer: {item['user_answer']}")
                    if not item["is_correct"]:
                        st.write(f"Correct answer: {item['correct_answer']}")
                    st.divider()

elif menu == "Analytics Dashboard":
    st.subheader("Quiz Analytics Dashboard")
    dataset = load_attempts(limit=200)
    rows = dataset["recent"]
    if not rows:
        st.info("No attempts yet. Submit a quiz to populate analytics.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Attempts", len(rows))
        with col2:
            best_pct = round(max(dataset["percentages"]), 2) if dataset["percentages"] else 0
            st.metric("Best Accuracy", f"{best_pct}%")
        with col3:
            avg_pct = round(sum(dataset["percentages"]) / len(dataset["percentages"]), 2)
            st.metric("Average Accuracy", f"{avg_pct}%")

        progress_df = pd.DataFrame(
            {"Attempt": list(range(1, len(dataset["percentages"]) + 1)), "Accuracy": dataset["percentages"]}
        )
        st.plotly_chart(
            px.line(progress_df, x="Attempt", y="Accuracy", markers=True, title="Accuracy Trend", template="plotly_white"),
            use_container_width=True,
        )

        st.plotly_chart(
            px.histogram(
                pd.DataFrame(rows),
                x="percentage",
                nbins=10,
                title="Score Percentage Distribution",
                labels={"percentage": "Accuracy (%)"},
                template="plotly_white",
            ),
            use_container_width=True,
        )

        breakdown = defaultdict(lambda: {"correct": 0, "total": 0})
        for row in rows:
            for diff, values in row.get("difficulty_breakdown", {}).items():
                breakdown[diff]["correct"] += values.get("correct", 0)
                breakdown[diff]["total"] += values.get("total", 0)

        if breakdown:
            diff_rows = []
            for diff, values in breakdown.items():
                accuracy = (values["correct"] / values["total"] * 100) if values["total"] else 0
                diff_rows.append({"Difficulty": diff.title(), "Accuracy": round(accuracy, 2)})
            diff_df = pd.DataFrame(diff_rows)
            st.plotly_chart(
                px.bar(diff_df, x="Difficulty", y="Accuracy", color="Difficulty", title="Difficulty Breakdown"),
                use_container_width=True,
            )

        table = pd.DataFrame(rows)[["user_name", "score", "total", "percentage", "submitted_at"]]
        table = table.rename(
            columns={
                "user_name": "User",
                "score": "Score",
                "total": "Total",
                "percentage": "Accuracy %",
                "submitted_at": "Submitted At (UTC)",
            }
        )
        st.dataframe(table, use_container_width=True, hide_index=True)
