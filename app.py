import base64
import csv
import hashlib
import hmac
import io
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory

from seed_data import QUESTIONS, STUDENTS


app = Flask(__name__, static_folder="static", static_url_path="")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-before-production")
app.config["DATABASE"] = os.environ.get(
    "DATABASE_PATH", os.path.join(app.instance_path, "exam.sqlite")
)
BOOTSTRAPPED = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ok(data=None, message="操作成功"):
    return jsonify({"code": 200, "message": message, "data": data})


def fail(message, code=400):
    return jsonify({"code": code, "message": message, "data": None}), code


def db():
    if "db" not in g:
        os.makedirs(app.instance_path, exist_ok=True)
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def row_dict(row):
    return dict(row) if row else None


def sign(payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    body64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = hmac.new(app.config["SECRET_KEY"].encode(), body64.encode(), hashlib.sha256)
    return f"{body64}.{sig.hexdigest()}"


def verify_token(token):
    try:
        body64, signature = token.split(".", 1)
        expected = hmac.new(
            app.config["SECRET_KEY"].encode(), body64.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(body64) % 4)
        payload = json.loads(base64.urlsafe_b64decode((body64 + padding).encode()))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def hash_password(password):
    salt = app.config["SECRET_KEY"][:16]
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"sha256${digest}"


def verify_password(stored_hash, password):
    return hmac.compare_digest(stored_hash or "", hash_password(password))


def auth_required(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header.replace("Bearer ", "", 1).strip()
            payload = verify_token(token)
            if not payload or payload.get("role") != role:
                return fail("登录已失效，请重新登录", 401)
            g.user = payload
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def init_schema():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teachers (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS papers (
            paper_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            total_score INTEGER NOT NULL,
            duration_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            question_no INTEGER NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer TEXT,
            score INTEGER NOT NULL,
            keywords_json TEXT,
            FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
        );
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            start_time TEXT,
            end_time TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
        );
        CREATE TABLE IF NOT EXISTS exam_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            confirm_name TEXT,
            login_time TEXT,
            start_time TEXT,
            submit_time TEXT,
            status TEXT NOT NULL,
            objective_score REAL DEFAULT 0,
            subjective_score REAL DEFAULT 0,
            total_score REAL DEFAULT 0,
            forced INTEGER DEFAULT 0,
            UNIQUE(exam_id, student_id),
            FOREIGN KEY (exam_id) REFERENCES exams(id),
            FOREIGN KEY (student_id) REFERENCES students(student_id)
        );
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            answer_text TEXT,
            score REAL DEFAULT 0,
            grading_method TEXT NOT NULL DEFAULT 'auto',
            updated_at TEXT NOT NULL,
            UNIQUE(record_id, question_id),
            FOREIGN KEY (record_id) REFERENCES exam_records(id),
            FOREIGN KEY (question_id) REFERENCES questions(id)
        );
        """
    )
    conn.commit()


def seed():
    conn = db()
    init_schema()
    conn.execute(
        """
        INSERT INTO teachers(username, password_hash) VALUES(?, ?)
        ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
        """,
        ("admin", hash_password("admin123")),
    )
    for student in STUDENTS:
        password = student["student_id"][-5:]
        conn.execute(
            """
            INSERT INTO students(student_id, name, class_name, password_hash, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                name = excluded.name,
                class_name = excluded.class_name,
                password_hash = excluded.password_hash
            """,
            (
                student["student_id"],
                student["name"],
                student["class_name"],
                hash_password(password),
                now_iso(),
            ),
        )
    paper_id = "P20260604001"
    total_score = sum(q["score"] for q in QUESTIONS)
    conn.execute(
        """
        INSERT OR IGNORE INTO papers(paper_id, title, total_score, duration_minutes, created_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (paper_id, "局域网考试系统课堂测试", total_score, 90, now_iso()),
    )
    existing = conn.execute(
        "SELECT COUNT(*) AS count FROM questions WHERE paper_id = ?", (paper_id,)
    ).fetchone()["count"]
    if existing == 0:
        for question in QUESTIONS:
            conn.execute(
                """
                INSERT INTO questions(paper_id, question_no, type, content, options_json, answer, score, keywords_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    question["question_no"],
                    question["type"],
                    question["content"],
                    json.dumps(question.get("options", []), ensure_ascii=False),
                    question.get("answer", ""),
                    question["score"],
                    json.dumps(question.get("keywords", []), ensure_ascii=False),
                ),
            )
    exam_count = conn.execute("SELECT COUNT(*) AS count FROM exams").fetchone()["count"]
    if exam_count == 0:
        conn.execute(
            """
            INSERT INTO exams(name, paper_id, status, duration_minutes, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            ("25移动互联3-1班 Web 期末模拟考试", paper_id, "waiting", 90, now_iso()),
        )
    conn.commit()


def current_exam():
    return db().execute(
        "SELECT * FROM exams ORDER BY id DESC LIMIT 1"
    ).fetchone()


def get_or_create_record(exam_id, student_id, status="logged_in"):
    conn = db()
    conn.execute(
        """
        INSERT OR IGNORE INTO exam_records(exam_id, student_id, login_time, status)
        VALUES(?, ?, ?, ?)
        """,
        (exam_id, student_id, now_iso(), status),
    )
    record = conn.execute(
        "SELECT * FROM exam_records WHERE exam_id = ? AND student_id = ?",
        (exam_id, student_id),
    ).fetchone()
    conn.commit()
    return record


def questions_for_paper(paper_id, include_answer=False):
    rows = db().execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY question_no", (paper_id,)
    ).fetchall()
    items = []
    for row in rows:
        item = row_dict(row)
        item["options"] = json.loads(item.pop("options_json") or "[]")
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        if not include_answer:
            item.pop("answer", None)
            item.pop("keywords", None)
        return_id = item.pop("id")
        item["question_id"] = return_id
        items.append(item)
    return items


def normalize_answer(value):
    if isinstance(value, list):
        return ",".join(sorted(str(v) for v in value))
    return str(value).strip()


def save_answers(record_id, answers):
    conn = db()
    questions = conn.execute("SELECT id FROM questions").fetchall()
    valid_ids = {str(q["id"]) for q in questions}
    for question_id, answer in answers.items():
        qid = str(question_id)
        if qid not in valid_ids:
            continue
        conn.execute(
            """
            INSERT INTO answers(record_id, question_id, answer_text, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(record_id, question_id)
            DO UPDATE SET answer_text = excluded.answer_text, updated_at = excluded.updated_at
            """,
            (record_id, int(qid), normalize_answer(answer), now_iso()),
        )
    conn.commit()


def grade_record(record_id):
    conn = db()
    rows = conn.execute(
        """
        SELECT a.id AS answer_id, a.answer_text, q.type, q.answer, q.score
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        WHERE a.record_id = ?
        """,
        (record_id,),
    ).fetchall()
    objective = 0
    subjective = 0
    for row in rows:
        if row["type"] in ("single_choice", "multiple_choice", "true_false"):
            score = row["score"] if normalize_answer(row["answer"]) == normalize_answer(row["answer_text"]) else 0
            objective += score
            conn.execute(
                "UPDATE answers SET score = ?, grading_method = 'auto' WHERE id = ?",
                (score, row["answer_id"]),
            )
        else:
            subjective += float(
                conn.execute(
                    "SELECT score FROM answers WHERE id = ?", (row["answer_id"],)
                ).fetchone()["score"]
                or 0
            )
    conn.execute(
        """
        UPDATE exam_records
        SET objective_score = ?, subjective_score = ?, total_score = ?
        WHERE id = ?
        """,
        (objective, subjective, objective + subjective, record_id),
    )
    conn.commit()


def record_payload(record):
    student = db().execute(
        "SELECT student_id, name, class_name FROM students WHERE student_id = ?",
        (record["student_id"],),
    ).fetchone()
    answers = db().execute(
        "SELECT question_id, answer_text FROM answers WHERE record_id = ?", (record["id"],)
    ).fetchall()
    return {
        **row_dict(record),
        "student": row_dict(student),
        "answers": {str(a["question_id"]): a["answer_text"] for a in answers},
    }


@app.before_request
def bootstrap():
    global BOOTSTRAPPED
    if not BOOTSTRAPPED:
        seed()
        BOOTSTRAPPED = True


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/student/login", methods=["POST"])
def student_login():
    data = request.get_json(force=True)
    student_id = str(data.get("student_id", "")).strip()
    password = str(data.get("password", "")).strip()
    student = db().execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not student or not verify_password(student["password_hash"], password):
        return fail("学号或密码错误", 401)
    exam = current_exam()
    if not exam:
        return fail("暂无考试", 404)
    record = get_or_create_record(exam["id"], student_id)
    token = sign({"role": "student", "student_id": student_id, "exp": int(time.time()) + 86400})
    return ok({"token": token, "student": row_dict(student), "record": record_payload(record)})


@app.route("/api/student/confirm-name", methods=["POST"])
@auth_required("student")
def confirm_name():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    student = db().execute(
        "SELECT * FROM students WHERE student_id = ?", (g.user["student_id"],)
    ).fetchone()
    if name != student["name"]:
        return fail("姓名确认失败，请输入学生名单中的真实姓名", 400)
    exam = current_exam()
    record = get_or_create_record(exam["id"], g.user["student_id"])
    db().execute(
        "UPDATE exam_records SET confirm_name = ?, status = ? WHERE id = ?",
        (name, "confirmed", record["id"]),
    )
    db().commit()
    return ok({"record": record_payload(get_or_create_record(exam["id"], g.user["student_id"]))})


@app.route("/api/teacher/login", methods=["POST"])
def teacher_login():
    data = request.get_json(force=True)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    teacher = db().execute("SELECT * FROM teachers WHERE username = ?", (username,)).fetchone()
    if not teacher or not verify_password(teacher["password_hash"], password):
        return fail("教师账号或密码错误", 401)
    token = sign({"role": "teacher", "username": username, "exp": int(time.time()) + 86400})
    return ok({"token": token, "username": username})


@app.route("/api/exam/current")
def api_current_exam():
    exam = current_exam()
    if not exam:
        return fail("暂无考试", 404)
    paper = db().execute("SELECT * FROM papers WHERE paper_id = ?", (exam["paper_id"],)).fetchone()
    return ok({"exam": row_dict(exam), "paper": row_dict(paper)})


@app.route("/api/exam/paper")
@auth_required("student")
def api_exam_paper():
    exam = current_exam()
    record = get_or_create_record(exam["id"], g.user["student_id"], "answering")
    if record["status"] == "submitted":
        return fail("你已经交卷，不能重复答题", 409)
    start_time = record["start_time"] or now_iso()
    db().execute(
        "UPDATE exam_records SET start_time = ?, status = ? WHERE id = ?",
        (start_time, "answering", record["id"]),
    )
    db().commit()
    paper = db().execute("SELECT * FROM papers WHERE paper_id = ?", (exam["paper_id"],)).fetchone()
    return ok(
        {
            "exam": row_dict(exam),
            "paper": row_dict(paper),
            "questions": questions_for_paper(exam["paper_id"]),
            "record": record_payload(get_or_create_record(exam["id"], g.user["student_id"])),
        }
    )


@app.route("/api/exam/auto-save", methods=["POST"])
@auth_required("student")
def auto_save():
    data = request.get_json(force=True)
    exam = current_exam()
    record = get_or_create_record(exam["id"], g.user["student_id"], "answering")
    if record["status"] == "submitted":
        return fail("已交卷，不能继续保存", 409)
    save_answers(record["id"], data.get("answers", {}))
    db().execute("UPDATE exam_records SET status = ? WHERE id = ?", ("answering", record["id"]))
    db().commit()
    return ok({"saved_at": now_iso()})


@app.route("/api/exam/status")
@auth_required("student")
def exam_status():
    exam = current_exam()
    record = get_or_create_record(exam["id"], g.user["student_id"])
    return ok({"exam": row_dict(exam), "record": record_payload(record)})


@app.route("/api/exam/submit", methods=["POST"])
@auth_required("student")
def submit_exam():
    data = request.get_json(force=True)
    exam = current_exam()
    record = get_or_create_record(exam["id"], g.user["student_id"], "answering")
    if record["status"] == "submitted":
        return ok({"record": record_payload(record)}, "已提交，无需重复交卷")
    save_answers(record["id"], data.get("answers", {}))
    db().execute(
        "UPDATE exam_records SET submit_time = ?, status = ? WHERE id = ?",
        (now_iso(), "submitted", record["id"]),
    )
    db().commit()
    grade_record(record["id"])
    return ok({"record": record_payload(get_or_create_record(exam["id"], g.user["student_id"]))}, "提交成功")


@app.route("/api/teacher/start-exam", methods=["POST"])
@auth_required("teacher")
def start_exam():
    exam = current_exam()
    db().execute(
        "UPDATE exams SET status = ?, start_time = ?, end_time = NULL WHERE id = ?",
        ("running", now_iso(), exam["id"]),
    )
    db().commit()
    return ok(row_dict(current_exam()), "考试已开始")


@app.route("/api/teacher/end-exam", methods=["POST"])
@auth_required("teacher")
def end_exam():
    exam = current_exam()
    db().execute(
        "UPDATE exams SET status = ?, end_time = ? WHERE id = ?",
        ("ended", now_iso(), exam["id"]),
    )
    records = db().execute(
        "SELECT id FROM exam_records WHERE exam_id = ?", (exam["id"],)
    ).fetchall()
    for record in records:
        grade_record(record["id"])
    db().commit()
    return ok(row_dict(current_exam()), "考试已结束")


@app.route("/api/teacher/force-submit", methods=["POST"])
@auth_required("teacher")
def force_submit():
    data = request.get_json(force=True)
    student_id = str(data.get("student_id", "")).strip()
    exam = current_exam()
    record = get_or_create_record(exam["id"], student_id)
    db().execute(
        """
        UPDATE exam_records
        SET status = ?, forced = 1, submit_time = COALESCE(submit_time, ?)
        WHERE id = ?
        """,
        ("forced", now_iso(), record["id"]),
    )
    db().commit()
    grade_record(record["id"])
    return ok({"record": record_payload(record)}, "已强制收卷")


@app.route("/api/teacher/monitor")
@auth_required("teacher")
def monitor():
    exam = current_exam()
    students = db().execute("SELECT student_id, name, class_name FROM students ORDER BY student_id").fetchall()
    records = db().execute(
        "SELECT * FROM exam_records WHERE exam_id = ?", (exam["id"],)
    ).fetchall()
    record_map = {r["student_id"]: r for r in records}
    total_questions = db().execute(
        "SELECT COUNT(*) AS count FROM questions WHERE paper_id = ?", (exam["paper_id"],)
    ).fetchone()["count"]
    items = []
    for student in students:
        record = record_map.get(student["student_id"])
        answered = 0
        if record:
            answered = db().execute(
                """
                SELECT COUNT(*) AS count FROM answers
                WHERE record_id = ? AND COALESCE(answer_text, '') != ''
                """,
                (record["id"],),
            ).fetchone()["count"]
        status = record["status"] if record else "not_logged_in"
        items.append(
            {
                **row_dict(student),
                "status": status,
                "answered": answered,
                "total": total_questions,
                "submitted": status in ("submitted", "forced"),
                "login_time": record["login_time"] if record else None,
                "submit_time": record["submit_time"] if record else None,
            }
        )
    stats = {
        "total": len(items),
        "logged_in": sum(1 for i in items if i["status"] != "not_logged_in"),
        "answering": sum(1 for i in items if i["status"] == "answering"),
        "submitted": sum(1 for i in items if i["status"] in ("submitted", "forced")),
        "not_logged_in": sum(1 for i in items if i["status"] == "not_logged_in"),
    }
    return ok({"exam": row_dict(exam), "stats": stats, "students": items})


@app.route("/api/teacher/results")
@auth_required("teacher")
def results():
    exam = current_exam()
    rows = db().execute(
        """
        SELECT s.student_id, s.name, s.class_name, r.id AS record_id, r.status,
               COALESCE(r.objective_score, 0) AS objective_score,
               COALESCE(r.subjective_score, 0) AS subjective_score,
               COALESCE(r.total_score, 0) AS total_score
        FROM students s
        LEFT JOIN exam_records r ON r.student_id = s.student_id AND r.exam_id = ?
        ORDER BY s.student_id
        """,
        (exam["id"],),
    ).fetchall()
    scores = [float(r["total_score"] or 0) for r in rows]
    stats = {
        "average": round(sum(scores) / len(scores), 2) if scores else 0,
        "highest": max(scores) if scores else 0,
        "lowest": min(scores) if scores else 0,
        "pass_rate": round(sum(1 for s in scores if s >= 60) / len(scores) * 100, 2) if scores else 0,
    }
    return ok({"results": [row_dict(r) for r in rows], "stats": stats})


@app.route("/api/teacher/export")
@auth_required("teacher")
def export_results():
    result_data = results().json["data"]["results"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["学号", "姓名", "班级", "客观分", "主观分", "总分", "状态"])
    for row in result_data:
        writer.writerow(
            [
                row["student_id"],
                row["name"],
                row["class_name"],
                row["objective_score"],
                row["subjective_score"],
                row["total_score"],
                row["status"] or "未登录",
            ]
        )
    csv_bytes = output.getvalue().encode("utf-8-sig")
    return Response(
        csv_bytes,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=exam-results.csv"},
    )


@app.route("/api/papers", methods=["GET", "POST"])
@auth_required("teacher")
def papers():
    if request.method == "GET":
        rows = db().execute("SELECT * FROM papers ORDER BY created_at DESC").fetchall()
        return ok([row_dict(r) for r in rows])
    data = request.get_json(force=True)
    paper_id = data.get("paper_id") or f"P{int(time.time())}"
    questions = data.get("questions", [])
    total_score = sum(int(q.get("score", 0)) for q in questions)
    db().execute(
        "INSERT INTO papers(paper_id, title, total_score, duration_minutes, created_at) VALUES(?, ?, ?, ?, ?)",
        (paper_id, data.get("title", "新试卷"), total_score, int(data.get("duration_minutes", 90)), now_iso()),
    )
    for index, q in enumerate(questions, 1):
        db().execute(
            """
            INSERT INTO questions(paper_id, question_no, type, content, options_json, answer, score, keywords_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                index,
                q.get("type", "single_choice"),
                q.get("content", ""),
                json.dumps(q.get("options", []), ensure_ascii=False),
                q.get("answer", ""),
                int(q.get("score", 0)),
                json.dumps(q.get("keywords", []), ensure_ascii=False),
            ),
        )
    db().commit()
    return ok({"paper_id": paper_id}, "试卷已创建")


@app.route("/api/papers/<paper_id>", methods=["DELETE"])
@auth_required("teacher")
def delete_paper(paper_id):
    db().execute("DELETE FROM questions WHERE paper_id = ?", (paper_id,))
    db().execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
    db().commit()
    return ok(message="试卷已删除")


@app.route("/api/students")
@auth_required("teacher")
def students():
    rows = db().execute(
        "SELECT student_id, name, class_name, created_at FROM students ORDER BY student_id"
    ).fetchall()
    return ok([row_dict(r) for r in rows])


@app.route("/api/students/import", methods=["POST"])
@auth_required("teacher")
def import_students():
    file = request.files.get("file")
    if not file:
        return fail("请上传 Excel 文件", 400)
    from openpyxl import load_workbook

    workbook = load_workbook(file, read_only=True)
    sheet = workbook.active
    count = 0
    for row in sheet.iter_rows(min_row=2, values_only=True):
        student_id, name, class_name = [str(value or "").strip() for value in row[:3]]
        if not student_id:
            continue
        db().execute(
            """
            INSERT OR REPLACE INTO students(student_id, name, class_name, password_hash, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (student_id, name, class_name, hash_password(student_id[-5:]), now_iso()),
        )
        count += 1
    db().commit()
    return ok({"count": count}, "学生导入成功")


@app.route("/api/grading/manual", methods=["POST"])
@auth_required("teacher")
def manual_grading():
    data = request.get_json(force=True)
    answer_id = int(data.get("answer_id"))
    score = float(data.get("score", 0))
    answer = db().execute("SELECT record_id FROM answers WHERE id = ?", (answer_id,)).fetchone()
    if not answer:
        return fail("答案不存在", 404)
    db().execute(
        "UPDATE answers SET score = ?, grading_method = 'manual' WHERE id = ?",
        (score, answer_id),
    )
    db().commit()
    grade_record(answer["record_id"])
    return ok(message="人工评分已保存")


@app.route("/api/grading/records")
@auth_required("teacher")
def grading_records():
    exam = current_exam()
    rows = db().execute(
        """
        SELECT r.id AS record_id, s.student_id, s.name, q.question_no, q.content, q.score AS max_score,
               a.id AS answer_id, a.answer_text, a.score
        FROM answers a
        JOIN exam_records r ON r.id = a.record_id
        JOIN students s ON s.student_id = r.student_id
        JOIN questions q ON q.id = a.question_id
        WHERE r.exam_id = ? AND q.type = 'short_answer'
        ORDER BY s.student_id, q.question_no
        """,
        (exam["id"],),
    ).fetchall()
    return ok([row_dict(r) for r in rows])


@app.cli.command("init-db")
def init_db_command():
    seed()
    print("Database initialized.")


if __name__ == "__main__":
    with app.app_context():
        seed()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
