import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json 
import logging
import signal
import threading
import re
import sys
import atexit
import requests
from flask import Flask
from threading import Thread
import traceback
import socket
import random

# ==========================================
# 🔧 CONFIGURATION
# ==========================================
print("🐍 Starting Bot...")

# --- CONFIG ---
TOKEN = '8755893416:AAHCHTDuy-eriXx9Y9booaSupBQWnKCSKtg'
OWNER_ID = 8229233196
ADMIN_ID = 8229233196
YOUR_USERNAME = '@YOUR_USERNAME'
UPDATE_CHANNEL = 'https://t.me/YOUR_CHANNEL'

# --- Folder Setup ---
try:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
    IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
    DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')
    
    os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
    os.makedirs(IROTECH_DIR, exist_ok=True)
    print(f"✅ Folders created")
except Exception as e:
    print(f"❌ Folder creation error: {e}")
    sys.exit(1)

# --- Flask Keep-Alive ---
app = Flask('')

@app.route('/')
def home():
    return "I'm Yash File Host"

@app.route('/health')
def health():
    return {"status": "ok", "bot": "running", "scripts": len(bot_scripts)}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    try:
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        print(f"⚠️ Flask error: {e}")

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive started.")

# --- Initialize Bot ---
try:
    bot = telebot.TeleBot(TOKEN)
    print("✅ Bot initialized!")
    
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
        time.sleep(2)
    except:
        pass
        
except Exception as e:
    print(f"❌ Bot error: {e}")
    sys.exit(1)

# --- Global Variables ---
bot_scripts = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
pending_uploads = {}
pending_counter = 0
PENDING_LOCK = threading.Lock()

# --- Logging ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Cloud Backup ---
GOFILE_API = "https://api.gofile.io"

def get_gofile_server():
    try:
        resp = requests.get(f"{GOFILE_API}/getServer", timeout=10)
        if resp.status_code == 200:
            return resp.json()["data"]["server"]
    except:
        pass
    return "store1.gofile.io"

def backup_file_to_cloud(local_path, user_id, file_name):
    try:
        server = get_gofile_server()
        url = f"https://{server}/uploadFile"
        with open(local_path, "rb") as f:
            files = {"file": (file_name, f)}
            resp = requests.post(url, files=files, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data["status"] == "ok":
                return data["data"]["downloadPage"]
    except:
        pass
    return None

# --- Database ---
def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT, cloud_url TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_uploads
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      file_name TEXT,
                      file_type TEXT,
                      file_path TEXT,
                      cloud_url TEXT,
                      timestamp TEXT,
                      status TEXT DEFAULT 'pending',
                      chat_id INTEGER,
                      message_id INTEGER,
                      temp_dir TEXT,
                      zip_path TEXT,
                      single_file TEXT,
                      upload_chat_id INTEGER)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"DB error: {e}")

# --- Helper Functions ---
def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except:
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
    return False

def kill_process_tree(process_info):
    try:
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try:
                            child.terminate()
                        except:
                            try:
                                child.kill()
                            except:
                                pass
                    try:
                        parent.terminate()
                    except:
                        try:
                            parent.kill()
                        except:
                            pass
                except:
                    pass
    except:
        pass

# --- Menu Creation ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload', callback_data='upload'),
        types.InlineKeyboardButton('📂 Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Speed', callback_data='speed'),
        types.InlineKeyboardButton('📞 Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('📊 Stats', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock' if not bot_locked else '🔓 Unlock',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All', callback_data='run_all_scripts'),
            types.InlineKeyboardButton('📥 Pending', callback_data='pending_uploads')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[2])
        markup.add(admin_buttons[3], admin_buttons[4])
        markup.add(admin_buttons[5], buttons[4])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(types.InlineKeyboardButton('📊 Stats', callback_data='stats'))
        markup.add(buttons[4])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id in admin_ids:
        layout = [
            ["📢 Updates"],
            ["📤 Upload", "📂 Files"],
            ["⚡ Speed", "📊 Stats"],
            ["📢 Broadcast"],
            ["🔒 Lock", "🟢 Run All"],
            ["👑 Admin", "📥 Pending"],
            ["📞 Owner"]
        ]
    else:
        layout = [
            ["📢 Updates"],
            ["📤 Upload", "📂 Files"],
            ["⚡ Speed", "📊 Stats"],
            ["📞 Owner"]
        ]
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'))
    else:
        markup.row(types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'))
    markup.row(
        types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
        types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

# --- Database Operations ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py', cloud_url=None):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, cloud_url) VALUES (?, ?, ?, ?)',
                      (user_id, file_name, file_type, cloud_url))
            conn.commit()
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id] = [(fn, ft, cu) for (fn, ft, cu) in user_files.get(user_id, []) if fn != file_name]
            user_files[user_id].append((file_name, file_type, cloud_url))
        except Exception as e:
            logger.error(f"Save error: {e}")
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]:
                    del user_files[user_id]
        except:
            pass
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except:
            pass
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except:
            pass
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            if c.rowcount > 0:
                admin_ids.discard(admin_id)
                return True
            return False
        except:
            return False
        finally:
            conn.close()

