import os
import time
import uuid
import asyncio
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient

# --- HOSTING ENV VARIABLES ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongodb_uri")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
PORT = int(os.environ.get("PORT", 8080))
BASE_URL = os.environ.get("BASE_URL", "https://your-app-name.onrender.com")

# --- DATABASE SETUP ---
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["file_store_pro"]
users_col = db["users"]
tokens_col = db["tokens"]
config_col = db["config"]
stats_col = db["stats"]

app = Client("FileStoreMaster", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Helper: Get/Set Dynamic Config
async def get_config():
    conf = await config_col.find_one({"_id": "settings"})
    if not conf:
        conf = {
            "_id": "settings",
            "db_channel": 0,
            "fsub_channel": 0,
            "shortner1_url": "",
            "shortner1_api": "",
            "shortner2_url": "",
            "shortner2_api": "",
            "verify_time": 12, # hours
            "tutorial": "https://t.me",
            "backup_link": "https://t.me"
        }
        await config_col.insert_one(conf)
    return conf

# Helper: Shortener API Call
async def get_shortlink(domain, api_key, long_url):
    if not domain or not api_key:
        return long_url
    api_url = f"https://{domain}/st?api={api_key}&url={long_url}"
    try:
        async with web.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                data = await resp.json()
                return data.get("shortenedUrl") or data.get("url") or long_url
    except Exception:
        return long_url

# --- WEB PLAYER (PROTECTED: NO DOWNLOAD / NO SHARE) ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Protected Stream Player</title>
    <style>
        body {{ margin: 0; background: #07090e; display: flex; align-items: center; justify-content: center; height: 100vh; overflow: hidden; font-family: sans-serif; }}
        .box {{ width: 100%; max-width: 900px; background: #000; border-radius: 8px; overflow: hidden; }}
        video {{ width: 100%; height: auto; max-height: 85vh; display: block; outline: none; }}
        .bar {{ padding: 8px 12px; background: #121721; color: #fff; font-size: 13px; display: flex; justify-content: space-between; }}
    </style>
</head>
<body oncontextmenu="return false;">
    <div class="box">
        <div class="bar">
            <span>▶️ Online Video Player</span>
            <span style="color: #ff3366;">Direct Streaming</span>
        </div>
        <video controls controlsList="nodownload noremoteplayback" disablePictureInPicture autoplay>
            <source src="{stream_url}" type="video/mp4">
        </video>
    </div>
    <script>
        document.addEventListener('contextmenu', e => e.preventDefault());
        document.onkeydown = function(e) {{
            if (e.keyCode == 123 || (e.ctrlKey && e.shiftKey && e.keyCode == 73)) return false;
        }};
    </script>
</body>
</html>
"""

async def player_route(request):
    file_id = request.match_info.get("file_id")
    stream_url = f"{BASE_URL}/stream/{file_id}"
    return web.Response(text=HTML_PAGE.format(stream_url=stream_url), content_type="text/html")

async def stream_route(request):
    file_id = int(request.match_info.get("file_id"))
    conf = await get_config()
    db_ch = conf.get("db_channel")
    if not db_ch:
        return web.Response(status=404, text="DB Channel Not Configured")
    
    msg = await app.get_messages(db_ch, file_id)
    resp = web.StreamResponse(status=200, reason='OK', headers={'Content-Type': 'video/mp4', 'Content-Disposition': 'inline'})
    await resp.prepare(request)
    async for chunk in app.stream_media(msg):
        await resp.write(chunk)
    await resp.write_eof()
    return resp

# --- BOT HANDLERS ---

@app.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    user_id = message.from_user.id
    conf = await get_config()
    text = message.text.split()
    
    # 1. User DB Check
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "vip_until": 0, "verified_until": 0, "last_step": 0}
        await users_col.insert_one(user)
        await stats_col.update_one({"_id": "analytics"}, {"$inc": {"total_users": 1}}, upsert=True)

    # 2. Force Subscribe Check
    fsub = conf.get("fsub_channel")
    if fsub:
        try:
            m = await client.get_chat_member(fsub, user_id)
            if m.status in ["kicked", "left"]:
                raise Exception()
        except Exception:
            chat = await client.get_chat(fsub)
            link = chat.invite_link or conf.get("backup_link")
            return await message.reply_text(
                "⚠️ **Access Blocked!**\nPehle channel join karein fir access milega:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Channel", url=link)],
                    [InlineKeyboardButton("🔄 Check Again", url=f"https://t.me/{client.me.username}?start={text[1] if len(text) > 1 else ''}")]
                ])
            )

    # 3. Shortener Verification Route
    if len(text) > 1 and text[1].startswith("verify_"):
        token_id = text[1].replace("verify_", "")
        token_data = await tokens_col.find_one({"token": token_id, "user_id": user_id})
        
        if token_data and token_data["expires_at"] > time.time():
            hrs = conf.get("verify_time", 12)
            validity = time.time() + (hrs * 3600)
            step_done = token_data.get("step", 1)
            
            await users_col.update_one(
                {"_id": user_id},
                {"$set": {"verified_until": validity, "last_step": step_done}}
            )
            await tokens_col.delete_one({"_id": token_data["_id"]})
            await stats_col.update_one({"_id": "analytics"}, {"$inc": {"verified_count": 1}}, upsert=True)
            
            return await message.reply_text(f"✅ **Verified!** Agle {hrs} ghante ke liye VIP access unlock ho gaya.")
        else:
            return await message.reply_text("❌ Verification token expire ho gaya hai.")

    # 4. File Retrieval
    if len(text) > 1 and text[1].startswith("file_"):
        file_id = int(text[1].replace("file_", ""))
        
        is_vip = user.get("vip_until", 0) > time.time()
        is_verified = user.get("verified_until", 0) > time.time()
        
        # Lock Screen: Ad shortener required
        if not is_vip and not is_verified:
            next_step = 2 if user.get("last_step", 0) == 1 and conf.get("shortner2_url") else 1
            s_url = conf.get(f"shortner{next_step}_url")
            s_api = conf.get(f"shortner{next_step}_api")
            
            uid = str(uuid.uuid4())[:8]
            await tokens_col.insert_one({"token": uid, "user_id": user_id, "step": next_step, "expires_at": time.time() + 900})
            
            raw_url = f"https://t.me/{client.me.username}?start=verify_{uid}"
            short_link = await get_shortlink(s_url, s_api, raw_url)
            await stats_col.update_one({"_id": "analytics"}, {"$inc": {"verify_clicks": 1}}, upsert=True)
            
            return await message.reply_text(
                f"🔒 **Verification Required!**\n\nNeeche diye link se ad verify karein ({conf.get('verify_time', 12)} Hours Free Access):\n\n"
                f"Agar samajh na aaye toh 'How to Open' tutorial dekhein.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔓 Verify Access", url=short_link)],
                    [InlineKeyboardButton("▶️ How To Open (Tutorial)", url=conf.get("tutorial"))]
                ])
            )
            
        # Deliver File + Auto Attach Stream Web Player Button
        try:
            db_ch = conf.get("db_channel")
            watch_url = f"{BASE_URL}/watch/{file_id}"
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Watch Online (No Download)", url=watch_url)],
                [InlineKeyboardButton("🛡 Universal Backup", url=conf.get("backup_link"))]
            ])
            await client.copy_message(
                chat_id=user_id,
                from_chat_id=db_ch,
                message_id=file_id,
                protect_content=True,
                reply_markup=buttons
            )
            return
        except Exception as e:
            return await message.reply_text(f"❌ File deliver karne me error aayi: {e}")

    await message.reply_text("👋 Welcome to File Hub Bot! Send or click on links to get files.")

# --- ADMIN STORE FILE (WITH AUTO QUOTE & THUMBNAIL/CAPTION FETCH) ---
@app.on_message(filters.private & filters.user(ADMIN_ID) & (filters.document | filters.video | filters.audio))
async def save_admin_file(client, message):
    conf = await get_config()
    db_ch = conf.get("db_channel")
    if not db_ch:
        return await message.reply_text("❌ Pehle `/set_db <channel_id>` se database channel set karo!")
    
    # Database channel me forward
    fwd = await message.forward(db_ch)
    file_link = f"https://t.me/{client.me.username}?start=file_{fwd.id}"
    
    # Title / Caption fetch
    file_name = "Video File"
    if message.video and message.video.file_name:
        file_name = message.video.file_name
    elif message.document and message.document.file_name:
        file_name = message.document.file_name
    elif message.caption:
        file_name = message.caption.split("\n")[0]
        
    caption_text = (
        f"✅ **Saved in Database!**\n\n"
        f"📁 **Title:** `{file_name}`\n"
        f"🔗 **Link:** `{file_link}`"
    )
    
    # Message ko quote-reply karega taaki video ka thumbnail + title preview bane
    await message.reply_text(
        text=caption_text,
        quote=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Share Link", url=file_link)]
        ])
    )

# --- BROADCAST SYSTEM ---
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("broadcast"))
async def broadcast_msg(client, message):
    if not message.reply_to_message:
        return await message.reply_text("Broadcast karne ke liye kisi message ko reply karke `/broadcast` likho.")
    
    sent = 0
    failed = 0
    status = await message.reply_text("⏳ Broadcasting shuru ho gayi...")
    
    cursor = users_col.find({})
    async for u in cursor:
        try:
            await message.reply_to_message.copy(u["_id"])
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1
            
    await status.edit_text(f"📢 **Broadcast Complete!**\n\n✅ Sent: `{sent}`\n❌ Failed: `{failed}`")

# --- ANALYTICS / STATS ---
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("stats"))
async def get_stats(client, message):
    st = await stats_col.find_one({"_id": "analytics"}) or {}
    total = st.get("total_users", 0)
    clicks = st.get("verify_clicks", 0)
    done = st.get("verified_count", 0)
    c_rate = round((done / clicks * 100), 2) if clicks > 0 else 0
    
    await message.reply_text(
        f"📊 **BOT DASHBOARD STATS**\n\n"
        f"👥 Total Users: `{total}`\n"
        f"🔗 Verification Attempts: `{clicks}`\n"
        f"✅ Verified Users: `{done}`\n"
        f"📈 Success Rate: `{c_rate}%`"
    )

# --- SETTINGS COMMANDS (DYNAMIC NO-REDEPLOY) ---
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_db"))
async def s_db(c, m):
    val = int(m.text.split()[1])
    await config_col.update_one({"_id": "settings"}, {"$set": {"db_channel": val}})
    await m.reply_text(f"✅ DB Channel set to: `{val}`")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_fsub"))
async def s_fsub(c, m):
    val = int(m.text.split()[1])
    await config_col.update_one({"_id": "settings"}, {"$set": {"fsub_channel": val}})
    await m.reply_text(f"✅ F-Sub Channel set to: `{val}`")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_shortner1"))
async def s_s1(c, m):
    p = m.text.split()
    await config_col.update_one({"_id": "settings"}, {"$set": {"shortner1_url": p[1], "shortner1_api": p[2]}})
    await m.reply_text(f"✅ Shortener 1 set: `{p[1]}`")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_shortner2"))
async def s_s2(c, m):
    p = m.text.split()
    await config_col.update_one({"_id": "settings"}, {"$set": {"shortner2_url": p[1], "shortner2_api": p[2]}})
    await m.reply_text(f"✅ Shortener 2 set: `{p[1]}`")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_tutorial"))
async def s_tut(c, m):
    val = m.text.split()[1]
    await config_col.update_one({"_id": "settings"}, {"$set": {"tutorial": val}})
    await m.reply_text("✅ Tutorial link updated.")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("set_backup"))
async def s_back(c, m):
    val = m.text.split()[1]
    await config_col.update_one({"_id": "settings"}, {"$set": {"backup_link": val}})
    await m.reply_text("✅ Universal Backup link updated.")

@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command("add_paid"))
async def s_paid(c, m):
    p = m.text.split()
    uid, days = int(p[1]), int(p[2])
    exp = time.time() + (days * 86400)
    await users_col.update_one({"_id": uid}, {"$set": {"vip_until": exp}}, upsert=True)
    await m.reply_text(f"✅ User `{uid}` ko {days} din ka VIP pass de diya.")

# --- CLONE ENGINE ---
@app.on_message(filters.private & filters.command("clone"))
async def make_clone(c, m):
    p = m.text.split()
    if len(p) < 2:
        return await m.reply_text("Usage: `/clone <BOT_TOKEN>`")
    tok = p[1]
    status = await m.reply_text("⏳ Cloned bot start ho raha hai...")
    try:
        clone_app = Client(f"clone_{tok.split(':')[0]}", api_id=API_ID, api_hash=API_HASH, bot_token=tok)
        await clone_app.start()
        b_info = await clone_app.get_me()
        await status.edit_text(f"🚀 **Clone Bot Live!**\n@{b_info.username}\nMain DB aur Web player se sync ho gaya.")
    except Exception as e:
        await status.edit_text(f"❌ Clone Error: {e}")

# --- APP STARTUP ---
async def start_services():
    await app.start()
    
    webapp = web.Application()
    webapp.router.add_get("/watch/{file_id}", player_route)
    webapp.router.add_get("/stream/{file_id}", stream_route)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    print("Bot + Protected Web Player Engine Started Successfully!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(start_services())
