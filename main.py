"""
Free Coder Bot 2 - @Coding_2_bot
Generates websites and sends LIVE preview URLs.
Uses aiohttp webhook server — zero polling conflicts.
"""

import os, re, uuid, asyncio, aiohttp, logging, json
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatAction

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN_2"]
NVIDIA_NIM_API_KEY = os.environ["NVIDIA_NIM_API_KEY"]
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
WEBHOOK_URL        = "https://bot2-live-url-production.up.railway.app"
PORT               = int(os.environ.get("PORT", 8080))
NIM_URL            = "https://integrate.api.nvidia.com/v1"

MODELS = {
    "glm":  {"id": "z-ai/glm4.7",                "label": "GLM 4.7 ⚡"},
    "step": {"id": "stepfun-ai/step-3.5-flash",   "label": "Step 3.5 🚀"},
    "kimi": {"id": "moonshotai/kimi-k2-thinking", "label": "Kimi K2 🧠"},
}
DEFAULT_MODEL = "glm"
user_sessions: dict = {}
user_models:   dict = {}

BUILD_SYSTEM = """You are an expert frontend developer. Output ONLY raw HTML — no markdown, no backticks, no explanation.
Start with <!DOCTYPE html> and end with </html>.
Use Tailwind CSS CDN and Alpine.js CDN. Make it stunning: gradients, shadows, animations.
Realistic content, mobile responsive, under 8000 characters total."""

CHAT_SYSTEM = "You are an expert coding assistant. Answer concisely with working code in markdown code blocks."


def extract_content(msg):
    c = msg.get("content") or msg.get("reasoning_content") or ""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", c, flags=re.DOTALL).strip()
    return cleaned if cleaned else c.strip()

def extract_html(text):
    text = text.strip()
    m = re.search(r"```(?:html)?\n?([\s\S]*?)```", text, re.IGNORECASE)
    if m: text = m.group(1).strip()
    i = text.lower().find("<!doctype"); 
    if i > 0: text = text[i:]
    j = text.lower().rfind("</html>")
    if j != -1: text = text[:j+7]
    return text.strip()

def is_build(text):
    t = text.lower()
    return any(b in t for b in ["build","create","make","design","generate","develop"]) and \
           any(w in t for w in ["page","website","web","html","landing","portfolio","dashboard","ui","site","app","saas","shop","blog","form"])

async def call_nim(messages, model_key=DEFAULT_MODEL, max_tokens=6000):
    model_id = MODELS.get(model_key, MODELS[DEFAULT_MODEL])["id"]
    headers = {"Authorization": f"Bearer {NVIDIA_NIM_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": messages, "temperature": 0.7, "max_tokens": max_tokens, "stream": False}
    last_err = None
    for attempt in range(3):
        if attempt: await asyncio.sleep(5 * attempt)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{NIM_URL}/chat/completions", headers=headers, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=180)) as r:
                    if r.status in (502,503,504):
                        last_err = Exception(f"NVIDIA busy ({r.status}), retrying...")
                        logger.warning(f"NVIDIA {r.status} attempt {attempt+1}")
                        continue
                    if r.status != 200:
                        t = await r.text()
                        raise Exception(f"NVIDIA {r.status}: {t[:200]}")
                    data = await r.json()
                    result = extract_content(data["choices"][0]["message"])
                    if not result: raise Exception("Empty response — retry or use /model glm")
                    return result
        except asyncio.TimeoutError:
            last_err = Exception("Timed out — use /model step for fastest")
        except aiohttp.ClientError as e:
            last_err = Exception(f"Network: {e}")
    raise last_err or Exception("Failed after 3 attempts")

async def publish_gist(html):
    name = f"site_{uuid.uuid4().hex[:8]}.html"
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.github.com/gists",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "User-Agent": "FreeCoder-Bot"},
            json={"description": "Free Coder Bot 2", "public": True, "files": {name: {"content": html}}},
            timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status == 201:
                d = await r.json()
                return f"https://htmlpreview.github.io/?{d['files'][name]['raw_url']}"
            raise Exception(f"Gist {r.status}: {(await r.text())[:100]}")

