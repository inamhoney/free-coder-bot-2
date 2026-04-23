import os, asyncio, aiohttp, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# File server config (our Railway fileserver)
FILE_SERVER_URL = os.environ.get("FILE_SERVER_URL", "https://html-fileserver-production.up.railway.app")
FILE_SERVER_SECRET = os.environ.get("FILE_SERVER_SECRET", "xK9mR2pL7nQ4")

MODELS = {
    "kimi": {"id": "moonshotai/kimi-k2-instruct", "label": "Kimi K2"},
    "glm":  {"id": "thudm/glm-4-9b-chat", "label": "GLM-4"},
    "step": {"id": "stepfun-ai/step-3-5-flash-instruct-2505", "label": "Step 3.5 Flash"},
}
DEFAULT_MODEL = "kimi"

BUILD_SYSTEM = """You are an expert frontend developer. Output ONLY raw HTML — no markdown, no backticks, no explanation.
Rules:
- Single self-contained HTML file with embedded CSS and JS
- Use modern design: gradients, glassmorphism, animations
- Mobile responsive with viewport meta tag
- Professional, visually stunning result
- No external dependencies except CDN links (tailwind, fonts, etc.)"""

user_model = {}
user_history = {}

def get_model(uid):
    return user_model.get(uid, DEFAULT_MODEL)

async def upload_to_fileserver(html: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{FILE_SERVER_URL}/upload",
            data=html.encode("utf-8"),
            headers={"X-Secret": FILE_SERVER_SECRET, "Content-Type": "text/plain"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                raise Exception(f"File server error: {resp.status}")
            data = await resp.json()
            return data["url"]

async def call_nvidia(messages, model_key=None):
    if model_key is None:
        model_key = DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODELS[model_key]["id"],
        "messages": messages,
        "max_tokens": 8192,
        "stream": False,
    }
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(4 * attempt)
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{NVIDIA_NIM_BASE_URL}/chat/completions",
                    headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status in (502, 503, 504):
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        raise Exception(f"NVIDIA API {resp.status}: {text[:200]}")
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()
        except asyncio.TimeoutError:
            if attempt == 2:
                raise Exception("Timed out. Try /model step for fastest responses.")
    raise Exception("Failed after 3 attempts")

def extract_html(text):
    """Pull raw HTML out of model response."""
    if "```html" in text:
        text = text.split("```html")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()

def is_build_request(text):
    keywords = ["build", "make", "create", "design", "generate", "website", "landing", "page", "app", "portfolio", "dashboard", "store", "shop"]
    t = text.lower()
    return any(k in t for k in keywords) or "html" in t

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = [
        [InlineKeyboardButton("🌐 Build a Landing Page", callback_data="build_landing")],
        [InlineKeyboardButton("📊 SaaS Dashboard", callback_data="build_saas")],
        [InlineKeyboardButton("🛒 E-commerce Page", callback_data="build_ecom")],
        [InlineKeyboardButton("🔧 Fix My Code", callback_data="fix_code")],
    ]
    model_label = MODELS[get_model(uid)]["label"]
    await update.message.reply_text(
        f"👋 <b>AI Coding Bot</b> — powered by NVIDIA NIM\n\nModel: <b>{model_label}</b>\nDescribe what to build and I'll give you a <b>live URL</b> to preview it instantly!\n\nOr pick a template:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def model_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = [[InlineKeyboardButton(f"{'✅ ' if get_model(uid)==k else ''}{v['label']}", callback_data=f"model_{k}")] for k, v in MODELS.items()]
    await update.message.reply_text("Choose AI model:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data.startswith("model_"):
        k = data[6:]
        user_model[uid] = k
        await q.edit_message_text(f"✅ Switched to <b>{MODELS[k]['label']}</b>! Now describe what to build.", parse_mode=ParseMode.HTML)
        return

    prompts = {
        "build_landing": "Build a stunning SaaS landing page with hero section, features, pricing table, and CTA. Make it modern with animations.",
        "build_saas": "Build a SaaS analytics dashboard with sidebar nav, charts (use Chart.js from CDN), stats cards, and dark theme.",
        "build_ecom": "Build a modern e-commerce product page with image gallery, product details, reviews, and add to cart button.",
        "fix_code": None
    }
    if data == "fix_code":
        await q.edit_message_text("Paste your code and describe the issue — I'll fix it!")
        return
    
    prompt = prompts.get(data)
    if prompt:
        await q.edit_message_text(f"🔨 Building... hang tight!")
        await handle_build(q.message, prompt, uid, ctx)

async def handle_build(message, prompt, uid, ctx):
    status = await message.reply_text("⚙️ Generating with AI...")
    
    history = user_history.get(uid, [])
    history.append({"role": "user", "content": prompt})
    
    messages = [{"role": "system", "content": BUILD_SYSTEM}] + history[-6:]
    
    try:
        await status.edit_text("🧠 AI is coding...")
        raw = await call_nvidia(messages, get_model(uid))
        html = extract_html(raw)
        
        if not html or len(html) < 100:
            await status.edit_text("❌ Model returned empty. Try /model step or rephrase.")
            return
        
        history.append({"role": "assistant", "content": html})
        user_history[uid] = history[-10:]
        
        await status.edit_text("📤 Publishing to live server...")
        url = await upload_to_fileserver(html)
        
        # Plain text — no parse mode, URL always works
        await status.edit_text(
            f"✅ Done! Open your site:\n\n{url}\n\n💬 Want changes? Just describe them."
        )
        
    except Exception as e:
        logger.error(f"Build error: {e}")
        await status.edit_text(f"❌ Error: {str(e)[:200]}\n\nTry /model to switch AI model.")

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if not text:
        return

    if is_build_request(text):
        await handle_build(update.message, text, uid, ctx)
    else:
        # General coding question
        messages = [
            {"role": "system", "content": "You are an expert developer. Answer coding questions concisely with code examples. Keep replies short and practical."},
            {"role": "user", "content": text}
        ]
        status = await update.message.reply_text("🤔 Thinking...")
        try:
            reply = await call_nvidia(messages, get_model(uid))
            # Split long replies
            if len(reply) > 4000:
                for i in range(0, len(reply), 4000):
                    await update.message.reply_text(reply[i:i+4000])
                await status.delete()
            else:
                await status.edit_text(reply)
        except Exception as e:
            await status.edit_text(f"❌ {str(e)[:200]}")

def main():
    logger.info("🚀 Starting Coding Bot 2 (Railway File Server mode)...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