def save_pending_upload(user_id, file_name, file_type, file_path, cloud_url, chat_id, message_id, temp_dir=None, zip_path=None, single_file=None):
    global pending_counter
    with PENDING_LOCK:
        pending_counter += 1
        pending_id = pending_counter
        timestamp = datetime.now().isoformat()
        
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('''INSERT INTO pending_uploads 
                         (id, user_id, file_name, file_type, file_path, cloud_url, timestamp, status, chat_id, message_id, temp_dir, zip_path, single_file)
                         VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)''',
                      (pending_id, user_id, file_name, file_type, file_path, cloud_url, timestamp, chat_id, message_id, temp_dir, zip_path, single_file))
            conn.commit()
            conn.close()
            
            pending_uploads[pending_id] = {
                'user_id': user_id,
                'file_name': file_name,
                'file_type': file_type,
                'file_path': file_path,
                'cloud_url': cloud_url,
                'timestamp': timestamp,
                'chat_id': chat_id,
                'message_id': message_id,
                'status': 'pending',
                'temp_dir': temp_dir,
                'zip_path': zip_path,
                'single_file': single_file,
                'upload_chat_id': chat_id
            }
            return pending_id
        except Exception as e:
            logger.error(f"Save pending error: {e}")
            return None

def update_pending_status(pending_id, status):
    with PENDING_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE pending_uploads SET status = ? WHERE id = ?', (status, pending_id))
            conn.commit()
            conn.close()
            if pending_id in pending_uploads:
                pending_uploads[pending_id]['status'] = status
            return True
        except:
            return False

def delete_pending_upload(pending_id):
    with PENDING_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM pending_uploads WHERE id = ?', (pending_id,))
            conn.commit()
            conn.close()
            if pending_id in pending_uploads:
                del pending_uploads[pending_id]
            return True
        except:
            return False

