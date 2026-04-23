"""
Free Coder Bot 2 - @Coding_2_bot
Generates websites and sends LIVE preview URLs.
Powered by NVIDIA NIM. Railway deployed.
Uses WEBHOOK mode — no polling conflicts, instant responses.
"""

import os
import re
import uuid
import asyncio
import aiohttp
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN_2"]
NVIDIA_NIM_API_KEY  = os.environ["NVIDIA_NIM_API_KEY"]
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
WEBHOOK_URL         = os.environ.get("WEBHOOK_URL", "https://bot2-live-url-production.up.railway.app")
PORT                = int(os.environ.get("PORT", 8080))

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

MODELS = {
    "glm":  {"id": "z-ai/glm4.7",                "label": "GLM 4.7 ⚡ (fast)"},
    "step": {"id": "stepfun-ai/step-3.5-flash",   "label": "Step 3.5 🚀 (fastest)"},
    "kimi": {"id": "moonshotai/kimi-k2-thinking", "label": "Kimi K2 🧠 (deep)"},
}
DEFAULT_MODEL = "glm"

user_sessions: dict[int, list] = {}

SYSTEM_PROMPT_CHAT = """You are an expert coding assistant. Answer concisely with working code examples.
Wrap all code in markdown code blocks with correct language tags. Keep responses under 3000 characters."""

SYSTEM_PROMPT_BUILD = """You are an expert frontend developer. Generate a complete, beautiful HTML page.

CRITICAL RULES:
1. Output ONLY valid HTML — start with <!DOCTYPE html>, end with </html>
2. NO markdown, NO backticks, NO explanation — ONLY raw HTML
3. Use Tailwind CSS: <script src="https://cdn.tailwindcss.com"></script>
4. Use Alpine.js: <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
5. STUNNING design: gradients, shadows, animations, hover effects
6. 100% functional — all sections complete, realistic content, no placeholders
7. Mobile responsive
8. Keep total HTML under 8000 characters"""


def extract_content(message: dict) -> str:
    content   = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    raw = content if content else reasoning
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.DOTALL).strip()
    return cleaned if cleaned else raw.strip()


def extract_html(text: str) -> str:
    text = text.strip()
    html_match = re.search(r"```(?:html)?\n?([\s\S]*?)```", text, re.IGNORECASE)
    if html_match:
        text = html_match.group(1).strip()
    doctype_idx = text.lower().find("<!doctype")
    if doctype_idx > 0:
        text = text[doctype_idx:]
    html_end = text.lower().rfind("</html>")
    if html_end != -1:
        text = text[:html_end + 7]
    return text.strip()


def is_build_request(text: str) -> bool:
    t = text.lower()
    build_words = ["build", "create", "make", "design", "generate", "develop"]
    web_words   = ["page", "website", "web", "html", "landing", "portfolio",
                   "dashboard", "ui", "interface", "site", "frontend", "form",
                   "app", "saas", "startup", "ecommerce", "shop", "blog"]
    return any(b in t for b in build_words) and any(w in t for w in web_words)


async def call_nvidia_nim(messages: list, model_key: str = DEFAULT_MODEL, max_tokens: int = 6000) -> str:
    model_id = MODELS.get(model_key, MODELS[DEFAULT_MODEL])["id"]
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": False,
    }
    last_err = None
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(5 * attempt)
                logger.info(f"Retry attempt {attempt+1} for NVIDIA API")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NVIDIA_NIM_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status in (502, 503, 504):
                        last_err = Exception(f"NVIDIA busy ({resp.status}), retrying...")
                        logger.warning(f"NVIDIA {resp.status} on attempt {attempt+1}")
                        continue
                    if resp.status != 200:
                        err_text = await resp.text()
                        raise Exception(f"NVIDIA API {resp.status}: {err_text[:200]}")
                    data = await resp.json()
                    msg = data["choices"][0]["message"]
                    result = extract_content(msg)
                    if not result:
                        raise Exception("Model returned empty — try /model glm or retry")
                    return result
        except asyncio.TimeoutError:
            last_err = Exception("Timed out — try /model step for fastest responses")
            continue
        except aiohttp.ClientError as e:
            last_err = Exception(f"Network error: {str(e)}")
            continue
    raise last_err or Exception("Failed after 3 attempts")


