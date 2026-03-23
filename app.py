import random
import re
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
import pdfplumber
import streamlit as st
from docx import Document
import os
import json
from dotenv import load_dotenv
from groq import Groq
import math

load_dotenv()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


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


def chunk_text(text, max_chars=12000):
    """
    Splits text into chunks, respecting paragraph breaks where possible,
    to ensure we don't exceed API token limits.
    """
    paragraphs = text.split('\n')
    chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        # If adding the next paragraph keeps us under the limit, add it
        if len(current_chunk) + len(p) < max_chars:
            current_chunk += p + "\n"
        else:
            # Otherwise, save the current chunk and start a new one
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            # Edge case: What if a single paragraph is massive? Force split it.
            if len(p) > max_chars:
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i:i+max_chars])
                current_chunk = ""
            else:
                current_chunk = p + "\n"
                
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks


def generate_quiz(text, question_count, difficulty, question_type):
    """
    Generates quiz questions using the Groq API with chunking and rate-limit handling.
    """
    chunks = chunk_text(text, max_chars=12000) # Safe limit for ~6000 TPM
    all_questions = []
    
    # Figure out roughly how many questions we need per chunk to hit the user's total
    questions_per_chunk = math.ceil(question_count / len(chunks))
    
    for i, chunk in enumerate(chunks):
        # Stop if we already have enough questions
        if len(all_questions) >= question_count:
            break
            
        # Determine how many questions to ask for in this specific request
        requested_count = min(questions_per_chunk, question_count - len(all_questions))
        
        prompt = f"""
        You are an expert educator. Create a {requested_count}-question {question_type} quiz based on the text below.
        The difficulty level should be {difficulty}.

        Text:
        \"\"\"{chunk}\"\"\"

        Output the result strictly in the following JSON format. Ensure the key is "questions" and the value is a list of objects.
        {{
            "questions": [
                {{
                    "question": "The question text",
                    "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
                    "answer": "The correct answer",
                    "type": "{question_type}",
                    "difficulty": "{difficulty.lower()}"
                }}
            ]
        }}
        """

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant designed to output strict JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3 
                )
                
                result_json = json.loads(response.choices[0].message.content)
                chunk_questions = result_json.get("questions", [])
                all_questions.extend(chunk_questions)
                
                # Success! Break out of the retry loop and move to the next chunk
                break 
                
            except Exception as e:
                error_msg = str(e).lower()
                # If we hit a rate limit (413 or 429), pause for 60 seconds
                if "rate_limit" in error_msg or "429" in error_msg or "413" in error_msg or "too large" in error_msg:
                    st.warning(f"⏳ API rate limit reached on chunk {i+1}/{len(chunks)}. Waiting 60 seconds to cool down... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(60) 
                else:
                    st.error(f"Error generating quiz from chunk {i+1}: {e}")
                    break # Break on non-rate-limit errors so we don't infinitely retry a bad prompt

    # Return exactly the number of questions the user asked for (trim excess if any)
    return all_questions[:question_count]


def evaluate_short_answer(question, correct_answer, user_answer):
    """
    Uses Groq to evaluate subjective short answers semantically.
    """
    prompt = f"""
    You are an educator grading a short answer question.
    Question: "{question}"
    Correct Answer/Rubric: "{correct_answer}"
    Student's Answer: "{user_answer}"
    
    Assess if the student's answer is correct based on the core meaning, even if phrased differently.
    Return JSON only: {{"is_correct": true/false, "feedback": "Short explanation of why"}}
    """
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant", # Smaller, faster model is fine for grading
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"is_correct": False, "feedback": f"Evaluation error: {str(e)}"}


def evaluate_answer(question_data, user_answer):
    """
    Evaluates a single user's answer against the question data.
    Returns True if correct, False otherwise.
    """
    # Handle cases where the user left the answer blank
    if user_answer is None:
        user_answer = ""
        
    user_ans = str(user_answer).strip()
    correct_ans = str(question_data.get("answer", ""))
    q_type = question_data.get("type", "")

    # Exact match logic for Objective questions
    if q_type in ["Multiple Choice Questions (MCQ)", "True/False", "MCQ"]:
        return user_ans.lower() == correct_ans.lower()
    
    # LLM Evaluation logic for Subjective questions
    elif q_type == "Short Answer":
        if not user_ans: # If blank, it's automatically wrong
            return False
            
        eval_result = evaluate_short_answer(
            question=question_data["question"],
            correct_answer=correct_ans,
            user_answer=user_ans
        )
        return eval_result.get("is_correct", False)
        
    return False


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

