import os
import threading
import time
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify
from instagrapi import Client

app = Flask(__name__)

# ============================================================
# Instagram Group Locker
# - Login: Session ID OR Username/Password
# - Group thread ID
# - Locked group name
# - Start / Stop
# - Configurable check interval
# - Live status + logs
# ============================================================

LOCKER_THREAD = None
STOP_EVENT = threading.Event()

LOGS = []
LOG_LOCK = threading.Lock()

CLIENT = None
LOGIN_METHOD = ""
ACCOUNT_USERNAME = ""

LOCKER_CONFIG = {
    "thread_id": "",
    "locked_name": "",
    "interval": 5,
}

LOCKER_STATS = {
    "status": "stopped",
    "checks": 0,
    "changes": 0,
    "errors": 0,
    "current_name": "",
    "thread_id": "",
    "locked_name": "",
    "started_at": 0,
    "uptime": 0,
}


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    with LOG_LOCK:
        LOGS.append(line)
        if len(LOGS) > 200:
            del LOGS[:-200]
    print(line, flush=True)


def clear_runtime_logs():
    with LOG_LOCK:
        LOGS.clear()


def get_logs():
    with LOG_LOCK:
        return list(LOGS[-100:])


def reset_stats():
    LOCKER_STATS.update({
        "status": "stopped",
        "checks": 0,
        "changes": 0,
        "errors": 0,
        "current_name": "",
        "thread_id": LOCKER_CONFIG["thread_id"],
        "locked_name": LOCKER_CONFIG["locked_name"],
        "started_at": 0,
        "uptime": 0,
    })


def login_with_session(token):
    global CLIENT, LOGIN_METHOD, ACCOUNT_USERNAME

    token = (token or "").strip()
    if not token:
        raise ValueError("Session token is empty.")

    cl = Client()
    cl.login_by_sessionid(token)
    user = cl.account_info()

    CLIENT = cl
    LOGIN_METHOD = "session_token"
    ACCOUNT_USERNAME = user.username

    log("✅ Session token login successful!")
    log(f"👤 @{ACCOUNT_USERNAME} ready!")
    return cl


def login_with_credentials(username, password):
    global CLIENT, LOGIN_METHOD, ACCOUNT_USERNAME

    username = (username or "").strip()
    password = password or ""

    if not username or not password:
        raise ValueError("Username and password are required.")

    cl = Client()
    cl.login(username, password)
    user = cl.account_info()

    CLIENT = cl
    LOGIN_METHOD = "username_password"
    ACCOUNT_USERNAME = user.username

    # Keep a reusable local session for this process/deployment.
    try:
        cl.dump_settings("session.json")
    except Exception:
        pass

    log("✅ Username/Password login successful!")
    log(f"👤 @{ACCOUNT_USERNAME} ready!")
    return cl