def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        c.execute('SELECT user_id, file_name, file_type, cloud_url FROM user_files')
        for user_id, file_name, file_type, cloud_url in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type, cloud_url))

        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())

        conn.close()
        logger.info(f"Loaded: {len(active_users)} users, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"Load data error: {e}")

# --- Package Installation ---
def attempt_install_pip(module_name, message):
    try:
        bot.reply_to(message, f"📦 Installing `{module_name}`...", parse_mode='Markdown')
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', module_name], 
                              capture_output=True, text=True, check=False)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Installed `{module_name}`", parse_mode='Markdown')
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install `{module_name}`", parse_mode='Markdown')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    script_key = f"{script_owner_id}_{file_name}"
    
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script not found!")
            remove_user_file_db(script_owner_id, file_name)
            return

        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        
        process = subprocess.Popen(
            [sys.executable, script_path], 
            cwd=user_folder, 
            stdout=log_file, 
            stderr=log_file,
            stdin=subprocess.PIPE,
            encoding='utf-8', 
            errors='ignore'
        )
        
        bot_scripts[script_key] = {
            'process': process, 
            'log_file': log_file, 
            'file_name': file_name,
            'script_owner_id': script_owner_id,
            'script_key': script_key
        }
        
        bot.reply_to(message_obj, f"✅ Started `{file_name}` (PID: {process.pid})", parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message_obj, f"❌ Error: {str(e)}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    script_key = f"{script_owner_id}_{file_name}"
    
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script not found!")
            remove_user_file_db(script_owner_id, file_name)
            return

        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        
        process = subprocess.Popen(
            ['node', script_path], 
            cwd=user_folder, 
            stdout=log_file, 
            stderr=log_file,
            stdin=subprocess.PIPE,
            encoding='utf-8', 
            errors='ignore'
        )
        
        bot_scripts[script_key] = {
            'process': process, 
            'log_file': log_file, 
            'file_name': file_name,
            'script_owner_id': script_owner_id,
            'script_key': script_key
        }
        
        bot.reply_to(message_obj, f"✅ Started JS `{file_name}` (PID: {process.pid})", parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message_obj, f"❌ Error: {str(e)}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# ==========================================
# ✅ LOCK/UNLOCK FUNCTIONS
# ==========================================

def set_bot_locked(value):
    global bot_locked
    bot_locked = value

# ==========================================
# ✅ FILE UPLOAD HANDLER
# ==========================================
@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked.")
        return
    
    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, "⚠️ No file name.")
        return
    
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only `.py`, `.js`, `.zip` allowed.")
        return
    
    max_size = 20 * 1024 * 1024
    if doc.file_size > max_size:
        bot.reply_to(message, f"⚠️ Max {max_size//1024//1024} MB.")
        return
    
    try:
        bot.forward_message(OWNER_ID, chat_id, message.message_id)
        bot.send_message(OWNER_ID, 
            f"📥 **New Upload**\n👤 {message.from_user.first_name}\n🆔 `{user_id}`\n📁 `{file_name}`",
            parse_mode='Markdown'
        )
    except:
        pass
    
    download_msg = bot.reply_to(message, f"⏳ Downloading...")
    file_info = bot.get_file(doc.file_id)
    content = bot.download_file(file_info.file_path)
    bot.edit_message_text(f"✅ Downloaded. Waiting for approval...", chat_id, download_msg.message_id)
    
    user_folder = get_user_folder(user_id)
    temp_path = os.path.join(user_folder, f"_pending_{file_name}")
    with open(temp_path, 'wb') as f:
        f.write(content)
    
    main_name = file_name
    main_type = file_ext[1:]
    actual_path = temp_path
    temp_dir = None
    zip_path = temp_path
    single_file = None
    req_path = None
    has_req = False
    
    if file_ext == '.zip':
        try:
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
            with zipfile.ZipFile(temp_path, 'r') as z:
                z.extractall(temp_dir)
            
            items = os.listdir(temp_dir)
            py_files = [f for f in items if f.endswith('.py')]
            js_files = [f for f in items if f.endswith('.js')]
            
            if 'requirements.txt' in items:
                req_path = os.path.join(temp_dir, 'requirements.txt')
                has_req = True
            
            preferred = ['main.py', 'bot.py', 'app.py', 'run.py', 'start.py', 'index.py']
            main_name = None
            for p in preferred:
                if p in py_files:
                    main_name = p
                    main_type = 'py'
                    break
            if not main_name and py_files:
                main_name = py_files[0]
                main_type = 'py'
            elif not main_name and js_files:
                main_name = js_files[0]
                main_type = 'js'
            
            if main_name:
                actual_path = os.path.join(temp_dir, main_name)
            else:
                bot.reply_to(message, f"❌ No script found in zip!")
                shutil.rmtree(temp_dir, ignore_errors=True)
                os.remove(temp_path)
                return
                
        except Exception as e:
            bot.reply_to(message, f"❌ Zip error: {str(e)}")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            os.remove(temp_path)
            return
    else:
        single_file = temp_path
    
    pending_id = save_pending_upload(
        user_id, main_name, main_type, actual_path,
        None, chat_id, download_msg.message_id,
        temp_dir, zip_path, single_file
    )
    
    if not pending_id:
        bot.reply_to(message, "❌ Error saving.")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.remove(temp_path)
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user_id}_{main_name}_{pending_id}'),
        types.InlineKeyboardButton("❌ Reject", callback_data=f'reject_{user_id}_{main_name}_{pending_id}')
    )
    if has_req and req_path:
        markup.add(types.InlineKeyboardButton("📦 Install", callback_data=f'install_pkg_{pending_id}'))
    
    bot.send_message(
        OWNER_ID,
        f"📥 **Approve Upload**\n👤 User: `{user_id}`\n📁 File: `{main_name}`\n🆔 ID: `{pending_id}`",
        parse_mode='Markdown',
        reply_markup=markup
    )
    
    pending_uploads[pending_id]['upload_chat_id'] = chat_id
    pending_uploads[pending_id]['req_file_path'] = req_path if has_req else None
    pending_uploads[pending_id]['user_folder'] = user_folder
    
    bot.send_message(chat_id, f"📤 `{main_name}` sent for approval.\n⏳ Please wait.", parse_mode='Markdown')

