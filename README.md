<<<<<<< HEAD
# SmartQuizzer-project
=======
# SmartQuizzer Live

SmartQuizzer Live is an AI-powered quiz generator built using Python and Streamlit.

## Features

- Generate quizzes from pasted text, PDFs, DOCX, audio, and video
- AI-style quiz generation with MCQ, True/False, and short-answer modes
- Interactive analytics dashboard
- MySQL backend storage for users, quizzes, and attempts
- Live performance tracking

## Tech Stack

- Python
- Streamlit
- Pandas
- Plotly
- MySQL

## Run Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Set MySQL connection details if needed:

```powershell
$env:MYSQL_HOST="localhost"
$env:MYSQL_PORT="3306"
$env:MYSQL_USER="root"
$env:MYSQL_PASSWORD=""
$env:MYSQL_DATABASE="smartquizz"
```

Run the app:

```bash
streamlit run app.py
```

## Backend Database

The app connects to MySQL and automatically creates the required tables in the `smartquizz` database:

- `users`
- `quizzes`
- `attempts`

## Project Structure

```text
SmartQuizzer
|-- app.py
|-- db.py
|-- analytics.py
|-- question_generator.py
|-- quiz_engine.py
|-- text_extractor.py
|-- utils/
|   `-- storage.py
`-- requirements.txt
```
>>>>>>> b5b67ef (Added SmartQuizzer project files)
