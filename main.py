from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import json, os, httpx, hashlib, secrets
from datetime import datetime

app = FastAPI(title="Nexora API", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "en3CLbFqBTLfss7Tr5fn5WCCpxeDpXxw")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "mistral-tiny"
SUPABASE_URL    = os.getenv("SUPABASE_URL", "https://wmhduwjkvbngmawzsgjb.supabase.co")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "https://wmhduwjkvbngmawzsgjb.supabase.co")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "nexora2025")

# ── HELPERS
def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def gen_token() -> str:
    return secrets.token_hex(32)

def gen_client_id() -> str:
    return secrets.token_hex(8)

def supa_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

async def db_insert(table: str, data: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=supa_headers(), json=data)
        return r.json() if r.status_code in [200, 201] else None
    except:
        return None

async def db_select(table: str, filters: str = "", limit: int = 100):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        url = f"{SUPABASE_URL}/rest/v1/{table}?order=created_at.desc&limit={limit}"
        if filters: url += f"&{filters}"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=supa_headers())
        return r.json() if r.status_code == 200 else []
    except:
        return []

async def db_update(table: str, filters: str, data: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                f"{SUPABASE_URL}/rest/v1/{table}?{filters}",
                headers=supa_headers(), json=data
            )
        return r.json() if r.status_code in [200, 201] else None
    except:
        return None

# ── KNOWLEDGE BASE
def load_knowledge(client_id: str = None):
    if client_id:
        path = f"knowledge_{client_id}.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    with open("knowledge.json", "r", encoding="utf-8") as f:
        return json.load(f)

def build_system_prompt(k):
    e = k["entreprise"]
    chambres = "\n".join(f"  - {c['type']} : {c['prix']} — {c['description']}" for c in k.get("chambres", []))
    services = "\n".join(f"  - {s}" for s in k.get("services", []))
    faq      = "\n".join(f"  Q: {f['question']}\n  R: {f['reponse']}" for f in k.get("faq", []))
    return f"""Tu es l assistant IA officiel de {e['nom']}, {e['type']} situe a {e['ville']}.
INFOS: Tel: {e['telephone']} | Email: {e['email']} | {e['horaires']}
{k['description']}
PRODUITS/SERVICES: {chambres}
SERVICES: {services}
FAQ: {faq}
REGLES: Reponds en francais sans Markdown. Max 3 phrases. Si besoin: collecte nom, email, tel.
Redirige vers {e['telephone']} si tu ne sais pas. Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}"""

# Fallback mémoire
_clients, _leads, _convs, _subs, _sessions = [], [], [], [], {}

# ── PAGES
@app.get("/")
def root(): return FileResponse("index.html")
@app.get("/admin")
def admin(): return FileResponse("admin.html")
@app.get("/dashboard")
def dashboard(): return FileResponse("dashboard.html")
@app.get("/login")
def login_page(): return FileResponse("login.html")
@app.get("/register")
def register_page(): return FileResponse("register.html")

@app.get("/health")
def health():
    return {"status": "ok", "version": "4.0.0", "supabase": bool(SUPABASE_URL)}

# ── AUTH CLIENTS
@app.post("/auth/register")
async def register(req: Request):
    """Inscription d'un nouveau client Nexora."""
    b = await req.json()
    email    = b.get("email", "").strip().lower()
    password = b.get("password", "").strip()
    company  = b.get("company", "Mon Entreprise").strip()
    plan     = b.get("plan", "starter")

    if not email or "@" not in email:
        return JSONResponse({"error": "Email invalide"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "Mot de passe trop court (min 6 caractères)"}, status_code=400)

    # Vérifie si email déjà utilisé
    existing = await db_select("clients", f"email=eq.{email}", limit=1)
    if existing:
        return JSONResponse({"error": "Cet email est déjà utilisé"}, status_code=409)

    client_id = gen_client_id()
    token     = gen_token()

    client = {
        "client_id":    client_id,
        "email":        email,
        "password":     hash_password(password),
        "company":      company,
        "plan":         plan,
        "token":        token,
        "assistant_name": f"Assistant {company}",
        "assistant_color": "#00e5ff",
        "welcome_msg":  f"Bonjour ! Je suis l'assistant de {company}. Comment puis-je vous aider ?",
        "created_at":   datetime.now().isoformat()
    }

    saved = await db_insert("clients", client)
    if not saved:
        _clients.append(client)

    # Crée une knowledge base par défaut pour ce client
    default_kb = {
        "entreprise": {
            "nom": company, "type": "Entreprise", "ville": "Libreville, Gabon",
            "telephone": "+241 00 00 00 00", "email": email, "horaires": "Lundi-Vendredi 8h-18h"
        },
        "description": f"Bienvenue chez {company}. Nous sommes à votre service.",
        "chambres": [], "services": ["Service client", "Support technique"],
        "faq": [{"question": "Comment vous contacter ?", "reponse": f"Contactez-nous à {email}"}]
    }
    with open(f"knowledge_{client_id}.json", "w", encoding="utf-8") as f:
        json.dump(default_kb, f, ensure_ascii=False, indent=2)

    return {"status": "success", "token": token, "client_id": client_id, "company": company}


@app.post("/auth/login")
async def login(req: Request):
    """Connexion d'un client existant."""
    b = await req.json()
    email    = b.get("email", "").strip().lower()
    password = b.get("password", "").strip()

    clients = await db_select("clients", f"email=eq.{email}", limit=1)
    if not clients:
        # Fallback mémoire
        clients = [c for c in _clients if c["email"] == email]
    if not clients:
        return JSONResponse({"error": "Email ou mot de passe incorrect"}, status_code=401)

    client = clients[0]
    if client["password"] != hash_password(password):
        return JSONResponse({"error": "Email ou mot de passe incorrect"}, status_code=401)

    # Nouveau token de session
    token = gen_token()
    await db_update("clients", f"email=eq.{email}", {"token": token})

    return {
        "status":    "success",
        "token":     token,
        "client_id": client["client_id"],
        "company":   client["company"],
        "plan":      client["plan"],
        "email":     client["email"]
    }


