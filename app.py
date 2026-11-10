from flask import Flask, request, redirect, url_for, render_template_string, flash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, json, uuid

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "yk-fake-account-report")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

UPLOAD_DIR = "evidence"
DATA_FILE = "reports.json"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf", "txt"}
os.makedirs(UPLOAD_DIR, exist_ok=True)

def load_reports():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_reports(reports):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

PAGE = r"""
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YK TRICKS INDIA • Fake Account Report</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:Inter,Arial,sans-serif;color:#f5f7fb;background:radial-gradient(circle at 15% 15%,rgba(124,58,237,.28),transparent 30%),radial-gradient(circle at 85% 80%,rgba(236,72,153,.20),transparent 32%),#090b12}.wrap{width:min(1180px,94%);margin:auto;padding:28px 0 50px}header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:20px 24px;border:1px solid rgba(255,255,255,.1);border-radius:24px;background:rgba(20,23,34,.78);backdrop-filter:blur(16px)}.brand{font-size:22px;font-weight:900;letter-spacing:1px}.brand span{display:block;font-size:11px;color:#aeb6c8;margin-top:5px;letter-spacing:2px}.badge{padding:9px 13px;border-radius:999px;background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.28);color:#86efac;font-size:12px}.hero{padding:55px 8px 30px}.hero h1{font-size:clamp(34px,6vw,64px);line-height:1;margin:0 0 18px}.hero p{max-width:760px;color:#aeb6c8;font-size:16px;line-height:1.7}.grid{display:grid;grid-template-columns:1.45fr .8fr;gap:22px}.card{border:1px solid rgba(255,255,255,.10);border-radius:26px;background:rgba(18,21,31,.86);padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.28)}h2{margin:0 0 20px;font-size:21px}label{display:block;font-size:12px;font-weight:800;color:#b8c0d1;margin:18px 0 8px}input,select,textarea{width:100%;border:1px solid rgba(255,255,255,.12);outline:none;background:#0d1018;color:#fff;border-radius:14px;padding:14px 15px;font-size:14px}textarea{min-height:130px;resize:vertical}input:focus,select:focus,textarea:focus{border-color:#8b5cf6}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}button,.btn{border:0;border-radius:14px;padding:14px 18px;font-weight:900;cursor:pointer;background:linear-gradient(135deg,#7c3aed,#db2777);color:white;text-decoration:none;display:inline-block;margin-top:20px}.secondary{background:#191d29;border:1px solid rgba(255,255,255,.1)}.notice{padding:15px 16px;border-radius:15px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);color:#fcd34d;font-size:13px;line-height:1.6}.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px}.stat{padding:18px;border-radius:18px;background:#10131c;border:1px solid rgba(255,255,255,.08)}.stat b{font-size:25px;display:block}.stat small,.muted{color:#929bad}.report{padding:17px 0;border-bottom:1px solid rgba(255,255,255,.08)}.flash{margin:15px 0;padding:14px;border-radius:14px;background:rgba(34,197,94,.1);color:#86efac}footer{text-align:center;color:#6f7788;font-size:12px;padding:25px}@media(max-width:800px){.grid,.row{grid-template-columns:1fr}.hero{padding-top:35px}.card{padding:21px}}
</style></head><body><div class="wrap">
<header><div class="brand">YK TRICKS INDIA <span>FAKE ACCOUNT • EVIDENCE CENTER</span></div><div class="badge">● LOCAL TRACKER</div></header>
<section class="hero"><h1>Fake Account<br>Report Center</h1><p>Document a suspected fake or impersonating Instagram account and keep the supporting evidence organized. This app does not send mass or automated reports.</p></section>
{% with messages=get_flashed_messages() %}{% for message in messages %}<div class="flash">{{message}}</div>{% endfor %}{% endwith %}
<div class="grid"><section class="card"><h2>Create evidence report</h2>
<form action="{{url_for('create_report')}}" method="post" enctype="multipart/form-data">
<div class="row"><div><label>Fake account username</label><input name="username" placeholder="@example_account" required></div><div><label>Profile URL</label><input name="profile_url" type="url" placeholder="https://www.instagram.com/example_account/"></div></div>
<label>Report reason</label><select name="reason" required><option value="">Select a reason</option><option>Impersonation / fake identity</option><option>Scam or fraud</option><option>Harassment or bullying</option><option>Other policy violation</option></select>
<label>Evidence / incident notes</label><textarea name="notes" placeholder="Describe what happened and why the report is legitimate."></textarea>
<label>Evidence files</label><input name="evidence" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.pdf,.txt">
<div class="notice" style="margin-top:18px"><b>Privacy:</b> Do not upload passwords, session tokens, cookies, payment details, or other credentials.</div>
<button type="submit">SAVE REPORT &amp; EVIDENCE</button></form></section>
<aside class="card"><h2>Report summary</h2><div class="stats"><div class="stat"><b>{{reports|length}}</b><small>Saved reports</small></div><div class="stat"><b>{{evidence_count}}</b><small>Evidence files</small></div></div>
<div class="notice" style="margin-top:18px"><b>Next step</b><br>Use Instagram's own in-app/reporting process to submit the appropriate report. This tracker does not submit reports.</div>
<a class="btn secondary" href="https://www.instagram.com/" target="_blank" rel="noopener">OPEN INSTAGRAM</a></aside></div>
<section class="card" style="margin-top:22px"><h2>Saved reports</h2>
{% if reports %}{% for r in reports|reverse %}<div class="report"><strong>@{{r.username}} — {{r.reason}}</strong><div class="muted">{{r.created_at}} · ID {{r.id}}</div>{% if r.profile_url %}<div class="muted">{{r.profile_url}}</div>{% endif %}{% if r.files %}<div class="muted">Evidence: {{r.files|join(", ")}}</div>{% endif %}</div>{% endfor %}{% else %}<div class="muted">No reports saved yet.</div>{% endif %}
</section><footer>YK TRICKS INDIA • Evidence tracking only • No automated mass reporting</footer></div></body></html>
"""

@app.get("/")
def index():
    reports = load_reports()
    return render_template_string(PAGE, reports=reports, evidence_count=sum(len(x.get("files", [])) for x in reports))

@app.post("/create-report")
def create_report():
    username = request.form.get("username", "").strip().lstrip("@")
    profile_url = request.form.get("profile_url", "").strip()
    reason = request.form.get("reason", "").strip()
    notes = request.form.get("notes", "").strip()
    if not username or not reason:
        flash("Username and report reason are required.")
        return redirect(url_for("index"))
    report_id = uuid.uuid4().hex[:10].upper()
    saved_files = []
    for file in request.files.getlist("evidence"):
        if file and file.filename and allowed_file(file.filename):
            safe = secure_filename(file.filename)
            final = f"{report_id}_{safe}"
            file.save(os.path.join(UPLOAD_DIR, final))
            saved_files.append(final)
    reports = load_reports()
    reports.append({"id":report_id,"username":username,"profile_url":profile_url,"reason":reason,"notes":notes,"files":saved_files,"created_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    save_reports(reports)
    flash(f"Report {report_id} saved successfully.")
    return redirect(url_for("index"))

@app.get("/health")
def health():
    return {"status":"ok","service":"fake-account-evidence-center"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
