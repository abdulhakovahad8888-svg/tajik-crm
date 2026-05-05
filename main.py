"""
TAJIK AI CRM — Сервер с Groq AI
Telegram бот + Instagram webhook
"""

import os, json
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from supabase import create_client, Client

load_dotenv()

app = FastAPI(title="Tajik AI CRM")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID")
BUSINESS_ID     = os.getenv("BUSINESS_ID")
GROQ_KEY        = os.getenv("GROQ_API_KEY")
TG_API          = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """Ты живой AI-менеджер по имени Алӣ для Avicena Life в Таджикистане.
Ты ведёшь настоящий живой разговор — слушаешь клиента и отвечаешь именно на его слова.
Никогда не повторяй один и тот же ответ дважды.

ТОВАРЫ:
- DiaNova — 280 сомони (витамины для женщин)
- Maximus Complex — 450 сомони (для мужчин)
- Testofertil — 520 сомони (репродуктивное здоровье)
- Консультация — 200 сомони
- Avicena Plus — 380 сомони (иммунитет)

Доставка Душанбе — бесплатно от 300 с. Регионы — 30 с.

ПОВЕДЕНИЕ:
- Отвечай коротко (1-3 предложения), живо, на языке клиента (таджикский/русский/узбекский)
- Если спрашивают "шумо ки?" или "кто ты?" — скажи "Ман Алӣ — AI-ёрдамчии Avicena Life"
- Если "намехом" или "не хочу" — спроси почему или предложи другой товар
- Если спрашивает цену — назови цену и спроси хочет ли заказать
- Если хочет купить — попроси имя и телефон
- Если даёт телефон — поблагодари, скажи менеджер перезвонит через 15 минут
- НЕ ставь диагнозы и медицинские советы
- Используй эмодзи умеренно"""

# ════════════════════════════════════════════
# GROQ — ЖИВОЙ AI ОТВЕТ
# ════════════════════════════════════════════