if "splash_done" not in st.session_state:
    st.session_state.splash_done = False

st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;800&display=swap');
        
        html, body, [class*="css"]  {
            font-family: 'Poppins', sans-serif;
        }

        .stApp {
            background: linear-gradient(-45deg, #ee7752, #e73c7e, #23a6d5, #23d5ab);
            background-size: 400% 400%;
            animation: gradientBG 15s ease infinite;
        }
        
        @keyframes gradientBG {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        /* Glassmorphism Block Container */
        .block-container {
            background: rgba(255, 255, 255, 0.45);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 20px;
            border: 1px solid rgba(255, 255, 255, 0.5);
            padding: 40px !important;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.15);
            margin-top: 2rem;
            margin-bottom: 2rem;
        }

        .hero {
            background: linear-gradient(115deg, #ff0844 0%, #ffb199 100%);
            color: white;
            border-radius: 20px;
            padding: 30px 40px;
            margin-bottom: 24px;
            box-shadow: 0 10px 40px rgba(255, 8, 68, 0.3);
            text-align: center;
        }
        
        .hero h1 {
            margin: 0;
            font-size: 3rem;
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }
        .hero p {
            margin: 10px 0 0;
            opacity: 0.95;
            font-size: 1.1rem;
            font-weight: 300;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.65);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.8);
            box-shadow: 0 8px 32px rgba(31, 38, 135, 0.05);
            transition: transform 0.3s ease;
        }
        .card:hover {
            transform: scale(1.02);
            box-shadow: 0 15px 40px rgba(31, 38, 135, 0.15);
        }
        
        /* Vibrant Buttons */
        div.stButton > button {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            color: white !important;
            border: none;
            border-radius: 50px;
            padding: 12px 28px;
            font-weight: 600;
            font-size: 1.1rem;
            box-shadow: 0 4px 15px rgba(118, 75, 162, 0.4);
            transition: all 0.3s ease;
            width: 100%;
        }
        div.stButton > button:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(118, 75, 162, 0.6);
            color: white !important;
        }

        /* Input fields */
        .stTextInput>div>div>input, .stTextArea textarea {
            border-radius: 12px;
            background: rgba(255,255,255,0.7) !important;
            border: 2px solid rgba(255,255,255,0.9) !important;
            transition: all 0.3s ease;
        }
        .stTextInput>div>div>input:focus, .stTextArea textarea:focus {
            box-shadow: 0 0 15px rgba(118, 75, 162, 0.3) !important;
            border-color: #764ba2 !important;
        }
        
        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.3);
            backdrop-filter: blur(15px);
            border-right: 1px solid rgba(255, 255, 255, 0.5);
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Splash Screen ---
if not st.session_state.splash_done:
    splash_placeholder = st.empty()
    with splash_placeholder.container():
        st.markdown(
            """
            <div style="
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                height: 70vh;
                text-align: center;
            ">
                <h1 style="
                    font-size: 5rem;
                    font-weight: 800;
                    color: white;
                    text-shadow: 0 10px 30px rgba(0,0,0,0.3);
                    margin-bottom: 20px;
                    animation: fadeIn 2s ease-in-out;
                ">SmartQuizzer</h1>
                <p style="
                    font-size: 1.5rem;
                    color: rgba(255,255,255,0.9);
                    font-weight: 300;
                    animation: fadeIn 3s ease-in-out;
                ">The Future of AI-Powered Learning</p>
                <div style="
                    margin-top: 40px;
                    width: 50px;
                    height: 50px;
                    border: 5px solid rgba(255,255,255,0.3);
                    border-radius: 50%;
                    border-top-color: white;
                    animation: spin 1s linear infinite;
                "></div>
            </div>
            <style>
                @keyframes fadeIn {
                    0% { opacity: 0; transform: translateY(20px); }
                    100% { opacity: 1; transform: translateY(0); }
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
            """,
            unsafe_allow_html=True
        )
        time.sleep(2)
    st.session_state.splash_done = True
    st.rerun()




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
                    st.session_state.menu_selection = "Take Quiz"
                    st.rerun()

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

        # Show only current question
        current_idx = st.session_state.current_q
        if current_idx < len(questions):
            question = questions[current_idx]
            st.divider()
            st.markdown(f"### Question {current_idx + 1} of {len(questions)}")
            
            with st.container():
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown(f"**{question['question']}**")
                
                # Question Input logic based on type
                if question["type"] in ["Multiple Choice Questions (MCQ)", "MCQ"]:
                    choice = st.radio(
                        "Choose one:",
                        question["options"],
                        index=None if current_idx not in st.session_state.answers else question["options"].index(st.session_state.answers[current_idx]),
                        key=f"q_radio_{current_idx}"
                    )
                    st.session_state.answers[current_idx] = choice
                
                elif question["type"] == "True/False":
                    choice = st.radio(
                        "True or False?",
                        ["True", "False"],
                        index=None if current_idx not in st.session_state.answers else (0 if st.session_state.answers[current_idx] == "True" else 1),
                        key=f"q_tf_{current_idx}"
                    )
                    st.session_state.answers[current_idx] = choice
                
                elif question["type"] == "Short Answer":
                    ans = st.text_area(
                        "Your Answer:",
                        value=st.session_state.answers.get(current_idx, ""),
                        key=f"q_sa_{current_idx}"
                    )
                    st.session_state.answers[current_idx] = ans
                st.markdown('</div>', unsafe_allow_html=True)

            # Navigation buttons
            nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 1])
            with nav_col1:
                if st.button("⬅️ Previous", disabled=(current_idx == 0)):
                    st.session_state.current_q -= 1
                    st.rerun()
            
            with nav_col3:
                if current_idx < len(questions) - 1:
                    if st.button("Next ➡️"):
                        st.session_state.current_q += 1
                        st.rerun()
                else:
                    if st.button("✅ Submit Quiz", type="primary"):
                        # EVALUATION LOGIC
                        with st.spinner("Grading your quiz..."):
                            score = 0
                            details = []
                            diff_totals = defaultdict(lambda: {"correct": 0, "total": 0})
                            
                            for i, q in enumerate(questions):
                                user_ans = st.session_state.answers.get(i, "")
                                is_correct = evaluate_answer(q, user_ans)
                                
                                if is_correct:
                                    score += 1
                                    diff_totals[q["difficulty"]]["correct"] += 1
                                
                                diff_totals[q["difficulty"]]["total"] += 1
                                details.append({
                                    "question": q["question"],
                                    "user_answer": user_ans,
                                    "correct_answer": q["answer"],
                                    "is_correct": is_correct,
                                    "type": q["type"]
                                })
                            
                            save_attempt(
                                score=score,
                                total=len(questions),
                                user_name=st.session_state.auth_user,
                                details=details,
                                difficulty_breakdown=dict(diff_totals)
                            )
                            st.session_state.quiz_submitted = True
                            st.session_state.results = {
                                "score": score,
                                "total": len(questions),
                                "details": details
                            }
                            st.rerun()

        # RESULTS PAGE
        if st.session_state.get("quiz_submitted"):
            res = st.session_state.results
            st.balloons()
            st.header("🎊 Quiz Results")
            st.metric("Final Score", f"{res['score']} / {res['total']}", delta=f"{int(res['score']/res['total']*100)}%")
            
            with st.expander("Review Your Answers", expanded=True):
                for i, d in enumerate(res['details']):
                    color = "green" if d['is_correct'] else "red"
                    st.markdown(f"**Q{i+1}: {d['question']}**")
                    st.markdown(f"<span style='color:{color}'>Your Answer: {d['user_answer']}</span>", unsafe_allow_html=True)
                    if not d['is_correct']:
                        st.markdown(f"**Correct Answer:** {d['correct_answer']}")
                    st.divider()
            
            if st.button("🔄 Try Another Quiz"):
                st.session_state.current_q = 0
                st.session_state.quiz_submitted = False
                st.session_state.answers = {}
                st.session_state.menu_selection = "Generate Quiz"
                st.rerun()

elif menu == "Analytics Dashboard":
    st.subheader("Quiz Analytics Dashboard")
    dataset = load_attempts(limit=200)
    render_dashboard(dataset)
