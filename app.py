from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3, os, re, csv, io, math, json

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "civicai.db"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

app = Flask(__name__)
app.secret_key = os.environ.get("CIVICAI_SECRET_KEY", "change-this-demo-secret-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

CATEGORIES = [
    "Roads and Potholes", "Drainage and Waterlogging", "Waste Management",
    "Streetlights and Electricity", "Water Supply", "Public Infrastructure",
    "Traffic and Public Safety", "Other"
]
DEPARTMENTS = {
    "Roads and Potholes": "Roads and Public Works",
    "Drainage and Waterlogging": "Drainage Department",
    "Waste Management": "Sanitation Department",
    "Streetlights and Electricity": "Electrical Maintenance",
    "Water Supply": "Water Supply Department",
    "Public Infrastructure": "Public Works Department",
    "Traffic and Public Safety": "Public Safety Department",
    "Other": "General Civic Services"
}
STATUSES = ["Submitted", "Under Review", "Assigned", "In Progress", "Resolved", "Reopened", "Rejected", "Duplicate"]

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def same_department(first, second):
    """Compare department labels safely despite case or accidental whitespace differences."""
    return bool(first and second and str(first).strip().casefold() == str(second).strip().casefold())

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('citizen','admin','officer')),
        department TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        public_id TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        summary TEXT NOT NULL,
        location TEXT NOT NULL,
        latitude REAL,
        longitude REAL,
        priority TEXT NOT NULL,
        priority_score INTEGER NOT NULL,
        priority_reason TEXT NOT NULL,
        department TEXT NOT NULL,
        status TEXT NOT NULL,
        image_filename TEXT,
        resolution_summary TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        complaint_id INTEGER NOT NULL,
        actor TEXT NOT NULL,
        status TEXT NOT NULL,
        public_message TEXT NOT NULL,
        internal_note TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        complaint_id INTEGER NOT NULL,
        related_id INTEGER NOT NULL,
        similarity INTEGER NOT NULL,
        reason TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        UNIQUE(complaint_id, related_id),
        FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE,
        FOREIGN KEY(related_id) REFERENCES complaints(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        complaint_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        comment TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(complaint_id, user_id),
        FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        complaint_id INTEGER,
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
    CREATE INDEX IF NOT EXISTS idx_complaints_category ON complaints(category);
    CREATE INDEX IF NOT EXISTS idx_complaints_location ON complaints(latitude, longitude);
    """)
    # Demo accounts are created only if absent.
    demos = [
        ("admin", "Admin123!", "admin", None),
        ("officer", "Officer123!", "officer", "Drainage Department"),
        ("citizen", "Citizen123!", "citizen", None),
    ]
    for username, password, role, department in demos:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,department,created_at) VALUES(?,?,?,?,?)",
                (username, generate_password_hash(password), role, department, now())
            )
    conn.commit()
    seed_demo_data(conn)
    conn.close()

def seed_demo_data(conn):
    count = conn.execute("SELECT COUNT(*) AS n FROM complaints").fetchone()["n"]
    if count:
        return
    citizen = conn.execute("SELECT id FROM users WHERE username='citizen'").fetchone()["id"]
    samples = [
        ("Pothole near Central Bus Stand", "A large pothole is making it difficult for two-wheelers to pass safely.", "Roads and Potholes", "Central Bus Stand Road", 12.9141, 74.8560, "High", 72, "Road damage and reported safety concern increase priority.", "Roads and Public Works", "Submitted"),
        ("Drain blocked near market", "Dirty water is overflowing from a blocked drain near the market entrance.", "Drainage and Waterlogging", "City Market", 12.9165, 74.8550, "High", 78, "Reported waterlogging and public access impact increase priority.", "Drainage Department", "Assigned"),
        ("Garbage overflowing", "The public bin has been full for two days and waste is spreading on the footpath.", "Waste Management", "Market Lane", 12.9167, 74.8553, "Medium", 54, "Overflowing waste and reported duration contribute to priority.", "Sanitation Department", "In Progress"),
        ("Streetlight not working", "The streetlight near the bus stop has not worked for three nights.", "Streetlights and Electricity", "Bus Stop Junction", 12.9138, 74.8565, "Medium", 58, "A public lighting issue with repeated duration was reported.", "Electrical Maintenance", "Resolved"),
        ("Water supply interruption", "No water supply in the lane since this morning.", "Water Supply", "Lake View Lane", 12.9182, 74.8580, "Medium", 50, "Reported interruption to a public service.", "Water Supply Department", "Under Review"),
        ("Another pothole report", "Road surface has a deep pothole close to Central Bus Stand.", "Roads and Potholes", "Central Bus Stand Road", 12.9142, 74.8561, "High", 70, "Similar wording and nearby coordinates suggest a related report.", "Roads and Public Works", "Submitted"),
        ("Drain water pooling", "Water is collecting on the road beside the market because the drain seems blocked.", "Drainage and Waterlogging", "City Market", 12.9164, 74.8551, "High", 74, "Reported water pooling and proximity to other reports increase priority.", "Drainage Department", "Submitted"),
        ("Loose electrical cable", "A low hanging electrical cable is visible near the public walkway. Please inspect it.", "Traffic and Public Safety", "Public Walkway", 12.9129, 74.8548, "Critical", 92, "Potential immediate hazard requires prompt human assessment.", "Public Safety Department", "Under Review"),
        ("Damaged park bench", "A bench in the public park is broken and has a sharp edge.", "Public Infrastructure", "Town Park", 12.9190, 74.8530, "Medium", 48, "Damaged public infrastructure with a possible injury concern.", "Public Works Department", "Resolved"),
        ("Garbage at market lane again", "Waste has accumulated again near the same market lane after collection.", "Waste Management", "Market Lane", 12.9168, 74.8552, "Medium", 60, "Similar location and issue suggest possible recurrence.", "Sanitation Department", "Reopened"),
    ]
    for i, row in enumerate(samples, 1):
        title, desc, category, location, lat, lon, priority, score, reason, dept, status = row
        summary = make_summary(desc)
        public_id = f"CMP{100+i}"
        created = (datetime.now() - timedelta(days=(i % 8))).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute("""INSERT INTO complaints
            (public_id,user_id,title,description,category,summary,location,latitude,longitude,priority,priority_score,priority_reason,department,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (public_id,citizen,title,desc,category,summary,location,lat,lon,priority,score,reason,dept,status,created,created))
        cid = cur.lastrowid
        conn.execute("INSERT INTO updates(complaint_id,actor,status,public_message,created_at) VALUES(?,?,?,?,?)",
                     (cid,"CivicAI 360","Submitted","Demo complaint loaded for demonstration.",created))
    conn.commit()
    # Link likely duplicates for demo records.
    rows = conn.execute("SELECT id, public_id, title, description, category, latitude, longitude FROM complaints ORDER BY id").fetchall()
    for a in rows:
        for b in rows:
            if a["id"] >= b["id"]:
                continue
            sim, reason = similarity_between(dict(a), dict(b))
            if sim >= 45:
                conn.execute("INSERT OR IGNORE INTO relationships(complaint_id,related_id,similarity,reason,confirmed) VALUES(?,?,?,?,0)",
                             (a["id"], b["id"], sim, reason))
                conn.execute("INSERT OR IGNORE INTO relationships(complaint_id,related_id,similarity,reason,confirmed) VALUES(?,?,?,?,0)",
                             (b["id"], a["id"], sim, reason))
    conn.commit()

def tokenize(text):
    stop = {"the","a","an","is","are","in","on","at","to","of","and","for","near","has","have","it","this","that","with","from","by","please"}
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in stop}

