"""
TAJIK AI CRM — Сервер с Google Gemini
Telegram бот + Instagram webhook
"""

import os, json, re
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
FORCE_LOCAL_AI  = os.getenv("FORCE_LOCAL_AI", "false").lower() in ("1", "true", "yes", "on")

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
# GEMINI — ЖИВОЙ AI ОТВЕТ
# ════════════════════════════════════════════

def normalize_text(text: str) -> str:
    return (text or "").lower().strip()


def extract_phone_local(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"(?:\+?992)?[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}", text)
    if not m:
        m = re.search(r"\b\d{9}\b", text)
    return m.group(0).strip() if m else None


def detect_product(text: str) -> str | None:
    t = normalize_text(text)
    if any(x in t for x in ["диаб", "қанд", "канд", "сахар", "dia", "дианова", "dianova"]):
        return "DiaNova"
    if any(x in t for x in ["maximus", "максимус", "мард", "мардона", "қувват", "кувват", "асбоб", "потенц", "эрекц"]):
        return "Maximus Complex"
    if any(x in t for x in ["testofertil", "тестофертил", "фарзанд", "репродук"]):
        return "Testofertil"
    if any(x in t for x in ["иммун", "avicena plus", "авицена плюс"]):
        return "Avicena Plus"
    return None


def last_known_product(history: list | None) -> str | None:
    if not history:
        return None
    for m in reversed(history[-12:]):
        p = detect_product(m.get("content", ""))
        if p:
            return p
    return None


def client_language(text: str) -> str:
    t = normalize_text(text)
    ru = ["привет", "здравствуйте", "цена", "сколько", "хочу", "купить", "можно", "не хочу", "спасибо"]
    uz = ["salom", "qancha", "necha", "sotib", "olmoq", "kerak", "rahmat", "xohlayman"]
    if any(x in t for x in ru):
        return "ru"
    if any(x in t for x in uz):
        return "uz"
    return "tj"


def product_price(product: str | None) -> int | None:
    return {
        "DiaNova": 280,
        "Maximus Complex": 450,
        "Testofertil": 520,
        "Avicena Plus": 380,
    }.get(product or "")


