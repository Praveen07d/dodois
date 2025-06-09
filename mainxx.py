import asyncio
import logging
import re
from typing import Dict, Optional
from datetime import datetime

from config import PROXIES, BOT_TOKEN, ADMIN_IDS
from dataclasses import dataclass
from aiohttp import ClientSession, ClientTimeout
from telegram import (
    Update,
    InputFile,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# ────────────────────────────────  Logging  ────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ────────────────────────────────  Constants  ───────────────────────────────
COOKIE_REGEX = re.compile(
    r"([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t(.+)"
)
NETFLIX_DOMAINS = (".netflix.com", ".nflxso.net", ".nflxext.com")
REQUEST_TIMEOUT = ClientTimeout(total=15)


# ────────────────────────────────  Data Class  ──────────────────────────────
@dataclass
class UserData:
    cookies: Dict[str, str]
    last_check: Optional[datetime] = None
    check_count: int = 0


user_sessions: Dict[int, UserData] = {}

# ────────────────────────────────  Helpers  ────────────────────────────────
async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Allow only ADMIN_IDS."""
    if update.effective_user.id in ADMIN_IDS:
        return True

    await update.message.reply_text("🚫 Access denied. This bot is private.")
    return False


def parse_cookies(cookie_text: str) -> Dict[str, str]:
    """Parse Netscape cookies, keep only un‑expired Netflix cookies."""
    cookies: Dict[str, str] = {}
    now_ts = datetime.now().timestamp()

    for line in cookie_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if match := COOKIE_REGEX.match(line):
            domain, _, _path, _secure, exp, name, val = match.groups()
            if any(domain.endswith(d) for d in NETFLIX_DOMAINS):
                if exp and float(exp) < now_ts:  # skip expired
                    continue
                cookies[name] = val
    return cookies


async def check_netflix_cookies(cookies: Dict[str, str]) -> bool:
    """Hit two Netflix pages behind every proxy until one succeeds."""
    test_urls = [
        "https://www.netflix.com/browse",
        "https://www.netflix.com/YourAccount",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with ClientSession(cookies=cookies, headers=headers, timeout=REQUEST_TIMEOUT) as sess:
        for proxy in PROXIES:
            try:
                for url in test_urls:
                    async with sess.get(url, proxy=proxy, allow_redirects=False) as r:
                        if r.status == 200 and ("Sign Out" in await r.text() or "Your Account" in await r.text()):
                            return True
            except Exception as e:
                logger.warning(f"[Proxy‑fail] {proxy}: {e}")
    return False


async def send_loading_animation(update: Update, interval: float = 1.0):
    frames = ["🔘 ○ ○ ○", "○ 🔘 ○ ○", "○ ○ 🔘 ○", "○ ○ ○ 🔘"]
    msg = await update.message.reply_text(
        "Checking cookie validity…\n" + frames[0],
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_check")]]),
    )
    for fr in frames[1:]:
        try:
            await asyncio.sleep(interval)
            await msg.edit_text(
                "Checking cookie validity…\n" + fr,
                reply_markup=msg.reply_markup,
            )
        except Exception:
            break
    return msg


# ────────────────────────────────  Command Handlers  ───────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_check(update, context):
        return

    await context.bot.set_my_commands(
        [
            BotCommand("start", "Show this menu"),
            BotCommand("check", "Check cookie validity"),
            BotCommand("clear", "Clear stored cookies"),
            BotCommand("help", "Show help information"),
        ]
    )

    with open("logo.png", "rb") as f:
        await update.message.reply_photo(
            photo=InputFile(f),
            caption=(
                "✨ *X‑Netlify Premium* ✨\n\n"
                "Send your Netflix cookie file (.txt) to begin.\n\n"
                "• Cookie check\n• Validity testing\n• Profile info\n\n"
                "Developer: [@vein_xox](https://t.me/vein_xox)"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📤 Upload Cookies", callback_data="upload_help")],
                    [InlineKeyboardButton("❓ Help", callback_data="show_help")],
                ]
            ),
        )


async def check_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_sessions:
        await update.message.reply_text("⚠️ No cookies found. Upload a `.txt` file first.")
        return

    loading = await send_loading_animation(update)
    valid = await check_netflix_cookies(user_sessions[uid].cookies)
    await loading.edit_text(
        "✅ Cookie is *valid!*" if valid else "❌ Cookie is *invalid.*", parse_mode="Markdown"
    )


async def clear_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if user_sessions.pop(update.effective_user.id, None):
        await update.message.reply_text("🗑️ Cookies cleared.")
    else:
        await update.message.reply_text("⚠️ No cookies stored.")


async def handle_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_check(update, context):
        return

    uid = update.effective_user.id
    file = await update.message.document.get_file()

    try:
        text = (await file.download_as_bytearray()).decode("utf‑8", "ignore")
        cookies = parse_cookies(text)
        if not cookies:
            await update.message.reply_text("❌ No valid Netflix cookies found.")
            return

        user_sessions[uid] = UserData(cookies=cookies)

        preview = (
            "🍿 *Cookie Preview* 🍿\n\n"
            f"• Total cookies: {len(cookies)}\n"
            f"• Session cookies: {sum(1 for k in cookies if k.startswith('NetflixId'))}\n\n"
            "Sample:\n```\n"
            + "\n".join(f"{k[:15]}…: {v[:15]}…" for k, v in list(cookies.items())[:3])
            + "\n```"
        )
        await update.message.reply_text(
            preview,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Check Validity", callback_data="check_valid")]]),
        )
    except Exception as e:
        logger.error(f"[Cookie‑parse‑error] {e}")
        await update.message.reply_text("❌ Error processing cookie file.")


# ────────────────────────────────  Callback Buttons  ───────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "check_valid":
        if uid not in user_sessions:
            await query.edit_message_text("⚠️ No cookies found.")
            return
        await query.edit_message_text("🔍 Checking cookie…")
        valid = await check_netflix_cookies(user_sessions[uid].cookies)
        await query.edit_message_text(
            "✅ Cookie is *valid!*" if valid else "❌ Cookie is *invalid.*", parse_mode="Markdown"
        )
    elif query.data == "cancel_check":
        await query.edit_message_text("❌ Check canceled.")
    elif query.data == "upload_help":
        await query.edit_message_text("📄 Please upload a `.txt` cookie file.")
    elif query.data == "show_help":
        await query.edit_message_text("ℹ️ Help:\n1️⃣ Send `.txt` cookie file.\n2️⃣ Tap ✅ to test.")


# ────────────────────────────────  Setup & Run  ────────────────────────────
def setup_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_cookies))
    app.add_handler(CommandHandler("clear", clear_cookies))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_cookie_file))
    app.add_handler(CallbackQueryHandler(button_handler))


async def post_init(app):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("check", "Check cookie validity"),
            BotCommand("clear", "Clear stored cookies"),
        ]
    )


async def _main():
    logger.info("🔧 Initializing bot…")
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    setup_handlers(application)
    logger.info("🤖 Bot is running…")
    await application.run_polling()


# ────────────────────────────────  Entry Point  ────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except RuntimeError as err:
        # Handles “event loop already running” (e.g. Jupyter, some shells)
        if "already running" in str(err):
            import nest_asyncio

            nest_asyncio.apply()
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_main())
        else:
            raise
