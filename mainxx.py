import asyncio
import logging
import re
from io import BytesIO
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
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
COOKIE_REGEX = re.compile(
    r'([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t(.+)'
)
NETFLIX_DOMAINS = ('.netflix.com', '.nflxso.net', '.nflxext.com')
REQUEST_TIMEOUT = ClientTimeout(total=15)

@dataclass
class UserData:
    cookies: Dict[str, str]
    last_check: Optional[datetime] = None
    check_count: int = 0

user_sessions: Dict[int, UserData] = {}

# --- Security Check ---
async def security_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user.id in ADMIN_IDS:
        return True
    await update.message.reply_text("🚫 Access denied. This bot is private.")
    return False

# --- Cookie Parser ---
def parse_cookies(cookie_text: str) -> Dict[str, str]:
    cookies = {}
    for line in cookie_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if match := COOKIE_REGEX.match(line):
            domain, _, path, secure, expiration, name, value = match.groups()
            if any(domain.endswith(nd) for nd in NETFLIX_DOMAINS):
                if expiration and float(expiration) < datetime.now().timestamp():
                    continue
                cookies[name] = value
    return cookies

# --- Netflix Cookie Checker ---
async def check_netflix_cookies(cookies: Dict[str, str]) -> bool:
    test_urls = [
        "https://www.netflix.com/browse",
        "https://www.netflix.com/YourAccount"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9"
    }

    async with ClientSession(
        cookies=cookies,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    ) as session:
        for proxy in PROXIES:
            try:
                for url in test_urls:
                    async with session.get(url, proxy=proxy, allow_redirects=False) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            if "Sign Out" in text or "Your Account" in text:
                                return True
            except Exception as e:
                logger.warning(f"Proxy {proxy} failed: {e}")
    return False

# --- UI Animation ---
async def send_loading_animation(update: Update, interval: float = 1.0):
    frames = ["🔘 ○ ○ ○", "○ 🔘 ○ ○", "○ ○ 🔘 ○", "○ ○ ○ 🔘"]
    message = await update.message.reply_text(
        "Checking cookie validity...\n" + frames[0],
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_check")]
        ])
    )
    for frame in frames[1:]:
        try:
            await asyncio.sleep(interval)
            await message.edit_text(
                "Checking cookie validity...\n" + frame,
                reply_markup=message.reply_markup
            )
        except:
            break
    return message

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_check(update, context):
        return

    await context.bot.set_my_commands([
        BotCommand("start", "Show this menu"),
        BotCommand("check", "Check cookie validity"),
        BotCommand("clear", "Clear stored cookies"),
        BotCommand("help", "Show help information")
    ])

    with open("logo.png", "rb") as f:
        await update.message.reply_photo(
            photo=InputFile(f),
            caption=(
                "✨ *X-Netlify Premium* ✨\n\n"
                "Send your Netflix cookie file (.txt) to begin\n\n"
                "• Cookie check\n• Validity testing\n• Profile info\n\n"
                "Developer: [@vein_xox](https://t.me/vein_xox)"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Upload Cookies", callback_data="upload_help")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")]
            ])
        )

async def check_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("⚠️ No cookies found. Send a `.txt` file first.")
        return

    loading_msg = await send_loading_animation(update)
    is_valid = await check_netflix_cookies(user_sessions[user_id].cookies)

    await loading_msg.edit_text("✅ Cookie is *valid!*" if is_valid else "❌ Cookie is *invalid.*", parse_mode="Markdown")

async def clear_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("🗑️ Cookies cleared.")
    else:
        await update.message.reply_text("⚠️ No cookies stored.")

async def handle_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_check(update, context):
        return

    user_id = update.effective_user.id
    file = await update.message.document.get_file()

    try:
        file_bytes = await file.download_as_bytearray()
        cookie_text = file_bytes.decode("utf-8", errors="ignore")
        cookies = parse_cookies(cookie_text)

        if not cookies:
            await update.message.reply_text("❌ No valid Netflix cookies found.")
            return

        user_sessions[user_id] = UserData(cookies=cookies)

        preview = (
            "🍿 *Cookie Preview* 🍿\n\n"
            f"• Total cookies: {len(cookies)}\n"
            f"• Session cookies: {sum(1 for k in cookies if k.startswith('NetflixId'))}\n\n"
            "Sample cookies:\n```\n" +
            "\n".join(f"{k[:15]}...: {v[:15]}..." for k,v in list(cookies.items())[:3]) +
            "\n```"
        )

        await update.message.reply_text(
            preview,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Check Validity", callback_data="check_valid")]
            ])
        )
    except Exception as e:
        logger.error(f"Cookie processing failed: {e}")
        await update.message.reply_text("❌ Error processing cookie file.")

# --- Button Handler ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "check_valid":
        if user_id not in user_sessions:
            await query.edit_message_text("⚠️ No cookies found.")
            return
        msg = await query.edit_message_text("🔍 Checking cookie...")
        is_valid = await check_netflix_cookies(user_sessions[user_id].cookies)
        await msg.edit_text("✅ Cookie is *valid!*" if is_valid else "❌ Cookie is *invalid.*", parse_mode="Markdown")
    elif query.data == "cancel_check":
        await query.edit_message_text("❌ Check canceled.")
    elif query.data == "upload_help":
        await query.edit_message_text("📄 Please upload a `.txt` cookie file.")
    elif query.data == "show_help":
        await query.edit_message_text("ℹ️ Send a `.txt` file with your Netflix cookies to begin.")

# --- Init & Run ---
def setup_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_cookies))
    app.add_handler(CommandHandler("clear", clear_cookies))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_cookie_file))
    app.add_handler(CallbackQueryHandler(button_handler))

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("check", "Check cookie validity"),
        BotCommand("clear", "Clear stored cookies")
    ])

async def main():
    print("🔧 Bot is starting...")
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    setup_handlers(application)
    logger.info("Bot is running...")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