def make_summary(description):
    text = re.sub(r"\s+", " ", (description or "").strip())
    if len(text) <= 125:
        return text
    sentence = re.split(r"(?<=[.!?])\s+", text)[0]
    return sentence[:122].rstrip() + ("..." if len(sentence) > 122 else "")

def infer_category(text):
    t = (text or "").lower()
    rules = [
        (["pothole","road damage","road surface","crater"], "Roads and Potholes"),
        (["drain","waterlogging","water logging","dirty water","water pooling","blocked drainage"], "Drainage and Waterlogging"),
        (["garbage","waste","rubbish","bin","trash"], "Waste Management"),
        (["streetlight","street light","lamp","electrical cable","electric wire"], "Streetlights and Electricity"),
        (["water supply","no water","water interruption","tap water"], "Water Supply"),
        (["bench","park","footpath","building","public infrastructure"], "Public Infrastructure"),
        (["traffic","unsafe","accident","hazard","walkway"], "Traffic and Public Safety"),
    ]
    for keywords, category in rules:
        if any(k in t for k in keywords):
            return category, 0.78, "Matched issue-related terms in the report."
    return "Other", 0.42, "The report did not match a strong category rule; please review."

def priority_for(text, category, duration=""):
    t = ((text or "") + " " + (duration or "")).lower()
    score, factors = 25, []
    urgent = ["live wire","electrical cable","fire","collapse","injury","injured","immediate danger","exposed wire","unsafe"]
    high = ["overflow","flood","waterlogging","pothole","blocked drain","sewage","no water","cannot pass","cannot walk"]
    if any(k in t for k in urgent):
        score += 42; factors.append("potential safety hazard mentioned")
    if any(k in t for k in high):
        score += 22; factors.append("public access or service disruption mentioned")
    if any(k in t for k in ["days","week","weeks","again","repeated","since yesterday"]):
        score += 10; factors.append("duration or recurrence mentioned")
    if category in ("Traffic and Public Safety", "Streetlights and Electricity"):
        score += 8; factors.append("category may involve public safety")
    score = min(score, 100)
    level = "Critical" if score >= 85 else "High" if score >= 65 else "Medium" if score >= 42 else "Low"
    if not factors:
        factors.append("limited severity information was provided")
    return score, level, "; ".join(factors).capitalize() + "."

