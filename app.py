import base64
import csv
import hashlib
import hmac
import io
import json
import os
import random
import re as _re
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory
from seed_data import QUESTIONS, STUDENTS


app = Flask(__name__, instance_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance"),
            static_folder="static", static_url_path="")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-before-production")
app.config["DATABASE"] = os.environ.get(
    "DATABASE_PATH", os.path.join(app.instance_path, "exam.sqlite")
)
BOOTSTRAPPED = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def auto_finalize_stale_records(student_id=None):
    """对超过考试时长 + 5 分钟宽限的活动 record 做强制收卷。
    防止学生长时间挂机不交卷，导致状态永远停在 answering/logged_in/confirmed。
    如指定 student_id，则只清理该学生。
    """
    conn = db()
    sql = """
        SELECT r.*, e.duration_minutes
        FROM exam_records r JOIN exams e ON r.exam_id = e.id
        WHERE r.status IN ('logged_in', 'confirmed', 'answering')
    """
    if student_id:
        sql += " AND r.student_id = ?"
        rows = conn.execute(sql, (student_id,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    now = datetime.now(timezone.utc)
    finalized = 0
    for r in rows:
        st = _parse_iso_dt(r["start_time"]) or _parse_iso_dt(r["login_time"])
        if not st:
            continue
        # 无 start_time 的（仅 logged_in）按 login_time 算
        elapsed_min = (now - st).total_seconds() / 60.0
        threshold = (r["duration_minutes"] or 0) + 5  # 5 分钟宽限
        if elapsed_min >= threshold:
            conn.execute(
                "UPDATE exam_records SET status = 'submitted', forced = 1, "
                "submit_time = ? WHERE id = ?",
                (now_iso(), r["id"]),
            )
            try:
                grade_record(r["id"])
            except Exception:
                pass
            finalized += 1
    if finalized:
        conn.commit()
    return finalized


def ok(data=None, message="操作成功"):
    return jsonify({"code": 200, "message": message, "data": data})


def fail(message, code=400, data=None):
    return jsonify({"code": code, "message": message, "data": data}), code


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


def get_request_locale():
    """从请求头 X-Locale 或查询参数 locale 读取语言；默认 zh-CN。"""
    loc = (request.headers.get("X-Locale") or request.args.get("locale") or "zh-CN").lower()
    return "en-US" if loc.startswith("en") else "zh-CN"


def localize_options(options_zh, options_en_json, locale):
    """根据 locale 返回对应的选项列表（对象 {key,value}）。"""
    if locale == "en-US" and options_en_json:
        try:
            en = json.loads(options_en_json)
            if isinstance(en, list) and en:
                return en
        except Exception:
            pass
    return options_zh or []


def localize_question(item, locale, include_answer=False):
    """将 question dict 的 content/options 按 locale 替换为对应语言。
    不修改原 dict，返回新的 dict。
    """
    out = dict(item)
    if locale == "en-US":
        content_en = (out.get("content_en") or "").strip()
        if content_en:
            out["content"] = content_en
        out["options"] = localize_options(
            out.get("options"), out.get("options_en_json"), locale
        )
        out.pop("options_en_json", None)
        out.pop("content_en", None)
    else:
        out.pop("options_en_json", None)
        out.pop("content_en", None)
    if not include_answer:
        out.pop("answer", None)
        out.pop("keywords", None)
    return out


def auth_required(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header.replace("Bearer ", "", 1).strip()
            payload = verify_token(token)
            if not payload or payload.get("role") != role:
                return fail("AUTH_FAILED", 401)
            g.user = payload
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _content_disposition(filename):
    """生成兼容中文文件名的 Content-Disposition 头（RFC 5987）。"""
    try:
        from urllib.parse import quote
        encoded = quote(filename)
    except Exception:
        encoded = filename
    return {
        "Content-Disposition": (
            f"attachment; filename*=UTF-8''{encoded}; "
            f"filename=\"{filename.encode('latin-1', 'replace').decode('latin-1')}\""
        )
    }


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
            content_en TEXT,
            options_en_json TEXT,
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
            max_attempts INTEGER NOT NULL DEFAULT 1,
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
            attempt_no INTEGER NOT NULL DEFAULT 1,
            UNIQUE(exam_id, student_id, attempt_no),
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
            blank_scores TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(record_id, question_id),
            FOREIGN KEY (record_id) REFERENCES exam_records(id),
            FOREIGN KEY (question_id) REFERENCES questions(id)
        );
        """
    )
    # 索引/字段容错升级
    cols = {row[1] for row in conn.execute("PRAGMA table_info(exams)").fetchall()}
    if "max_attempts" not in cols:
        conn.execute("ALTER TABLE exams ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 1")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(exam_records)").fetchall()}
    if "attempt_no" not in cols:
        conn.execute("ALTER TABLE exam_records ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 1")
    # 兼容旧库中 exam_records UNIQUE 约束的迁移
    # 重建 exam_records 表以应用新 UNIQUE 约束（如旧表为 (exam_id, student_id)）
    rec_cols = {row[1]: row for row in conn.execute("PRAGMA table_info(exam_records)").fetchall()}
    if rec_cols:
        # 通过 sqlite_master 直接读 CREATE TABLE SQL，判断 UNIQUE 约束是否已包含 attempt_no
        create_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='exam_records'"
        ).fetchone()
        create_sql = ((create_sql_row[0] if create_sql_row else "") or "").replace(" ", "")
        new_constraint = "UNIQUE(exam_id,studentid,attempt_no)" in create_sql or \
            "UNIQUE(exam_id,student_id,attempt_no)" in create_sql
        if not new_constraint:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS exam_records_new (
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
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    tab_switch_count INTEGER DEFAULT 0,
                    UNIQUE(exam_id, student_id, attempt_no),
                    FOREIGN KEY (exam_id) REFERENCES exams(id),
                    FOREIGN KEY (student_id) REFERENCES students(student_id)
                );
                INSERT INTO exam_records_new
                    (id, exam_id, student_id, confirm_name, login_time, start_time, submit_time,
                     status, objective_score, subjective_score, total_score, forced,
                     attempt_no, tab_switch_count)
                SELECT
                    id, exam_id, student_id, confirm_name, login_time, start_time, submit_time,
                    status, objective_score, subjective_score, total_score, forced,
                    COALESCE(attempt_no, 1), COALESCE(tab_switch_count, 0)
                FROM exam_records
                ORDER BY id;
                DROP TABLE exam_records;
                ALTER TABLE exam_records_new RENAME TO exam_records;
                """
            )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(answers)").fetchall()}
    if "blank_scores" not in cols:
        conn.execute("ALTER TABLE answers ADD COLUMN blank_scores TEXT")
    # 升级：questions 表新增英文题干/选项字段
    qcols = {row[1] for row in conn.execute("PRAGMA table_info(questions)").fetchall()}
    if "content_en" not in qcols:
        conn.execute("ALTER TABLE questions ADD COLUMN content_en TEXT")
    if "options_en_json" not in qcols:
        conn.execute("ALTER TABLE questions ADD COLUMN options_en_json TEXT")
    # 升级：exam_records 表新增切屏计数
    ecols = {row[1] for row in conn.execute("PRAGMA table_info(exam_records)").fetchall()}
    if "tab_switch_count" not in ecols:
        conn.execute("ALTER TABLE exam_records ADD COLUMN tab_switch_count INTEGER DEFAULT 0")
    # 启动时回收遗留的『running』状态考试：默认行为是把它们退回『waiting』，
    # 这样教师能重新看到一张干净的『待开始』考试列表。end_time / start_time 一并清空。
    running = conn.execute(
        "SELECT id, name FROM exams WHERE status='running'"
    ).fetchall()
    if running:
        conn.execute(
            "UPDATE exams SET status='waiting', start_time=NULL, end_time=NULL WHERE status='running'"
        )
        print(f"[migrate] 启动回收：将 {len(running)} 场遗留 running 考试重置为 waiting -> {','.join(str(r['id']) for r in running)}")
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
            options_en = question.get("options_en", [])
            options_en_json = json.dumps(options_en, ensure_ascii=False) if options_en else ""
            conn.execute(
                """
                INSERT INTO questions(paper_id, question_no, type, content, options_json, answer, score, keywords_json, content_en, options_en_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    (question.get("content_en") or "").strip(),
                    options_en_json,
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
    """向后兼容：使用 UNIQUE 约束 (exam_id, student_id) 的旧行为。"""
    conn = db()
    conn.execute(
        """
        INSERT OR IGNORE INTO exam_records(exam_id, student_id, login_time, status, attempt_no)
        VALUES(?, ?, ?, ?, 1)
        """,
        (exam_id, student_id, now_iso(), status),
    )
    record = conn.execute(
        "SELECT * FROM exam_records WHERE exam_id = ? AND student_id = ? ORDER BY id DESC LIMIT 1",
        (exam_id, student_id),
    ).fetchone()
    conn.commit()
    return record


def get_or_create_attempt_record(exam_id, student_id):
    """基于 attempts 控制的新流程：先检查剩余次数，再决定复用/新建 record。"""
    conn = db()
    exam = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not exam:
        return None, None, "EXAM_NOT_FOUND"
    max_n = int(exam["max_attempts"] or 1)
    submitted = conn.execute(
        """
        SELECT * FROM exam_records
        WHERE exam_id = ? AND student_id = ? AND status IN ('submitted', 'forced')
        """,
        (exam_id, student_id),
    ).fetchall()
    used = len(submitted)
    if max_n > 0 and used >= max_n:
        return None, None, "NO_ATTEMPTS_LEFT"

    # 是否存在进行中（断点续考）
    active = conn.execute(
        """
        SELECT * FROM exam_records
        WHERE exam_id = ? AND student_id = ? AND status IN ('logged_in', 'confirmed', 'answering')
        ORDER BY id DESC LIMIT 1
        """,
        (exam_id, student_id),
    ).fetchone()
    if active:
        return active, None, None

    # 新建一次
    attempt_no = used + 1
    cur = conn.execute(
        """
        INSERT INTO exam_records(exam_id, student_id, login_time, status, attempt_no)
        VALUES(?, ?, ?, 'logged_in', ?)
        """,
        (exam_id, student_id, now_iso(), attempt_no),
    )
    conn.commit()
    record = conn.execute("SELECT * FROM exam_records WHERE id = ?", (cur.lastrowid,)).fetchone()
    return record, attempt_no, None


def compute_student_exam_status(student_id, exam):
    """计算学生在该考试中的状态摘要。

    返回 (status, used, last_score, last_record_id, last_status)。
    status 取值：
      - not_started       学生从未进入这场考试
      - not_open          考试未开始（waiting），且学生没 record
      - ended_locked      考试已结束（ended/closed），且学生没 record
      - answering         正在答题中
      - confirmed         已确认须知，但还没开始答题
      - logged_in         已登录但未确认
      - submitted_retryable  交卷了但还有次数
      - finished          次数已用完 / 考试已结束
    """
    conn = db()
    exam_status = (exam["status"] or "").lower()
    is_ended = exam_status in ("ended", "closed")
    is_waiting = exam_status == "waiting"
    is_running = exam_status == "running"

    records = conn.execute(
        """
        SELECT * FROM exam_records WHERE exam_id = ? AND student_id = ?
        ORDER BY id DESC
        """,
        (exam["id"], student_id),
    ).fetchall()
    submitted = [r for r in records if r["status"] in ("submitted", "forced")]
    used = len(submitted)
    max_n = int(exam["max_attempts"] or 1) if exam["max_attempts"] is not None else 1

    # 1) 考试已结束：所有路径都不能再进入
    if is_ended:
        if not records:
            return "ended_locked", 0, None, None, None
        last = records[0]
        # 已交卷的 record：按次数是否用完决定 finished vs view_only
        if last["status"] in ("submitted", "forced"):
            if max_n > 0 and used >= max_n:
                return "finished", used, last["total_score"], last["id"], last["status"]
            return "finished", used, last["total_score"], last["id"], last["status"]
        # 进行中 record（考试被强制收卷前留下的活跃状态）
        return "ended_locked", used, last["total_score"], last["id"], last["status"]

    # 2) 考试未开始（waiting）：仅允许有进行中 record 的学生恢复（极端边界），否则锁定
    if is_waiting:
        if not records:
            return "not_open", 0, None, None, None
        last = records[0]
        if last["status"] in ("logged_in", "confirmed", "answering"):
            return "not_open", used, last["total_score"], last["id"], last["status"]
        return "not_open", used, last["total_score"], last["id"], last["status"]

    # 3) 考试进行中（running）：按 record 状态计算
    if not records:
        return "not_started", 0, None, None, None
    if max_n > 0 and used >= max_n:
        last = submitted[0]
        return "finished", used, last["total_score"], last["id"], last["status"]
    last = records[0]
    last_status = last["status"]
    last_score = last["total_score"]
    last_id = last["id"]
    if last_status in ("submitted", "forced"):
        return "submitted_retryable", used, last_score, last_id, last_status
    if last_status == "answering":
        return "answering", used, last_score, last_id, last_status
    if last_status == "confirmed":
        return "confirmed", used, last_score, last_id, last_status
    if last_status == "logged_in":
        return "logged_in", used, last_score, last_id, last_status
    return last_status, used, last_score, last_id, last_status


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


def localized_questions_for_paper(paper_id, locale, include_answer=False):
    """根据 locale 渲染题目内容（content/options）。"""
    base = questions_for_paper(paper_id, include_answer=include_answer)
    if locale == "zh-CN":
        return base
    return [localize_question(q, locale, include_answer=include_answer) for q in base]


def normalize_answer(value):
    if isinstance(value, list):
        return ",".join(sorted(str(v) for v in value))
    return str(value).strip()


def _parse_answer_field(value):
    """将 answer 字段解析为列表（兼容 JSON 字符串 / 字符串 / 列表）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    s = str(value).strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
    if "," in s:
        return [x.strip() for x in s.split(",")]
    return [s]


def _blank_match(student, correct, fuzzy=True):
    """填空题宽松匹配：去空格、不区分大小写、数字等价、可选模糊包含。"""
    a = (student or "").strip()
    b = (correct or "").strip()
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    # 数字等价
    try:
        if abs(float(a) - float(b)) < 1e-9:
            return True
    except Exception:
        pass
    if fuzzy and (b in a or a in b):
        return True
    return False


def grade_fill_blank(question, student_answer_text):
    """填空题自动批改，返回 (total_score, blank_scores_json)。"""
    blanks = (question["content"] or "").count("__")
    correct = _parse_answer_field(question.get("answer"))
    student = _parse_answer_field(student_answer_text)
    per_blank = (float(question["score"] or 0) / blanks) if blanks > 0 else 0
    total = 0.0
    blank_scores = []
    if blanks == 0:
        return 0.0, json.dumps([])
    for i in range(blanks):
        s = student[i] if i < len(student) else ""
        c = correct[i] if i < len(correct) else ""
        ok = _blank_match(s, c, fuzzy=True)
        sc = per_blank if ok else 0.0
        total += sc
        blank_scores.append(round(sc, 2))
    return round(total, 2), json.dumps(blank_scores, ensure_ascii=False)


def save_answers(record_id, answers):
    conn = db()
    questions = conn.execute("SELECT id FROM questions").fetchall()
    valid_ids = {str(q["id"]) for q in questions}
    for question_id, answer in answers.items():
        qid = str(question_id)
        if qid not in valid_ids:
            continue
        # 填空题以 JSON 字符串存储（来自 list 答案），其他题型保持字符串
        if isinstance(answer, list):
            value = json.dumps(answer, ensure_ascii=False)
        else:
            value = normalize_answer(answer)
        conn.execute(
            """
            INSERT INTO answers(record_id, question_id, answer_text, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(record_id, question_id)
            DO UPDATE SET answer_text = excluded.answer_text, updated_at = excluded.updated_at
            """,
            (record_id, int(qid), value, now_iso()),
        )
    conn.commit()


def grade_record(record_id):
    conn = db()
    rows = conn.execute(
        """
        SELECT a.id AS answer_id, a.answer_text, a.blank_scores, q.type, q.answer, q.score, q.content
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
                "UPDATE answers SET score = ?, grading_method = 'auto', blank_scores = NULL WHERE id = ?",
                (score, row["answer_id"]),
            )
        elif row["type"] == "fill_blank":
            q = {
                "content": row["content"],
                "answer": row["answer"],
                "score": row["score"],
            }
            score, blank_scores = grade_fill_blank(q, row["answer_text"] or "")
            objective += score
            conn.execute(
                "UPDATE answers SET score = ?, grading_method = 'auto', blank_scores = ? WHERE id = ?",
                (score, blank_scores, row["answer_id"]),
            )
        else:
            # 主观题保留教师人工评分
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


def safe_filename(name, default="export"):
    """生成安全的导出文件名（去除路径分隔符与不可见字符）。"""
    s = _re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", (name or "").strip())
    s = _re.sub(r"\s+", "_", s)
    return s[:80] or default


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
        return fail("AUTH_FAILED", 401)
    if not current_exam():
        return fail("NO_EXAM_AVAILABLE", 404)
    token = sign({"role": "student", "student_id": student_id, "exp": int(time.time()) + 86400})
    return ok({"token": token, "student": row_dict(student)})


@app.route("/api/student/exams", methods=["GET"])
@auth_required("student")
def student_exams():
    """学生视角：列出可参加的考试（含状态、剩余次数等）。"""
    student_id = g.user["student_id"]
    # 先清理超时未交卷的僵尸记录（duration_minutes + 5 分钟宽限）
    try:
        auto_finalize_stale_records(student_id=student_id)
    except Exception:
        pass
    rows = db().execute(
        """
        SELECT e.*, p.title AS paper_title, p.total_score AS paper_total_score
        FROM exams e
        LEFT JOIN papers p ON p.paper_id = e.paper_id
        ORDER BY e.id ASC
        """
    ).fetchall()
    items = []
    for row in rows:
        exam = row_dict(row)
        my_status, used, last_score, last_record_id, last_status = compute_student_exam_status(student_id, exam)
        items.append({
            "exam_id": exam["id"],
            "exam_name": exam["name"],
            "paper_id": exam["paper_id"],
            "paper_title": exam.get("paper_title") or "",
            "status": exam["status"],
            "duration_minutes": exam["duration_minutes"],
            "start_time": exam["start_time"],
            "end_time": exam["end_time"],
            "max_attempts": int(exam["max_attempts"] or 1),
            "used_attempts": used,
            "remaining_attempts": (
                max(0, int(exam["max_attempts"] or 1) - used) if int(exam["max_attempts"] or 1) > 0 else 9999
            ),
            "my_status": my_status,
            "my_record_id": last_record_id,
            "last_score": last_score,
            "last_record_status": last_status,
        })
    return ok({"exams": items})


@app.route("/api/student/confirm-name", methods=["POST"])
@auth_required("student")
def confirm_name():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    student = db().execute(
        "SELECT * FROM students WHERE student_id = ?", (g.user["student_id"],)
    ).fetchone()
    if name != student["name"]:
        return fail("NAME_MISMATCH", 400)
    return ok({"student": row_dict(student)})


@app.route("/api/exam/start", methods=["POST"])
@auth_required("student")
def exam_start():
    """学生开始考试：基于 max_attempts 控制次数；存在进行中记录时复用（断点续考）。"""
    data = request.get_json(force=True)
    exam_id = int(data.get("exam_id") or 0)
    student_id = g.user["student_id"]

    exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    # 仅当教师将考试『开始』后，学生方可作答；待开始/已结束均不允许开考
    if exam["status"] != "running":
        if exam["status"] == "waiting":
            return fail("EXAM_NOT_STARTED", 400, "教师尚未开始这场考试，请等待教师开启后刷新")
        return fail("EXAM_NOT_OPEN", 400)

    record, attempt_no, code = get_or_create_attempt_record(exam_id, student_id)
    if code == "NO_ATTEMPTS_LEFT":
        return fail("NO_ATTEMPTS_LEFT", 403)
    if code == "EXAM_NOT_FOUND" or record is None:
        return fail("EXAM_NOT_FOUND", 404)

    return ok({
        "exam_record_id": record["id"],
        "attempt_no": record["attempt_no"] or attempt_no or 1,
        "start_time": record["start_time"],
        "is_resume": record["status"] in ("answering", "confirmed", "logged_in"),
        "status": record["status"],
    })


@app.route("/api/exam/paper")
@auth_required("student")
def api_exam_paper():
    data = request.args
    exam_id = int(data.get("exam_id") or 0)
    record_id = int(data.get("record_id") or 0)
    student_id = g.user["student_id"]
    conn = db()
    exam = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone() if exam_id else current_exam()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    if record_id:
        record = conn.execute(
            "SELECT * FROM exam_records WHERE id = ? AND student_id = ?",
            (record_id, student_id),
        ).fetchone()
    else:
        record = conn.execute(
            """
            SELECT * FROM exam_records WHERE exam_id = ? AND student_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (exam["id"], student_id),
        ).fetchone()
    if not record:
        return fail("RECORD_NOT_FOUND", 404)
    if record["status"] in ("submitted", "forced"):
        return fail("ALREADY_SUBMITTED", 409)
    start_time = record["start_time"] or now_iso()
    conn.execute(
        "UPDATE exam_records SET start_time = ?, status = ? WHERE id = ?",
        (start_time, "answering", record["id"]),
    )
    conn.commit()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (exam["paper_id"],)).fetchone()
    locale = get_request_locale()
    return ok(
        {
            "exam": row_dict(exam),
            "paper": row_dict(paper),
            "questions": localized_questions_for_paper(exam["paper_id"], locale),
            "record": record_payload(record),
        }
    )


@app.route("/api/teacher/login", methods=["POST"])
def teacher_login():
    data = request.get_json(force=True)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    teacher = db().execute("SELECT * FROM teachers WHERE username = ?", (username,)).fetchone()
    if not teacher or not verify_password(teacher["password_hash"], password):
        return fail("AUTH_FAILED", 401)
    token = sign({"role": "teacher", "username": username, "exp": int(time.time()) + 86400})
    return ok({"token": token, "username": username})


@app.route("/api/exam/current")
def api_current_exam():
    exam = current_exam()
    if not exam:
        return fail("NO_EXAM_AVAILABLE", 404)
    paper = db().execute("SELECT * FROM papers WHERE paper_id = ?", (exam["paper_id"],)).fetchone()
    return ok({"exam": row_dict(exam), "paper": row_dict(paper)})


@app.route("/api/exam/auto-save", methods=["POST"])
@auth_required("student")
def auto_save():
    data = request.get_json(force=True)
    record_id = int(data.get("record_id") or 0)
    student_id = g.user["student_id"]
    if record_id:
        record = db().execute(
            "SELECT * FROM exam_records WHERE id = ? AND student_id = ?",
            (record_id, student_id),
        ).fetchone()
    else:
        record = db().execute(
            """
            SELECT * FROM exam_records WHERE student_id = ? AND status NOT IN ('submitted','forced')
            ORDER BY id DESC LIMIT 1
            """,
            (student_id,),
        ).fetchone()
    if not record:
        return fail("RECORD_NOT_FOUND", 404)
    if record["status"] in ("submitted", "forced"):
        return fail("ALREADY_SUBMITTED", 409)
    save_answers(record["id"], data.get("answers", {}))
    db().execute("UPDATE exam_records SET status = ? WHERE id = ?", ("answering", record["id"]))
    db().commit()
    return ok({"saved_at": now_iso(), "record_id": record["id"]})


@app.route("/api/exam/status")
@auth_required("student")
def exam_status():
    student_id = g.user["student_id"]
    records = db().execute(
        "SELECT * FROM exam_records WHERE student_id = ? ORDER BY id DESC",
        (student_id,),
    ).fetchall()
    return ok({"records": [row_dict(r) for r in records]})


@app.route("/api/student/reset-exam", methods=["POST"])
@auth_required("student")
def reset_exam():
    """重置学生已交卷的考试记录：清空答案、重置状态和开始时间、清除分数。"""
    data = request.get_json(silent=True) or {}
    record_id = int(data.get("record_id") or 0)
    student_id = g.user["student_id"]
    conn = db()
    if record_id:
        record = conn.execute(
            "SELECT * FROM exam_records WHERE id = ? AND student_id = ?",
            (record_id, student_id),
        ).fetchone()
    else:
        record = conn.execute(
            "SELECT * FROM exam_records WHERE student_id = ? AND status = 'submitted' ORDER BY id DESC LIMIT 1",
            (student_id,),
        ).fetchone()
    if record is None:
        return ok(message="NO_RECORD_TO_RESET")
    conn.execute("DELETE FROM answers WHERE record_id = ?", (record["id"],))
    conn.execute(
        """
        UPDATE exam_records
        SET status = ?, start_time = NULL, submit_time = NULL,
            forced = 0, objective_score = 0, subjective_score = 0, total_score = 0,
            confirm_name = NULL
        WHERE id = ?
        """,
        ("logged_in", record["id"]),
    )
    conn.commit()
    fresh = conn.execute("SELECT * FROM exam_records WHERE id = ?", (record["id"],)).fetchone()
    return ok({"record": record_payload(fresh)}, "RESET_OK")


@app.route("/api/exam/tab-switch", methods=["POST"])
@auth_required("student")
def exam_tab_switch():
    """切屏计数。累计 3 次时强制收卷（status=forced），并消耗 1 次机会。"""
    data = request.get_json(force=True, silent=True) or {}
    record_id = int(data.get("record_id") or 0)
    if not record_id:
        return fail("RECORD_REQUIRED", 400)
    student_id = g.user["student_id"]
    conn = db()
    record = conn.execute(
        "SELECT * FROM exam_records WHERE id = ? AND student_id = ?",
        (record_id, student_id),
    ).fetchone()
    if not record:
        return fail("RECORD_NOT_FOUND", 404)
    if record["status"] in ("submitted", "forced"):
        return ok({"count": record["tab_switch_count"] or 0, "force_submitted": True,
                   "limit": TAB_SWITCH_LIMIT}, "ALREADY_SUBMITTED")
    new_count = (record["tab_switch_count"] or 0) + 1
    force = new_count >= TAB_SWITCH_LIMIT
    if force:
        # 强制收卷：自动评分 + 标记为 forced
        conn.execute(
            "UPDATE exam_records SET tab_switch_count = ?, status = ?, forced = 1, "
            "submit_time = COALESCE(submit_time, ?) WHERE id = ?",
            (new_count, "forced", now_iso(), record_id),
        )
        # 跑一次自动评分
        try:
            grade_record(record_id)
        except Exception:
            pass
    else:
        conn.execute(
            "UPDATE exam_records SET tab_switch_count = ? WHERE id = ?",
            (new_count, record_id),
        )
    conn.commit()
    return ok({
        "count": new_count,
        "limit": TAB_SWITCH_LIMIT,
        "force_submitted": force,
    }, "TAB_SWITCH_RECORDED" if not force else "TAB_SWITCH_VIOLATION")


TAB_SWITCH_LIMIT = 3


@app.route("/api/exam/submit", methods=["POST"])
@auth_required("student")
def submit_exam():
    data = request.get_json(force=True)
    record_id = int(data.get("record_id") or 0)
    student_id = g.user["student_id"]
    if record_id:
        record = db().execute(
            "SELECT * FROM exam_records WHERE id = ? AND student_id = ?",
            (record_id, student_id),
        ).fetchone()
    else:
        record = db().execute(
            """
            SELECT * FROM exam_records WHERE student_id = ? AND status NOT IN ('submitted','forced')
            ORDER BY id DESC LIMIT 1
            """,
            (student_id,),
        ).fetchone()
    if not record:
        return fail("RECORD_NOT_FOUND", 404)
    if record["status"] in ("submitted", "forced"):
        return ok({"record": record_payload(record)}, "ALREADY_SUBMITTED")
    save_answers(record["id"], data.get("answers", {}))
    db().execute(
        "UPDATE exam_records SET submit_time = ?, status = ? WHERE id = ?",
        (now_iso(), "submitted", record["id"]),
    )
    db().commit()
    grade_record(record["id"])
    fresh = db().execute("SELECT * FROM exam_records WHERE id = ?", (record["id"],)).fetchone()
    return ok({"record": record_payload(fresh)}, "SUBMIT_OK")


@app.route("/api/teacher/start-exam", methods=["POST"])
@auth_required("teacher")
def start_exam():
    data = request.get_json(force=True, silent=True) or {}
    exam_id = data.get("exam_id")
    if exam_id:
        exam = db().execute("SELECT * FROM exams WHERE id = ?", (int(exam_id),)).fetchone()
    else:
        exam = current_exam()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    db().execute(
        "UPDATE exams SET status = ?, start_time = ?, end_time = NULL WHERE id = ?",
        ("running", now_iso(), exam["id"]),
    )
    db().commit()
    fresh = db().execute("SELECT * FROM exams WHERE id = ?", (exam["id"],)).fetchone()
    return ok(row_dict(fresh), "考试已开始")


@app.route("/api/teacher/reset-exam", methods=["POST"])
@auth_required("teacher")
def teacher_reset_exam():
    """教师把考试从『running / ended / closed』退回『waiting』，并清理 start_time / end_time。

    默认行为：仅重置单场。如果 body.all = true 则重置所有非 waiting 的考试。
    """
    data = request.get_json(force=True, silent=True) or {}
    reset_all = bool(data.get("all"))
    exam_id = data.get("exam_id")
    if reset_all:
        db().execute(
            "UPDATE exams SET status = 'waiting', start_time = NULL, end_time = NULL "
            "WHERE status != 'waiting'"
        )
        db().commit()
        return ok({"reset_all": True}, "所有考试已重置为待开始")
    if not exam_id:
        return fail("EXAM_ID_REQUIRED", 400)
    exam = db().execute("SELECT * FROM exams WHERE id = ?", (int(exam_id),)).fetchone()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    db().execute(
        "UPDATE exams SET status = 'waiting', start_time = NULL, end_time = NULL WHERE id = ?",
        (int(exam_id),),
    )
    db().commit()
    fresh = db().execute("SELECT * FROM exams WHERE id = ?", (int(exam_id),)).fetchone()
    return ok(row_dict(fresh), f"考试 #{exam_id} 已重置为待开始")


@app.route("/api/teacher/end-exam", methods=["POST"])
@auth_required("teacher")
def end_exam():
    data = request.get_json(force=True, silent=True) or {}
    exam_id = data.get("exam_id")
    if exam_id:
        exam = db().execute("SELECT * FROM exams WHERE id = ?", (int(exam_id),)).fetchone()
    else:
        exam = current_exam()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
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
    fresh = db().execute("SELECT * FROM exams WHERE id = ?", (exam["id"],)).fetchone()
    return ok(row_dict(fresh), "考试已结束")


@app.route("/api/teacher/exams", methods=["GET"])
@auth_required("teacher")
def list_exams():
    """E5 考试列表 - 列出所有历史考试"""
    rows = db().execute(
        """
        SELECT e.*, p.title AS paper_title
        FROM exams e
        LEFT JOIN papers p ON p.paper_id = e.paper_id
        ORDER BY e.id DESC
        """
    ).fetchall()
    return ok([row_dict(r) for r in rows])


@app.route("/api/teacher/exams", methods=["POST"])
@auth_required("teacher")
def create_exam():
    """E1 创建考试 - 选择已有试卷、设置名称、设置时长、设置允许考试次数"""
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    paper_id = str(data.get("paper_id", "")).strip()
    duration = int(data.get("duration_minutes") or 90)
    max_attempts = data.get("max_attempts", 1)
    try:
        max_attempts = int(max_attempts)
    except (TypeError, ValueError):
        max_attempts = 1
    if max_attempts < 0 or max_attempts > 99:
        return fail("INVALID_MAX_ATTEMPTS", 400)
    if not name:
        return fail("EXAM_NAME_REQUIRED", 400)
    if not paper_id:
        return fail("PAPER_REQUIRED", 400)
    paper = db().execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        return fail("PAPER_NOT_FOUND", 404)
    cur = db().execute(
        """
        INSERT INTO exams(name, paper_id, status, duration_minutes, max_attempts, created_at)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (name, paper_id, "waiting", duration, max_attempts, now_iso()),
    )
    db().commit()
    new_id = cur.lastrowid
    new_exam = db().execute("SELECT * FROM exams WHERE id = ?", (new_id,)).fetchone()
    return ok(row_dict(new_exam), "EXAM_CREATED")


@app.route("/api/teacher/exams/<int:exam_id>", methods=["PUT", "PATCH"])
@auth_required("teacher")
def update_exam(exam_id):
    """编辑考试信息：name / duration_minutes / max_attempts。试卷不可修改以避免题号错位。"""
    exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    data = request.get_json(force=True)
    name = str(data.get("name", exam["name"])).strip() or exam["name"]
    duration = int(data.get("duration_minutes") or exam["duration_minutes"])
    if "max_attempts" in data:
        try:
            ma = int(data.get("max_attempts"))
        except (TypeError, ValueError):
            return fail("INVALID_MAX_ATTEMPTS", 400)
        if ma < 0 or ma > 99:
            return fail("INVALID_MAX_ATTEMPTS", 400)
    else:
        ma = int(exam["max_attempts"] or 1)
    db().execute(
        "UPDATE exams SET name = ?, duration_minutes = ?, max_attempts = ? WHERE id = ?",
        (name, duration, ma, exam_id),
    )
    db().commit()
    fresh = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    return ok(row_dict(fresh), "EXAM_UPDATED")


@app.route("/api/teacher/exams/<int:exam_id>", methods=["DELETE"])
@auth_required("teacher")
def delete_exam(exam_id):
    """删除考试（仅在没有答卷时允许）"""
    exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not exam:
        return fail("EXAM_NOT_FOUND", 404)
    cnt = db().execute(
        "SELECT COUNT(*) AS c FROM exam_records WHERE exam_id = ?", (exam_id,)
    ).fetchone()["c"]
    if cnt > 0:
        return fail("EXAM_HAS_RECORDS", 400)
    db().execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    db().commit()
    return ok(message="EXAM_DELETED")


@app.route("/api/teacher/exams/<int:exam_id>/activate", methods=["POST"])
@auth_required("teacher")
def activate_exam(exam_id):
    """激活指定考试（设为当前考试）"""
    exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not exam:
        return fail("考试不存在", 404)
    return ok(row_dict(exam), "考试已激活")


@app.route("/api/teacher/auto-end-check", methods=["POST"])
@auth_required("teacher")
def auto_end_check():
    """检查考试时长是否到期，到期则自动结束并对未交卷学生强制收卷"""
    data = request.get_json(force=True, silent=True) or {}
    exam_id = data.get("exam_id")
    if exam_id:
        exam = db().execute("SELECT * FROM exams WHERE id = ?", (int(exam_id),)).fetchone()
    else:
        exam = current_exam()
    if not exam or exam["status"] != "running":
        return ok({"ended": False, "forced_count": 0})
    start = exam["start_time"]
    if not start:
        return ok({"ended": False, "forced_count": 0})
    try:
        start_dt = datetime.fromisoformat(start)
    except Exception:
        return ok({"ended": False, "forced_count": 0})
    elapsed = (datetime.now(timezone.utc) - start_dt).total_seconds()
    duration_seconds = int(exam["duration_minutes"]) * 60
    if elapsed < duration_seconds:
        return ok({"ended": False, "forced_count": 0, "remaining_seconds": int(duration_seconds - elapsed)})
    # 到期：结束考试 + 强制收卷所有未交卷学生
    db().execute(
        "UPDATE exams SET status = ?, end_time = ? WHERE id = ?",
        ("ended", now_iso(), exam["id"]),
    )
    uncommitted = db().execute(
        "SELECT id, student_id FROM exam_records WHERE exam_id = ? AND status NOT IN ('submitted', 'forced')",
        (exam["id"],),
    ).fetchall()
    forced_count = 0
    for rec in uncommitted:
        db().execute(
            """
            UPDATE exam_records
            SET status = ?, forced = 1, submit_time = COALESCE(submit_time, ?)
            WHERE id = ?
            """,
            ("forced", now_iso(), rec["id"]),
        )
        grade_record(rec["id"])
        forced_count += 1
    db().commit()
    return ok({"ended": True, "forced_count": forced_count}, "考试时长已到，已自动结束并强制收卷")


@app.route("/api/teacher/force-submit", methods=["POST"])
@auth_required("teacher")
def force_submit():
    data = request.get_json(force=True)
    student_id = str(data.get("student_id", "")).strip()
    record_id = int(data.get("record_id") or 0)
    exam_id = int(data.get("exam_id") or 0)
    conn = db()
    if record_id:
        record = conn.execute("SELECT * FROM exam_records WHERE id = ?", (record_id,)).fetchone()
    else:
        target_exam_id = exam_id or (current_exam() or {}).get("id")
        record = conn.execute(
            """
            SELECT * FROM exam_records
            WHERE exam_id = ? AND student_id = ? AND status NOT IN ('submitted','forced')
            ORDER BY id DESC LIMIT 1
            """,
            (target_exam_id, student_id),
        ).fetchone()
    if not record:
        return fail("RECORD_NOT_FOUND", 404)
    conn.execute(
        """
        UPDATE exam_records
        SET status = ?, forced = 1, submit_time = COALESCE(submit_time, ?)
        WHERE id = ?
        """,
        ("forced", now_iso(), record["id"]),
    )
    conn.commit()
    grade_record(record["id"])
    return ok({"record": record_payload(record)}, "FORCE_SUBMITTED")


@app.route("/api/teacher/dashboard")
@auth_required("teacher")
def teacher_dashboard():
    exam = current_exam()
    students = db().execute("SELECT student_id, name, class_name FROM students ORDER BY student_id").fetchall()
    records = db().execute(
        "SELECT * FROM exam_records WHERE exam_id = ?", (exam["id"],)
    ).fetchall()
    record_map = {r["student_id"]: r for r in records}
    total_questions = db().execute(
        "SELECT COUNT(*) AS count FROM questions WHERE paper_id = ?", (exam["paper_id"],)
    ).fetchone()["count"]
    status_counts = {
        "not_logged_in": 0, "logged_in": 0, "answering": 0,
        "submitted": 0, "forced": 0,
    }
    for student in students:
        record = record_map.get(student["student_id"])
        if not record:
            status_counts["not_logged_in"] += 1
        else:
            s = record["status"]
            if s == "not_logged_in":
                status_counts["not_logged_in"] += 1
            elif s in ("logged_in", "confirmed"):
                status_counts["logged_in"] += 1
            elif s == "answering":
                status_counts["answering"] += 1
            elif s == "submitted":
                status_counts["submitted"] += 1
            elif s == "forced":
                status_counts["forced"] += 1
    answered_distribution = {"0": 0, "1-3": 0, "4-6": 0, "all": 0}
    for student in students:
        record = record_map.get(student["student_id"])
        if not record:
            continue
        answered = db().execute(
            """
            SELECT COUNT(*) AS count FROM answers
            WHERE record_id = ? AND COALESCE(answer_text, '') != ''
            """,
            (record["id"],),
        ).fetchone()["count"]
        if answered == 0:
            answered_distribution["0"] += 1
        elif answered <= 3:
            answered_distribution["1-3"] += 1
        elif answered < total_questions:
            answered_distribution["4-6"] += 1
        else:
            answered_distribution["all"] += 1
    score_rows = db().execute(
        """
        SELECT total_score FROM exam_records WHERE exam_id = ?
        """,
        (exam["id"],),
    ).fetchall()
    score_buckets = {
        "0-59": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0,
    }
    for r in score_rows:
        s = float(r["total_score"] or 0)
        if s < 60:
            score_buckets["0-59"] += 1
        elif s < 70:
            score_buckets["60-69"] += 1
        elif s < 80:
            score_buckets["70-79"] += 1
        elif s < 90:
            score_buckets["80-89"] += 1
        else:
            score_buckets["90-100"] += 1
    return ok({
        "status_distribution": status_counts,
        "answered_distribution": answered_distribution,
        "score_distribution": score_buckets,
        "totals": {
            "students": len(students),
            "questions": total_questions,
        }
    })


@app.route("/api/teacher/monitor")
@auth_required("teacher")
def monitor():
    exam_id = request.args.get("exam_id", type=int)
    if exam_id:
        exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if not exam:
            return fail("EXAM_NOT_FOUND", 404)
    else:
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
    exam_id = int(request.args.get("exam_id") or 0)
    class_name = str(request.args.get("class_name", "")).strip()
    if not exam_id:
        cur = current_exam()
        exam_id = cur["id"] if cur else 0
    if not exam_id:
        return fail("NO_EXAM_AVAILABLE", 404)
    data = _build_results_data(exam_id, class_name)
    return ok({**data, "exam_id": exam_id, "class_name": class_name})


def _build_results_data(exam_id, class_name=""):
    """根据考试与班级过滤聚合学生-成绩数据，供 results() 与 export_results() 共用。
    返回结构: {"results": [...], "stats": {...}}
    不依赖 Flask request 上下文，可在任意位置直接调用。
    """
    sql = """
        SELECT s.student_id, s.name, s.class_name, r.id AS record_id, r.status,
               r.attempt_no,
               COALESCE(r.objective_score, 0) AS objective_score,
               COALESCE(r.subjective_score, 0) AS subjective_score,
               COALESCE(r.total_score, 0) AS total_score,
               r.submit_time
        FROM students s
        LEFT JOIN exam_records r
          ON r.student_id = s.student_id
         AND r.exam_id = ?
         AND r.id = (
            SELECT r2.id FROM exam_records r2
            WHERE r2.student_id = s.student_id AND r2.exam_id = ?
            ORDER BY r2.attempt_no DESC, r2.id DESC LIMIT 1
         )
    """
    params = [exam_id, exam_id]
    if class_name:
        sql += " WHERE s.class_name = ?"
        params.append(class_name)
    sql += " ORDER BY s.class_name, s.student_id"
    rows = db().execute(sql, params).fetchall()
    # 排名
    sorted_rows = sorted(rows, key=lambda r: -float(r["total_score"] or 0))
    rank_map = {}
    last_score = None
    last_rank = 0
    for i, r in enumerate(sorted_rows, 1):
        s = float(r["total_score"] or 0)
        if last_score is None or s < last_score:
            last_rank = i
            last_score = s
        rank_map[r["student_id"] + "::" + str(r["record_id"] or 0)] = last_rank
    items = []
    for r in rows:
        d = row_dict(r)
        key = d["student_id"] + "::" + str(d["record_id"] or 0)
        d["rank"] = rank_map.get(key)
        items.append(d)
    scores = [float(r["total_score"] or 0) for r in rows if r["record_id"]]
    stats = {
        "average": round(sum(scores) / len(scores), 2) if scores else 0,
        "highest": max(scores) if scores else 0,
        "lowest": min(scores) if scores else 0,
        "pass_rate": round(sum(1 for s in scores if s >= 60) / len(scores) * 100, 2) if scores else 0,
        "count": len(scores),
    }
    return {"results": items, "stats": stats}


@app.route("/api/teacher/classes")
@auth_required("teacher")
def list_classes():
    rows = db().execute(
        "SELECT DISTINCT class_name FROM students WHERE class_name IS NOT NULL AND class_name != '' ORDER BY class_name"
    ).fetchall()
    return ok([r["class_name"] for r in rows])


@app.route("/api/teacher/export")
@auth_required("teacher")
def export_results():
    """导出成绩：format=xlsx|csv。

    自定义导出支持三种范围（互斥，由 class_name 决定）：
      - 指定 class_name：仅导出该班级的成绩（Excel 一个 Sheet，CSV 仅该班级）。
      - class_name=__ALL__ 或不传：导出全部班级（Excel 汇总 + 按班级分 Sheet；CSV 全部）。
    始终通过 exam_id 限定为单场考试的数据。
    """
    fmt = str(request.args.get("format", "xlsx")).lower()
    exam_id = int(request.args.get("exam_id") or 0)
    raw_class = str(request.args.get("class_name", "")).strip()
    if not exam_id:
        cur = current_exam()
        exam_id = cur["id"] if cur else 0
    if not exam_id:
        return fail("NO_EXAM_AVAILABLE", 404)
    # 规范化班级参数：__ALL__ 视为「全部班级」，空值同义
    if raw_class.lower() in ("", "__all__", "all"):
        class_name = ""
        scope_label = "all"
    else:
        class_name = raw_class
        scope_label = "class"
    exam = db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone() if exam_id else None
    exam_title = safe_filename(exam["name"] if exam else f"exam_{exam_id}", default="exam")
    cn_class = safe_filename(class_name, default="all_classes")
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    data = _build_results_data(exam_id, class_name)
    rows = data["results"]
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["学号", "姓名", "班级", "客观分", "主观分", "总分", "排名", "状态", "提交时间"])
        for row in rows:
            writer.writerow([
                row["student_id"], row["name"], row["class_name"] or "",
                row["objective_score"], row["subjective_score"], row["total_score"],
                row.get("rank") or "", row["status"] or "未登录", row.get("submit_time") or "",
            ])
        csv_bytes = output.getvalue().encode("utf-8-sig")
        suffix = "全部班级" if scope_label == "all" else class_name
        filename = f"成绩单_{exam_title}_{suffix}_{ts}.csv"
        return Response(
            csv_bytes,
            mimetype="text/csv; charset=utf-8",
            headers=_content_disposition(filename),
        )
    # Excel 导出（多 Sheet）
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return fail("OPENPYXL_MISSING", 500)
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4A90D9")
    align_center = Alignment(horizontal="center", vertical="center")

    def write_header(ws):
        headers = ["学号", "姓名", "班级", "客观分", "主观分", "总分", "排名", "状态", "提交时间"]
        ws.append(headers)
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
        for col, w in enumerate([16, 12, 20, 10, 10, 10, 8, 12, 22], 1):
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = w

    def write_rows(ws, items):
        for r in items:
            ws.append([
                r["student_id"], r["name"], r["class_name"] or "",
                r["objective_score"], r["subjective_score"], r["total_score"],
                r.get("rank") or "", r["status"] or "未登录", r.get("submit_time") or "",
            ])

    def write_stats(ws, stats):
        ws.append([])
        ws.append(["统计", "参考人数", "平均分", "最高分", "最低分", "及格率(%)"])
        ws.append(["", stats.get("count", 0), stats.get("average", 0),
                   stats.get("highest", 0), stats.get("lowest", 0), stats.get("pass_rate", 0)])

    if scope_label == "class":
        # 单个班级：单 Sheet
        ws = wb.active
        ws.title = class_name[:30]
        write_header(ws)
        write_rows(ws, rows)
        ws.append([])
        write_stats(ws, data["stats"])
    else:
        # 全部班级：汇总 Sheet + 按班级分 Sheet
        ws = wb.active
        ws.title = "汇总"
        write_header(ws)
        write_rows(ws, rows)
        ws.append([])
        write_stats(ws, data["stats"])
        # 按班级分 Sheet
        groups = {}
        for r in rows:
            groups.setdefault(r["class_name"] or "未分班", []).append(r)
        for cls, items in groups.items():
            ws_cls = wb.create_sheet(title=(cls or "未分班")[:30])
            write_header(ws_cls)
            write_rows(ws_cls, items)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    suffix = "全部班级" if scope_label == "all" else class_name
    filename = f"成绩单_{exam_title}_{suffix}_{ts}.xlsx"
    return Response(
        out.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_content_disposition(filename),
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


@app.route("/api/papers/<paper_id>", methods=["GET", "DELETE"])
@auth_required("teacher")
def paper_detail(paper_id):
    if request.method == "DELETE":
        db().execute("DELETE FROM questions WHERE paper_id = ?", (paper_id,))
        db().execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        db().commit()
        return ok(message="试卷已删除")
    # GET：返回试卷基本信息 + 全部题目（含教师可见的答案/关键字）
    paper = db().execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        return fail("PAPER_NOT_FOUND", 404)
    qrows = db().execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY question_no",
        (paper_id,),
    ).fetchall()
    locale = get_request_locale()
    questions = []
    for r in qrows:
        item = row_dict(r)
        item["options"] = json.loads(item.pop("options_json") or "[]")
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        questions.append(item)
    questions = [localize_question(q, locale, include_answer=True) for q in questions]
    payload = row_dict(paper)
    payload["questions"] = questions
    payload["question_count"] = len(questions)
    return ok(payload)


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
        return fail("请上传文件", 400)
    filename = file.filename or ""
    count = 0
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            from openpyxl import load_workbook
            workbook = load_workbook(file, read_only=True)
            sheet = workbook.active
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
        except Exception as e:
            return fail(f"Excel 解析失败: {e}", 400)
    elif filename.lower().endswith(".csv"):
        try:
            content = file.read().decode("utf-8-sig")
            reader = csv.reader(io.StringIO(content))
            for index, row in enumerate(reader):
                if index == 0:
                    continue
                if not row:
                    continue
                student_id = str(row[0] or "").strip()
                name = str(row[1] or "").strip() if len(row) > 1 else ""
                class_name = str(row[2] or "").strip() if len(row) > 2 else ""
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
        except Exception as e:
            return fail(f"CSV 解析失败: {e}", 400)
    else:
        return fail("仅支持 Excel (.xlsx/.xls) 或 CSV (.csv) 文件", 400)
    db().commit()
    return ok({"count": count}, f"成功导入 {count} 名学生")


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
    exam_id = int(request.args.get("exam_id") or 0)
    if not exam_id:
        cur = current_exam()
        exam_id = cur["id"] if cur else 0
    if not exam_id:
        return fail("NO_EXAM_AVAILABLE", 404)
    rows = db().execute(
        """
        SELECT r.id AS record_id, s.student_id, s.name, q.question_no, q.content, q.score AS max_score,
               a.id AS answer_id, a.answer_text, a.score, q.type, q.answer
        FROM answers a
        JOIN exam_records r ON r.id = a.record_id
        JOIN students s ON s.student_id = r.student_id
        JOIN questions q ON q.id = a.question_id
        WHERE r.exam_id = ? AND q.type IN ('short_answer', 'fill_blank')
        ORDER BY s.student_id, q.question_no
        """,
        (exam_id,),
    ).fetchall()
    return ok([row_dict(r) for r in rows])


@app.route("/api/teacher/grading/exams")
@auth_required("teacher")
def grading_exams():
    """批改可选考试列表：返回至少有一条已交卷答卷的考试。"""
    rows = db().execute(
        """
        SELECT e.id, e.name, e.paper_id, e.status, e.duration_minutes, e.max_attempts,
               e.start_time, e.end_time, e.created_at,
               (SELECT COUNT(*) FROM exam_records r WHERE r.exam_id = e.id AND r.status IN ('submitted','forced')) AS record_count,
               (SELECT COUNT(*) FROM answers a JOIN exam_records r ON r.id = a.record_id
                  JOIN questions q ON q.id = a.question_id
                  WHERE r.exam_id = e.id AND q.type IN ('short_answer','fill_blank')) AS grading_count
        FROM exams e
        ORDER BY e.id DESC
        """
    ).fetchall()
    return ok([row_dict(r) for r in rows])


@app.route("/api/teacher/grading/list")
@auth_required("teacher")
def grading_list():
    """批改列表（带筛选/排序/分页）。"""
    exam_id = int(request.args.get("exam_id") or 0)
    status = str(request.args.get("status", "all")).strip()  # all | pending | graded
    keyword = str(request.args.get("keyword", "")).strip()
    sort = str(request.args.get("sort", "student_id")).strip()
    order = "DESC" if str(request.args.get("order", "asc")).lower() == "desc" else "ASC"
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    if not exam_id:
        cur = current_exam()
        exam_id = cur["id"] if cur else 0
    if not exam_id:
        return fail("NO_EXAM_AVAILABLE", 404)

    sort_whitelist = {"student_id", "score", "submitted_at", "name"}
    if sort not in sort_whitelist:
        sort = "student_id"
    sort_col = {
        "student_id": "s.student_id",
        "score": "r.total_score",
        "submitted_at": "r.submit_time",
        "name": "s.name",
    }[sort]

    where = ["r.exam_id = ?", "r.status IN ('submitted','forced')"]
    params = [exam_id]
    if keyword:
        where.append("(s.student_id LIKE ? OR s.name LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    # pending: 至少存在一个未批改的简答/填空题
    pending_filter = ""
    if status == "pending":
        pending_filter = (
            " AND EXISTS (SELECT 1 FROM answers a JOIN questions q ON q.id = a.question_id "
            " WHERE a.record_id = r.id AND q.type IN ('short_answer','fill_blank') AND a.grading_method = 'auto' AND (a.score IS NULL OR a.score = 0))"
        )
    elif status == "graded":
        pending_filter = (
            " AND NOT EXISTS (SELECT 1 FROM answers a JOIN questions q ON q.id = a.question_id "
            " WHERE a.record_id = r.id AND q.type IN ('short_answer','fill_blank') AND a.grading_method = 'auto' AND (a.score IS NULL OR a.score = 0))"
        )

    base_sql = f"""
        FROM students s
        JOIN exam_records r ON r.student_id = s.student_id
         AND r.id = (SELECT r2.id FROM exam_records r2
                     WHERE r2.student_id = s.student_id AND r2.exam_id = r.exam_id
                     ORDER BY r2.attempt_no DESC, r2.id DESC LIMIT 1)
        WHERE r.exam_id = ? AND r.status IN ('submitted','forced')
        {" AND (s.student_id LIKE ? OR s.name LIKE ?)" if keyword else ""}
        {pending_filter}
    """
    base_params = [exam_id]
    if keyword:
        base_params.extend([f"%{keyword}%", f"%{keyword}%"])

    total = db().execute(f"SELECT COUNT(*) AS c {base_sql}", base_params).fetchone()["c"]
    rows = db().execute(
        f"SELECT s.student_id, s.name, s.class_name, r.id AS record_id, r.status, r.attempt_no, "
        f"r.objective_score, r.subjective_score, r.total_score, r.submit_time {base_sql} "
        f"ORDER BY {sort_col} {order}, s.student_id ASC LIMIT ? OFFSET ?",
        base_params + [per_page, (page - 1) * per_page],
    ).fetchall()
    items = []
    for r in rows:
        d = row_dict(r)
        # 标记是否已批改完（无 pending 主观题）
        pending = db().execute(
            """
            SELECT COUNT(*) AS c FROM answers a JOIN questions q ON q.id = a.question_id
            WHERE a.record_id = ? AND q.type IN ('short_answer','fill_blank')
              AND a.grading_method = 'auto' AND (a.score IS NULL OR a.score = 0)
            """,
            (r["record_id"],),
        ).fetchone()["c"]
        d["pending_count"] = pending
        d["is_fully_graded"] = pending == 0
        items.append(d)
    pages = (total + per_page - 1) // per_page if total else 0
    return ok({
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
        "results": items,
    })


@app.cli.command("init-db")
def init_db_command():
    seed()
    print("Database initialized.")


# ============================================
# 试卷导入（Excel / JSON / Markdown）
# ============================================
VALID_QUESTION_TYPES = {
    "single_choice", "multiple_choice", "true_false",
    "short_answer", "fill_blank",
}


def _normalize_question_type(qtype):
    raw = str(qtype or "single_choice").strip()
    aliases = {
        "单选题": "single_choice",
        "单选": "single_choice",
        "single": "single_choice",
        "single_choice": "single_choice",
        "多选题": "multiple_choice",
        "多选": "multiple_choice",
        "multiple": "multiple_choice",
        "multiple_choice": "multiple_choice",
        "判断题": "true_false",
        "判断": "true_false",
        "是非题": "true_false",
        "true_false": "true_false",
        "简答题": "short_answer",
        "简答": "short_answer",
        "short_answer": "short_answer",
        "填空题": "fill_blank",
        "填空": "fill_blank",
        "fill_blank": "fill_blank",
    }
    return aliases.get(raw, raw)


def _parse_options_text(text):
    """解析选项文本，支持 'A. xxx' 或 'A、xxx'，多行分隔。"""
    if not text:
        return []
    out = []
    for line in str(text).replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        # 尝试匹配 A. / A、/ A: / A） 形式
        m = None
        for sep in [".", "、", ":", ":", "：", ")", "）"]:
            idx = line.find(sep)
            if idx > 0 and idx <= 3 and line[:idx].strip().isalpha() and len(line[:idx].strip()) == 1:
                m = (line[:idx].strip().upper(), line[idx + 1:].strip())
                break
        if m:
            out.append({"key": m[0], "value": m[1]})
        else:
            # 无前缀，依次编号
            keys = ["A", "B", "C", "D", "E", "F"]
            out.append({"key": keys[len(out) % len(keys)], "value": line})
    return out


def _parse_answer_text(answer_text, qtype):
    """将单元格中的答案文本解析为数据库可用的值。"""
    if answer_text is None:
        return "" if qtype != "fill_blank" else "[]"
    s = str(answer_text).strip()
    if qtype == "single_choice":
        return s.upper().strip(".：:")[:1]
    if qtype == "multiple_choice":
        parts = [p.strip().upper().strip(".：:")[:1] for p in s.replace("，", ",").split(",") if p.strip()]
        return ",".join(sorted(set(parts)))
    if qtype == "true_false":
        if s in ("对", "正确", "T", "true", "True", "√", "yes", "YES"):
            return "true"
        if s in ("错", "错误", "F", "false", "False", "×", "no", "NO"):
            return "false"
        return s
    if qtype == "fill_blank":
        # 多种分隔：换行 / | / 中文逗号 / 英文逗号
        parts = [p.strip() for p in s.replace("\n", "|").replace("，", "|").replace(",", "|").split("|") if p.strip()]
        return json.dumps(parts, ensure_ascii=False)
    # short_answer
    return s


def _validate_question(q, idx):
    """校验单个题目，返回错误列表。"""
    errors = []
    if q.get("type") not in VALID_QUESTION_TYPES:
        errors.append({"row": idx, "field": "type", "reason": f"UNSUPPORTED_TYPE:{q.get('type')}"})
    if not q.get("content"):
        errors.append({"row": idx, "field": "content", "reason": "CONTENT_EMPTY"})
    if q.get("type") in ("single_choice", "multiple_choice"):
        opts = q.get("options") or []
        if not opts:
            errors.append({"row": idx, "field": "options", "reason": "OPTIONS_REQUIRED"})
        elif q.get("type") == "single_choice":
            if not q.get("answer") or q["answer"] not in [o["key"] for o in opts]:
                errors.append({"row": idx, "field": "answer", "reason": f"ANSWER_NOT_IN_OPTIONS:{q.get('answer')}"})
        else:
            if not q.get("answer"):
                errors.append({"row": idx, "field": "answer", "reason": "ANSWER_REQUIRED"})
            else:
                keys = set(q["answer"].split(","))
                valid = {o["key"] for o in opts}
                bad = keys - valid
                if bad:
                    errors.append({"row": idx, "field": "answer", "reason": f"ANSWER_INVALID:{','.join(bad)}"})
    if q.get("type") == "fill_blank":
        content = q.get("content", "")
        blanks = content.count("__")
        try:
            arr = json.loads(q.get("answer") or "[]")
        except Exception:
            arr = []
        if blanks == 0:
            errors.append({"row": idx, "field": "content", "reason": "NO_BLANK_IN_CONTENT"})
        elif len(arr) != blanks:
            errors.append({
                "row": idx, "field": "answer",
                "reason": f"BLANK_MISMATCH:blanks={blanks},answers={len(arr)}",
            })
    if not q.get("score") or int(q["score"]) <= 0:
        errors.append({"row": idx, "field": "score", "reason": "SCORE_INVALID"})
    return errors


def _parse_excel_paper(file_obj):
    from openpyxl import load_workbook
    wb = load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("EMPTY_FILE")
    header_row = [str(c or "").strip() for c in rows[0]]
    # 期望列：题号|题型|题干|题干(EN)|选项|选项(EN)|答案|分值|关键词|填空数量
    header_aliases = {
        "题号": "question_no", "题型": "type", "题干": "content", "题目": "content",
        "题干(EN)": "content_en", "题干(en)": "content_en", "题干EN": "content_en",
        "选项": "options", "选项(EN)": "options_en", "选项(en)": "options_en", "选项EN": "options_en",
        "答案": "answer", "参考答案": "answer", "分值": "score",
        "关键词": "keywords", "填空数量": "blank_count",
    }
    col_map = {}
    for i, h in enumerate(header_row):
        col_map[header_aliases.get(h, h)] = i
    questions = []
    for ri, row in enumerate(rows[1:], 2):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        def get(key, default=""):
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return default
            v = row[idx]
            return v if v is not None else default
        q = {
            "question_no": get("question_no", len(questions) + 1),
            "type": _normalize_question_type(get("type", "single_choice")),
            "content": str(get("content", "")).strip(),
            "content_en": str(get("content_en", "")).strip(),
            "options": _parse_options_text(get("options", "")),
            "options_en": _parse_options_text(get("options_en", "")),
            "answer": _parse_answer_text(get("answer", ""), _normalize_question_type(get("type", "single_choice"))),
            "score": int(get("score", 0) or 0),
            "keywords": [],
        }
        # 填空题：blank_count 兜底
        if q["type"] == "fill_blank":
            blank_count = get("blank_count", 0)
            if isinstance(blank_count, int) and blank_count > 0 and q["content"].count("__") == 0:
                q["content"] = "__ " * blank_count
                q["content"] = q["content"].strip()
        questions.append(q)
    return questions


def _parse_json_paper(file_obj):
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    obj = json.loads(raw, strict=False)
    questions = []
    for i, q in enumerate(obj.get("questions", []), 1):
        qtype = str(q.get("type", "single_choice")).strip()
        ans = q.get("answer", "")
        if qtype == "fill_blank":
            if isinstance(ans, list):
                ans = json.dumps(ans, ensure_ascii=False)
            elif isinstance(ans, str):
                ans = ans.strip()
                if not ans.startswith("["):
                    ans = json.dumps([p.strip() for p in ans.split(",") if p.strip()], ensure_ascii=False)
        elif qtype == "multiple_choice":
            if isinstance(ans, list):
                ans = ",".join(sorted({a.strip().upper()[:1] for a in ans}))
        elif qtype == "true_false":
            ans = "true" if str(ans).lower() in ("true", "对", "t", "yes", "1") else "false"
        else:
            ans = str(ans).strip()
        questions.append({
            "question_no": q.get("question_no", i),
            "type": qtype,
            "content": str(q.get("content", "")).strip(),
            "content_en": str(q.get("content_en") or q.get("contentEN") or "").strip(),
            "options": q.get("options", []) or [],
            "options_en": q.get("options_en") or q.get("optionsEN") or [],
            "answer": ans,
            "score": int(q.get("score", 0) or 0),
            "keywords": q.get("keywords", []) or [],
        })
    return questions, obj.get("title", "导入试卷"), obj.get("duration_minutes", 90)


def _parse_markdown_paper(file_obj):
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    lines = raw.replace("\r\n", "\n").split("\n")
    title = "导入试卷"
    duration = 90
    questions = []
    type_map = {
        "单选题": "single_choice", "单选": "single_choice",
        "多选题": "multiple_choice", "多选": "multiple_choice",
        "判断题": "true_false", "判断": "true_false",
        "简答题": "short_answer", "简答": "short_answer",
        "填空题": "fill_blank", "填空": "fill_blank",
    }
    cur_type = None
    cur_score = 2
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 文件头
        if line.startswith("# ") and not questions and title == "导入试卷":
            title = line[2:].strip()
            i += 1
            continue
        if line.startswith(">") and "总分" in line and "时长" in line:
            # 解析总分/时长
            m_total = _re.search(r"总分[:：]\s*(\d+)", line)
            m_dur = _re.search(r"时长[:：]\s*(\d+)", line)
            if m_total:
                # 不一定每题固定分值，留作参考
                pass
            if m_dur:
                duration = int(m_dur.group(1))
            i += 1
            continue
        # 题型段
        if line.startswith("## "):
            seg = line[3:].strip()
            # 提取 "每题 X 分"
            m = _re.search(r"每[题空][^0-9]*(\d+)\s*分", seg)
            if m:
                cur_score = int(m.group(1))
            for k, v in type_map.items():
                if k in seg:
                    cur_type = v
                    break
            i += 1
            continue
        # 题干
        if line.startswith("**") and cur_type:
            m = _re.match(r"\*\*(\d+)\.\s*(.*?)\*\*", line)
            if not m:
                i += 1
                continue
            qno = int(m.group(1))
            content = m.group(2)
            content_en = ""
            opts = []
            opts_en = []
            j = i + 1
            while j < len(lines):
                sub = lines[j].strip()
                if sub.startswith("[EN]"):
                    content_en = sub[4:].strip()
                    j += 1
                    continue
                if sub.startswith("- ") and len(sub) > 4 and sub[2].isalpha() and sub[3] in (".", "、", ":", "："):
                    parsed = _parse_options_text(sub[2:])
                    if parsed:
                        opts.append(parsed[0])
                    j += 1
                elif sub.startswith("-EN ") or sub.startswith("[EN] -"):
                    parsed = _parse_options_text(sub.split(" ", 1)[-1])
                    if parsed:
                        opts_en.append(parsed[0])
                    j += 1
                else:
                    break
            # 答案行
            answer_text = ""
            while j < len(lines):
                sub = lines[j].strip()
                if sub.startswith("**答案"):
                    answer_text = sub.split("**", 2)[-1].lstrip("：:").strip()
                    j += 1
                    break
                if sub.startswith("---") or sub.startswith("**") or sub.startswith("## "):
                    break
                j += 1
            q = {
                "question_no": qno,
                "type": cur_type,
                "content": content,
                "content_en": content_en,
                "options": opts,
                "options_en": opts_en,
                "score": cur_score,
            }
            if cur_type == "fill_blank":
                if "____" not in content and "__" not in content:
                    content = content + " __"
                q["content"] = content
            q["answer"] = _parse_answer_text(answer_text, cur_type)
            questions.append(q)
            i = j
            continue
        i += 1
    return questions, title, duration


@app.route("/api/teacher/papers/import", methods=["POST"])
@auth_required("teacher")
def import_paper():
    file = request.files.get("file")
    if not file:
        return fail("FILE_REQUIRED", 400)
    filename = (file.filename or "").lower()
    try:
        if filename.endswith((".xlsx", ".xls")):
            questions = _parse_excel_paper(file)
            paper_title = "导入试卷(Excel)"
            duration = 90
        elif filename.endswith(".json"):
            questions, paper_title, duration = _parse_json_paper(file)
        elif filename.endswith((".md", ".markdown")):
            questions, paper_title, duration = _parse_markdown_paper(file)
        else:
            return fail("UNSUPPORTED_FORMAT", 400)
    except ValueError as e:
        return fail(f"PARSE_ERROR:{e}", 400)
    except Exception as e:
        return fail(f"PARSE_ERROR:{e}", 400)

    # 校验所有题目
    all_errors = []
    for idx, q in enumerate(questions, 1):
        all_errors.extend(_validate_question(q, idx))
    if all_errors:
        return fail("VALIDATION_FAILED", 400, data={"errors": all_errors})

    # 写入
    paper_id = f"P{int(time.time())}{random.randint(10, 99)}"
    total_score = sum(int(q["score"]) for q in questions)
    data = request.form or {}
    title = data.get("title") or paper_title
    try:
        duration = int(data.get("duration_minutes") or duration)
    except (TypeError, ValueError):
        pass
    db().execute(
        "INSERT INTO papers(paper_id, title, total_score, duration_minutes, created_at) VALUES(?, ?, ?, ?, ?)",
        (paper_id, title, total_score, duration, now_iso()),
    )
    for index, q in enumerate(questions, 1):
        options_en = q.get("options_en") or q.get("optionsEN")
        if isinstance(options_en, list) and options_en:
            options_en_json = json.dumps(options_en, ensure_ascii=False)
        else:
            options_en_json = ""
        db().execute(
            """
            INSERT INTO questions(paper_id, question_no, type, content, options_json, answer, score, keywords_json, content_en, options_en_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id, index, q["type"], q["content"],
                json.dumps(q.get("options", []), ensure_ascii=False),
                q.get("answer", ""),
                int(q.get("score", 0)),
                json.dumps(q.get("keywords", []), ensure_ascii=False),
                (q.get("content_en") or q.get("contentEN") or "").strip(),
                options_en_json,
            ),
        )
    db().commit()
    return ok({
        "paper_id": paper_id,
        "title": title,
        "question_count": len(questions),
        "total_score": total_score,
        "duration_minutes": duration,
    }, "PAPER_IMPORTED")


@app.route("/api/teacher/papers/template", methods=["GET"])
@auth_required("teacher")
def paper_template():
    fmt = str(request.args.get("format", "xlsx")).lower()
    if fmt == "json":
        sample = {
            "paper_title": "Web 前端开发模拟卷 / Web Front-end Mock Paper",
            "total_score": 100,
            "duration_minutes": 90,
            "questions": [
                {
                    "question_no": 1, "type": "single_choice",
                    "content": "HTML 的全称是什么？",
                    "content_en": "What does HTML stand for?",
                    "options": [
                        {"key": "A", "value": "HyperText Markup Language"},
                        {"key": "B", "value": "HyperText Machine Language"},
                        {"key": "C", "value": "HighText Markup Language"},
                        {"key": "D", "value": "HyperTool Markup Language"},
                    ],
                    "options_en": [
                        {"key": "A", "value": "HyperText Markup Language"},
                        {"key": "B", "value": "HyperText Machine Language"},
                        {"key": "C", "value": "HighText Markup Language"},
                        {"key": "D", "value": "HyperTool Markup Language"},
                    ],
                    "answer": "A", "score": 2,
                },
                {
                    "question_no": 4, "type": "fill_blank",
                    "content": "HTTP 默认端口是 __，HTTPS 是 __",
                    "content_en": "The default port for HTTP is __, and for HTTPS is __",
                    "answer": "80,443", "score": 2,
                },
            ],
        }
        body = json.dumps(sample, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            body, mimetype="application/json; charset=utf-8",
            headers=_content_disposition("paper_template.json"),
        )
    if fmt in ("md", "markdown"):
        body = (
            "# Web 前端开发模拟卷 / Web Front-end Mock Paper\n\n"
            "> 总分：100 | 时长：90 分钟\n\n"
            "## 一、单选题（每题 2 分）\n\n"
            "**1. HTML 的全称是什么？**\n"
            "[EN] What does HTML stand for?\n"
            "- A. HyperText Markup Language\n"
            "- B. HyperText Machine Language\n"
            "- C. HighText Markup Language\n"
            "- D. HyperTool Markup Language\n\n"
            "**答案：** A\n\n"
            "---\n\n"
            "## 二、填空题（每空 1 分）\n\n"
            "**2. OSI 模型分为 __ 层。**\n"
            "[EN] The OSI model has __ layers.\n"
            "**答案：** 7\n\n"
            "**3. HTTP 默认端口是 __，HTTPS 是 __。**\n"
            "[EN] The default port for HTTP is __, and for HTTPS is __.\n"
            "**答案：** 80 | 443\n"
        ).encode("utf-8")
        return Response(
            body, mimetype="text/markdown; charset=utf-8",
            headers=_content_disposition("paper_template.md"),
        )
    # Excel 模板
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "questions"
    ws.append(["题号", "题型", "题干", "题干(EN)", "选项", "选项(EN)", "答案", "分值", "关键词", "填空数量"])
    ws.append([
        1, "single_choice", "HTML 的全称是什么？", "What does HTML stand for?",
        "A.HyperText Markup Language\nB.HyperText Machine Language\nC.HighText Markup Language\nD.HyperTool Markup Language",
        "A.HyperText Markup Language\nB.HyperText Machine Language\nC.HighText Markup Language\nD.HyperTool Markup Language",
        "A", 2, "", 0,
    ])
    ws.append([
        2, "multiple_choice", "下列属于 JS 框架的有", "Which of the following are JS frameworks?",
        "A.React\nB.Vue\nC.Django\nD.Angular",
        "A.React\nB.Vue\nC.Django\nD.Angular",
        "A,B,D", 3, "", 0,
    ])
    ws.append([3, "true_false", "TCP 是面向连接的协议", "TCP is a connection-oriented protocol", "", "", "对", 1, "", 0])
    ws.append([4, "fill_blank", "OSI 模型有 __ 层", "The OSI model has __ layers", "", "", "7", 2, "", 1])
    ws.append([
        5, "short_answer", "简述 HTTP 与 HTTPS 的区别", "Briefly describe the difference between HTTP and HTTPS",
        "", "", "HTTPS 在 HTTP 基础上加入 SSL/TLS 加密", 5, "加密,协议,端口", 0,
    ])
    ws.append([6, "fill_blank", "HTTP 默认端口是 __，HTTPS 是 __", "The default port for HTTP is __, and for HTTPS is __", "", "", "80,443", 2, "", 2])
    for col, w in enumerate([8, 18, 40, 40, 40, 40, 30, 8, 20, 10], 1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = w
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return Response(
        out.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_content_disposition("paper_template.xlsx"),
    )


if __name__ == "__main__":
    # 启动时一次性完成 seed，避免在请求处理时再次加锁
    with app.app_context():
        seed()
    BOOTSTRAPPED = True
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
