"""HTTP status server per il gateway nanobot.

Espone:
  GET  /health      — health check (no auth)
  GET  /login       — pagina di login
  POST /login       — verifica credenziali, imposta cookie di sessione
  POST /logout      — cancella la sessione
  GET  /api/status  — JSON stato completo (richiede sessione)
  GET  /dashboard   — HTML dashboard (richiede sessione)
  GET  /            — redirect a /dashboard

Credenziali configurate via env var (nessuna registrazione):
  NANOBOT_DASHBOARD_EMAIL    — email dell'unico utente
  NANOBOT_DASHBOARD_PASSWORD — password in chiaro (viene hashata in memoria)

Sessioni: token casuali 32-byte in un dizionario in-memory, TTL 12 ore.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from loguru import logger

# ──────────────────────────────────────────────────────────────────────────────
# Sessioni in-memory
# ──────────────────────────────────────────────────────────────────────────────

_SESSION_TTL = 12 * 3600  # 12 ore
_sessions: dict[str, float] = {}  # token → expiry timestamp
_COOKIE = "nb_session"


def _new_session() -> str:
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + _SESSION_TTL
    return token


def _valid_session(token: str | None) -> bool:
    if not token:
        return False
    exp = _sessions.get(token)
    if exp is None:
        return False
    if time.time() > exp:
        _sessions.pop(token, None)
        return False
    return True


def _revoke_session(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)


def _get_session_token(request: web.Request) -> str | None:
    return request.cookies.get(_COOKIE)


def _is_authenticated(request: web.Request) -> bool:
    return _valid_session(_get_session_token(request))


def _check_credentials(request: web.Request, email: str, password: str) -> bool:
    expected_email: str = request.app["auth_email"]
    expected_hash: bytes = request.app["auth_password_hash"]
    candidate_hash = hashlib.sha256(password.encode()).digest()
    email_ok = hmac.compare_digest(email.lower().strip(), expected_email)
    pass_ok = hmac.compare_digest(candidate_hash, expected_hash)
    return email_ok and pass_ok


# ──────────────────────────────────────────────────────────────────────────────
# Log capture
# ──────────────────────────────────────────────────────────────────────────────

_RECENT_LOG_LINES: list[dict] = []
_MAX_LOG_BUFFER = 100


class _LogSink:
    def write(self, message):
        record = message.record
        lvl = record["level"].name
        if lvl in ("ERROR", "WARNING", "CRITICAL"):
            _RECENT_LOG_LINES.append({
                "time": record["time"].strftime("%H:%M:%S"),
                "level": lvl,
                "module": record["name"],
                "message": record["message"],
            })
            if len(_RECENT_LOG_LINES) > _MAX_LOG_BUFFER:
                _RECENT_LOG_LINES.pop(0)


def install_log_sink():
    logger.add(_LogSink(), format="{message}", level="WARNING")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers dati
# ──────────────────────────────────────────────────────────────────────────────

def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ms_to_relative(ms: int | None) -> str:
    if ms is None:
        return "—"
    now = time.time() * 1000
    diff = ms - now
    abs_diff = abs(diff)
    if abs_diff < 60_000:
        label = f"{int(abs_diff/1000)}s"
    elif abs_diff < 3_600_000:
        label = f"{int(abs_diff/60_000)}m"
    elif abs_diff < 86_400_000:
        label = f"{int(abs_diff/3_600_000)}h"
    else:
        label = f"{int(abs_diff/86_400_000)}d"
    return f"tra {label}" if diff > 0 else f"{label} fa"


def _read_sessions(workspace: Path) -> list[dict]:
    sessions_dir = workspace / "sessions"
    if not sessions_dir.exists():
        return []
    result = []
    for p in sorted(sessions_dir.glob("*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").strip().splitlines()
            size_kb = round(p.stat().st_size / 1024, 1)
            last_ts = None
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                    ts = obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
                    if ts:
                        last_ts = str(ts)
                        break
                except Exception:
                    continue
            result.append({
                "name": p.stem,
                "messages": len(lines),
                "last_activity": last_ts or "—",
                "size_kb": size_kb,
            })
        except Exception:
            pass
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Route handlers
# ──────────────────────────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "nanobot-gateway"})


async def handle_login_get(request: web.Request) -> web.Response:
    if _is_authenticated(request):
        raise web.HTTPFound("/dashboard")
    error = request.rel_url.query.get("error", "")
    return web.Response(text=_build_login_html(error), content_type="text/html", charset="utf-8")


async def handle_login_post(request: web.Request) -> web.Response:
    try:
        data = await request.post()
        email = str(data.get("email", ""))
        password = str(data.get("password", ""))
    except Exception:
        raise web.HTTPFound("/login?error=bad_request")

    if not _check_credentials(request, email, password):
        logger.warning("Dashboard: login fallito per email={}", email[:40])
        raise web.HTTPFound("/login?error=invalid")

    token = _new_session()
    logger.info("Dashboard: login riuscito per {}", email[:40])
    response = web.HTTPFound("/dashboard")
    response.set_cookie(
        _COOKIE, token,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="Lax",
        secure=True,
    )
    return response


async def handle_logout(request: web.Request) -> web.Response:
    token = _get_session_token(request)
    _revoke_session(token)
    response = web.HTTPFound("/login")
    response.del_cookie(_COOKIE)
    return response


async def handle_dashboard(request: web.Request) -> web.Response:
    if not _is_authenticated(request):
        raise web.HTTPFound("/login")
    return web.Response(text=_build_dashboard_html(), content_type="text/html", charset="utf-8")


async def handle_status(request: web.Request) -> web.Response:
    if not _is_authenticated(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    cron_service = request.app["cron_service"]
    workspace: Path = request.app["workspace"]

    raw_jobs = cron_service.list_jobs(include_disabled=True)
    jobs = []
    for j in raw_jobs:
        history = j.state.run_history[-5:]
        jobs.append({
            "id": j.id,
            "name": j.name,
            "enabled": j.enabled,
            "kind": j.schedule.kind,
            "expr": j.schedule.expr or j.schedule.every_ms or j.schedule.at_ms,
            "tz": j.schedule.tz,
            "next_run_iso": _ms_to_iso(j.state.next_run_at_ms),
            "next_run_rel": _ms_to_relative(j.state.next_run_at_ms),
            "last_run_iso": _ms_to_iso(j.state.last_run_at_ms),
            "last_run_rel": _ms_to_relative(j.state.last_run_at_ms),
            "last_status": j.state.last_status,
            "last_error": j.state.last_error,
            "run_count": len(j.state.run_history),
            "error_count": sum(1 for r in j.state.run_history if r.status == "error"),
            "history": [
                {
                    "run_at": _ms_to_iso(r.run_at_ms),
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
                for r in history
            ],
        })

    agent_sessions = _read_sessions(workspace)
    errors = list(_RECENT_LOG_LINES[-30:])

    data = {
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "api": {"ok": True, "status": "running"},
        "jobs": jobs,
        "sessions": agent_sessions,
        "recent_errors": errors,
        "summary": {
            "total_jobs": len(jobs),
            "enabled_jobs": sum(1 for j in jobs if j["enabled"]),
            "total_errors": sum(j["error_count"] for j in jobs),
            "jobs_with_errors": sum(1 for j in jobs if j["last_status"] == "error"),
            "total_sessions": len(agent_sessions),
            "total_messages": sum(s["messages"] for s in agent_sessions),
        },
    }
    return web.json_response(data)


async def handle_root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/dashboard")


# ──────────────────────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────────────────────

def create_status_app(
    cron_service,
    workspace: Path,
    auth_email: str,
    auth_password: str,
    dashboard_token: str | None = None,  # ignorato, tenuto per compatibilità
) -> web.Application:
    app = web.Application()
    app["cron_service"] = cron_service
    app["workspace"] = workspace
    app["auth_email"] = auth_email.lower().strip()
    app["auth_password_hash"] = hashlib.sha256(auth_password.encode()).digest()

    app.router.add_get("/health", handle_health)
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_post("/logout", handle_logout)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/", handle_root)
    return app


# ──────────────────────────────────────────────────────────────────────────────
# HTML — Login
# ──────────────────────────────────────────────────────────────────────────────

def _build_login_html(error: str = "") -> str:
    error_html = ""
    if error == "invalid":
        error_html = '<p class="err-msg">Email o password non corretti.</p>'
    elif error:
        error_html = '<p class="err-msg">Errore di accesso. Riprova.</p>'

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nanobot — Accesso</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:'SF Mono','Cascadia Code',monospace;
        display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .box{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px 36px;width:340px}}
  .logo{{font-size:22px;font-weight:700;margin-bottom:8px}}
  .sub{{color:#8b949e;font-size:12px;margin-bottom:28px}}
  label{{display:block;font-size:11px;color:#8b949e;text-transform:uppercase;
         letter-spacing:.05em;margin-bottom:6px}}
  input{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
         color:#e6edf3;font-family:inherit;font-size:13px;padding:10px 12px;
         margin-bottom:16px;outline:none}}
  input:focus{{border-color:#58a6ff}}
  button{{width:100%;background:#238636;border:none;border-radius:6px;color:#fff;
          cursor:pointer;font-family:inherit;font-size:13px;font-weight:600;
          padding:11px;margin-top:4px}}
  button:hover{{background:#2ea043}}
  .err-msg{{color:#f85149;font-size:12px;margin-bottom:16px;
            background:rgba(248,81,73,.1);border-radius:6px;padding:10px 12px}}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🤖 Nanobot</div>
  <div class="sub">Dashboard di controllo</div>
  {error_html}
  <form method="post" action="/login">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" autocomplete="email" required autofocus>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" autocomplete="current-password" required>
    <button type="submit">Accedi</button>
  </form>
</div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
# HTML — Dashboard
# ──────────────────────────────────────────────────────────────────────────────

def _build_dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nanobot Dashboard</title>
<style>
  :root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#e6edf3;
        --muted:#8b949e;--green:#3fb950;--yellow:#d29922;--red:#f85149;--orange:#d18616}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'SF Mono','Cascadia Code',monospace;font-size:13px}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;
         display:flex;align-items:center;gap:16px}
  header h1{font-size:16px;font-weight:600}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
  .dot.dead{background:var(--red);animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .ts{color:var(--muted);font-size:11px;margin-left:auto}
  .logout{color:var(--muted);font-size:11px;text-decoration:none;margin-left:16px;
          border:1px solid var(--border);border-radius:4px;padding:4px 10px}
  .logout:hover{color:var(--text);border-color:var(--muted)}
  main{padding:20px 24px;display:grid;gap:20px}
  .row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
  .card-title{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
  .card-value{font-size:28px;font-weight:700}
  .ok{color:var(--green)}.warn{color:var(--yellow)}.err{color:var(--red)}
  .section{background:var(--surface);border:1px solid var(--border);border-radius:8px}
  .section-header{padding:12px 16px;border-bottom:1px solid var(--border);font-weight:600;font-size:13px}
  table{width:100%;border-collapse:collapse}
  th{padding:8px 16px;text-align:left;color:var(--muted);font-size:11px;
     text-transform:uppercase;border-bottom:1px solid var(--border)}
  td{padding:10px 16px;border-bottom:1px solid #21262d;vertical-align:top}
  tr:last-child td{border-bottom:none}
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
  .b-ok{background:rgba(63,185,80,.15);color:var(--green)}
  .b-err{background:rgba(248,81,73,.15);color:var(--red)}
  .b-idle{background:rgba(139,148,158,.1);color:var(--muted)}
  .b-off{background:rgba(139,148,158,.1);color:var(--muted)}
  .hdot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:2px}
  .h-ok{background:var(--green)}.h-err{background:var(--red)}.h-skip{background:var(--muted)}
  .err-line{font-size:11px;padding:6px 16px;border-bottom:1px solid #21262d;word-break:break-all}
  .err-line.WARNING{color:var(--yellow)}.err-line.ERROR,.err-line.CRITICAL{color:var(--red)}
  .err-src{color:var(--orange);margin-right:6px}
  .err-time{color:var(--muted);margin-right:6px}
  .empty{padding:20px 16px;color:var(--muted);text-align:center;font-size:12px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:700px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div class="dot" id="live-dot"></div>
  <h1>🤖 Nanobot Gateway</h1>
  <span class="ts" id="last-update">—</span>
  <form method="post" action="/logout" style="margin:0">
    <button type="submit" class="logout" style="cursor:pointer;background:none">Esci</button>
  </form>
</header>
<main>
  <div class="row" id="summary-row"></div>
  <div class="section">
    <div class="section-header">⏱ Cron Jobs</div>
    <div id="jobs-body"></div>
  </div>
  <div class="grid2">
    <div class="section">
      <div class="section-header">💬 Sessioni</div>
      <div id="sessions-body"></div>
    </div>
    <div class="section">
      <div class="section-header">🚨 Log Errori</div>
      <div id="errors-body"></div>
    </div>
  </div>
</main>
<script>
function badge(s){
  if(!s)return'<span class="badge b-idle">mai eseguito</span>';
  if(s==='ok')return'<span class="badge b-ok">ok</span>';
  if(s==='error')return'<span class="badge b-err">errore</span>';
  return'<span class="badge b-idle">'+s+'</span>';
}
function hdots(h){
  return(h||[]).map(r=>{
    const c=r.status==='ok'?'h-ok':r.status==='error'?'h-err':'h-skip';
    return'<span class="hdot '+c+'" title="'+r.run_at+(r.error?' — '+r.error:'')+'"></span>';
  }).join('');
}
function render(d){
  document.getElementById('live-dot').className='dot'+(d.api.ok?'':' dead');
  document.getElementById('last-update').textContent=d.timestamp;
  const s=d.summary;
  document.getElementById('summary-row').innerHTML=[
    {l:'Jobs attivi',v:s.enabled_jobs+' / '+s.total_jobs,c:'ok'},
    {l:'Errori jobs',v:s.total_errors,c:s.total_errors>0?'err':'ok'},
    {l:'Sessioni',v:s.total_sessions,c:'ok'},
    {l:'Messaggi totali',v:s.total_messages,c:'ok'},
    {l:'Log warnings',v:d.recent_errors.length,c:d.recent_errors.length>0?'warn':'ok'},
  ].map(c=>'<div class="card"><div class="card-title">'+c.l+'</div><div class="card-value '+c.c+'">'+c.v+'</div></div>').join('');

  const jb=document.getElementById('jobs-body');
  if(!d.jobs.length){jb.innerHTML='<div class="empty">Nessun job</div>';}
  else{
    jb.innerHTML='<table><thead><tr><th>Nome</th><th>Stato</th><th>Schedule</th><th>Prossima run</th><th>Ultima run</th><th>Cronologia</th></tr></thead><tbody>'+
    d.jobs.map(j=>{
      const dis=j.enabled?'':'<span class="badge b-off" style="margin-left:6px">off</span>';
      const sch=j.kind==='cron'?(j.expr+(j.tz?' '+j.tz:''))
               :j.kind==='every'?'ogni '+Math.round(j.expr/60000)+'m'
               :j.kind==='at'?'una volta':j.kind||'—';
      const err=j.last_error?'<br><span style="color:var(--red);font-size:10px">'+j.last_error.substring(0,80)+'</span>':'';
      return'<tr>'+
        '<td><strong>'+j.name+'</strong>'+dis+'</td>'+
        '<td>'+badge(j.last_status)+err+'</td>'+
        '<td style="color:var(--muted)">'+sch+'</td>'+
        '<td>'+j.next_run_rel+'<br><span style="color:var(--muted);font-size:10px">'+(j.next_run_iso||'')+'</span></td>'+
        '<td>'+j.last_run_rel+'<br><span style="color:var(--muted);font-size:10px">'+(j.last_run_iso||'')+'</span></td>'+
        '<td>'+hdots(j.history)+(j.run_count?'<span style="color:var(--muted);font-size:10px;margin-left:4px">'+j.run_count+' run</span>':'')+'</td>'+
      '</tr>';
    }).join('')+'</tbody></table>';
  }

  const sb=document.getElementById('sessions-body');
  if(!d.sessions.length){sb.innerHTML='<div class="empty">Nessuna sessione</div>';}
  else{
    sb.innerHTML='<table><thead><tr><th>Sessione</th><th>Msg</th><th>Ultima attività</th></tr></thead><tbody>'+
    d.sessions.map(s=>'<tr><td>'+s.name+'</td><td>'+s.messages+'</td><td style="color:var(--muted);font-size:11px">'+s.last_activity+'</td></tr>').join('')+'</tbody></table>';
  }

  const eb=document.getElementById('errors-body');
  if(!d.recent_errors.length){eb.innerHTML='<div class="empty ok">✓ Nessun errore recente</div>';}
  else{
    eb.innerHTML=d.recent_errors.slice().reverse().slice(0,25).map(e=>
      '<div class="err-line '+e.level+'"><span class="err-time">'+e.time+'</span><span class="err-src">'+e.module+'</span>'+e.message.substring(0,150)+'</div>'
    ).join('');
  }
}
async function refresh(){
  const r=await fetch('/api/status');
  if(r.status===401){window.location='/login';return;}
  if(r.ok){const d=await r.json();render(d);}
  else{document.getElementById('live-dot').className='dot dead';}
}
refresh();setInterval(refresh,5000);
</script>
</body>
</html>"""