def local_sales_reply(user_message: str, history: list | None = None) -> str:
    """Жёсткий локальный менеджер Avicena Life без Gemini.
    Цель: коротко, понятно, без странных фраз и без повторов.
    """
    t = normalize_text(user_message)
    lang = client_language(user_message)
    product = detect_product(t) or last_known_product(history)
    price = product_price(product)
    phone = extract_phone_local(user_message)

    has_greet = any(x in t for x in ["салом", "ассалом", "salom", "привет", "здравствуйте", "hello"])
    asks_identity = any(x in t for x in ["шумо ки", "кто ты", "ту кто", "ки ҳастӣ", "кӣ ҳастӣ", "ты кто"])
    asks_price = any(x in t for x in ["нарх", "цена", "чанд", "чансум", "сомон", "сомонӣ", "сколько", "qancha", "necha"])
    wants_buy = any(x in t for x in ["мехом", "мехоҳам", "мегирам", "олам", "фармоиш", "заказ", "ха", "ҳа", "да", "ок", "хочу", "беру", "заказываю"])
    refuses = any(x in t for x in ["намехом", "намехоҳам", "не хочу", "не надо", "даркор нест", "лозим нест", "нет"])
    insult = any(x in t for x in ["блять", "бля", "сука", "нах", "туп", "говно", "лох", "мошен", "обман"])

    if phone:
        if lang == "ru":
            return "Принял номер. Менеджер Avicena Life свяжется с вами в течение 15 минут 📞"
        if lang == "uz":
            return "Raqamingizni oldim. Avicena Life menejeri 15 daqiqa ichida siz bilan bog‘lanadi 📞"
        return "Рақаматонро гирифтам. Менеджери Avicena Life то 15 дақиқа бо шумо тамос мегирад 📞"

    if asks_identity:
        if lang == "ru":
            return "Я Алӣ — AI-помощник Avicena Life. Отвечаю по товарам, ценам и заказам."
        if lang == "uz":
            return "Men Ali — Avicena Life AI-yordamchisiman. Mahsulot, narx va buyurtma bo‘yicha yordam beraman."
        return "Ман Алӣ — AI-ёрдамчии Avicena Life. Дар бораи маҳсулот, нарх ва фармоиш кӯмак мекунам."

    # Цена конкретного товара
    if asks_price and product and price:
        if lang == "ru":
            return f"{product} — {price} сомони. Хотите оформить заказ?"
        if lang == "uz":
            return f"{product} — {price} somoniy. Buyurtma berasizmi?"
        return f"{product} — {price} сомонӣ. Фармоиш медиҳед?"

    # Пользователь согласился после цены / товара
    if wants_buy and product:
        if lang == "ru":
            return "Хорошо. Отправьте имя, номер телефона и город доставки. Менеджер подтвердит заказ."
        if lang == "uz":
            return "Yaxshi. Ism, telefon raqam va yetkazib berish shahrini yuboring. Menejer buyurtmani tasdiqlaydi."
        return "Хуб. Ном, рақами телефон ва шаҳри дастрасониро фиристед. Менеджер фармоишро тасдиқ мекунад."

    # Пользователь просто согласился, но товар неясен
    if wants_buy and not product:
        if lang == "ru":
            return "Хорошо. Какой товар нужен: DiaNova, Maximus Complex, Testofertil или Avicena Plus?"
        if lang == "uz":
            return "Yaxshi. Qaysi mahsulot kerak: DiaNova, Maximus Complex, Testofertil yoki Avicena Plus?"
        return "Хуб. Кадом маҳсулот лозим: DiaNova, Maximus Complex, Testofertil ё Avicena Plus?"

    if refuses:
        if lang == "ru":
            return "Понял. Можете написать причину: цена, нет доверия или товар не подходит?"
        if lang == "uz":
            return "Tushunarli. Sababi narxmi, ishonch yo‘qmi yoki mahsulot mos emasmi?"
        return "Фаҳмо. Сабабаш нарх аст, боварӣ нест ё маҳсулот мувофиқ нест?"

    if insult:
        if lang == "ru":
            return "Понимаю. Я отвечаю только по товарам Avicena Life: цена, состав, доставка и заказ. Какой вопрос у вас?"
        if lang == "uz":
            return "Tushundim. Men Avicena Life mahsulotlari bo‘yicha javob beraman: narx, tarkib, yetkazib berish va buyurtma. Savolingiz nima?"
        return "Фаҳмо. Ман танҳо дар бораи маҳсулоти Avicena Life ҷавоб медиҳам: нарх, таркиб, дастрасонӣ ва фармоиш. Саволатон чист?"

    if has_greet:
        if lang == "ru":
            return "Здравствуйте. Я Алӣ, помощник Avicena Life. Какой товар вас интересует: DiaNova, Maximus Complex или Testofertil?"
        if lang == "uz":
            return "Salom. Men Ali, Avicena Life yordamchisiman. Qaysi mahsulot qiziqtiryapti: DiaNova, Maximus Complex yoki Testofertil?"
        return "Салом. Ман Алӣ, ёрдамчии Avicena Life. Кадом маҳсулот лозим аст: DiaNova, Maximus Complex ё Testofertil?"

    # Интерес по товару без вопроса цены
    if product == "DiaNova":
        if lang == "ru":
            return "DiaNova — БАД для поддержки организма при контроле сахара. Цена 280 сомони. Хотите заказать?"
        return "DiaNova — БАД барои дастгирии организм ҳангоми назорати қанд. Нархаш 280 сомонӣ. Фармоиш медиҳед?"
    if product == "Maximus Complex":
        if lang == "ru":
            return "Maximus Complex — комплекс для мужской энергии и тонуса. Цена 450 сомони. Хотите заказать?"
        return "Maximus Complex — комплекс барои қувват ва тонуси мардона. Нархаш 450 сомонӣ. Фармоиш медиҳед?"
    if product == "Testofertil":
        if lang == "ru":
            return "Testofertil — комплекс для поддержки репродуктивного здоровья. Цена 520 сомони. Хотите заказать?"
        return "Testofertil — комплекс барои дастгирии саломатии репродуктивӣ. Нархаш 520 сомонӣ. Фармоиш медиҳед?"
    if product == "Avicena Plus":
        if lang == "ru":
            return "Avicena Plus — комплекс для поддержки иммунитета. Цена 380 сомони. Хотите заказать?"
        return "Avicena Plus — комплекс барои дастгирии иммунитет. Нархаш 380 сомонӣ. Фармоиш медиҳед?"

    # Общая цена без товара
    if asks_price:
        if lang == "ru":
            return "Цены: DiaNova — 280с, Maximus Complex — 450с, Testofertil — 520с, Avicena Plus — 380с. Какой товар интересует?"
        if lang == "uz":
            return "Narxlar: DiaNova — 280s, Maximus Complex — 450s, Testofertil — 520s, Avicena Plus — 380s. Qaysi biri kerak?"
        return "Нархҳо: DiaNova — 280с, Maximus Complex — 450с, Testofertil — 520с, Avicena Plus — 380с. Кадомаш лозим?"

    if lang == "ru":
        return "Напишите, какой товар интересует: DiaNova, Maximus Complex, Testofertil или Avicena Plus. Я скажу цену и условия заказа."
    if lang == "uz":
        return "Qaysi mahsulot qiziqtirayotganini yozing: DiaNova, Maximus Complex, Testofertil yoki Avicena Plus. Narx va buyurtma shartlarini aytaman."
    return "Нависед кадом маҳсулот лозим: DiaNova, Maximus Complex, Testofertil ё Avicena Plus. Нарх ва шартҳои фармоишро мегӯям."