async def publish_to_gist(html_content: str) -> str:
    gist_name = f"site_{uuid.uuid4().hex[:8]}.html"
    payload = {
        "description": "Generated by Free Coder Bot 2",
        "public": True,
        "files": {gist_name: {"content": html_content}},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.github.com/gists",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "FreeCoder-Bot",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                raw_url = data["files"][gist_name]["raw_url"]
                return f"https://htmlpreview.github.io/?{raw_url}"
            else:
                err = await resp.text()
                raise Exception(f"Gist upload failed {resp.status}: {err[:150]}")


def get_session(user_id: int) -> list:
    if user_id not in user_sessions:
        user_sessions[user_id] = [{"role": "system", "content": SYSTEM_PROMPT_CHAT}]
    return user_sessions[user_id]


def clear_session(user_id: int):
    user_sessions[user_id] = [{"role": "system", "content": SYSTEM_PROMPT_CHAT}]


def split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def keep_typing(bot, chat_id: int, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(4)


async def progress_updater(status_msg, stop_event: asyncio.Event):
    stages = [
        "⚡ Generating your website...\n(~20-40 sec, hang tight!)",
        "⚡ Generating... 🔄 Still writing code...",
        "⚡ Generating... 🔄 Finalizing design...",
        "⚡ Generating... 🔄 Almost done...",
    ]
    idx = 0
    while not stop_event.is_set():
        await asyncio.sleep(9)
        if stop_event.is_set():
            break
        try:
            idx = min(idx + 1, len(stages) - 1)
            await status_msg.edit_text(stages[idx])
        except Exception:
            pass


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_session(user.id)
    keyboard = [
        [InlineKeyboardButton("🌐 Build a Landing Page", callback_data="ex_landing")],
        [InlineKeyboardButton("📊 SaaS Dashboard",       callback_data="ex_dashboard")],
        [InlineKeyboardButton("🛒 E-commerce Page",      callback_data="ex_ecommerce")],
        [InlineKeyboardButton("🐛 Fix My Code",          callback_data="ex_fix")],
    ]
    await update.message.reply_text(
        f"🚀 <b>Free Coder Bot 2</b> — Live Website Builder\n\n"
        f"Hey {user.first_name}! Describe a website and I'll build it + send a <b>live preview link</b>.\n\n"
        f"<b>Examples:</b>\n"
        f"• Build a landing page for a 3PL company in Germany\n"
        f"• Create a SaaS pricing page for 'CloudSync'\n"
        f"• Make a portfolio for a photographer\n"
        f"• Build a restaurant website with menu\n\n"
        f"🔗 You get a live URL to open in your browser!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_session(update.effective_user.id)
    await update.message.reply_text("🔄 Cleared! Describe what you want to build.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("model", DEFAULT_MODEL)
    await update.message.reply_text(
        f"🤖 <b>Bot 2 — Live Website Builder</b>\n\n"
        f"I generate complete websites and send you a live preview URL!\n\n"
        f"<b>Commands:</b> /start /new /model /help\n\n"
        f"<b>Models (all free via NVIDIA NIM):</b>\n"
        f"⚡ glm  — GLM 4.7 (fast, default)\n"
        f"🚀 step — Step 3.5 Flash (ultrafast)\n"
        f"🧠 kimi — Kimi K2 (deep, slow)\n\n"
        f"Current: <b>{MODELS[current]['label']}</b>",
        parse_mode=ParseMode.HTML,
    )


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("model", DEFAULT_MODEL)
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if current == k else ''}{v['label']}",
            callback_data=f"model_{k}"
        )]
        for k, v in MODELS.items()
    ]
    await update.message.reply_text(
        "Choose AI model (all free via NVIDIA NIM):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("model_"):
        key = data[6:]
        context.user_data["model"] = key
        label = MODELS[key]["label"]
        await query.edit_message_text(
            f"✅ Switched to <b>{label}</b>\n\nNow describe what you want to build!",
            parse_mode=ParseMode.HTML,
        )
    elif data.startswith("ex_"):
        examples = {
            "ex_landing":   "Build a stunning landing page for a luxury gym called 'IronForge'. Include hero with CTA, features section, pricing plans, and footer.",
            "ex_dashboard": "Create a beautiful SaaS analytics dashboard for 'DataFlow'. Include sidebar nav, KPI cards, chart areas.",
            "ex_ecommerce": "Build an e-commerce product page for premium wireless headphones 'SoundPeak Pro'. Include image gallery, specs, reviews, add to cart.",
            "ex_fix":       "Fix this Python bug:\n\ndef divide(a, b):\n    return a / b\n\nprint(divide(10, 0))",
        }
        prompt = examples.get(data, "Build something cool!")
        if data == "ex_fix":
            await query.edit_message_text("⚡ Working on it...")
            await handle_chat(user_id, prompt, query.message, context)
        else:
            await query.edit_message_text("⚡ Starting generation...")
            await handle_build(user_id, prompt, query.message, context)


async def handle_build(user_id: int, prompt: str, message, context: ContextTypes.DEFAULT_TYPE):
    model_key   = context.user_data.get("model", DEFAULT_MODEL)
    model_label = MODELS[model_key]["label"]

    status_msg = await context.bot.send_message(
        chat_id=message.chat_id,
        text=f"⚡ Generating your website with {model_label}...\n(~20-40 sec, hang tight!)",
    )

    stop_event = asyncio.Event()
    progress_task = asyncio.create_task(progress_updater(status_msg, stop_event))

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_BUILD},
            {"role": "user",   "content": prompt},
        ]
        raw_html     = await call_nvidia_nim(messages, model_key, max_tokens=6000)
        html_content = extract_html(raw_html)

        stop_event.set()
        await progress_task

        # Try live URL first
        try:
            await status_msg.edit_text("📤 Uploading to get live URL...")
            live_url = await publish_to_gist(html_content)

            await status_msg.edit_text(
                f"✅ <b>Website Ready!</b>\n\n"
                f"🔗 <b>Live Preview:</b>\n{live_url}\n\n"
                f"Open in browser to see your site!\n\n"
                f"Want changes? Just describe them.",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            logger.info(f"Sent live URL to user {user_id}: {live_url}")

        except Exception as upload_err:
            logger.error(f"Gist upload failed: {upload_err}")
            import io
            await status_msg.edit_text("✅ Generated! Sending as HTML file...")
            await context.bot.send_document(
                chat_id=message.chat_id,
                document=io.BytesIO(html_content.encode("utf-8")),
                filename=f"website_{uuid.uuid4().hex[:6]}.html",
                caption="📄 Download this file → open in browser to preview!\n\nWant changes? Just describe them.",
            )

        # Save to session
        session = get_session(user_id)
        session.append({"role": "user",      "content": prompt})
        session.append({"role": "assistant", "content": f"[Generated {len(html_content)} char website]"})

    except Exception as e:
        stop_event.set()
        try:
            await progress_task
        except Exception:
            pass

        err = str(e) or "Unknown error"
        logger.error(f"Build error for user {user_id}: {err}")

        if "429" in err or "rate" in err.lower():
            msg = "⏳ Rate limit hit. Wait a moment and try again!"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            msg = f"⏳ {model_label} timed out.\n\nSend /model and pick Step 3.5 (fastest), then retry."
        elif "empty" in err.lower():
            msg = "⚠️ Model returned empty. Send /model → pick GLM, then retry."
        else:
            msg = f"❌ Error: {err[:200]}\n\nTry /new to reset."

        try:
            await status_msg.edit_text(msg)
        except Exception:
            await context.bot.send_message(chat_id=message.chat_id, text=msg)


async def handle_chat(user_id: int, text: str, message, context: ContextTypes.DEFAULT_TYPE):
    session   = get_session(user_id)
    model_key = context.user_data.get("model", DEFAULT_MODEL)
    session.append({"role": "user", "content": text})

    stop_event  = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, message.chat_id, stop_event))

    try:
        response = await call_nvidia_nim(session, model_key, max_tokens=3000)
        session.append({"role": "assistant", "content": response})

        stop_event.set()
        await typing_task

        for chunk in split_message(response):
            try:
                await context.bot.send_message(
                    chat_id=message.chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await context.bot.send_message(chat_id=message.chat_id, text=chunk)

    except Exception as e:
        stop_event.set()
        try:
            await typing_task
        except Exception:
            pass
        err = str(e) or "Unknown error"
        msg = f"❌ {err[:200]}\n\nTry /new to reset."
        await context.bot.send_message(chat_id=message.chat_id, text=msg)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text    = update.message.text
    user_id = update.effective_user.id
    if is_build_request(text):
        await handle_build(user_id, text, update.message, context)
    else:
        await handle_chat(user_id, text, update.message, context)


# ── Main — WEBHOOK mode ────────────────────────────────────────────────────────

def main():
    logger.info("🚀 Starting Free Coder Bot 2 (webhook mode)...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start_command))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("new",    new_command))
    app.add_handler(CommandHandler("model",  model_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Webhook — no polling, no conflicts, instant
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=f"{WEBHOOK_URL}/webhook",
        url_path="/webhook",
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
