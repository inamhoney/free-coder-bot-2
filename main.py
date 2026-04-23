"""
Free Coder Bot 2 - @Coding_2_bot
Telegram bot with LIVE URL feature — generates websites and sends live links.
Powered by NVIDIA NIM (Kimi K2). Zero cost.
"""

import os
import re
import uuid
import asyncio
import aiohttp
import logging
import tempfile
from pathlib import Path

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
NVIDIA_NIM_API_KEY = os.environ["NVIDIA_NIM_API_KEY"]
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Cloudflare Workers KV or just use 0x0.st / file.io for free file hosting
# We'll use a simple approach: host on telegra.ph (no auth needed, permanent)
TELEGRAPH_API = "https://api.telegra.ph"

MODELS = {
    "kimi": {"id": "moonshotai/kimi-k2-thinking", "label": "Kimi K2 Thinking 🧠"},
    "glm":  {"id": "z-ai/glm4.7",                 "label": "GLM 4.7 ⚡"},
    "step": {"id": "stepfun-ai/step-3.5-flash",    "label": "Step 3.5 Flash 🚀"},
}
DEFAULT_MODEL = "kimi"

user_sessions: dict[int, list] = {}

SYSTEM_PROMPT_CHAT = """You are an expert full-stack coding assistant. You build complete, production-ready code.

When answering code questions: be concise, show working examples, wrap code in markdown blocks.
When asked to fix bugs: explain what was wrong, show the fix.
Keep responses clear and well-formatted for Telegram."""

SYSTEM_PROMPT_BUILD = """You are an expert frontend developer. Your job is to generate complete, beautiful, production-ready HTML pages.

STRICT RULES:
1. Output ONLY raw HTML — no markdown, no backticks, no explanation, no code blocks
2. Start with <!DOCTYPE html> and end with </html>
3. Use Tailwind CSS via CDN: <script src="https://cdn.tailwindcss.com"></script>
4. Use Alpine.js for interactivity: <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
5. Include Google Fonts for beautiful typography
6. Make it STUNNING — gradients, animations, hover effects, glassmorphism, modern design
7. Fully functional — all buttons work, forms work, all sections complete
8. Mobile responsive
9. No placeholder text — use realistic content

Output ONLY the HTML. Nothing else."""


def extract_content(message: dict) -> str:
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    raw = content if content else reasoning
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return cleaned if cleaned else raw.strip()


def is_build_request(text: str) -> bool:
    """Detect if user wants a website/app built (needs live URL)."""
    text_lower = text.lower()
    build_keywords = [
        "build", "create", "make", "design", "generate",
        "landing page", "website", "webpage", "web page",
        "portfolio", "dashboard", "app", "tool", "calculator",
        "todo", "form", "signup", "login", "pricing page",
        "saas", "startup"
    ]
    # Must mention building + web-related thing
    web_keywords = [
        "page", "website", "web", "html", "app", "landing",
        "portfolio", "dashboard", "ui", "interface", "design",
        "site", "frontend"
    ]
    has_build = any(kw in text_lower for kw in build_keywords)
    has_web = any(kw in text_lower for kw in web_keywords)
    return has_build and has_web


async def call_nvidia_nim(messages: list, model_key: str = DEFAULT_MODEL) -> str:
    model_id = MODELS.get(model_key, MODELS[DEFAULT_MODEL])["id"]
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": False,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{NVIDIA_NIM_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"API error {resp.status}: {error_text[:300]}")
            data = await resp.json()
            msg = data["choices"][0]["message"]
            return extract_content(msg)


