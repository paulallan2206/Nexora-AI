from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import json, os, httpx
from datetime import datetime

app = FastAPI(title="Nexora API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "mistral-tiny"

def load_knowledge():
    with open("knowledge.json", "r", encoding="utf-8") as f:
        return json.load(f)

def build_system_prompt(k):
    e = k["entreprise"]
    chambres  = "\n".join(f"  - {c['type']} : {c['prix']} — {c['description']}" for c in k.get("chambres", []))
    services  = "\n".join(f"  - {s}" for s in k.get("services", []))
    faq       = "\n".join(f"  Q: {f['question']}\n  R: {f['reponse']}" for f in k.get("faq", []))
    return f"""Tu es l'assistant IA officiel de {e['nom']}, {e['type']} situé à {e['ville']}.

INFOS :
- Téléphone : {e['telephone']} | Email : {e['email']} | Horaires : {e['horaires']}
- {k['description']}

CHAMBRES :
{chambres}

SERVICES :
{services}

FAQ :
{faq}

RÈGLES :
- Réponds en français, chaleureusement et de façon concise (max 3 phrases).
- Si le client veut réserver, collecte : nom, email, téléphone, dates, type de chambre.
- Si tu ne sais pas, redirige vers {e['telephone']}.
- Date actuelle : {datetime.now().strftime('%d/%m/%Y à %H:%M')}
"""

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Message]] = []

class LeadData(BaseModel):
    nom: str
    email: str
    telephone: Optional[str] = ""
    message: Optional[str] = ""

leads_store = []

@app.get("/")
def root():
    return FileResponse("index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat(req: ChatRequest):
    knowledge = load_knowledge()
    messages = [{"role": "system", "content": build_system_prompt(knowledge)}]
    for m in (req.history or [])[-10:]:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            MISTRAL_URL,
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
            json={"model": MISTRAL_MODEL, "messages": messages, "max_tokens": 400, "temperature": 0.7}
        )
    data = resp.json()
    reply = data["choices"][0]["message"]["content"]
    return {"reply": reply}

@app.post("/leads")
def save_lead(lead: LeadData):
    entry = {**lead.dict(), "timestamp": datetime.now().isoformat()}
    leads_store.append(entry)
    return {"status": "success", "message": f"Lead {lead.nom} enregistré !"}

@app.get("/leads")
def get_leads():
    return {"total": len(leads_store), "leads": leads_store}
