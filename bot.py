import os
import time
import uuid
import asyncio
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient

# --- HOSTING ENV VARIABLES ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongodb_uri")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1557553087"))
PORT = int(os.environ.get("PORT", "8080"))
BASE_URL = os.environ.get("BASE_URL", "https://file-store-bot.onrender.com")

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
            "verify_time": 12,
            "tutorial": "https://t.me",
            "backup_link": "https://t.me"
        }
        await config_col.insert_one(conf)
    return conf

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

# --- WEB PLAYER ROUTING ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Player</title>
    <style>
        body {{ margin: 0; background: #07090e; display: flex; align-items: center; justify-content: center; height: 100vh; overflow: hidden; }}
        video {{ width: 100%; max-height: 90vh; outline: none; }}
    </style>
</head>
<body oncontextmenu="return false;">
    <video controls controlsList="nodownload" autoplay>
        <source src="{stream_url}" type="video/mp4">
    </video>
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

# --- BOT COMMANDS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = message.from_user.id
    conf = await get_config()
    text = message.text.split()
    
    user = await users_col.find_one({"_id": user_id})
    if not user:
        await users_col.insert_one({"_id": user_id, "vip_until": 0, "verified_until": 0, "last_step": 0})
        await stats_col.update_one({"_id": "analytics"}, {"$inc": {"total_users": 1}}, upsert=True)

    # Retrieval
    if len(text) > 1 and text[1].startswith("file_"):
        file_id = int(text[1].replace("file_", ""))
        db_ch = conf.get("db_channel")
        if not db_ch:
            return await message.reply_text("❌ DB Channel set nahi hai.")
        watch_url = f"{BASE_URL}/watch/{file_id}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Watch Online", url=watch_url)]
        ])
        await client.copy_message(chat_id=user_id, from_chat_id=db_ch, message_id=file_id, reply_markup=buttons)
        return

    await message.reply_text("👋 Bot active hai! Admin commands ke liye `/set_db` use karein.")

@app.on_message(filters.command("set_db") & filters.private)
async def set_database(client, message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply_text(f"❌ Sirf Admin use kar sakta hai. Aapki ID: `{message.from_user.id}` | Admin ID: `{ADMIN_ID}`")
    try:
        val = int(message.text.split()[1])
        await config_col.update_one({"_id": "settings"}, {"$set": {"db_channel": val}}, upsert=True)
        await message.reply_text(f"✅ DB Channel set ho gaya: `{val}`")
    except Exception as e:
        await message.reply_text(f"Format galat hai: `/set_db -100xxxxxxxxxx`\nError: {e}")

@app.on_message(filters.private & filters.user(ADMIN_ID) & (filters.document | filters.video | filters.audio))
async def save_file(client, message):
    conf = await get_config()
    db_ch = conf.get("db_channel")
    if not db_ch:
        return await message.reply_text("❌ Pehle `/set_db -100...` se database channel set karo!")
    
    fwd = await message.forward(db_ch)
    file_link = f"https://t.me/{(await client.get_me()).username}?start=file_{fwd.id}"
    
    file_name = "Video File"
    if message.video and message.video.file_name:
        file_name = message.video.file_name
    elif message.document and message.document.file_name:
        file_name = message.document.file_name
    elif message.caption:
        file_name = message.caption.split("\n")[0]
        
    caption_text = f"✅ **Saved!**\n\n📁 **Title:** `{file_name}`\n🔗 **Link:** `{file_link}`"
    await message.reply_text(text=caption_text, quote=True)

# --- START ENGINE ---
async def main():
    await app.start()
    bot_info = await app.get_me()
    print(f"--- BOT STARTED: @{bot_info.username} ---")

    webapp = web.Application()
    webapp.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    webapp.router.add_get("/watch/{file_id}", player_route)
    webapp.router.add_get("/stream/{file_id}", stream_route)
    
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