# ════════════════════════════════════════════
# GEMINI — AI ОТВЕТ + ЛОКАЛЬНЫЙ FALLBACK
# ════════════════════════════════════════════

async def get_ai_response(user_message: str, history: list) -> str:
    if FORCE_LOCAL_AI or not GROQ_KEY:
        return local_sales_reply(user_message, history)

    contents = []
    last_role = None
    for m in history[-10:]:
        role = "model" if m["role"] == "bot" else "user"
        if role == last_role:
            continue
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
        last_role = role

    if last_role == "user":
        contents = contents[:-1]

    contents.append({"role": "user", "parts": [{"text": user_message}]})

    if contents and contents[0]["role"] == "model":
        contents = contents[1:]

    try:
        messages_for_groq = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-10:]:
            role = "assistant" if m["role"] == "bot" else "user"
            messages_for_groq.append({"role": role, "content": m["content"]})
        messages_for_groq.append({"role": "user", "content": user_message})

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={"model": "llama-3.1-8b-instant", "messages": messages_for_groq,
                      "max_tokens": 200, "temperature": 0.7}
            )
            data = r.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"].strip()
            print(f"Groq error: {data}")
            return local_sales_reply(user_message, history)

    except Exception as e:
        print(f"Groq exception: {e}")
        return local_sales_reply(user_message, history)


async def extract_lead_info(messages: list) -> dict:
    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in messages])

    all_text = "\n".join([m.get("content", "") for m in messages])
    local = {}
    phone = extract_phone_local(all_text)
    product = detect_product(all_text)
    if phone:
        local["phone"] = phone
    if product:
        local["product"] = product

    if not GROQ_KEY or FORCE_LOCAL_AI:
        return local

    try:
        prompt = f"""
Извлеки из диалога данные клиента. Ответь ТОЛЬКО JSON без markdown:
{{"name": "имя или null", "phone": "телефон или null", "product": "товар или null", "city": "город или null"}}

Диалог:
{conversation}
"""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={"model": "llama-3.1-8b-instant",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 150, "temperature": 0}
            )
            data = r.json()
            if "choices" not in data:
                return local
            text = data["choices"][0]["message"]["content"].strip()
            start, end = text.find('{'), text.rfind('}') + 1
            ai = json.loads(text[start:end]) if start >= 0 else {}
            return {**local, **{k: v for k, v in ai.items() if v and v != "null"}}
    except Exception as e:
        print(f"extract_lead_info fallback: {e}")
        return local


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