def similarity_between(a, b):
    text_a = f"{a.get('title','')} {a.get('description','')}"
    text_b = f"{b.get('title','')} {b.get('description','')}"
    ta, tb = tokenize(text_a), tokenize(text_b)
    union = ta | tb
    text_score = int(100 * len(ta & tb) / len(union)) if union else 0
    cat_score = 20 if a.get("category") == b.get("category") else 0
    geo_score = 0
    try:
        if a.get("latitude") is not None and b.get("latitude") is not None:
            dist = haversine(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
            geo_score = 25 if dist <= 0.25 else 12 if dist <= 0.75 else 0
    except (TypeError, ValueError):
        pass
    score = min(100, int(text_score * 0.65 + cat_score + geo_score))
    reason_parts = []
    if text_score >= 20: reason_parts.append("overlapping description terms")
    if cat_score: reason_parts.append("same category")
    if geo_score: reason_parts.append("nearby coordinates")
    return score, ", ".join(reason_parts) or "limited matching signals"

def haversine(lat1, lon1, lat2, lon2):
    r = 6371
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2)-float(lat1))
    dl = math.radians(float(lon2)-float(lon1))
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1, math.sqrt(x)))

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def roles_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def audit(actor, action, complaint_id=None, details=""):
    conn = get_db()
    conn.execute("INSERT INTO audit_logs(actor,action,complaint_id,details,created_at) VALUES(?,?,?,?,?)",
                 (actor, action, complaint_id, details, now()))
    conn.commit()
    conn.close()

@app.route("/")
def index():
    conn = get_db()
    stats = {
        "total": conn.execute("SELECT COUNT(*) n FROM complaints").fetchone()["n"],
        "resolved": conn.execute("SELECT COUNT(*) n FROM complaints WHERE status='Resolved'").fetchone()["n"],
        "open": conn.execute("SELECT COUNT(*) n FROM complaints WHERE status NOT IN ('Resolved','Rejected','Duplicate')").fetchone()["n"],
        "categories": conn.execute("SELECT COUNT(DISTINCT category) n FROM complaints").fetchone()["n"]
    }
    conn.close()
    return render_template("index.html", stats=stats)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["department"] = user["department"]
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    role = session["role"]
    if role == "citizen":
        complaints = conn.execute("SELECT * FROM complaints WHERE user_id=? ORDER BY created_at DESC", (session["user_id"],)).fetchall()
    elif role == "officer":
        # Refresh the officer's department from the database in case it was changed by an admin.
        officer = conn.execute("SELECT department FROM users WHERE id=? AND role='officer'", (session["user_id"],)).fetchone()
        if officer:
            session["department"] = officer["department"]
        department = (session.get("department") or "").strip()
        complaints = conn.execute(
            "SELECT * FROM complaints WHERE LOWER(TRIM(department))=LOWER(TRIM(?)) ORDER BY created_at DESC",
            (department,)
        ).fetchall()
    else:
        complaints = conn.execute("SELECT * FROM complaints ORDER BY created_at DESC").fetchall()
    all_rows = conn.execute("SELECT * FROM complaints ORDER BY created_at DESC").fetchall()
    counts = {
        "total": len(all_rows),
        "open": sum(1 for r in all_rows if r["status"] not in ("Resolved","Rejected","Duplicate")),
        "critical": sum(1 for r in all_rows if r["priority"] in ("Critical","High") and r["status"] not in ("Resolved","Rejected","Duplicate")),
        "resolved": sum(1 for r in all_rows if r["status"] == "Resolved"),
        "duplicates": conn.execute("SELECT COUNT(*) n FROM relationships WHERE similarity>=45 AND confirmed=0").fetchone()["n"],
        "recurring": conn.execute("SELECT COUNT(*) n FROM complaints WHERE status='Reopened'").fetchone()["n"],
    }
    categories = conn.execute("SELECT category, COUNT(*) AS count FROM complaints GROUP BY category ORDER BY count DESC").fetchall()
    recent_logs = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8").fetchall() if role == "admin" else []
    conn.close()
    return render_template("dashboard.html", complaints=complaints, counts=counts, categories=categories,
                           statuses=STATUSES, categories_list=CATEGORIES, recent_logs=recent_logs)

