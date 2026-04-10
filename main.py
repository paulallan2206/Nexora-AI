from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import json, os, httpx
from datetime import datetime

app = FastAPI(title="Nexora API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "mistral-tiny"
SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "nexora2025")

# ── KNOWLEDGE BASE (fichier par défaut + override par client)
def load_knowledge(client_id: str = None):
    # Si un client_id est fourni et qu'un fichier spécifique existe
    if client_id:
        path = f"knowledge_{client_id}.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    # Fallback: knowledge.json par défaut
    with open("knowledge.json", "r", encoding="utf-8") as f:
        return json.load(f)

def build_system_prompt(k):
    e = k["entreprise"]
    chambres = "\n".join(f"  - {c['type']} : {c['prix']} — {c['description']}" for c in k.get("chambres", []))
    services = "\n".join(f"  - {s}" for s in k.get("services", []))
    faq      = "\n".join(f"  Q: {f['question']}\n  R: {f['reponse']}" for f in k.get("faq", []))
    return f"""Tu es l assistant IA officiel de {e['nom']}, {e['type']} situe a {e['ville']}.
INFOS: Tel: {e['telephone']} | Email: {e['email']} | Horaires: {e['horaires']}
{k['description']}
CHAMBRES/PRODUITS: {chambres}
SERVICES: {services}
FAQ: {faq}
REGLES ABSOLUES:
- Reponds UNIQUEMENT en francais, sans aucun Markdown (pas de **, pas de #, pas de tirets listes).
- Sois chaleureux et concis (max 3 phrases par reponse).
- Si reservation/commande: collecte nom, email, telephone, details.
- Si tu ne sais pas: redirige vers {e['telephone']}.
- Ne parle jamais d autres entreprises ou concurrents.
- Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}"""

# ── SUPABASE HELPERS
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

async def db_select(table: str, limit=100, filters: str = ""):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        url = f"{SUPABASE_URL}/rest/v1/{table}?order=created_at.desc&limit={limit}"
        if filters:
            url += f"&{filters}"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=supa_headers())
        return r.json() if r.status_code == 200 else []
    except:
        return []

async def db_count(table: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{SUPABASE_URL}/rest/v1/{table}?select=id",
                headers={**supa_headers(), "Prefer": "count=exact"}
            )
        count_header = r.headers.get("content-range", "0")
        return int(count_header.split("/")[-1]) if "/" in count_header else 0
    except:
        return 0

# Fallback mémoire
_leads, _convs, _subs = [], [], []

# ── ROUTES PUBLIQUES
@app.get("/")
def root(): return FileResponse("index.html")

@app.get("/admin")
def admin(): return FileResponse("admin.html")

@app.get("/config")
def config_page(): return FileResponse("config.html")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "supabase": bool(SUPABASE_URL),
        "mistral": bool(MISTRAL_API_KEY)
    }

@app.post("/chat")
async def chat(req: Request):
    body      = await req.json()
    message   = body.get("message", "").strip()
    history   = body.get("history", [])
    session   = body.get("session_id", "anon")
    client_id = body.get("client_id", None)

    if not message:
        return JSONResponse({"error": "Message vide"}, status_code=400)

    k    = load_knowledge(client_id)
    msgs = [{"role": "system", "content": build_system_prompt(k)}]
    for m in history[-10:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                MISTRAL_URL,
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
                json={"model": MISTRAL_MODEL, "messages": msgs, "max_tokens": 400, "temperature": 0.7}
            )
        reply = r.json()["choices"][0]["message"]["content"]

        # Sauvegarde dans Supabase
        for sender, msg in [("user", message), ("bot", reply)]:
            conv = {"lead_id": None, "message": msg, "sender": sender}
            if not await db_insert("conversations", conv):
                _convs.append({**conv, "created_at": datetime.now().isoformat()})

        return {"reply": reply, "session_id": session}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/leads")
async def save_lead(req: Request):
    b     = await req.json()
    name  = b.get("name") or b.get("nom", "Inconnu")
    email = b.get("email", "")
    phone = b.get("phone") or b.get("telephone", "")

    lead = {"name": name, "email": email, "phone": phone}
    saved = await db_insert("leads", lead)
    if not saved:
        _leads.append({**lead, "created_at": datetime.now().isoformat()})

    if b.get("message"):
        await db_insert("conversations", {"lead_id": None, "message": b.get("message"), "sender": "user"})

    return {"status": "success", "message": f"Lead {name} enregistré !"}


@app.post("/subscribe")
async def subscribe(req: Request):
    b     = await req.json()
    email = b.get("email", "").strip()
    if not email or "@" not in email:
        return JSONResponse({"error": "Email invalide"}, status_code=400)
    saved = await db_insert("subscribers", {"email": email})
    if not saved:
        _subs.append({"email": email, "subscribed_at": datetime.now().isoformat()})
    return {"status": "success", "message": "Inscription enregistrée !"}


# ── KNOWLEDGE BASE CONFIG (pour page /config)
@app.get("/knowledge")
def get_knowledge(client_id: str = None):
    try:
        k = load_knowledge(client_id)
        return k
    except:
        return JSONResponse({"error": "Knowledge base introuvable"}, status_code=404)

@app.post("/knowledge")
async def update_knowledge(req: Request):
    """Met à jour la knowledge base via le formulaire de config."""
    b = await req.json()
    token = b.get("token", "")
    if token != "nexora-admin-2025":
        return JSONResponse({"error": "Non autorisé"}, status_code=401)

    client_id = b.get("client_id", "default")
    knowledge = b.get("knowledge", {})
    filename  = "knowledge.json" if client_id == "default" else f"knowledge_{client_id}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, ensure_ascii=False, indent=2)

    return {"status": "success", "message": f"Knowledge base '{client_id}' mise à jour !"}


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

    leads = await db_select("leads", 50)         or _leads
    convs = await db_select("conversations", 50) or _convs
    subs  = await db_select("subscribers", 50)   or _subs

    # Stats par jour (7 derniers jours)
    today = datetime.now().strftime('%Y-%m-%d')

    return {
        "stats": {
            "leads":         len(leads),
            "conversations": len(convs),
            "subscribers":   len(subs),
            "messages_today": len([c for c in convs if c.get("created_at","").startswith(today)])
        },
        "leads":         leads[:20],
        "conversations": convs[:30],
        "subscribers":   subs[:20]
    }