def login_from_request():
    session_token = request.form.get("token", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if session_token:
        return login_with_session(session_token)

    if username and password:
        return login_with_credentials(username, password)

    raise ValueError("Enter a Session Token OR Username + Password.")


def get_thread_title(cl, thread_id):
    thread = cl.direct_thread(int(thread_id), amount=1)
    title = getattr(thread, "thread_title", None)
    if title is None:
        title = getattr(thread, "title", None)
    return (title or "").strip()


def update_thread_title(cl, thread_id, title):
    # Current instagrapi exposes direct_thread_update_title().
    return cl.direct_thread_update_title(int(thread_id), title)


def locker_loop(cl, thread_id, locked_name, interval):
    log("🔒 Group locker worker started.")

    while not STOP_EVENT.is_set():
        try:
            current_name = get_thread_title(cl, thread_id)
            LOCKER_STATS["checks"] += 1
            LOCKER_STATS["current_name"] = current_name

            if current_name != locked_name:
                log(f'⚠️ Group name is "{current_name}" — restoring "{locked_name}"...')
                update_thread_title(cl, thread_id, locked_name)
                LOCKER_STATS["changes"] += 1
                LOCKER_STATS["current_name"] = locked_name
                log("✅ Group name restored successfully.")
            else:
                log(f'✅ Group name correct: "{locked_name}"')

        except Exception as exc:
            LOCKER_STATS["errors"] += 1
            log(f"⚠️ Locker error: {exc}")

        for _ in range(interval):
            if STOP_EVENT.is_set():
                break
            time.sleep(1)

    LOCKER_STATS["status"] = "stopped"
    LOCKER_STATS["uptime"] = (
        int(time.time() - LOCKER_STATS["started_at"])
        if LOCKER_STATS["started_at"]
        else 0
    )
    log("🛑 Group locker stopped.")


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/login", methods=["POST"])
def login():
    try:
        login_from_request()
        return jsonify({
            "ok": True,
            "message": f"✅ Logged in as @{ACCOUNT_USERNAME}",
            "username": ACCOUNT_USERNAME,
        })
    except Exception as exc:
        log(f"⚠️ Login error: {exc}")
        return jsonify({"ok": False, "message": f"❌ Login failed: {exc}"}), 400


@app.route("/logout", methods=["POST"])
def logout():
    global CLIENT, LOGIN_METHOD, ACCOUNT_USERNAME

    stop_locker_internal()
    CLIENT = None
    LOGIN_METHOD = ""
    ACCOUNT_USERNAME = ""

    log("🔓 Instagram account disconnected.")
    return jsonify({"ok": True, "message": "✅ Logged out."})


@app.route("/start", methods=["POST"])
def start_locker():
    global LOCKER_THREAD

    if LOCKER_THREAD and LOCKER_THREAD.is_alive():
        return jsonify({"ok": False, "message": "⚙️ Locker is already running."})

    if CLIENT is None:
        return jsonify({"ok": False, "message": "❌ Login to Instagram first."}), 400

    thread_id = request.form.get("thread_id", "").strip()
    locked_name = request.form.get("locked_name", "").strip()

    try:
        interval = int(request.form.get("interval", "5"))
    except ValueError:
        interval = 5

    if not thread_id:
        return jsonify({"ok": False, "message": "❌ Group Thread ID is required."}), 400

    if not thread_id.isdigit():
        return jsonify({"ok": False, "message": "❌ Thread ID must contain digits only."}), 400

    if not locked_name:
        return jsonify({"ok": False, "message": "❌ Locked Group Name is required."}), 400

    interval = max(5, min(interval, 3600))

    # Validate access before starting the background loop.
    try:
        current_name = get_thread_title(CLIENT, thread_id)
    except Exception as exc:
        log(f"⚠️ Could not open group thread: {exc}")
        return jsonify({
            "ok": False,
            "message": f"❌ Group thread could not be opened: {exc}",
        }), 400

    LOCKER_CONFIG.update({
        "thread_id": thread_id,
        "locked_name": locked_name,
        "interval": interval,
    })

    STOP_EVENT.clear()
    reset_stats()
    LOCKER_STATS["status"] = "running"
    LOCKER_STATS["current_name"] = current_name
    LOCKER_STATS["started_at"] = time.time()

    log(f"🚀 Locker started for thread {thread_id}.")
    log(f'🎯 Locked name: "{locked_name}"')
    log(f"⏱️ Check interval: {interval}s")
    log(f'📌 Current group name: "{current_name}"')

    LOCKER_THREAD = threading.Thread(
        target=locker_loop,
        args=(CLIENT, thread_id, locked_name, interval),
        daemon=True,
    )
    LOCKER_THREAD.start()

    return jsonify({"ok": True, "message": "✅ Group locker started."})


@app.route("/stop", methods=["POST"])
def stop_locker():
    stop_locker_internal()
    return jsonify({"ok": True, "message": "🛑 Group locker stopped."})


def stop_locker_internal():
    global LOCKER_THREAD

    STOP_EVENT.set()

    if LOCKER_THREAD and LOCKER_THREAD.is_alive():
        LOCKER_THREAD.join(timeout=10)

    LOCKER_THREAD = None
    LOCKER_STATS["status"] = "stopped"


@app.route("/logs")
def logs():
    return jsonify({"logs": get_logs()})


@app.route("/status")
def status():
    running = bool(LOCKER_THREAD and LOCKER_THREAD.is_alive())

    if running and LOCKER_STATS["started_at"]:
        uptime = int(time.time() - LOCKER_STATS["started_at"])
    else:
        uptime = LOCKER_STATS.get("uptime", 0)

    return jsonify({
        "logged_in": CLIENT is not None,
        "username": ACCOUNT_USERNAME,
        "login_method": LOGIN_METHOD,
        "running": running,
        "status": "running" if running else "stopped",
        "thread_id": LOCKER_CONFIG["thread_id"],
        "locked_name": LOCKER_CONFIG["locked_name"],
        "current_name": LOCKER_STATS["current_name"],
        "interval": LOCKER_CONFIG["interval"],
        "checks": LOCKER_STATS["checks"],
        "changes": LOCKER_STATS["changes"],
        "errors": LOCKER_STATS["errors"],
        "uptime": uptime,
    })


PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Instagram Group Locker</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
    --violet:#8b2dff;
    --cyan:#22d3ee;
    --mint:#00ff9d;
    --red:#ff4757;
    --bg:#05030a;
}
html,body{min-height:100%;background:var(--bg);color:#fff}
body{
    font-family:Poppins,sans-serif;
    overflow-x:hidden;
}
.backdrop{
    position:fixed;inset:0;z-index:0;overflow:hidden;
    background:
      radial-gradient(ellipse at 50% 115%,rgba(139,45,255,.35),transparent 60%),
      radial-gradient(ellipse at 15% -10%,rgba(34,211,238,.18),transparent 45%),
      linear-gradient(180deg,#07040d,#0a0614 45%,#12081f 75%,#05020a);
}
.mist{
    position:absolute;border-radius:50%;
    background:radial-gradient(circle,rgba(60,15,110,.5),transparent 70%);
    filter:blur(18px);animation:drift 24s linear infinite;
}
.m1{width:420px;height:420px;top:5%;left:-10%}
.m2{width:520px;height:520px;top:18%;right:-15%;animation-duration:27s}
.m3{width:340px;height:340px;bottom:5%;left:20%;animation-duration:35s}
@keyframes drift{
    0%,100%{transform:translate(0,0) scale(1)}
    50%{transform:translate(40px,-30px) scale(1.15)}
}
.container{
    position:relative;z-index:2;
    max-width:780px;margin:auto;padding:24px 16px 45px;
}
h1{
    text-align:center;font-family:Orbitron,monospace;
    font-size:clamp(1.7rem,7vw,2.5rem);
    margin-bottom:7px;
    background:linear-gradient(45deg,var(--cyan),var(--violet),var(--mint));
    background-size:200% 200%;
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    animation:hueflow 6s ease infinite;
}
@keyframes hueflow{
    0%,100%{background-position:0 50%}
    50%{background-position:100% 50%}
}
.subtag{
    text-align:center;color:#c3a8e0;font-size:.78rem;
    letter-spacing:1.5px;text-transform:uppercase;margin-bottom:18px;
}
.page{
    display:none;
    animation:pageIn .35s ease;
}
.page.active{display:block}
@keyframes pageIn{
    from{opacity:0;transform:translateY(10px)}
    to{opacity:1;transform:translateY(0)}
}
.pagedots{
    display:flex;justify-content:center;gap:9px;margin:5px 0 18px;
}
.dot{
    width:9px;height:9px;border-radius:50%;
    background:rgba(255,255,255,.15);
    border:1px solid rgba(139,45,255,.45);
    cursor:pointer;
}
.dot.on{
    background:var(--violet);
    box-shadow:0 0 12px rgba(139,45,255,.8);
    transform:scale(1.2);
}
.card{
    background:rgba(20,10,35,.57);
    backdrop-filter:blur(18px) saturate(140%);
    border:1px solid rgba(139,45,255,.35);
    border-radius:20px;padding:22px;margin-bottom:18px;
    box-shadow:0 20px 40px rgba(0,0,0,.5),0 0 24px rgba(139,45,255,.1);
}
.card:before{
    content:"";display:block;height:3px;margin:-22px -22px 20px;
    background:linear-gradient(90deg,var(--cyan),var(--violet),var(--mint));
    background-size:300% 100%;animation:shimmer 3s infinite;
}
@keyframes shimmer{
    0%,100%{background-position:0 0}
    50%{background-position:100% 0}
}
.head{
    display:flex;align-items:center;gap:10px;margin-bottom:15px;
}
.num{
    font-family:Orbitron,monospace;color:var(--violet);
    border:1px solid rgba(139,45,255,.55);border-radius:8px;padding:3px 10px;
}
.head h2{
    font-size:1rem;letter-spacing:1.3px;text-transform:uppercase;
    color:#e6d6ff;
}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.full{grid-column:1/-1}
label{
    display:block;color:var(--cyan);font-size:.8rem;font-weight:600;
    text-transform:uppercase;letter-spacing:1px;margin-bottom:9px;
}
input{
    width:100%;padding:15px 16px;
    background:rgba(255,255,255,.06);
    border:2px solid rgba(139,45,255,.35);
    border-radius:14px;color:#fff;font-size:15px;
    font-family:Poppins,sans-serif;
}
input:focus{
    outline:none;border-color:var(--cyan);
    box-shadow:0 0 20px rgba(34,211,238,.25);
}
.smallnote{
    color:#bca9d0;font-size:.78rem;line-height:1.55;margin-top:9px;
}
.login-tabs{display:flex;gap:8px;margin-bottom:16px}
.tab{
    flex:1;padding:12px;border:1px solid rgba(139,45,255,.4);
    border-radius:12px;background:rgba(255,255,255,.04);
    color:#d9c8ed;cursor:pointer;font-weight:600;
}
.tab.active{
    background:rgba(139,45,255,.22);
    border-color:var(--violet);color:#fff;
}
.login-method{display:none}
.login-method.active{display:block}
.status-grid{
    display:grid;grid-template-columns:repeat(2,1fr);gap:13px;
}
.status{
    background:rgba(255,255,255,.055);
    border:1px solid rgba(139,45,255,.3);
    border-radius:16px;padding:17px 10px;text-align:center;
}
.status strong{display:block;font-family:Orbitron,monospace;font-size:1.35rem;color:var(--mint);margin-bottom:6px}
.status span{font-size:.82rem;color:#e4d9ec}
.buttons{display:flex;gap:13px;flex-wrap:wrap}
.btn{
    flex:1;min-width:140px;border:0;border-radius:14px;
    padding:16px 20px;color:#fff;font-family:Orbitron,monospace;
    font-weight:700;cursor:pointer;transition:.25s;
}
.btn:hover{transform:translateY(-2px);filter:brightness(1.08)}
.green{background:linear-gradient(135deg,#00ff9d,#00a968);box-shadow:0 0 20px rgba(0,255,157,.3)}
.blue{background:linear-gradient(135deg,var(--cyan),#6d28d9);box-shadow:0 0 20px rgba(139,45,255,.4)}
.red{background:linear-gradient(135deg,#ff4757,#b8001f);box-shadow:0 0 20px rgba(255,71,87,.35)}
.nav{
    display:flex;gap:12px;margin-top:15px;
}
.nav button{
    flex:1;padding:14px;border-radius:13px;
    border:1px solid var(--violet);
    background:rgba(139,45,255,.13);color:#eadcff;
    cursor:pointer;font-weight:700;
}
.logbox{
    height:min(48vh,370px);overflow-y:auto;
    background:rgba(0,0,0,.72);border:1px solid rgba(139,45,255,.45);
    border-radius:14px;padding:17px;
    font-family:"Courier New",monospace;font-size:12.5px;
    line-height:1.65;color:#c9a8ff;
}
.banner{
    padding:15px;border-radius:14px;margin-bottom:15px;
    background:rgba(0,255,157,.08);
    border:1px solid rgba(0,255,157,.3);
    color:#bfffe8;line-height:1.5;
}
@media(max-width:560px){
    .grid{grid-template-columns:1fr}
    .full{grid-column:auto}
    .status-grid{grid-template-columns:1fr 1fr}
    .btn{width:100%}
}
</style>
</head>
<body>
<div class="backdrop">
    <div class="mist m1"></div><div class="mist m2"></div><div class="mist m3"></div>
</div>

<div class="container">
    <h1><i class="fab fa-instagram"></i> GROUP LOCKER</h1>
    <div class="subtag">Instagram account control panel</div>

    <div class="pagedots">
        <span class="dot on" data-page="1"></span>
        <span class="dot" data-page="2"></span>
        <span class="dot" data-page="3"></span>
        <span class="dot" data-page="4"></span>
    </div>

    <!-- PAGE 1 -->
    <section class="page active" data-page="1">
        <div class="head"><span class="num">01</span><h2>Instagram Login</h2></div>

        <div class="card">
            <div class="login-tabs">
                <button class="tab active" onclick="showLogin('token',this)">Session Token</button>
                <button class="tab" onclick="showLogin('password',this)">Username / Password</button>
            </div>

            <div id="tokenMethod" class="login-method active">
                <label>Session Token</label>
                <input id="token" type="password" placeholder="Paste Instagram session ID">
                <div class="smallnote">The token is used only for this running process.</div>
            </div>

            <div id="passwordMethod" class="login-method">
                <div class="grid">
                    <div>
                        <label>Instagram Username</label>
                        <input id="username" type="text" placeholder="@username">
                    </div>
                    <div>
                        <label>Instagram Password</label>
                        <input id="password" type="password" placeholder="Password">
                    </div>
                </div>
            </div>

            <div class="buttons" style="margin-top:18px">
                <button class="btn green" onclick="loginInstagram()">
                    <i class="fas fa-right-to-bracket"></i> LOGIN
                </button>
                <button class="btn red" onclick="logoutInstagram()">
                    <i class="fas fa-right-from-bracket"></i> LOGOUT
                </button>
            </div>

            <div id="loginMsg" class="smallnote"></div>
        </div>

        <div class="card">
            <div class="status-grid">
                <div class="status">
                    <strong id="loginState">OFF</strong>
                    <span>Instagram Login</span>
                </div>
                <div class="status">
                    <strong id="accountName">—</strong>
                    <span>Account</span>
                </div>
            </div>
        </div>

        <div class="nav">
            <button disabled>Previous</button>
            <button onclick="goPage(2)">Next <i class="fas fa-chevron-right"></i></button>
        </div>
    </section>

    <!-- PAGE 2 -->
    <section class="page" data-page="2">
        <div class="head"><span class="num">02</span><h2>Group Configuration</h2></div>

        <div class="card">
            <div class="banner">
                <b>🔒 Group Name Locker</b><br>
                The worker checks the selected group and restores the configured name when it is different.
            </div>

            <div class="grid">
                <div class="full">
                    <label>Group Thread ID</label>
                    <input id="threadId" type="text" placeholder="794932516795889">
                </div>

                <div class="full">
                    <label>Locked Group Name</label>
                    <input id="lockedName" type="text" placeholder="GROUP LOCKED">
                </div>

                <div>
                    <label>Check Interval (seconds)</label>
                    <input id="interval" type="number" min="5" max="3600" value="5">
                </div>

                <div>
                    <label>Current Group Name</label>
                    <input id="currentName" type="text" value="—" readonly>
                </div>
            </div>
        </div>

        <div class="nav">
            <button onclick="goPage(1)"><i class="fas fa-chevron-left"></i> Previous</button>
            <button onclick="goPage(3)">Next <i class="fas fa-chevron-right"></i></button>
        </div>
    </section>

    <!-- PAGE 3 -->
    <section class="page" data-page="3">
        <div class="head"><span class="num">03</span><h2>Controls & Stats</h2></div>

        <div class="card">
            <div class="buttons">
                <button class="btn blue" onclick="startLocker()">
                    <i class="fas fa-lock"></i> START LOCKER
                </button>
                <button class="btn red" onclick="stopLocker()">
                    <i class="fas fa-stop"></i> STOP LOCKER
                </button>
            </div>
        </div>

        <div class="card">
            <div class="status-grid">
                <div class="status"><strong id="runState">STOPPED</strong><span>Status</span></div>
                <div class="status"><strong id="checks">0</strong><span>Checks</span></div>
                <div class="status"><strong id="changes">0</strong><span>Name Restores</span></div>
                <div class="status"><strong id="errors">0</strong><span>Errors</span></div>
                <div class="status"><strong id="uptime">00:00:00</strong><span>Uptime</span></div>
                <div class="status"><strong id="loggedUser">—</strong><span>Logged Account</span></div>
            </div>
        </div>

        <div class="nav">
            <button onclick="goPage(2)"><i class="fas fa-chevron-left"></i> Previous</button>
            <button onclick="goPage(4)">Next <i class="fas fa-chevron-right"></i></button>
        </div>
    </section>

    <!-- PAGE 4 -->
    <section class="page" data-page="4">
        <div class="head"><span class="num">04</span><h2>Live Logs</h2></div>

        <div class="card">
            <div class="logbox" id="logs">Waiting for Instagram login...</div>
        </div>

        <div class="nav">
            <button onclick="goPage(3)"><i class="fas fa-chevron-left"></i> Previous</button>
            <button disabled>Next</button>
        </div>
    </section>
</div>

<script>
let currentPage = 1;

function goPage(page){
    if(page === currentPage) return;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelector('.page[data-page="' + page + '"]').classList.add('active');
    document.querySelectorAll('.dot').forEach(d => d.classList.toggle('on', Number(d.dataset.page) === page));
    currentPage = page;
    window.scrollTo({top:0, behavior:'smooth'});
}

document.querySelectorAll('.dot').forEach(d => {
    d.addEventListener('click', () => goPage(Number(d.dataset.page)));
});

function showLogin(type, button){
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    button.classList.add('active');

    document.querySelectorAll('.login-method').forEach(x => x.classList.remove('active'));
    document.getElementById(type === 'token' ? 'tokenMethod' : 'passwordMethod').classList.add('active');
}

async function loginInstagram(){
    const form = new FormData();
    form.append('token', document.getElementById('token').value.trim());
    form.append('username', document.getElementById('username').value.trim());
    form.append('password', document.getElementById('password').value);

    const res = await fetch('/login', {method:'POST', body:form});
    const data = await res.json();

    document.getElementById('loginMsg').textContent = data.message;

    if(data.ok){
        document.getElementById('loginState').textContent = 'READY';
        document.getElementById('accountName').textContent = '@' + data.username;
        document.getElementById('loggedUser').textContent = '@' + data.username;
    }
}

async function logoutInstagram(){
    const res = await fetch('/logout', {method:'POST'});
    const data = await res.json();

    document.getElementById('loginMsg').textContent = data.message;
    document.getElementById('loginState').textContent = 'OFF';
    document.getElementById('accountName').textContent = '—';
    document.getElementById('loggedUser').textContent = '—';
}

async function startLocker(){
    const form = new FormData();
    form.append('thread_id', document.getElementById('threadId').value.trim());
    form.append('locked_name', document.getElementById('lockedName').value.trim());
    form.append('interval', document.getElementById('interval').value);

    const res = await fetch('/start', {method:'POST', body:form});
    const data = await res.json();
    alert(data.message);
}

async function stopLocker(){
    const res = await fetch('/stop', {method:'POST'});
    const data = await res.json();
    alert(data.message);
}

async function refreshLogs(){
    try{
        const res = await fetch('/logs');
        const data = await res.json();
        const box = document.getElementById('logs');
        box.innerHTML = data.logs.length ? data.logs.join('<br>') : 'No logs yet...';
        box.scrollTop = box.scrollHeight;
    }catch(e){}
}

async function refreshStatus(){
    try{
        const res = await fetch('/status');
        const d = await res.json();

        document.getElementById('runState').textContent = d.running ? 'RUNNING' : 'STOPPED';
        document.getElementById('checks').textContent = d.checks;
        document.getElementById('changes').textContent = d.changes;
        document.getElementById('errors').textContent = d.errors;
        document.getElementById('loggedUser').textContent = d.username ? '@' + d.username : '—';
        document.getElementById('accountName').textContent = d.username ? '@' + d.username : '—';
        document.getElementById('loginState').textContent = d.logged_in ? 'READY' : 'OFF';
        document.getElementById('currentName').value = d.current_name || '—';

        const total = Number(d.uptime || 0);
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        document.getElementById('uptime').textContent =
            String(h).padStart(2,'0') + ':' +
            String(m).padStart(2,'0') + ':' +
            String(s).padStart(2,'0');
    }catch(e){}
}

setInterval(refreshLogs, 2000);
setInterval(refreshStatus, 2000);
refreshLogs();
refreshStatus();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