@app.route("/complaints/new", methods=["GET","POST"])
@roles_required("citizen")
def new_complaint():
    if request.method == "POST":
        title = request.form.get("title","").strip()
        description = request.form.get("description","").strip()
        category = request.form.get("category","").strip()
        location = request.form.get("location","").strip()
        duration = request.form.get("duration","").strip()
        lat_raw, lon_raw = request.form.get("latitude","").strip(), request.form.get("longitude","").strip()
        if not title or not description or not location:
            flash("Title, description, and location are required.", "danger")
            return render_template("new_complaint.html", categories=CATEGORIES)
        if len(title) > 140 or len(description) > 4000 or len(location) > 250:
            flash("Please shorten the title, description, or location.", "danger")
            return render_template("new_complaint.html", categories=CATEGORIES)
        predicted, conf, category_reason = infer_category(title + " " + description)
        if category not in CATEGORIES:
            category = predicted
        elif category == "Other" and predicted != "Other":
            category = predicted
        score, priority, reason = priority_for(title + " " + description, category, duration)
        image_filename = None
        upload = request.files.get("photo")
        if upload and upload.filename:
            safe = secure_filename(upload.filename)
            ext = safe.rsplit(".",1)[-1].lower() if "." in safe else ""
            if ext not in ALLOWED_EXTENSIONS:
                flash("Upload a PNG, JPG, JPEG, or WEBP image.", "danger")
                return render_template("new_complaint.html", categories=CATEGORIES)
            upload.stream.seek(0, os.SEEK_END)
            size = upload.stream.tell()
            upload.stream.seek(0)
            if size > MAX_UPLOAD_BYTES:
                flash("Image must be 5 MB or smaller.", "danger")
                return render_template("new_complaint.html", categories=CATEGORIES)
            image_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe}"
            upload.save(UPLOAD_DIR / image_filename)
        try:
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None
            if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
                raise ValueError()
        except ValueError:
            flash("Enter valid latitude and longitude, or leave both empty.", "danger")
            return render_template("new_complaint.html", categories=CATEGORIES)
        conn = get_db()
        public_id = f"CMP{datetime.now().strftime('%y%m%d%H%M%S')}{os.urandom(2).hex().upper()}"
        department = DEPARTMENTS.get(category, DEPARTMENTS["Other"])
        cur = conn.execute("""INSERT INTO complaints
            (public_id,user_id,title,description,category,summary,location,latitude,longitude,priority,priority_score,priority_reason,department,status,image_filename,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (public_id,session["user_id"],title,description,category,make_summary(description),location,lat,lon,priority,score,reason,department,"Submitted",image_filename,now(),now()))
        cid = cur.lastrowid
        conn.execute("INSERT INTO updates(complaint_id,actor,status,public_message,created_at) VALUES(?,?,?,?,?)",
                     (cid,session["username"],"Submitted","Complaint received. AI-assisted triage is ready for review.",now()))
        # Compare against earlier records. Similarity is a suggestion, never an automatic merge.
        others = conn.execute("SELECT * FROM complaints WHERE id<>? ORDER BY id DESC LIMIT 200", (cid,)).fetchall()
        current = dict(conn.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone())
        for other in others:
            sim, why = similarity_between(current, dict(other))
            if sim >= 35:
                conn.execute("INSERT OR IGNORE INTO relationships(complaint_id,related_id,similarity,reason,confirmed) VALUES(?,?,?,?,0)",
                             (cid,other["id"],sim,why))
                conn.execute("INSERT OR IGNORE INTO relationships(complaint_id,related_id,similarity,reason,confirmed) VALUES(?,?,?,?,0)",
                             (other["id"],cid,sim,why))
        conn.commit()
        conn.close()
        audit(session["username"], "complaint_created", cid, f"Created {public_id}")
        flash(f"Complaint submitted successfully. Your reference ID is {public_id}.", "success")
        return redirect(url_for("complaint_detail", complaint_id=cid))
    return render_template("new_complaint.html", categories=CATEGORIES)

def can_view_complaint(row):
    role = session.get("role")
    return role == "admin" or row["user_id"] == session.get("user_id") or (role == "officer" and same_department(row["department"], session.get("department")))

@app.route("/complaints/<int:complaint_id>")
@login_required
def complaint_detail(complaint_id):
    conn = get_db()
    row = conn.execute("SELECT c.*, u.username AS reporter FROM complaints c JOIN users u ON c.user_id=u.id WHERE c.id=?", (complaint_id,)).fetchone()
    if not row:
        conn.close()
        flash("Complaint not found.", "danger")
        return redirect(url_for("dashboard"))
    if not can_view_complaint(row):
        conn.close()
        flash("You do not have permission to view this complaint.", "danger")
        return redirect(url_for("dashboard"))
    updates = conn.execute("SELECT * FROM updates WHERE complaint_id=? ORDER BY id DESC", (complaint_id,)).fetchall()
    related = conn.execute("""SELECT r.*, c.public_id, c.title, c.category, c.status, c.location
                              FROM relationships r JOIN complaints c ON c.id=r.related_id
                              WHERE r.complaint_id=? ORDER BY r.similarity DESC LIMIT 8""", (complaint_id,)).fetchall()
    feedback = conn.execute("SELECT * FROM feedback WHERE complaint_id=? AND user_id=?", (complaint_id,session["user_id"])).fetchone()
    conn.close()
    return render_template("complaint_detail.html", complaint=row, updates=updates, related=related, statuses=STATUSES, feedback=feedback)

@app.route("/complaints/<int:complaint_id>/update", methods=["POST"])
@login_required
def update_complaint(complaint_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
    if not row:
        conn.close(); flash("Complaint not found.", "danger"); return redirect(url_for("dashboard"))
    role = session["role"]
    allowed = role == "admin" or (role == "officer" and same_department(row["department"], session.get("department")))
    if not allowed:
        conn.close(); flash("Only an authorized administrator or assigned department officer can update this complaint.", "danger")
        return redirect(url_for("complaint_detail", complaint_id=complaint_id))
    status = request.form.get("status", row["status"])
    department = request.form.get("department", row["department"])
    public_message = request.form.get("public_message","").strip()
    internal_note = request.form.get("internal_note","").strip() if role == "admin" else ""
    resolution = request.form.get("resolution_summary","").strip()
    if status not in STATUSES:
        conn.close(); flash("Invalid status.", "danger"); return redirect(url_for("complaint_detail", complaint_id=complaint_id))
    if status == "Resolved" and not resolution:
        conn.close(); flash("Add a resolution summary before marking this complaint resolved.", "danger")
        return redirect(url_for("complaint_detail", complaint_id=complaint_id))
    if role == "officer":
        department = row["department"]
    if department not in set(DEPARTMENTS.values()):
        department = row["department"]
    if not public_message:
        public_message = f"Status updated to {status}."
    conn.execute("UPDATE complaints SET status=?, department=?, resolution_summary=?, updated_at=? WHERE id=?",
                 (status, department, resolution if status == "Resolved" else row["resolution_summary"], now(), complaint_id))
    conn.execute("INSERT INTO updates(complaint_id,actor,status,public_message,internal_note,created_at) VALUES(?,?,?,?,?,?)",
                 (complaint_id,session["username"],status,public_message,internal_note,now()))
    conn.commit()
    conn.close()
    audit(session["username"], "complaint_updated", complaint_id, f"Status={status}; Department={department}")
    flash("Complaint workflow updated.", "success")
    return redirect(url_for("complaint_detail", complaint_id=complaint_id))

@app.route("/complaints/<int:complaint_id>/feedback", methods=["POST"])
@login_required
def submit_feedback(complaint_id):
    rating = request.form.get("rating","")
    comment = request.form.get("comment","").strip()[:1000]
    try:
        rating = int(rating)
        if rating < 1 or rating > 5: raise ValueError()
    except ValueError:
        flash("Choose a rating from 1 to 5.", "danger")
        return redirect(url_for("complaint_detail", complaint_id=complaint_id))
    conn = get_db()
    row = conn.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
    if not row or row["user_id"] != session["user_id"]:
        conn.close(); flash("Only the reporting citizen can provide feedback.", "danger")
        return redirect(url_for("dashboard"))
    conn.execute("INSERT INTO feedback(complaint_id,user_id,rating,comment,created_at) VALUES(?,?,?,?,?) ON CONFLICT(complaint_id,user_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created_at=excluded.created_at",
                 (complaint_id,session["user_id"],rating,comment,now()))
    conn.commit(); conn.close()
    audit(session["username"], "feedback_submitted", complaint_id, f"Rating={rating}")
    flash("Thank you for your feedback.", "success")
    return redirect(url_for("complaint_detail", complaint_id=complaint_id))

@app.route("/admin/relationships")
@roles_required("admin")
def relationships():
    conn = get_db()
    rows = conn.execute("""SELECT r.*, a.public_id AS a_id, a.title AS a_title, b.public_id AS b_id,
                           b.title AS b_title, b.location AS b_location
                           FROM relationships r JOIN complaints a ON a.id=r.complaint_id
                           JOIN complaints b ON b.id=r.related_id
                           WHERE r.similarity>=35 AND r.complaint_id < r.related_id
                           ORDER BY r.similarity DESC LIMIT 100""").fetchall()
    conn.close()
    return render_template("relationships.html", relationships=rows)

@app.route("/admin/relationships/<int:rel_id>/confirm", methods=["POST"])
@roles_required("admin")
def confirm_relationship(rel_id):
    conn = get_db()
    conn.execute("UPDATE relationships SET confirmed=1 WHERE id=?", (rel_id,))
    conn.commit(); conn.close()
    audit(session["username"], "relationship_confirmed", None, f"Relationship ID {rel_id}")
    flash("Relationship marked as reviewed/confirmed.", "success")
    return redirect(url_for("relationships"))

@app.route("/admin/hotspots")
@roles_required("admin","officer")
def hotspots():
    conn = get_db()
    rows = conn.execute("SELECT * FROM complaints WHERE latitude IS NOT NULL AND longitude IS NOT NULL").fetchall()
    conn.close()
    # Lightweight geographic grouping for prototype; 0.004 degree grid, not an official boundary.
    clusters = {}
    for r in rows:
        key = (round(r["latitude"] / 0.004), round(r["longitude"] / 0.004))
        clusters.setdefault(key, []).append(dict(r))
    out = []
    for key, items in clusters.items():
        if len(items) < 2:
            continue
        open_count = sum(1 for x in items if x["status"] not in ("Resolved","Rejected","Duplicate"))
        out.append({
            "name": f"Hotspot {len(out)+1}",
            "latitude": sum(x["latitude"] for x in items)/len(items),
            "longitude": sum(x["longitude"] for x in items)/len(items),
            "count": len(items),
            "open": open_count,
            "categories": ", ".join(sorted(set(x["category"] for x in items))),
            "complaints": items
        })
    out.sort(key=lambda x: (x["open"],x["count"]), reverse=True)
    return render_template("hotspots.html", hotspots=out, all_rows=rows)

@app.route("/admin/export.csv")
@roles_required("admin")
def export_csv():
    conn = get_db()
    rows = conn.execute("""SELECT public_id,title,description,category,location,priority,priority_score,department,status,created_at,updated_at
                           FROM complaints ORDER BY created_at DESC""").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Complaint ID","Title","Description","Category","Location","Priority","Score","Department","Status","Created","Updated"])
    for row in rows:
        writer.writerow(list(row))
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=civicai_complaints.csv"})

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","app":"CivicAI 360","database":DB_PATH.exists()})

@app.errorhandler(413)
def too_large(_):
    flash("Upload is too large. Images must be 5 MB or smaller.", "danger")
    return redirect(request.referrer or url_for("new_complaint"))

@app.errorhandler(500)
def server_error(_):
    return render_template("error.html", message="Something went wrong. Please return to the dashboard and try again."), 500

with app.app_context():
    init_db()

if __name__ == "__main__":
    print("\\nCivicAI 360 is starting.")
    print("Demo logins: admin / Admin123! | officer / Officer123! | citizen / Citizen123!")
    print("Open http://127.0.0.1:5000\\n")
    app.run(debug=True, host="127.0.0.1", port=5000)
