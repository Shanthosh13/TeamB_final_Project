import hashlib
import json
from datetime import datetime

from db import ensure_database, get_connection


def _connect():
    ensure_database()
    connection = get_connection()
    return connection


def _column_exists(connection, table, column):
    cursor = connection.cursor()
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    exists = cursor.fetchone() is not None
    cursor.close()
    return exists


def init_db():
    with _connect() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS quizzes (
                id INT PRIMARY KEY AUTO_INCREMENT,
                questions_json LONGTEXT NOT NULL,
                source_name VARCHAR(255),
                metadata_json LONGTEXT,
                created_at VARCHAR(64) NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_name VARCHAR(255) NOT NULL,
                score INT NOT NULL,
                total INT NOT NULL,
                percentage DOUBLE NOT NULL,
                details_json LONGTEXT,
                difficulty_breakdown_json LONGTEXT,
                submitted_at VARCHAR(64) NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(64) NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
            """
        )

        if not _column_exists(connection, "quizzes", "metadata_json"):
            cursor.execute("ALTER TABLE quizzes ADD COLUMN metadata_json LONGTEXT NULL")

        if not _column_exists(connection, "attempts", "details_json"):
            cursor.execute("ALTER TABLE attempts ADD COLUMN details_json LONGTEXT NULL")

        if not _column_exists(connection, "attempts", "difficulty_breakdown_json"):
            cursor.execute("ALTER TABLE attempts ADD COLUMN difficulty_breakdown_json LONGTEXT NULL")

        connection.commit()
        cursor.close()


def _hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def register_user(username, password):
    cleaned = (username or "").strip()
    if len(cleaned) < 3:
        return False, "Username must be at least 3 characters."
    if len(password or "") < 6:
        return False, "Password must be at least 6 characters."

    with _connect() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (cleaned,))
        if cursor.fetchone():
            cursor.close()
            return False, "Username already exists."

        cursor.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (%s, %s, %s)
            """,
            (cleaned, _hash_password(password), datetime.utcnow().isoformat()),
        )
        connection.commit()
        cursor.close()
    return True, "Registration successful."


def authenticate_user(username, password):
    cleaned = (username or "").strip()
    if not cleaned or not password:
        return False, None

    with _connect() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT username, password_hash
            FROM users
            WHERE LOWER(username) = LOWER(%s)
            LIMIT 1
            """,
            (cleaned,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return False, None

        if row["password_hash"] != _hash_password(password):
            return False, None

        return True, row["username"]


def save_questions(questions, source_name="Uploaded File", metadata=None):
    metadata = metadata or {}
    created_at = datetime.utcnow().isoformat()
    with _connect() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO quizzes (questions_json, source_name, metadata_json, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (json.dumps(questions), source_name, json.dumps(metadata), created_at),
        )
        connection.commit()
        quiz_id = cursor.lastrowid
        cursor.close()
        return quiz_id


def load_questions():
    with _connect() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT questions_json, source_name, metadata_json, created_at
            FROM quizzes
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"questions": [], "metadata": {}, "source_name": None}

        return {
            "questions": json.loads(row["questions_json"]),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "source_name": row["source_name"],
            "created_at": row["created_at"],
        }


def save_attempt(score, total, user_name="Guest", details=None, difficulty_breakdown=None):
    details = details or []
    difficulty_breakdown = difficulty_breakdown or {}
    percentage = (score / total * 100) if total else 0.0
    submitted_at = datetime.utcnow().isoformat()
    with _connect() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO attempts (
                user_name, score, total, percentage, details_json, difficulty_breakdown_json, submitted_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_name,
                score,
                total,
                percentage,
                json.dumps(details),
                json.dumps(difficulty_breakdown),
                submitted_at,
            ),
        )
        connection.commit()
        attempt_id = cursor.lastrowid
        cursor.close()
        return attempt_id


def load_attempts(limit=20):
    with _connect() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT user_name, score, total, percentage, details_json, difficulty_breakdown_json, submitted_at
            FROM attempts
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        cursor.close()

    percentages = [row["percentage"] for row in rows]
    recent = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.get("details_json") or "[]")
        item["difficulty_breakdown"] = json.loads(item.get("difficulty_breakdown_json") or "{}")
        item.pop("details_json", None)
        item.pop("difficulty_breakdown_json", None)
        recent.append(item)

    return {
        "tests_taken": len(rows),
        "percentages": list(reversed(percentages)),
        "recent": recent,
    }