@app.get("/client/me")
async def get_me(token: str = ""):
    """Retourne les infos du client connecté."""
    if not token:
        return JSONResponse({"error": "Token manquant"}, status_code=401)
    clients = await db_select("clients", f"token=eq.{token}", limit=1)
    if not clients:
        clients = [c for c in _clients if c.get("token") == token]
    if not clients:
        return JSONResponse({"error": "Session expirée"}, status_code=401)
    client = clients[0]
    client.pop("password", None)
    return client


@app.get("/client/stats")
async def client_stats(token: str = ""):
    """Stats du client connecté."""
    me = await get_me(token)
    if isinstance(me, JSONResponse): return me
    cid = me["client_id"]
    leads = await db_select("leads", f"client_id=eq.{cid}")
    convs = await db_select("conversations", f"client_id=eq.{cid}")
    return {
        "leads":         len(leads),
        "conversations": len(convs),
        "leads_data":    leads[:10],
        "convs_data":    convs[:20]
    }


@app.post("/client/update")
async def update_client(req: Request):
    """Met à jour les paramètres de l'assistant du client."""
    b     = await req.json()
    token = b.get("token", "")
    me    = await get_me(token)
    if isinstance(me, JSONResponse): return me

    updates = {}
    for field in ["assistant_name", "assistant_color", "welcome_msg", "company"]:
        if field in b: updates[field] = b[field]

    await db_update("clients", f"token=eq.{token}", updates)
    return {"status": "success", "message": "Paramètres mis à jour !"}


@app.post("/client/knowledge")
async def update_client_knowledge(req: Request):
    """Met à jour la knowledge base du client."""
    b     = await req.json()
    token = b.get("token", "")
    me    = await get_me(token)
    if isinstance(me, JSONResponse): return me

    cid       = me["client_id"]
    knowledge = b.get("knowledge", {})
    with open(f"knowledge_{cid}.json", "w", encoding="utf-8") as f:
        json.dump(knowledge, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": "Assistant mis à jour !"}


@app.get("/client/knowledge")
async def get_client_knowledge(token: str = ""):
    """Retourne la knowledge base du client."""
    me = await get_me(token)
    if isinstance(me, JSONResponse): return me
    return load_knowledge(me["client_id"])


# ── CHAT (avec client_id)
@app.post("/chat")
async def chat(req: Request):
    b         = await req.json()
    message   = b.get("message", "").strip()
    history   = b.get("history", [])
    session   = b.get("session_id", "anon")
    client_id = b.get("client_id", None)

    if not message:
        return JSONResponse({"error": "Message vide"}, status_code=400)

    k    = load_knowledge(client_id)
    msgs = [{"role": "system", "content": build_system_prompt(k)}]
    for m in history[-10:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(MISTRAL_URL,
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
                json={"model": MISTRAL_MODEL, "messages": msgs, "max_tokens": 400, "temperature": 0.7})
        reply = r.json()["choices"][0]["message"]["content"]

        for sender, msg in [("user", message), ("bot", reply)]:
            conv = {"lead_id": None, "client_id": client_id, "message": msg, "sender": sender}
            if not await db_insert("conversations", conv):
                _convs.append({**conv, "created_at": datetime.now().isoformat()})

        return {"reply": reply}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/leads")
async def save_lead(req: Request):
    b     = await req.json()
    lead  = {
        "name":      b.get("name") or b.get("nom", "Inconnu"),
        "email":     b.get("email", ""),
        "phone":     b.get("phone") or b.get("telephone", ""),
        "client_id": b.get("client_id", None)
    }
    if not await db_insert("leads", lead):
        _leads.append({**lead, "created_at": datetime.now().isoformat()})
    return {"status": "success"}


@app.post("/subscribe")
async def subscribe(req: Request):
    b     = await req.json()
    email = b.get("email", "").strip()
    if not email or "@" not in email:
        return JSONResponse({"error": "Email invalide"}, status_code=400)
    if not await db_insert("subscribers", {"email": email}):
        _subs.append({"email": email})
    return {"status": "success", "redirect": "/register"}


# ── ADMIN
@app.post("/admin/login")
async def admin_login(req: Request):
    b = await req.json()
    if b.get("password") == ADMIN_PASSWORD:
        return {"status": "ok", "token": "nexora-admin-2025"}
    return JSONResponse({"error": "Mot de passe incorrect"}, status_code=401)

@app.get("/admin/data")
async def admin_data(token: str = ""):
    if token != "nexora-admin-2025":
        return JSONResponse({"error": "Non autorisé"}, status_code=401)
    leads   = await db_select("leads", limit=50)   or _leads
    convs   = await db_select("conversations", limit=50) or _convs
    subs    = await db_select("subscribers", limit=50)   or _subs
    clients = await db_select("clients", limit=50)       or _clients
    today   = datetime.now().strftime('%Y-%m-%d')
    return {
        "stats": {
            "leads": len(leads), "conversations": len(convs),
            "subscribers": len(subs), "clients": len(clients),
            "messages_today": len([c for c in convs if c.get("created_at","").startswith(today)])
        },
        "leads": leads[:20], "conversations": convs[:30],
        "subscribers": subs[:20],
        "clients": [{k:v for k,v in c.items() if k != "password"} for c in clients[:20]]
    }