async def upload_to_telegraph(html_content: str, title: str = "Generated Page") -> str:
    """
    Upload HTML to Telegraph and return the live URL.
    Telegraph supports basic HTML. For full CSS/JS we use a workaround:
    host on a pastebin-like service that renders HTML.
    """
    # Use htmlpreview.github.io trick via a temp gist, OR
    # Use surge.sh CLI — but easiest is CodeSandbox API or similar
    # Best free option: upload to file.io and use htmlpreview
    # Actually, use telegraph with iframe embedding the base64 encoded content
    
    # Encode the full page as a data URL and serve via a redirect page
    import base64
    encoded = base64.b64encode(html_content.encode()).decode()
    
    # Create a Telegraph page that auto-redirects to the data URL
    # But Telegraph strips scripts. Use a different approach:
    # Host on 0x0.st (free, no auth, permanent)
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field('file',
                      html_content.encode('utf-8'),
                      filename='index.html',
                      content_type='text/html')
        async with session.post('https://0x0.st', data=data,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                url = (await resp.text()).strip()
                return url
    raise Exception("Failed to upload file")


async def publish_to_htmlpreview(html_content: str) -> str:
    """Upload to GitHub Gist and get htmlpreview URL."""
    import json
    
    gist_token = os.environ.get("GITHUB_TOKEN", "")
    if not gist_token:
        raise Exception("No GitHub token")
    
    gist_name = f"page_{uuid.uuid4().hex[:8]}.html"
    payload = {
        "description": "Generated by Free Coder Bot",
        "public": True,
        "files": {
            gist_name: {"content": html_content}
        }
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.github.com/gists",
            headers={
                "Authorization": f"Bearer {gist_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                gist_id = data["id"]
                raw_url = data["files"][gist_name]["raw_url"]
                # Use htmlpreview to render it
                preview_url = f"https://htmlpreview.github.io/?{raw_url}"
                return preview_url
            else:
                err = await resp.text()
                raise Exception(f"Gist error {resp.status}: {err[:200]}")


def extract_html(text: str) -> str:
    """Extract HTML from model response (in case it wraps in backticks)."""
    # Try to find HTML block
    html_match = re.search(r'```html\n?([\s\S]*?)```', text, re.IGNORECASE)
    if html_match:
        return html_match.group(1).strip()
    
    # Try generic code block
    code_match = re.search(r'```\n?([\s\S]*?)```', text)
    if code_match:
        content = code_match.group(1).strip()
        if content.lower().startswith('<!doctype') or content.lower().startswith('<html'):
            return content
    
    # If it starts with DOCTYPE or html tag directly
    text = text.strip()
    if text.lower().startswith('<!doctype') or text.lower().startswith('<html'):
        return text
    
    return text


def get_session(user_id: int) -> list:
    if user_id not in user_sessions:
        user_sessions[user_id] = [{"role": "system", "content": SYSTEM_PROMPT_CHAT}]
    return user_sessions[user_id]


def clear_session(user_id: int):
    user_sessions[user_id] = [{"role": "system", "content": SYSTEM_PROMPT_CHAT}]


def split_message(text: str, max_length: int = 4000) -> list[str]:
    if len(text) <= max_length:
        return [text]
    chunks = []
    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_session(user.id)
    keyboard = [
        [InlineKeyboardButton("🌐 Build a Landing Page", callback_data="ex_landing")],
        [InlineKeyboardButton("📊 SaaS Dashboard", callback_data="ex_dashboard")],
        [InlineKeyboardButton("🛒 E-commerce Page", callback_data="ex_ecommerce")],
        [InlineKeyboardButton("🐛 Fix My Code", callback_data="ex_fix")],
    ]
    await update.message.reply_text(
        f"🚀 *Free Coder Bot 2* — NVIDIA NIM + Live URLs\n\n"
        f"Hey {user.first_name}! I build websites and send you a *live link* to preview instantly.\n\n"
        f"*Try saying:*\n"
        f"• Build a landing page for a gym\n"
        f"• Create a SaaS pricing page for 'CloudSync'\n"
        f"• Make a portfolio for a photographer\n"
        f"• Build a restaurant website\n\n"
        f"I'll generate the full design + send you a live URL 🔗",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_session(update.effective_user.id)
    await update.message.reply_text("🔄 Session cleared! Describe what you want to build.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot 2 — Live URL Builder*\n\n"
        "*What I do:*\n"
        "When you ask me to build a website/page/app, I:\n"
        "1. Generate the full HTML with NVIDIA NIM\n"
        "2. Upload it and send you a *live preview URL*\n\n"
        "*Commands:*\n"
        "/start — Welcome screen\n"
        "/new — Clear conversation\n"
        "/model — Switch AI model\n"
        "/help — This message\n\n"
        "*For code questions:* just ask normally (no URL generated)",
        parse_mode=ParseMode.MARKDOWN,
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
            f"✅ Switched to *{label}*\n\nDescribe what you want to build!",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("ex_"):
        examples = {
            "ex_landing":   "Build a stunning landing page for a gym called 'IronForge'. Include hero with CTA, features, pricing plans, testimonials, and footer.",
            "ex_dashboard": "Create a beautiful SaaS analytics dashboard for 'DataFlow'. Include sidebar nav, KPI cards, charts placeholders, recent activity feed, and dark mode toggle.",
            "ex_ecommerce": "Build an e-commerce product page for premium wireless headphones called 'SoundPeak Pro'. Include product images carousel, specs, reviews, add to cart, and related products.",
            "ex_fix":       "Answer this: Fix this Python bug:\n\ndef divide(a, b):\n    return a / b\n\nprint(divide(10, 0))",
        }
        prompt = examples.get(data, "Build something cool!")
        await query.edit_message_text("⚡ Generating now...")
        
        # Build requests go to the build pipeline
        if data != "ex_fix":
            await handle_build_request(user_id, prompt, query.message, context)
        else:
            await handle_chat_request(user_id, prompt, query.message, context)


async def handle_build_request(user_id: int, prompt: str, message, context: ContextTypes.DEFAULT_TYPE):
    """Generate a full website and upload for live preview."""
    model_key = context.user_data.get("model", DEFAULT_MODEL)
    
    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
        
        # Step 1: Generate HTML
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_BUILD},
            {"role": "user", "content": prompt}
        ]
        
        await context.bot.send_message(
            chat_id=message.chat_id,
            text="⚡ Generating your website with Kimi K2...\n_(this takes ~20-40 seconds)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        
        html_content = await call_nvidia_nim(messages, model_key)
        html_content = extract_html(html_content)
        
        # Step 2: Upload to GitHub Gist → htmlpreview
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        
        try:
            live_url = await publish_to_htmlpreview(html_content)
            
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=f"✅ *Website Generated!*\n\n"
                     f"🔗 *Live Preview:*\n{live_url}\n\n"
                     f"💡 _Tap the link to view in your browser_\n\n"
                     f"Want changes? Just describe what to update!",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
        except Exception as upload_err:
            logger.error(f"Upload error: {upload_err}")
            # Fallback: send as file
            html_bytes = html_content.encode('utf-8')
            filename = f"website_{uuid.uuid4().hex[:6]}.html"
            
            import io
            await context.bot.send_document(
                chat_id=message.chat_id,
                document=io.BytesIO(html_bytes),
                filename=filename,
                caption="✅ Website generated! Download this HTML file and open in your browser.\n\n_(Live URL hosting temporarily unavailable)_",
            )
        
        # Save to session for follow-up edits
        session = get_session(user_id)
        session.append({"role": "user", "content": prompt})
        session.append({"role": "assistant", "content": html_content})
        
    except Exception as e:
        err = str(e)
        logger.error(f"Build error for {user_id}: {err}")
        if "429" in err:
            await context.bot.send_message(chat_id=message.chat_id, text="⏳ Rate limit hit. Try again in a moment!")
        else:
            await context.bot.send_message(chat_id=message.chat_id, text=f"❌ Error: {err[:200]}\n\nTry /new and rephrase.")


async def handle_chat_request(user_id: int, text: str, message, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular code questions/fixes."""
    session = get_session(user_id)
    session.append({"role": "user", "content": text})
    model_key = context.user_data.get("model", DEFAULT_MODEL)

    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
        response = await call_nvidia_nim(session, model_key)
        session.append({"role": "assistant", "content": response})

        for chunk in split_message(response):
            try:
                await context.bot.send_message(
                    chat_id=message.chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await context.bot.send_message(chat_id=message.chat_id, text=chunk)

    except Exception as e:
        err = str(e)
        if "429" in err:
            await context.bot.send_message(chat_id=message.chat_id, text="⏳ Rate limit hit. Try again!")
        else:
            await context.bot.send_message(chat_id=message.chat_id, text=f"❌ Error: {err[:200]}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if is_build_request(text):
        await handle_build_request(user_id, text, update.message, context)
    else:
        await handle_chat_request(user_id, text, update.message, context)


def main():
    logger.info("🚀 Starting Free Coder Bot 2 (Live URL Builder)...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
