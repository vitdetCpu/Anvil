import sqlite3
import os
from flask import Flask, request, jsonify, g

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Recreate DB with seed data on every start."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "password TEXT NOT NULL,"
        "email TEXT)"
    )
    seed_users = [
        ("admin", "admin123", "admin@example.com"),
        ("alice", "password", "alice@example.com"),
        ("bob", "letmein", "bob@example.com"),
    ]
    db.executemany(
        "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
        seed_users,
    )
    db.commit()
    db.close()


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")
    email = data.get("email", "")
    db = get_db()
    # VULNERABLE: raw SQL, no sanitization, no password requirements
    db.execute(
        f"INSERT INTO users (username, password, email) VALUES ('{username}', '{password}', '{email}')"
    )
    db.commit()
    return jsonify({"message": f"User {username} created"}), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")
    db = get_db()
    # VULNERABLE: raw SQL injection
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    user = db.execute(query).fetchone()
    if user:
        return jsonify({"message": "Login successful", "user": dict(user)})
    return jsonify({"message": "Invalid credentials"}), 401


@app.route("/profile/<int:user_id>")
def profile(user_id):
    db = get_db()
    # VULNERABLE: no auth check (IDOR), XSS in username
    user = db.execute(f"SELECT * FROM users WHERE id={user_id}").fetchone()
    if user:
        return jsonify({
            "username": user["username"],
            "email": user["email"],
            "id": user["id"]
        })
    return jsonify({"error": "User not found"}), 404


if __name__ == "__main__":
    init_db()
    app.run(port=5050, debug=False)
