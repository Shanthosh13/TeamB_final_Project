# 🧠 SmartQuizzer Live

SmartQuizzer Live is an AI-powered quiz generator built using **Python and Streamlit**.

The application allows users to upload study materials and automatically generate quizzes with analytics.

---

## 🚀 Features

* 📄 Generate quizzes from uploaded **PDF notes**
* 🧠 AI-based quiz generation
* 📊 Interactive **analytics dashboard**
* 🗂 Backend **SQLite database storage**
* 📈 Live performance tracking
* 🧪 Stores quiz attempts and scores

---

## 🛠 Tech Stack

* Python
* Streamlit
* SQLite Database
* Matplotlib / Plotly
* NLP Concepts

---

## ▶️ Run Locally

Install dependencies:

```
pip install -r requirements.txt
```

Run the application:

```
streamlit run app.py
```

---

## 🗄 Backend Database

The backend database is automatically created at:

```
data/smartquizzer.db
```

It stores:

* Generated quizzes
* User attempts
* Scores
* Analytics data

---

## 📂 Project Structure

```
SmartQuizzer
│
├── app.py
├── text_extractor.py
├── question_generator.py
├── quiz_engine.py
├── analytics.py
│
├── data
│   ├── smartquizzer.db
│   ├── questions.json
│   └── attempts.json
│
├── utils
│   └── storage.py
│
└── requirements.txt
```

---

## 📦 GitHub Setup

If you are uploading the project for the first time:

```
git init
git add .
git commit -m "SmartQuizzer AI Quiz Generator"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

If the repository already exists:

```
git add .
git commit -m "Updated SmartQuizzer project"
git push
```

---

## 📊 Future Improvements

* AI-based question generation using LLMs
* Adaptive difficulty engine
* Leaderboard system
* Online deployment with Streamlit Cloud
* User authentication system

---

## 👨‍💻 Author

**Shiva Kashboina**