async def get_ai_response(user_message: str, history: list) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history[-10:]:
        role = "assistant" if m["role"] == "bot" else "user"
        messages.append({"role": role, "content": m["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={"model": "llama-3.1-8b-instant", "messages": messages, "max_tokens": 200, "temperature": 0.8}
            )
            data = r.json()
            print(f"Groq raw: {json.dumps(data)[:300]}")
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq error: {e}")
        return "Салом! Ман Алӣ аз Avicena Life. Чӣ тавр кӯмак кунам? 😊"


async def extract_lead_info(messages: list) -> dict:
    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": f"""
Извлеки из диалога данные клиента. Ответь ТОЛЬКО JSON без markdown:
{{\"name\": \"имя или null\", \"phone\": \"телефон или null\", \"product\": \"товар или null\", \"city\": \"город или null\"}}

Диалог:
{conversation}"""}],
                    "max_tokens": 150, "temperature": 0
                }
            )
            text = r.json()["choices"][0]["message"]["content"].strip()
            start, end = text.find('{'), text.rfind('}') + 1
            return json.loads(text[start:end]) if start >= 0 else {}
    except:
        return {}


# ════════════════════════════════════════════
# SUPABASE — ДАННЫЕ
# ════════════════════════════════════════════

def get_lead(channel_user_id: str, channel: str):
    r = supabase.table("leads").select("*")\
        .eq("channel_user_id", channel_user_id)\
        .eq("channel", channel)\
        .not_.in_("status", ["done", "lost"])\
        .order("created_at", desc=True).limit(1).execute()
    return r.data[0] if r.data else None

def create_lead(data: dict):
    r = supabase.table("leads").insert({
        "business_id": BUSINESS_ID,
        "name": data.get("name", "Клиент"),
        "phone": data.get("phone"),
        "product": data.get("product"),
        "channel": data.get("channel"),
        "channel_user_id": data.get("channel_user_id"),
        "status": "new",
        "city": data.get("city", "Душанбе"),
        "amount": data.get("amount", 0),
    }).execute()
    return r.data[0] if r.data else {}

def update_lead(lead_id: str, data: dict):
    supabase.table("leads").update(data).eq("id", lead_id).execute()

def save_message(lead_id: str, role: str, content: str, channel: str):
    supabase.table("messages").insert({
        "lead_id": lead_id, "business_id": BUSINESS_ID,
        "role": role, "content": content, "channel": channel,
    }).execute()

def get_history(lead_id: str) -> list:
    r = supabase.table("messages").select("role,content")\
        .eq("lead_id", lead_id).order("created_at").limit(20).execute()
    return r.data or []


# ════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════

async def tg_send(chat_id: str, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        })

async def notify_manager(lead: dict, channel: str):
    emoji = {"telegram": "✈️", "instagram": "📸", "whatsapp": "💬"}.get(channel, "💬")
    await tg_send(MANAGER_CHAT_ID, f"""🔥 <b>Новый лид!</b>

{emoji} <b>{channel.capitalize()}</b>
👤 {lead.get('name','—')}
📞 {lead.get('phone','Не указан')}
📦 {lead.get('product','—')}
📍 {lead.get('city','Душанбе')}

🤖 <i>{lead.get('ai_summary','Клиент готов к покупке')}</i>""")

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, bg: BackgroundTasks):
    data = await request.json()
    bg.add_task(process_telegram, data)
    return {"ok": True}

async def process_telegram(data: dict):
    msg = data.get("message") or data.get("edited_message")
    if not msg: return

    chat_id    = str(msg["chat"]["id"])
    text       = msg.get("text", "")
    first_name = msg["from"].get("first_name", "Клиент")
    username   = msg["from"].get("username", "")

    if not text or text == "/start":
        await tg_send(chat_id, f"""Салом, {first_name}! 👋

Хуш омадед ба <b>Avicena Life</b> 🌿

Маҳсулоти мо:
• DiaNova — 280 сомонӣ
• Maximus Complex — 450 сомонӣ
• Testofertil — 520 сомонӣ

Ман Алӣ — AI-ёрдамчии шумо. Чӣ тавр ёрӣ расонам? 😊""")
        return

    lead = get_lead(chat_id, "telegram")
    if not lead:
        lead = create_lead({
            "name": f"{first_name} @{username}" if username else first_name,
            "channel": "telegram", "channel_user_id": chat_id,
        })

    lead_id = lead["id"]
    save_message(lead_id, "user", text, "telegram")
    history = get_history(lead_id)

    bot_reply = await get_ai_response(text, history)
    save_message(lead_id, "bot", bot_reply, "telegram")
    await tg_send(chat_id, bot_reply)

    if len(history) >= 2:
        info = await extract_lead_info(history)
        update = {}
        if info.get("name"): update["name"] = info["name"]
        if info.get("product"): update["product"] = info["product"]
        if info.get("city"): update["city"] = info["city"]

        if info.get("phone") and not lead.get("phone"):
            update["phone"]      = info["phone"]
            update["status"]     = "work"
            update["ai_summary"] = f"Клиент интересуется {info.get('product','товаром')}. Телефон получен."
            update_lead(lead_id, update)
            lead.update(update)
            await notify_manager(lead, "telegram")
        elif update:
            update_lead(lead_id, update)


# ════════════════════════════════════════════
# INSTAGRAM
# ════════════════════════════════════════════

@app.get("/webhook/instagram")
async def ig_verify(request: Request):
    p = dict(request.query_params)
    if p.get("hub.verify_token") == os.getenv("INSTAGRAM_VERIFY_TOKEN", "tajik_crm_secret_2025"):
        return PlainTextResponse(p.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook/instagram")
async def ig_webhook(request: Request, bg: BackgroundTasks):
    data = await request.json()
    bg.add_task(process_instagram, data)
    return {"ok": True}

async def process_instagram(data: dict):
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event["sender"]["id"]
            if sender_id == event["recipient"]["id"]: continue
            user_text = event.get("message", {}).get("text", "")
            if not user_text: continue

            name = await get_ig_name(sender_id)
            lead = get_lead(sender_id, "instagram")
            if not lead:
                lead = create_lead({"name": name, "channel": "instagram", "channel_user_id": sender_id})

            lead_id = lead["id"]
            save_message(lead_id, "user", user_text, "instagram")
            history = get_history(lead_id)
            reply = await get_ai_response(user_text, history)
            save_message(lead_id, "bot", reply, "instagram")
            await send_ig_message(sender_id, reply)

            if len(history) >= 2:
                info = await extract_lead_info(history)
                if info.get("phone") and not lead.get("phone"):
                    update_lead(lead_id, {
                        "phone": info["phone"], "status": "work",
                        "product": info.get("product"),
                        "ai_summary": f"Instagram лид. Интерес: {info.get('product','—')}"
                    })
                    lead["phone"] = info["phone"]
                    await notify_manager(lead, "instagram")

async def get_ig_name(user_id: str) -> str:
    try:
        token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://graph.facebook.com/v18.0/{user_id}",
                params={"fields": "name", "access_token": token})
            return r.json().get("name", "Instagram клиент")
    except:
        return "Instagram клиент"

async def send_ig_message(recipient_id: str, text: str):
    token   = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    page_id = os.getenv("INSTAGRAM_PAGE_ID")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v18.0/{page_id}/messages",
            params={"access_token": token},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}}
        )


# ════════════════════════════════════════════
# CRM API
# ════════════════════════════════════════════

@app.get("/api/leads")
async def api_leads():
    r = supabase.table("leads").select("*")\
        .eq("business_id", BUSINESS_ID).order("created_at", desc=True).execute()
    return r.data

@app.patch("/api/leads/{lead_id}")
async def api_update_lead(lead_id: str, request: Request):
    data = await request.json()
    supabase.table("leads").update(data).eq("id", lead_id).execute()
    return {"ok": True}

@app.post("/api/leads")
async def api_create_lead(request: Request):
    data = await request.json()
    data["business_id"] = BUSINESS_ID
    r = supabase.table("leads").insert(data).execute()
    return r.data[0] if r.data else {}

@app.get("/api/messages/{lead_id}")
async def api_messages(lead_id: str):
    r = supabase.table("messages").select("*")\
        .eq("lead_id", lead_id).order("created_at").execute()
    return r.data

@app.get("/api/stats")
async def api_stats():
    data = supabase.table("leads").select("status,amount")\
        .eq("business_id", BUSINESS_ID).execute().data or []
    total = len(data)
    sales = len([l for l in data if l["status"] == "done"])
    rev   = sum(l["amount"] for l in data if l["status"] == "done")
    return {
        "total": total,
        "in_work": len([l for l in data if l["status"] == "work"]),
        "sales": sales, "revenue": rev,
        "conversion": round(sales / total * 100, 1) if total else 0
    }

@app.get("/setup/telegram")
async def setup_tg():
    base = os.getenv("WEBHOOK_BASE_URL", "")
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TG_API}/setWebhook",
            json={"url": f"{base}/webhook/telegram"})
        return r.json()

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