def get_session(uid):
    if uid not in user_sessions:
        user_sessions[uid] = [{"role":"system","content":CHAT_SYSTEM}]
    return user_sessions[uid]

def split_msg(text, n=4000):
    if len(text)<=n: return [text]
    chunks=[]
    while len(text)>n:
        cut=text.rfind("\n",0,n); cut=cut if cut>0 else n
        chunks.append(text[:cut]); text=text[cut:].lstrip("\n")
    if text: chunks.append(text)
    return chunks

async def typing_loop(bot, chat_id, stop):
    while not stop.is_set():
        try: await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except: pass
        await asyncio.sleep(4)

async def progress_loop(msg, stop):
    stages=["⚡ Generating... hang tight!","⚡ Still writing code... 🔄","⚡ Finalizing design... 🔄","⚡ Almost done... 🔄"]
    i=0
    while not stop.is_set():
        await asyncio.sleep(9)
        if stop.is_set(): break
        try: await msg.edit_text(stages[min(i,len(stages)-1)]); i+=1
        except: pass


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update, context):
    user_sessions.pop(update.effective_user.id, None)
    kb = [[InlineKeyboardButton("🌐 Landing Page",  callback_data="ex_landing")],
          [InlineKeyboardButton("📊 SaaS Dashboard", callback_data="ex_dash")],
          [InlineKeyboardButton("🛒 E-commerce",     callback_data="ex_shop")],
          [InlineKeyboardButton("🐛 Fix My Code",    callback_data="ex_fix")]]
    await update.message.reply_text(
        f"🚀 <b>Free Coder Bot 2</b>\n\nHey {update.effective_user.first_name}! "
        f"Tell me what to build and I'll send you a <b>live preview link</b> 🔗\n\n"
        f"<b>Try:</b>\n• Build a landing page for a 3PL company in Germany\n"
        f"• Create a SaaS pricing page for 'CloudSync'\n• Make a restaurant website",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

async def cmd_new(update, context):
    user_sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("🔄 Fresh start! What do you want to build?")

async def cmd_help(update, context):
    uid = update.effective_user.id
    cur = user_models.get(uid, DEFAULT_MODEL)
    await update.message.reply_text(
        f"🤖 <b>Commands:</b> /start /new /model /help\n\n"
        f"<b>Models (free via NVIDIA NIM):</b>\n"
        f"⚡ glm — GLM 4.7 (fast, default)\n🚀 step — Step 3.5 (fastest)\n🧠 kimi — Kimi K2 (deep)\n\n"
        f"Current: <b>{MODELS[cur]['label']}</b>",
        parse_mode=ParseMode.HTML)

async def cmd_model(update, context):
    uid = update.effective_user.id
    cur = user_models.get(uid, DEFAULT_MODEL)
    kb = [[InlineKeyboardButton(("✅ " if cur==k else "")+v["label"], callback_data=f"model_{k}")] for k,v in MODELS.items()]
    await update.message.reply_text("Choose model:", reply_markup=InlineKeyboardMarkup(kb))

async def on_button(update, context):
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    data = q.data
    if data.startswith("model_"):
        k = data[6:]; user_models[uid] = k
        await q.edit_message_text(f"✅ Switched to <b>{MODELS[k]['label']}</b>! Now describe what to build.", parse_mode=ParseMode.HTML)
    elif data.startswith("ex_"):
        prompts = {
            "ex_landing": "Build a stunning landing page for luxury gym 'IronForge'. Hero, features, pricing, footer.",
            "ex_dash":    "Create a beautiful SaaS analytics dashboard for 'DataFlow'. Sidebar, KPI cards, charts.",
            "ex_shop":    "Build an e-commerce page for headphones 'SoundPeak Pro'. Gallery, specs, reviews, cart.",
            "ex_fix":     "Fix this bug:\ndef divide(a,b):\n    return a/b\nprint(divide(10,0))",
        }
        p = prompts.get(data,"Build something cool!")
        await q.edit_message_text("⚡ On it...")
        if data == "ex_fix": await do_chat(uid, p, q.message, context)
        else: await do_build(uid, p, q.message, context)

async def do_build(uid, prompt, message, context):
    mk = user_models.get(uid, DEFAULT_MODEL)
    ml = MODELS[mk]["label"]
    status = await context.bot.send_message(message.chat_id, f"⚡ Generating with {ml}... (~20-40s)")
    stop = asyncio.Event()
    prog = asyncio.create_task(progress_loop(status, stop))
    try:
        msgs = [{"role":"system","content":BUILD_SYSTEM}, {"role":"user","content":prompt}]
        raw = await call_nim(msgs, mk, 6000)
        html = extract_html(raw)
        stop.set(); await prog
        try:
            await status.edit_text("📤 Getting live URL...")
            url = await publish_gist(html)
            await status.edit_text(
                f"✅ <b>Website Ready!</b>\n\n🔗 <b>Live Preview:</b>\n{url}\n\nWant changes? Just describe them.",
                parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            logger.info(f"Live URL sent to {uid}: {url}")
        except Exception as ue:
            logger.error(f"Gist failed: {ue}")
            import io
            await status.edit_text("✅ Done! Sending as HTML file...")
            await context.bot.send_document(message.chat_id,
                document=io.BytesIO(html.encode()), filename=f"site_{uuid.uuid4().hex[:6]}.html",
                caption="📄 Download → open in browser!\n\nWant changes? Describe them.")
        s = get_session(uid)
        s.append({"role":"user","content":prompt})
        s.append({"role":"assistant","content":f"[website {len(html)} chars]"})
    except Exception as e:
        stop.set()
        try: await prog
        except: pass
        err = str(e) or "Unknown error"
        msg = ("⏳ Rate limit hit, wait a sec!" if "429" in err or "rate" in err.lower()
               else f"⏳ Timed out. Try /model step then retry." if "timed out" in err.lower()
               else f"❌ {err[:200]}\n\nTry /new")
        try: await status.edit_text(msg)
        except: await context.bot.send_message(message.chat_id, msg)

async def do_chat(uid, text, message, context):
    session = get_session(uid); session.append({"role":"user","content":text})
    mk = user_models.get(uid, DEFAULT_MODEL)
    stop = asyncio.Event()
    t = asyncio.create_task(typing_loop(context.bot, message.chat_id, stop))
    try:
        resp = await call_nim(session, mk, 3000)
        session.append({"role":"assistant","content":resp})
        stop.set(); await t
        for chunk in split_msg(resp):
            try: await context.bot.send_message(message.chat_id, chunk, parse_mode=ParseMode.MARKDOWN)
            except: await context.bot.send_message(message.chat_id, chunk)
    except Exception as e:
        stop.set()
        try: await t
        except: pass
        await context.bot.send_message(message.chat_id, f"❌ {str(e)[:200]}\n\nTry /new")

async def on_message(update, context):
    text = update.message.text; uid = update.effective_user.id
    if is_build(text): await do_build(uid, text, update.message, context)
    else: await do_chat(uid, text, update.message, context)


# ── Webhook server ─────────────────────────────────────────────────────────────

async def webhook_handler(request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(text="OK")

async def health(request):
    return web.Response(text="Bot 2 running ✅")

application = None

async def main():
    global application
    logger.info("🚀 Starting Free Coder Bot 2 (webhook mode)...")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("new",   cmd_new))
    application.add_handler(CommandHandler("help",  cmd_help))
    application.add_handler(CommandHandler("model", cmd_model))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    await application.initialize()
    await application.start()

    # Register webhook with Telegram
    bot = application.bot
    wh_url = f"{WEBHOOK_URL}/webhook"
    await bot.set_webhook(url=wh_url, drop_pending_updates=True,
                          allowed_updates=["message","callback_query"])
    logger.info(f"✅ Webhook set: {wh_url}")

    # Start aiohttp server
    web_app = web.Application()
    web_app.router.add_post("/webhook", webhook_handler)
    web_app.router.add_get("/", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Server listening on port {PORT}")

    # Keep alive
    try:
        await asyncio.Event().wait()
    finally:
        await application.stop()
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