# ==========================================
# ✅ ALL CALLBACK HANDLERS
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_all_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    
    try:
        # --- Upload ---
        if data == 'upload':
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, 
                "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\n\n⚠️ **File will be sent for admin approval first!**",
                parse_mode='Markdown')
        
        # --- Check Files ---
        elif data == 'check_files':
            user_files_list = user_files.get(user_id, [])
            if not user_files_list:
                bot.answer_callback_query(call.id, "⚠️ No files.", show_alert=True)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
                bot.edit_message_text("📂 No files.", call.message.chat.id, call.message.message_id, reply_markup=markup)
                return
            
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup(row_width=1)
            for file_name, file_type, _ in sorted(user_files_list):
                running = is_bot_running(user_id, file_name)
                markup.add(types.InlineKeyboardButton(
                    f"{file_name} ({file_type}) - {'🟢' if running else '🔴'}",
                    callback_data=f'file_{user_id}_{file_name}'
                ))
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
            bot.edit_message_text("📂 Your files:", call.message.chat.id, call.message.message_id, reply_markup=markup)
        
        # --- Pending Uploads ---
        elif data == 'pending_uploads':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            pending_list = [p for p in pending_uploads.values() if p['status'] == 'pending']
            if not pending_list:
                bot.send_message(call.message.chat.id, "📭 No pending.")
                return
            
            msg = "📥 **Pending:**\n\n"
            for p in pending_list[:10]:
                msg += f"🆔 `{p['user_id']}`\n📁 `{p['file_name']}`\n─" * 10 + "\n"
            bot.send_message(call.message.chat.id, msg, parse_mode='Markdown')
        
        # --- Approve ---
        elif data.startswith('approve_'):
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            parts = data.split('_', 3)
            if len(parts) != 4:
                bot.answer_callback_query(call.id, "❌ Invalid.", show_alert=True)
                return
            
            target_user_id = int(parts[1])
            file_name = parts[2]
            pending_id = int(parts[3])
            
            if pending_id not in pending_uploads:
                bot.answer_callback_query(call.id, "❌ Not found.", show_alert=True)
                return
            
            pending_data = pending_uploads[pending_id]
            if pending_data['status'] != 'pending':
                bot.answer_callback_query(call.id, f"❌ Already {pending_data['status']}.", show_alert=True)
                return
            
            user_folder = get_user_folder(target_user_id)
            
            try:
                file_path = pending_data['file_path']
                file_type = pending_data['file_type']
                file_name = pending_data['file_name']
                
                if pending_data.get('temp_dir') and os.path.exists(pending_data['temp_dir']):
                    temp_dir = pending_data['temp_dir']
                    for item in os.listdir(temp_dir):
                        src = os.path.join(temp_dir, item)
                        dest = os.path.join(user_folder, item)
                        if os.path.exists(dest):
                            if os.path.isdir(dest):
                                shutil.rmtree(dest)
                            else:
                                os.remove(dest)
                        shutil.move(src, dest)
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    if pending_data.get('zip_path') and os.path.exists(pending_data['zip_path']):
                        os.remove(pending_data['zip_path'])
                elif pending_data.get('single_file') and os.path.exists(pending_data['single_file']):
                    src = pending_data['single_file']
                    dest = os.path.join(user_folder, file_name)
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(src, dest)
                    file_path = dest
                
                cloud_url = backup_file_to_cloud(file_path, target_user_id, file_name)
                save_user_file(target_user_id, file_name, file_type, cloud_url)
                
                bot.send_message(
                    pending_data['upload_chat_id'],
                    f"✅ **File Approved & Starting!**\n📁 `{file_name}`\n⏳ Starting automatically...",
                    parse_mode='Markdown'
                )
                
                try:
                    if file_type == 'py':
                        threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                    elif file_type == 'js':
                        threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                    
                    time.sleep(2)
                    is_running = is_bot_running(target_user_id, file_name)
                    status = "✅ Running" if is_running else "⚠️ Check logs"
                    bot.send_message(pending_data['upload_chat_id'], f"📊 **Status:** {status}", parse_mode='Markdown')
                except Exception as e:
                    bot.send_message(pending_data['upload_chat_id'], f"⚠️ Auto-start failed: `{str(e)}`", parse_mode='Markdown')
                
                bot.edit_message_text(
                    f"✅ **Approved & Started**\n👤 User: `{target_user_id}`\n📁 File: `{file_name}`",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown'
                )
                
                update_pending_status(pending_id, 'approved')
                bot.answer_callback_query(call.id, "✅ Approved and started!")
                
            except Exception as e:
                logger.error(f"Approve error: {e}")
                bot.answer_callback_query(call.id, f"❌ Error", show_alert=True)
        
        # --- Reject ---
        elif data.startswith('reject_'):
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            parts = data.split('_', 3)
            if len(parts) != 4:
                bot.answer_callback_query(call.id, "❌ Invalid.", show_alert=True)
                return
            
            target_user_id = int(parts[1])
            file_name = parts[2]
            pending_id = int(parts[3])
            
            if pending_id not in pending_uploads:
                bot.answer_callback_query(call.id, "❌ Not found.", show_alert=True)
                return
            
            pending_data = pending_uploads[pending_id]
            if pending_data['status'] != 'pending':
                bot.answer_callback_query(call.id, f"❌ Already {pending_data['status']}.", show_alert=True)
                return
            
            for path in [pending_data.get('temp_dir'), pending_data.get('zip_path'), pending_data.get('single_file')]:
                if path and os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                    except:
                        pass
            
            update_pending_status(pending_id, 'rejected')
            bot.send_message(pending_data['upload_chat_id'], f"❌ **File Rejected**\n📁 `{file_name}`", parse_mode='Markdown')
            bot.edit_message_text(
                f"❌ **Rejected**\n👤 User: `{target_user_id}`\n📁 File: `{file_name}`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
            delete_pending_upload(pending_id)
            bot.answer_callback_query(call.id, "❌ Rejected.")
        
        # --- File Control ---
        elif data.startswith('file_'):
            try:
                _, script_owner_id_str, file_name = data.split('_', 2)
                script_owner_id = int(script_owner_id_str)
                
                if not (user_id == script_owner_id or user_id in admin_ids):
                    bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                    return
                
                user_files_list = user_files.get(script_owner_id, [])
                if not any(f[0] == file_name for f in user_files_list):
                    bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True)
                    return
                
                bot.answer_callback_query(call.id)
                is_running = is_bot_running(script_owner_id, file_name)
                status_text = '🟢 Running' if is_running else '🔴 Stopped'
                file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?')
                
                bot.edit_message_text(
                    f"⚙️ Controls: `{file_name}` ({file_type})\nStatus: {status_text}",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
                    parse_mode='Markdown'
                )
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Start ---
        elif data.startswith('start_'):
            try:
                _, script_owner_id_str, file_name = data.split('_', 2)
                script_owner_id = int(script_owner_id_str)
                
                if not (user_id == script_owner_id or user_id in admin_ids):
                    bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
                    return
                
                user_files_list = user_files.get(script_owner_id, [])
                file_info = next((f for f in user_files_list if f[0] == file_name), None)
                if not file_info:
                    bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True)
                    return
                
                if is_bot_running(script_owner_id, file_name):
                    bot.answer_callback_query(call.id, "⚠️ Already running.", show_alert=True)
                    return
                
                file_type = file_info[1]
                user_folder = get_user_folder(script_owner_id)
                file_path = os.path.join(user_folder, file_name)
                
                if not os.path.exists(file_path):
                    bot.answer_callback_query(call.id, f"⚠️ File missing!", show_alert=True)
                    return
                
                bot.answer_callback_query(call.id, f"⏳ Starting...")
                
                if file_type == 'py':
                    threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
                else:
                    threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
                
                time.sleep(1.5)
                is_now_running = is_bot_running(script_owner_id, file_name)
                status_text = '🟢 Running' if is_now_running else '🟡 Starting'
                
                bot.edit_message_text(
                    f"⚙️ Controls: `{file_name}`\nStatus: {status_text}",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running),
                    parse_mode='Markdown'
                )
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Stop ---
        elif data.startswith('stop_'):
            try:
                _, script_owner_id_str, file_name = data.split('_', 2)
                script_owner_id = int(script_owner_id_str)
                
                if not (user_id == script_owner_id or user_id in admin_ids):
                    bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
                    return
                
                if not is_bot_running(script_owner_id, file_name):
                    bot.answer_callback_query(call.id, "⚠️ Already stopped.", show_alert=True)
                    return
                
                bot.answer_callback_query(call.id, f"⏳ Stopping...")
                script_key = f"{script_owner_id}_{file_name}"
                process_info = bot_scripts.get(script_key)
                
                if process_info:
                    kill_process_tree(process_info)
                    if script_key in bot_scripts:
                        del bot_scripts[script_key]
                
                time.sleep(0.5)
                bot.edit_message_text(
                    f"⚙️ Controls: `{file_name}`\nStatus: 🔴 Stopped",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_control_buttons(script_owner_id, file_name, False),
                    parse_mode='Markdown'
                )
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Delete ---
        elif data.startswith('delete_'):
            try:
                _, script_owner_id_str, file_name = data.split('_', 2)
                script_owner_id = int(script_owner_id_str)
                
                if not (user_id == script_owner_id or user_id in admin_ids):
                    bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
                    return
                
                bot.answer_callback_query(call.id, f"🗑️ Deleting...")
                
                if is_bot_running(script_owner_id, file_name):
                    script_key = f"{script_owner_id}_{file_name}"
                    process_info = bot_scripts.get(script_key)
                    if process_info:
                        kill_process_tree(process_info)
                        del bot_scripts[script_key]
                    time.sleep(0.5)
                
                user_folder = get_user_folder(script_owner_id)
                for f in [file_name, f"{os.path.splitext(file_name)[0]}.log"]:
                    path = os.path.join(user_folder, f)
                    if os.path.exists(path):
                        os.remove(path)
                
                remove_user_file_db(script_owner_id, file_name)
                bot.edit_message_text(
                    f"🗑️ File `{file_name}` deleted!",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                    parse_mode='Markdown'
                )
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Logs ---
        elif data.startswith('logs_'):
            try:
                _, script_owner_id_str, file_name = data.split('_', 2)
                script_owner_id = int(script_owner_id_str)
                
                if not (user_id == script_owner_id or user_id in admin_ids):
                    bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
                    return
                
                user_folder = get_user_folder(script_owner_id)
                log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
                
                if not os.path.exists(log_path):
                    bot.answer_callback_query(call.id, f"⚠️ No logs.", show_alert=True)
                    return
                
                bot.answer_callback_query(call.id)
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                
                if not log_content.strip():
                    log_content = "(Empty)"
                if len(log_content) > 4096:
                    log_content = "...\n" + log_content[-4096:]
                
                bot.send_message(call.message.chat.id, f"📜 Logs:\n```\n{log_content}\n```", parse_mode='Markdown')
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Speed ---
        elif data == 'speed':
            start = time.time()
            try:
                bot.edit_message_text("🏃 Testing...", call.message.chat.id, call.message.message_id)
                bot.send_chat_action(call.message.chat.id, 'typing')
                response = round((time.time() - start) * 1000, 2)
                status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
                level = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else "🆓 User")
                
                bot.answer_callback_query(call.id)
                bot.edit_message_text(
                    f"⚡ {response} ms\n🚦 {status}\n👤 {level}",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=create_main_menu_inline(user_id)
                )
            except Exception as e:
                bot.answer_callback_query(call.id, "Error.", show_alert=True)
        
        # --- Stats ---
        elif data == 'stats':
            bot.answer_callback_query(call.id)
            msg = f"📊 Stats:\n👥 {len(active_users)}\n📂 {sum(len(f) for f in user_files.values())}\n📥 {len([p for p in pending_uploads.values() if p['status'] == 'pending'])}\n🟢 {len(bot_scripts)}"
            if user_id in admin_ids:
                msg += f"\n🔒 {'🔴 Locked' if bot_locked else '🟢 Unlocked'}"
            
            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
        
        # --- Back to Main ---
        elif data == 'back_to_main':
            bot.answer_callback_query(call.id)
            status = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else "🆓 User")
            bot.edit_message_text(
                f"〽️ Welcome back!\n🆔 `{user_id}`\n🔰 {status}\n📁 {get_user_file_count(user_id)} files",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=create_main_menu_inline(user_id),
                parse_mode='Markdown'
            )
        
        # --- Lock/Unlock ---
        elif data == 'lock_bot':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            set_bot_locked(True)
            bot.answer_callback_query(call.id, "🔒 Locked.")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
        
        elif data == 'unlock_bot':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            set_bot_locked(False)
            bot.answer_callback_query(call.id, "🔓 Unlocked.")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
        
        # --- Run All ---
        elif data == 'run_all_scripts':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id, "⏳ Starting all...")
            started = 0
            
            for target_user_id, files in list(user_files.items()):
                if not files:
                    continue
                user_folder = get_user_folder(target_user_id)
                for file_name, file_type, _ in files:
                    if not is_bot_running(target_user_id, file_name):
                        file_path = os.path.join(user_folder, file_name)
                        if os.path.exists(file_path):
                            try:
                                if file_type == 'py':
                                    threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                                else:
                                    threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                                started += 1
                                time.sleep(0.7)
                            except:
                                pass
            
            bot.send_message(call.message.chat.id, f"✅ Started {started} scripts.")
        
        # --- Broadcast ---
        elif data == 'broadcast':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            msg = bot.send_message(call.message.chat.id, "📢 Send message.\n/cancel to abort.")
            bot.register_next_step_handler(msg, process_broadcast_message)
        
        # --- Admin Panel ---
        elif data == 'admin_panel':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            bot.edit_message_text("👑 Admin Panel", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
        
        # --- Add Admin ---
        elif data == 'add_admin':
            if user_id != OWNER_ID:
                bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            msg = bot.send_message(call.message.chat.id, "👑 Enter User ID.\n/cancel to abort.")
            bot.register_next_step_handler(msg, process_add_admin_id)
        
        # --- Remove Admin ---
        elif data == 'remove_admin':
            if user_id != OWNER_ID:
                bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            msg = bot.send_message(call.message.chat.id, "👑 Enter Admin ID.\n/cancel to abort.")
            bot.register_next_step_handler(msg, process_remove_admin_id)
        
        # --- List Admins ---
        elif data == 'list_admins':
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            bot.answer_callback_query(call.id)
            admin_list = "\n".join(f"- `{a}` {'(Owner)' if a == OWNER_ID else ''}" for a in sorted(admin_ids))
            bot.edit_message_text(f"👑 Admins:\n\n{admin_list}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
        
        # --- Install Packages ---
        elif data.startswith('install_pkg_'):
            if user_id not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
                return
            
            try:
                pending_id = int(data.split('_')[2])
                if pending_id not in pending_uploads:
                    bot.answer_callback_query(call.id, "❌ Not found.", show_alert=True)
                    return
                
                pending_data = pending_uploads[pending_id]
                req_file_path = pending_data.get('req_file_path')
                
                bot.answer_callback_query(call.id, "📦 Installing...")
                status_msg = bot.send_message(call.message.chat.id, "📦 Installing...", parse_mode='Markdown')
                
                packages = []
                if req_file_path and os.path.exists(req_file_path):
                    with open(req_file_path, 'r') as f:
                        packages = [line.strip() for line in f.readlines() if line.strip() and not line.startswith('#')]
                
                if not packages:
                    packages = ['requests', 'flask', 'psutil']
                
                installed, failed = [], []
                for i, pkg in enumerate(packages):
                    bot.edit_message_text(f"📦 {i+1}/{len(packages)}: `{pkg}`", call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
                    result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
                    if result.returncode == 0:
                        installed.append(pkg)
                    else:
                        failed.append(pkg)
                    time.sleep(0.3)
                
                bot.edit_message_text(f"✅ Installed: {len(installed)}\n❌ Failed: {len(failed)}", call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
                
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Error", show_alert=True)
        
        else:
            bot.answer_callback_query(call.id, "❓ Unknown.")
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            bot.answer_callback_query(call.id, "❌ Error", show_alert=True)
        except:
            pass

# ==========================================
# ✅ COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def command_start(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked.")
        return
    
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"🎉 New user!\n👤 {message.from_user.first_name}\n🆔 `{user_id}`", parse_mode='Markdown')
        except:
            pass
    
    status = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else "🆓 User")
    bot.send_message(
        chat_id,
        f"〽️ Welcome {message.from_user.first_name}!\n🆔 `{user_id}`\n🔰 {status}\n📁 {get_user_file_count(user_id)} files\n\n⚠️ Uploads need approval!\n👇 Use buttons.",
        reply_markup=create_reply_keyboard_main_menu(user_id),
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['install'])
def command_install(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not user_files.get(user_id):
        bot.reply_to(message, "❌ No files.")
        return
    
    user_folder = get_user_folder(user_id)
    req_path = os.path.join(user_folder, 'requirements.txt')
    
    if os.path.exists(req_path):
        install_packages(message, user_folder, req_path)
    else:
        install_packages(message, user_folder, None)

def install_packages(message, user_folder, req_path=None):
    chat_id = message.chat.id
    status_msg = bot.send_message(chat_id, "📦 Installing...", parse_mode='Markdown')
    
    packages = []
    if req_path and os.path.exists(req_path):
        with open(req_path, 'r') as f:
            packages = [line.strip() for line in f.readlines() if line.strip() and not line.startswith('#')]
    
    if not packages:
        packages = ['requests', 'flask', 'psutil']
    
    installed, failed = [], []
    for i, pkg in enumerate(packages):
        bot.edit_message_text(f"📦 {i+1}/{len(packages)}: `{pkg}`", chat_id, status_msg.message_id, parse_mode='Markdown')
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
        if result.returncode == 0:
            installed.append(pkg)
        else:
            failed.append(pkg)
        time.sleep(0.3)
    
    bot.edit_message_text(f"✅ Installed: {len(installed)}\n❌ Failed: {len(failed)}", chat_id, status_msg.message_id, parse_mode='Markdown')

@bot.message_handler(commands=['pending'])
def command_pending(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    
    pending = [p for p in pending_uploads.values() if p['status'] == 'pending']
    if not pending:
        bot.reply_to(message, "📭 No pending.")
        return
    
    msg = "📥 **Pending:**\n\n"
    for p in pending[:10]:
        msg += f"🆔 `{p['user_id']}`\n📁 `{p['file_name']}`\n─" * 10 + "\n"
    bot.reply_to(message, msg, parse_mode='Markdown')

# ==========================================
# ✅ REPLY KEYBOARD HANDLERS
# ==========================================

@bot.message_handler(func=lambda m: m.text == '📤 Upload')
def reply_upload(m):
    bot.reply_to(m, "📤 Send your `.py`, `.js`, or `.zip` file.\n⚠️ Needs admin approval!")

@bot.message_handler(func=lambda m: m.text == '📂 Files')
def reply_check(m):
    user_id = m.from_user.id
    files = user_files.get(user_id, [])
    
    if not files:
        bot.reply_to(m, "📂 No files.")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for name, ftype, _ in sorted(files):
        running = is_bot_running(user_id, name)
        markup.add(types.InlineKeyboardButton(
            f"{name} ({ftype}) - {'🟢' if running else '🔴'}",
            callback_data=f'file_{user_id}_{name}'
        ))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    bot.send_message(m.chat.id, "📂 Your files:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '⚡ Speed')
def reply_speed(m):
    start = time.time()
    bot.send_chat_action(m.chat.id, 'typing')
    response = round((time.time() - start) * 1000, 2)
    bot.reply_to(m, f"⚡ {response} ms\n🔓 {'Unlocked' if not bot_locked else 'Locked'}")

@bot.message_handler(func=lambda m: m.text == '📊 Stats')
def reply_stats(m):
    user_id = m.from_user.id
    msg = f"📊 Stats:\n👥 {len(active_users)}\n📂 {sum(len(f) for f in user_files.values())}\n📥 {len([p for p in pending_uploads.values() if p['status'] == 'pending'])}\n🟢 {len(bot_scripts)}"
    if user_id in admin_ids:
        msg += f"\n🔒 {'🔴 Locked' if bot_locked else '🟢 Unlocked'}"
    bot.reply_to(m, msg)

@bot.message_handler(func=lambda m: m.text == '📢 Broadcast')
def reply_broadcast(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "⚠️ Admin only.")
        return
    msg = bot.reply_to(m, "📢 Send message.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

@bot.message_handler(func=lambda m: m.text == '🔒 Lock')
def reply_lock(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "⚠️ Admin only.")
        return
    set_bot_locked(True)
    bot.reply_to(m, "🔒 Locked.")

@bot.message_handler(func=lambda m: m.text == '🟢 Run All')
def reply_run_all(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "⚠️ Admin only.")
        return
    bot.reply_to(m, "⏳ Starting all...")
    started = 0
    for uid, files in list(user_files.items()):
        if not files:
            continue
        folder = get_user_folder(uid)
        for name, ftype, _ in files:
            if not is_bot_running(uid, name):
                path = os.path.join(folder, name)
                if os.path.exists(path):
                    try:
                        if ftype == 'py':
                            threading.Thread(target=run_script, args=(path, uid, folder, name, m)).start()
                        else:
                            threading.Thread(target=run_js_script, args=(path, uid, folder, name, m)).start()
                        started += 1
                        time.sleep(0.7)
                    except:
                        pass
    bot.send_message(m.chat.id, f"✅ Started {started} scripts.")

@bot.message_handler(func=lambda m: m.text == '👑 Admin')
def reply_admin_panel(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "⚠️ Admin only.")
        return
    bot.reply_to(m, "👑 Admin Panel", reply_markup=create_admin_panel())

@bot.message_handler(func=lambda m: m.text == '📥 Pending')
def reply_pending(m):
    if m.from_user.id not in admin_ids:
        bot.reply_to(m, "⚠️ Admin only.")
        return
    pending = [p for p in pending_uploads.values() if p['status'] == 'pending']
    if not pending:
        bot.reply_to(m, "📭 No pending.")
        return
    msg = "📥 **Pending:**\n\n"
    for p in pending[:10]:
        msg += f"🆔 `{p['user_id']}`\n📁 `{p['file_name']}`\n─" * 10 + "\n"
    bot.reply_to(m, msg, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '📢 Updates')
def reply_updates(m):
    bot.reply_to(m, f"📢 {UPDATE_CHANNEL}")

@bot.message_handler(func=lambda m: m.text == '📞 Owner')
def reply_contact(m):
    bot.reply_to(m, f"📞 https://t.me/{YOUR_USERNAME.replace('@', '')}")

# ==========================================
# ✅ BROADCAST HELPERS
# ==========================================

def process_broadcast_message(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    
    content = message.text
    if not content:
        bot.reply_to(message, "⚠️ Empty.")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    
    bot.reply_to(
        message,
        f"⚠️ Send to {len(active_users)} users?\n```\n{content[:500]}\n```",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_broadcast_'))
def handle_confirm_broadcast(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    try:
        original = call.message.reply_to_message
        if not original or not original.text:
            raise ValueError("No message")
        
        text = original.text
        bot.answer_callback_query(call.id, "🚀 Broadcasting...")
        bot.edit_message_text(f"📢 Sending to {len(active_users)} users...", call.message.chat.id, call.message.message_id)
        
        threading.Thread(target=execute_broadcast, args=(text, call.message.chat.id)).start()
        
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        bot.edit_message_text("❌ Error.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_broadcast')
def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

def execute_broadcast(text, admin_chat_id):
    sent, failed, blocked = 0, 0, 0
    users = list(active_users)
    
    for i, uid in enumerate(users):
        try:
            bot.send_message(uid, text, parse_mode='Markdown')
            sent += 1
        except Exception as e:
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                blocked += 1
            else:
                failed += 1
        
        if (i + 1) % 25 == 0 and i < len(users) - 1:
            time.sleep(1.5)
    
    bot.send_message(admin_chat_id, f"📢 Done!\n✅ {sent}\n❌ {failed}\n🚫 {blocked}")

# ==========================================
# ✅ ADMIN HELPERS
# ==========================================

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    
    try:
        new_id = int(message.text.strip())
        if new_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Already Owner.")
            return
        if new_id in admin_ids:
            bot.reply_to(message, "⚠️ Already Admin.")
            return
        
        add_admin_db(new_id)
        bot.reply_to(message, f"✅ Admin `{new_id}` added.")
        try:
            bot.send_message(new_id, "🎉 You are now Admin!")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    
    try:
        admin_id = int(message.text.strip())
        if admin_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Cannot remove Owner.")
            return
        if admin_id not in admin_ids:
            bot.reply_to(message, "⚠️ Not Admin.")
            return
        
        if remove_admin_db(admin_id):
            bot.reply_to(message, f"✅ Admin `{admin_id}` removed.")
            try:
                bot.send_message(admin_id, "ℹ️ You are no longer Admin.")
            except:
                pass
        else:
            bot.reply_to(message, "❌ Failed.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

# ==========================================
# ✅ CLEANUP
# ==========================================

def cleanup():
    logger.warning("Shutting down...")
    for key in list(bot_scripts.keys()):
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
atexit.register(cleanup)

# ==========================================
# ✅ MAIN
# ==========================================

if __name__ == '__main__':
    print("="*50)
    print("🤖 Starting Bot...")
    print(f"📁 Base: {BASE_DIR}")
    print(f"🔑 Owner: {OWNER_ID}")
    print("="*50)
    
    try:
        init_db()
        load_data()
        keep_alive()
        
        print("✅ Bot Ready!")
        print("Press Ctrl+C to stop.")
        
        # ✅ Simple polling with error handling
        while True:
            try:
                bot.polling(none_stop=True, interval=0, timeout=60)
            except Exception as e:
                print(f"⚠️ Polling error: {e}")
                time.sleep(5)
                
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()