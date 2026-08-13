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

# ==========================================
# 🔧 CONFIGURATION
# ==========================================
print("🐍 Starting Bot...")

# --- CONFIG (Change these) ---
TOKEN = '8965299977:AAEBvjzDcbUP9QTgf0EcCLzT3DVu_hqHPWg'  # CHANGE THIS
OWNER_ID = 8562486480  # CHANGE THIS
ADMIN_ID = 8562486480  # CHANGE THIS
YOUR_USERNAME = '@YOUR_USERNAME'  # CHANGE THIS
UPDATE_CHANNEL = 'https://t.me/YOUR_CHANNEL'  # CHANGE THIS

# --- Folder Setup ---
try:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
    IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
    DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')
    
    os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
    os.makedirs(IROTECH_DIR, exist_ok=True)
    print(f"✅ Folders created: {UPLOAD_BOTS_DIR}, {IROTECH_DIR}")
except Exception as e:
    print(f"❌ Folder creation error: {e}")
    sys.exit(1)

# --- Flask Keep-Alive ---
try:
    app = Flask('')
    
    @app.route('/')
    def home():
        return "I'm Yash File Host"
    
    def find_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', 0))
            s.listen(1)
            port = s.getsockname()[1]
            return port
    
    def run_flask():
        port = int(os.environ.get("PORT", 0))
        if port == 0:
            port = find_free_port()
            print(f"✅ Using free port: {port}")
        try:
            app.run(host='0.0.0.0', port=port)
        except OSError as e:
            if "Address already in use" in str(e):
                new_port = find_free_port()
                print(f"⚠️ Port {port} in use, trying {new_port}")
                app.run(host='0.0.0.0', port=new_port)
            else:
                raise
    
    def keep_alive():
        t = Thread(target=run_flask)
        t.daemon = True
        t.start()
        print("Flask Keep-Alive server started.")
except Exception as e:
    print(f"⚠️ Flask error (ignored): {e}")

# --- Initialize Bot ---
try:
    bot = telebot.TeleBot(TOKEN)
    print("✅ Bot initialized successfully!")
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except:
        pass
except Exception as e:
    print(f"❌ Bot initialization error: {e}")
    sys.exit(1)

# --- Data Structures ---
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
    except Exception as e:
        logger.error(f"Gofile server fetch error: {e}")
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
    except Exception as e:
        logger.error(f"Backup failed for {file_name}: {e}")
    return None

def restore_file_from_cloud(download_page_url, dest_path):
    try:
        file_id = download_page_url.rstrip('/').split('/')[-1]
        server = get_gofile_server()
        direct_url = f"https://{server}/download/{file_id}"
        resp = requests.get(direct_url, stream=True, timeout=30)
        if resp.status_code == 200:
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception as e:
        logger.error(f"Restore failed from {download_page_url}: {e}")
    return False

# --- Database Setup ---
def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
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
                      approval_msg_id INTEGER,
                      upload_chat_id INTEGER)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

# --- Pending Uploads Functions ---
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
                'approval_msg_id': None,
                'upload_chat_id': chat_id
            }
            return pending_id
        except Exception as e:
            logger.error(f"Error saving pending upload: {e}")
            return None

def load_pending_uploads():
    global pending_counter
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''SELECT id, user_id, file_name, file_type, file_path, cloud_url, timestamp, status, chat_id, message_id, temp_dir, zip_path, single_file 
                     FROM pending_uploads WHERE status = 'pending' OR status = 'approved' OR status = 'rejected' ''')
        rows = c.fetchall()
        conn.close()
        
        for row in rows:
            (pid, user_id, file_name, file_type, file_path, cloud_url, timestamp, status, chat_id, message_id, temp_dir, zip_path, single_file) = row
            pending_uploads[pid] = {
                'user_id': user_id,
                'file_name': file_name,
                'file_type': file_type,
                'file_path': file_path,
                'cloud_url': cloud_url,
                'timestamp': timestamp,
                'chat_id': chat_id,
                'message_id': message_id,
                'status': status,
                'temp_dir': temp_dir,
                'zip_path': zip_path,
                'single_file': single_file,
                'approval_msg_id': None,
                'upload_chat_id': chat_id
            }
            if pid > pending_counter:
                pending_counter = pid
        logger.info(f"Loaded {len(pending_uploads)} pending uploads")
    except Exception as e:
        logger.error(f"Error loading pending uploads: {e}")

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
        except Exception as e:
            logger.error(f"Error updating pending status: {e}")
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
        except Exception as e:
            logger.error(f"Error deleting pending upload: {e}")
            return False

def load_data():
    logger.info("Loading data from database...")
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
        logger.info(f"Data loaded: {len(active_users)} users, {len(admin_ids)} admins.")
        
        load_pending_uploads()
        
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

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
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try:
                        script_info['log_file'].close()
                    except Exception as log_e:
                        logger.error(f"Error closing log file during cleanup {script_key}: {log_e}")
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try:
                    script_info['log_file'].close()
                except Exception as log_e:
                    logger.error(f"Error closing log file for non-existent process {script_key}: {log_e}")
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process status for {script_key}: {e}", exc_info=True)
            return False
    return False

def kill_process_tree(process_info):
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A')

    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"Closed log file for {script_key} (PID: {process_info.get('process', {}).get('pid', 'N/A')})")
            except Exception as log_e:
                logger.error(f"Error closing log file during kill for {script_key}: {log_e}")

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
                        except Exception as e:
                            try: child.kill()
                            except Exception: pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except Exception: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            parent.kill()
                    except psutil.NoSuchProcess:
                        pass
                except psutil.NoSuchProcess:
                    pass
        elif log_file_closed:
            logger.warning(f"Process object missing for {script_key}, but log file closed.")
    except Exception as e:
        logger.error(f"❌ Unexpected error killing process tree for PID {pid or 'N/A'} ({script_key}): {e}", exc_info=True)

# --- Package Installation ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'asyncio': None, 'json': None, 'datetime': None, 'os': None,
    'sys': None, 're': None, 'time': None, 'math': None, 'random': None,
    'logging': None, 'threading': None, 'subprocess': None, 'zipfile': None,
    'tempfile': None, 'shutil': None, 'sqlite3': None, 'atexit': None
}

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        logger.info(f"Module '{module_name}' is core. Skipping pip install.")
        return False
    try:
        bot.reply_to(message, f"🐍 Module `{module_name}` not found. Installing `{package_name}`...", parse_mode='Markdown')
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Package `{package_name}` installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{package_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            if len(error_msg) > 4000:
                error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing `{package_name}`: {str(e)}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Node package `{module_name}` not found. Installing locally...", parse_mode='Markdown')
        command = ['npm', 'install', module_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=user_folder, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Node package `{module_name}` installed locally.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install Node package `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            if len(error_msg) > 4000:
                error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ Error: 'npm' not found. Ensure Node.js/npm are installed and in PATH.")
        return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing Node package `{module_name}`: {str(e)}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run Python script: {script_path} (Key: {script_key})")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found!")
            if script_owner_id in user_files:
                user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1)
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            bot.reply_to(message_obj_for_reply, f"🔄 Install successful. Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed. Cannot run '{file_name}'.")
                            return
                    else:
                        error_summary = stderr[:500]
                        bot.reply_to(message_obj_for_reply, f"❌ Error in script pre-check:\n```\n{error_summary}\n```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
                    check_proc.communicate()
            except Exception as e:
                bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in pre-check: {e}")
                return
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
                    check_proc.communicate()

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        process = None
        try:
            log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file: {e}")
            return
        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ Python script '{file_name}' started! (PID: {process.pid})")
        except Exception as e:
            if log_file and not log_file.closed:
                log_file.close()
            bot.reply_to(message_obj_for_reply, f"❌ Error starting script: {str(e)}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts:
                del bot_scripts[script_key]
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Unexpected error: {str(e)}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run JS script: {script_path} (Key: {script_key})")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found!")
            if script_owner_id in user_files:
                user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_command = ['node', script_path]
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1)
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj_for_reply):
                                bot.reply_to(message_obj_for_reply, f"🔄 NPM install successful. Retrying '{file_name}'...")
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                                return
                            else:
                                bot.reply_to(message_obj_for_reply, f"❌ NPM install failed. Cannot run '{file_name}'.")
                                return
                    error_summary = stderr[:500]
                    bot.reply_to(message_obj_for_reply, f"❌ Error in JS pre-check:\n```\n{error_summary}\n```", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
                    check_proc.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj_for_reply, "❌ Error: 'node' not found. Install Node.js for JS files.")
                return
            except Exception as e:
                bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in JS pre-check: {e}")
                return
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
                    check_proc.communicate()

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        process = None
        try:
            log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file: {e}")
            return
        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ JS script '{file_name}' started! (PID: {process.pid})")
        except Exception as e:
            if log_file and not log_file.closed:
                log_file.close()
            bot.reply_to(message_obj_for_reply, f"❌ Error starting JS script: {str(e)}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts:
                del bot_scripts[script_key]
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Unexpected error: {str(e)}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

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
            logger.error(f"❌ Error saving file for {user_id}: {e}")
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
        except Exception as e:
            logger.error(f"❌ Error removing file for {user_id}: {e}")
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
        except Exception as e:
            logger.error(f"❌ Error adding active user {user_id}: {e}")
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
        except Exception as e:
            logger.error(f"❌ Error adding admin {admin_id}: {e}")
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            removed = c.rowcount > 0
            if removed:
                admin_ids.discard(admin_id)
            return removed
        except Exception as e:
            logger.error(f"❌ Error removing admin {admin_id}: {e}")
            return False
        finally:
            conn.close()

# --- Menu Creation ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts'),
            types.InlineKeyboardButton('📥 Pending Uploads', callback_data='pending_uploads')
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
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(buttons[4])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
        ["📢 Updates Channel"],
        ["📤 Upload File", "📂 Check Files"],
        ["⚡ Bot Speed", "📊 Statistics"],
        ["📞 Contact Owner"]
    ]
    ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
        ["📢 Updates Channel"],
        ["📤 Upload File", "📂 Check Files"],
        ["⚡ Bot Speed", "📊 Statistics"],
        ["📢 Broadcast"],
        ["🔒 Lock Bot", "🟢 Running All Code"],
        ["👑 Admin Panel", "📥 Pending Uploads"],
        ["📞 Contact Owner"]
    ]
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row_buttons_text in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row_buttons_text])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# ==========================================
# ✅ FILE UPLOAD HANDLER
# ==========================================
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return
    
    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, "⚠️ No file name.")
        return
    
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    
    max_file_size = 20 * 1024 * 1024
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB).")
        return
    
    try:
        bot.forward_message(OWNER_ID, chat_id, message.message_id)
        bot.send_message(OWNER_ID, 
            f"📥 **New File Upload Request**\n\n"
            f"👤 User: {message.from_user.first_name} (@{message.from_user.username or 'N/A'})\n"
            f"🆔 User ID: `{user_id}`\n"
            f"📁 File: `{file_name}`\n"
            f"📂 Type: `{file_ext}`\n"
            f"📏 Size: `{doc.file_size / 1024:.2f} KB`\n\n"
            f"⏳ Waiting for your approval...",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to forward file to owner: {e}")

    download_wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...")
    file_info = bot.get_file(doc.file_id)
    downloaded_file_content = bot.download_file(file_info.file_path)
    bot.edit_message_text(f"✅ Downloaded `{file_name}`. Waiting for approval...", chat_id, download_wait_msg.message_id)

    user_folder = get_user_folder(user_id)
    temp_file_path = os.path.join(user_folder, f"_pending_{file_name}")
    with open(temp_file_path, 'wb') as f:
        f.write(downloaded_file_content)
    
    main_script_name = file_name
    main_script_type = file_ext[1:]
    actual_file_path = temp_file_path
    temp_dir = None
    zip_path = temp_file_path
    single_file = None
    req_file_path = None
    has_requirements = False
    
    if file_ext == '.zip':
        try:
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_pending_zip_")
            
            with zipfile.ZipFile(temp_file_path, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                    if not member_path.startswith(os.path.abspath(temp_dir)):
                        raise zipfile.BadZipFile(f"Zip has unsafe path: {member.filename}")
                zip_ref.extractall(temp_dir)
            
            extracted_items = os.listdir(temp_dir)
            py_files = [f for f in extracted_items if f.endswith('.py')]
            js_files = [f for f in extracted_items if f.endswith('.js')]
            
            if 'requirements.txt' in extracted_items:
                req_file_path = os.path.join(temp_dir, 'requirements.txt')
                has_requirements = True
            
            preferred_py = ['main.py', 'bot.py', 'app.py', 'M2MSETUP.py', 'setup.py', 'run.py', 'start.py', 'index.py', 'launcher.py', 'server.py']
            preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js', 'server.js', 'start.js', 'launcher.js']
            
            main_script_name = None
            main_script_type = None
            
            for p in preferred_py:
                if p in py_files:
                    main_script_name = p
                    main_script_type = 'py'
                    break
            
            if not main_script_name:
                for p in preferred_js:
                    if p in js_files:
                        main_script_name = p
                        main_script_type = 'js'
                        break
            
            if not main_script_name:
                if py_files:
                    main_script_name = py_files[0]
                    main_script_type = 'py'
                elif js_files:
                    main_script_name = js_files[0]
                    main_script_type = 'js'
            
            if main_script_name:
                actual_file_path = os.path.join(temp_dir, main_script_name)
                logger.info(f"✅ Found main script: {main_script_name} (Type: {main_script_type})")
                if has_requirements:
                    bot.send_message(OWNER_ID, f"📄 `requirements.txt` found in the zip file!", parse_mode='Markdown')
            else:
                bot.reply_to(message, f"❌ No `.py` or `.js` script found in archive!\n\n📁 Files found: {', '.join(extracted_items[:10])}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                os.remove(temp_file_path)
                return
                
        except Exception as e:
            logger.error(f"Error processing zip: {e}")
            bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            os.remove(temp_file_path)
            return
    else:
        single_file = temp_file_path
        main_script_name = file_name
        main_script_type = file_ext[1:]
        actual_file_path = temp_file_path
    
    file_type = main_script_type if main_script_type in ['py', 'js'] else 'py'
    
    pending_id = save_pending_upload(
        user_id, main_script_name, file_type, actual_file_path, 
        None, chat_id, download_wait_msg.message_id,
        temp_dir, zip_path, single_file
    )
    
    if not pending_id:
        bot.reply_to(message, "❌ Error saving pending upload. Please try again.")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return
    
    approval_markup = types.InlineKeyboardMarkup(row_width=2)
    approval_markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user_id}_{main_script_name}_{pending_id}'),
        types.InlineKeyboardButton("❌ Reject", callback_data=f'reject_{user_id}_{main_script_name}_{pending_id}')
    )
    
    if has_requirements and req_file_path:
        approval_markup.add(types.InlineKeyboardButton("📦 Install Packages", callback_data=f'install_pkg_{pending_id}'))
    
    owner_msg = bot.send_message(
        OWNER_ID,
        f"📥 **File Upload Approval Required**\n\n"
        f"👤 User: {message.from_user.first_name} (@{message.from_user.username or 'N/A'})\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📁 File: `{main_script_name}`\n"
        f"📂 Type: `{file_type}`\n"
        f"🆔 Request ID: `{pending_id}`\n"
        f"{'📄 requirements.txt: ✅ Found' if has_requirements else '📄 requirements.txt: ❌ Not found'}\n\n"
        f"⚠️ **Approve only if file is trusted!**",
        parse_mode='Markdown',
        reply_markup=approval_markup
    )
    
    pending_uploads[pending_id]['approval_msg_id'] = owner_msg.message_id
    pending_uploads[pending_id]['upload_chat_id'] = chat_id
    pending_uploads[pending_id]['user_msg_id'] = download_wait_msg.message_id
    pending_uploads[pending_id]['req_file_path'] = req_file_path if has_requirements else None
    pending_uploads[pending_id]['user_folder'] = user_folder
    
    bot.send_message(
        chat_id,
        f"📤 Your file `{main_script_name}` has been sent for approval.\n"
        f"⏳ Please wait for admin to approve your upload.",
        parse_mode='Markdown'
    )

# ==========================================
# ✅ CALLBACK HANDLERS - ALL FIXED
# ==========================================

# --- Upload Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'upload')
def handle_upload_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id, 
        "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\n\n⚠️ **File will be sent for admin approval first!**",
        parse_mode='Markdown'
    )

# --- Check Files Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'check_files')
def handle_check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    user_files_list = user_files.get(user_id, [])
    
    if not user_files_list:
        bot.answer_callback_query(call.id, "⚠️ No files uploaded.", show_alert=True)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
        bot.edit_message_text(
            "📂 Your files:\n\n(No files uploaded)", 
            chat_id, 
            call.message.message_id, 
            reply_markup=markup
        )
        return
    
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type, _ in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    bot.edit_message_text(
        "📂 Your files:\nClick to manage.", 
        chat_id, 
        call.message.message_id, 
        reply_markup=markup, 
        parse_mode='Markdown'
    )

# --- Pending Uploads ---
@bot.callback_query_handler(func=lambda call: call.data == 'pending_uploads')
def handle_pending_uploads_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    pending_list = [data for data in pending_uploads.values() if data['status'] == 'pending']
    
    if not pending_list:
        bot.send_message(call.message.chat.id, "📭 No pending uploads.")
        return
    
    msg = "📥 **Pending Uploads:**\n\n"
    for data in pending_list[:10]:  # Limit to 10
        msg += f"🆔 User: `{data['user_id']}`\n"
        msg += f"📁 File: `{data['file_name']}`\n"
        msg += f"📂 Type: `{data['file_type']}`\n"
        msg += f"🕐 Time: `{data['timestamp']}`\n"
        msg += "─" * 10 + "\n"
    
    if len(pending_list) > 10:
        msg += f"\n... and {len(pending_list) - 10} more pending uploads."
    
    bot.send_message(call.message.chat.id, msg, parse_mode='Markdown')

# --- Approve Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_'))
def handle_approve_callback(call):
    user_id = call.from_user.id
    
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    
    try:
        parts = call.data.split('_', 3)
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "❌ Invalid request.", show_alert=True)
            return
        
        target_user_id = int(parts[1])
        file_name = parts[2]
        pending_id = int(parts[3])
        
        if pending_id not in pending_uploads:
            bot.answer_callback_query(call.id, "❌ Request not found.", show_alert=True)
            return
        
        pending_data = pending_uploads[pending_id]
        if pending_data['status'] != 'pending':
            bot.answer_callback_query(call.id, f"❌ Request already {pending_data['status']}.", show_alert=True)
            return
        
        user_folder = get_user_folder(target_user_id)
        
        try:
            file_path = pending_data['file_path']
            file_type = pending_data['file_type']
            file_name = pending_data['file_name']
            
            # Move file to user folder
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
            
            # Backup to cloud
            cloud_url = backup_file_to_cloud(file_path, target_user_id, file_name)
            if cloud_url:
                logger.info(f"Backed up {file_name} to {cloud_url}")
            
            # Save to database
            save_user_file(target_user_id, file_name, file_type, cloud_url)
            
            # Notify user
            bot.send_message(
                pending_data['upload_chat_id'],
                f"✅ **File Approved!**\n\n"
                f"📁 `{file_name}` has been approved and is starting automatically...",
                parse_mode='Markdown'
            )
            
            # ✅ AUTO-RUN THE SCRIPT
            try:
                if file_type == 'py':
                    threading.Thread(
                        target=run_script, 
                        args=(file_path, target_user_id, user_folder, file_name, call.message)
                    ).start()
                elif file_type == 'js':
                    threading.Thread(
                        target=run_js_script, 
                        args=(file_path, target_user_id, user_folder, file_name, call.message)
                    ).start()
                
                time.sleep(2)
                is_running = is_bot_running(target_user_id, file_name)
                status = "✅ Running" if is_running else "⚠️ Check logs for errors"
                
                bot.send_message(
                    pending_data['upload_chat_id'],
                    f"📊 **Script Status:** {status}\n"
                    f"📁 `{file_name}`\n\n"
                    f"Use /start to manage your files.",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error auto-starting script: {e}")
                bot.send_message(
                    pending_data['upload_chat_id'],
                    f"⚠️ File approved but failed to start automatically.\n"
                    f"Error: `{str(e)}`\n"
                    f"Please use the start button from /start menu.",
                    parse_mode='Markdown'
                )
            
            # Update admin message
            bot.edit_message_text(
                f"✅ **Approved & Started**\n\n"
                f"👤 User: `{target_user_id}`\n"
                f"📁 File: `{file_name}`\n"
                f"✅ Status: Running",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
            
            update_pending_status(pending_id, 'approved')
            bot.answer_callback_query(call.id, "✅ File approved and started!")
            
        except Exception as e:
            logger.error(f"Error processing approved file: {e}")
            bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error in approve callback: {e}")
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)

# --- Reject Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_'))
def handle_reject_callback(call):
    user_id = call.from_user.id
    
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    
    try:
        parts = call.data.split('_', 3)
        if len(parts) != 4:
            bot.answer_callback_query(call.id, "❌ Invalid request.", show_alert=True)
            return
        
        target_user_id = int(parts[1])
        file_name = parts[2]
        pending_id = int(parts[3])
        
        if pending_id not in pending_uploads:
            bot.answer_callback_query(call.id, "❌ Request not found.", show_alert=True)
            return
        
        pending_data = pending_uploads[pending_id]
        if pending_data['status'] != 'pending':
            bot.answer_callback_query(call.id, f"❌ Request already {pending_data['status']}.", show_alert=True)
            return
        
        # Cleanup files
        try:
            if pending_data.get('temp_dir') and os.path.exists(pending_data['temp_dir']):
                shutil.rmtree(pending_data['temp_dir'], ignore_errors=True)
            if pending_data.get('zip_path') and os.path.exists(pending_data['zip_path']):
                os.remove(pending_data['zip_path'])
            if pending_data.get('single_file') and os.path.exists(pending_data['single_file']):
                os.remove(pending_data['single_file'])
        except Exception as e:
            logger.error(f"Error cleaning up rejected files: {e}")
        
        update_pending_status(pending_id, 'rejected')
        
        bot.send_message(
            pending_data['upload_chat_id'],
            f"❌ **File Rejected**\n\n"
            f"📁 `{file_name}` was rejected by admin.\n"
            f"Please contact admin if you think this is a mistake.",
            parse_mode='Markdown'
        )
        
        bot.edit_message_text(
            f"❌ **Rejected**\n\n"
            f"👤 User: `{target_user_id}`\n"
            f"📁 File: `{file_name}`\n"
            f"❌ Status: Rejected",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
        
        delete_pending_upload(pending_id)
        bot.answer_callback_query(call.id, "❌ File rejected.")
        
    except Exception as e:
        logger.error(f"Error in reject callback: {e}")
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)

# --- Install Packages Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('install_pkg_'))
def handle_install_packages_callback(call):
    user_id = call.from_user.id
    
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    
    try:
        pending_id = int(call.data.split('_')[2])
        
        if pending_id not in pending_uploads:
            bot.answer_callback_query(call.id, "❌ Request not found.", show_alert=True)
            return
        
        pending_data = pending_uploads[pending_id]
        req_file_path = pending_data.get('req_file_path')
        user_folder = pending_data.get('user_folder', get_user_folder(pending_data['user_id']))
        
        bot.answer_callback_query(call.id, "📦 Starting package installation...")
        
        # Install packages
        status_msg = bot.send_message(call.message.chat.id, "📦 **Installing Packages...**\n⏳ Please wait...", parse_mode='Markdown')
        
        packages_to_install = []
        
        if req_file_path and os.path.exists(req_file_path):
            try:
                with open(req_file_path, 'r') as f:
                    packages_to_install = [line.strip() for line in f.readlines() if line.strip() and not line.startswith('#')]
                bot.edit_message_text(f"📦 **Installing from requirements.txt**\n📄 Found {len(packages_to_install)} packages...", call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Error reading requirements.txt: {e}")
        
        if not packages_to_install:
            packages_to_install = ['requests', 'flask', 'psutil']
            bot.edit_message_text("📦 **Installing Default Packages...**", call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
        
        installed = []
        failed = []
        
        for i, package in enumerate(packages_to_install):
            try:
                progress = f"📦 Installing packages...\n🔄 {i+1}/{len(packages_to_install)}: `{package}`"
                bot.edit_message_text(progress, call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
                
                command = [sys.executable, '-m', 'pip', 'install', package]
                result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
                
                if result.returncode == 0:
                    installed.append(package)
                else:
                    failed.append(package)
                    
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error installing {package}: {e}")
                failed.append(package)
        
        final_msg = f"✅ **Package Installation Complete!**\n\n"
        final_msg += f"✅ Installed: {len(installed)} packages\n"
        if installed:
            final_msg += f"📦 `{', '.join(installed[:10])}`" + ("..." if len(installed) > 10 else "") + "\n"
        final_msg += f"\n❌ Failed: {len(failed)} packages\n"
        if failed:
            final_msg += f"⚠️ `{', '.join(failed[:5])}`" + ("..." if len(failed) > 5 else "") + "\n"
        
        bot.edit_message_text(final_msg, call.message.chat.id, status_msg.message_id, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error installing packages: {e}")
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)

# --- File Control Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('file_'))
def handle_file_control_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ You can only manage your own files.", show_alert=True)
            return
        
        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id)
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Running' if is_running else '🔴 Stopped'
        file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?')
        
        bot.edit_message_text(
            f"⚙️ Controls for: `{file_name}` ({file_type})\nStatus: {status_text}",
            call.message.chat.id, 
            call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in file_control_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

# --- Start Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def handle_start_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
            return
        
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            return
        
        file_type = file_info[1]
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ File `{file_name}` missing!", show_alert=True)
            return
        
        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script already running.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, f"⏳ Starting {file_name}...")
        
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        
        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Running' if is_now_running else '🟡 Starting (or failed)'
        
        bot.edit_message_text(
            f"⚙️ Controls for: `{file_name}` ({file_type})\nStatus: {status_text}",
            call.message.chat.id, 
            call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in start_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error starting script.", show_alert=True)

# --- Stop Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def handle_stop_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
            return
        
        if not is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script already stopped.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, f"⏳ Stopping {file_name}...")
        script_key = f"{script_owner_id}_{file_name}"
        process_info = bot_scripts.get(script_key)
        
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
        
        time.sleep(0.5)
        bot.edit_message_text(
            f"⚙️ Controls for: `{file_name}`\nStatus: 🔴 Stopped",
            call.message.chat.id, 
            call.message.message_id,
            reply_markup=create_control_buttons(script_owner_id, file_name, False), 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in stop_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error stopping script.", show_alert=True)

# --- Delete Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def handle_delete_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, f"🗑️ Deleting {file_name}...")
        
        if is_bot_running(script_owner_id, file_name):
            script_key = f"{script_owner_id}_{file_name}"
            process_info = bot_scripts.get(script_key)
            if process_info:
                kill_process_tree(process_info)
                del bot_scripts[script_key]
            time.sleep(0.5)
        
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(log_path):
            os.remove(log_path)
        
        remove_user_file_db(script_owner_id, file_name)
        bot.edit_message_text(
            f"🗑️ File `{file_name}` deleted!",
            call.message.chat.id, 
            call.message.message_id, 
            reply_markup=None, 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in delete_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

# --- Logs Button ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('logs_'))
def handle_logs_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
            return
        
        user_folder = get_user_folder(script_owner_id)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, f"⚠️ No logs for '{file_name}'.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id)
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()
        
        if not log_content.strip():
            log_content = "(Log empty)"
        if len(log_content) > 4096:
            log_content = log_content[-4096:]
            log_content = "...\n" + log_content
        
        bot.send_message(
            call.message.chat.id, 
            f"📜 Logs for `{file_name}`:\n```\n{log_content}\n```", 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in logs_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error fetching logs.", show_alert=True)

# --- Speed Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'speed')
def handle_speed_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    start_time = time.time()
    
    try:
        bot.edit_message_text("🏃 Testing speed...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        
        if user_id == OWNER_ID:
            user_level = "👑 Owner"
        elif user_id in admin_ids:
            user_level = "🛡️ Admin"
        else:
            user_level = "🆓 User"
        
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     f"👤 Your Level: {user_level}")
        
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            speed_msg, 
            chat_id, 
            call.message.message_id, 
            reply_markup=create_main_menu_inline(user_id)
        )
    except Exception as e:
        logger.error(f"Error in speed_callback: {e}")
        bot.answer_callback_query(call.id, "Error in speed test.", show_alert=True)

# --- Back to Main Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'back_to_main')
def handle_back_to_main_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    current_files = get_user_file_count(user_id)
    user_status = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else "🆓 User")
    
    main_menu_text = (f"〽️ Welcome back, {call.from_user.first_name}!\n\n"
                      f"🆔 ID: `{user_id}`\n"
                      f"🔰 Status: {user_status}\n"
                      f"📁 Files: {current_files} (Unlimited)\n\n"
                      f"⚠️ **All uploads require admin approval!**\n\n"
                      f"📦 Use `/install` to install packages\n"
                      f"📤 Use `/start` to access your files\n\n"
                      f"👇 Use buttons or type commands.")
    
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        main_menu_text, 
        chat_id, 
        call.message.message_id,
        reply_markup=create_main_menu_inline(user_id), 
        parse_mode='Markdown'
    )

# --- Stats Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'stats')
def handle_stats_callback(call):
    bot.answer_callback_query(call.id)
    
    user_id = call.from_user.id
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())
    pending_count = len([p for p in pending_uploads.values() if p['status'] == 'pending'])
    running_scripts = len(bot_scripts)
    
    stats_msg = (f"📊 Bot Statistics:\n\n"
                 f"👥 Total Users: {total_users}\n"
                 f"📂 Total File Records: {total_files_records}\n"
                 f"📥 Pending Uploads: {pending_count}\n"
                 f"🟢 Running Scripts: {running_scripts}\n")
    
    if user_id in admin_ids:
        stats_msg += f"\n🔒 Bot Status: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}"
    
    bot.edit_message_text(
        stats_msg, 
        call.message.chat.id, 
        call.message.message_id,
        reply_markup=create_main_menu_inline(user_id)
    )

# --- Lock Bot Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'lock_bot')
def handle_lock_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    global bot_locked
    bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    bot.edit_message_reply_markup(
        call.message.chat.id, 
        call.message.message_id,
        reply_markup=create_main_menu_inline(call.from_user.id)
    )

# --- Unlock Bot Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'unlock_bot')
def handle_unlock_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    global bot_locked
    bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
    bot.edit_message_reply_markup(
        call.message.chat.id, 
        call.message.message_id,
        reply_markup=create_main_menu_inline(call.from_user.id)
    )

# --- Run All Scripts Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'run_all_scripts')
def handle_run_all_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id, "⏳ Starting all scripts...")
    bot.send_message(call.message.chat.id, "⏳ Starting process to run all user scripts...")
    
    started_count = 0
    
    for target_user_id, files_for_user in list(user_files.items()):
        if not files_for_user:
            continue
        user_folder = get_user_folder(target_user_id)
        for file_name, file_type, _ in files_for_user:
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, call.message)).start()
                            started_count += 1
                        time.sleep(0.7)
                    except Exception as e:
                        logger.error(f"Error starting {file_name}: {e}")
    
    bot.send_message(call.message.chat.id, f"✅ All Users' Scripts - Processing Complete!\n\n▶️ Attempted to start: {started_count} scripts.")

# --- Broadcast Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'broadcast')
def handle_broadcast_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Broadcast cancelled.")
        return
    
    broadcast_content = message.text
    if not broadcast_content:
        bot.reply_to(message, "⚠️ Cannot broadcast empty message.")
        return
    
    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    
    bot.reply_to(message, f"⚠️ Confirm Broadcast:\n\n```\n{broadcast_content[:1000]}\n```\n"
                          f"To **{target_count}** users. Sure?", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_broadcast_'))
def handle_confirm_broadcast_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    try:
        original_message = call.message.reply_to_message
        if not original_message:
            raise ValueError("Could not retrieve original message.")
        
        broadcast_text = original_message.text
        if not broadcast_text:
            raise ValueError("Message has no text.")
        
        bot.answer_callback_query(call.id, "🚀 Starting broadcast...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        
        thread = threading.Thread(target=execute_broadcast, args=(broadcast_text, chat_id))
        thread.start()
        
    except Exception as e:
        logger.error(f"Error in handle_confirm_broadcast: {e}", exc_info=True)
        bot.edit_message_text("❌ Error starting broadcast.", chat_id, call.message.message_id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_broadcast')
def handle_cancel_broadcast_callback(call):
    bot.answer_callback_query(call.id, "Broadcast cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

def execute_broadcast(broadcast_text, admin_chat_id):
    sent_count = 0
    failed_count = 0
    blocked_count = 0
    users_to_broadcast = list(active_users)
    total_users = len(users_to_broadcast)
    
    for i, user_id in enumerate(users_to_broadcast):
        try:
            bot.send_message(user_id, broadcast_text, parse_mode='Markdown')
            sent_count += 1
        except telebot.apihelper.ApiTelegramException as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found"]):
                blocked_count += 1
            else:
                failed_count += 1
        except Exception:
            failed_count += 1
        
        if (i + 1) % 25 == 0 and i < total_users - 1:
            time.sleep(1.5)
    
    result_msg = f"📢 Broadcast Complete!\n\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\n🚫 Blocked/Inactive: {blocked_count}\n👥 Targets: {total_users}"
    bot.send_message(admin_chat_id, result_msg)

# --- Admin Panel Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'admin_panel')
def handle_admin_panel_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "👑 Admin Panel\nManage admins (Owner actions may be restricted).",
        call.message.chat.id, 
        call.message.message_id, 
        reply_markup=create_admin_panel()
    )

# --- Add Admin Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'add_admin')
def handle_add_admin_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to promote to Admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Admin promotion cancelled.")
        return
    
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id <= 0:
            raise ValueError
        if new_admin_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Owner is already Owner.")
            return
        if new_admin_id in admin_ids:
            bot.reply_to(message, f"⚠️ User `{new_admin_id}` already Admin.")
            return
        
        add_admin_db(new_admin_id)
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.")
        
        try:
            bot.send_message(new_admin_id, "🎉 Congrats! You are now an Admin.")
        except Exception:
            pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID to promote or /cancel.")
        bot.register_next_step_handler(msg, process_add_admin_id)
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        bot.reply_to(message, "Error.")

# --- Remove Admin Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'remove_admin')
def handle_remove_admin_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID of Admin to remove.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only.")
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Admin removal cancelled.")
        return
    
    try:
        admin_id = int(message.text.strip())
        if admin_id <= 0:
            raise ValueError
        if admin_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Owner cannot remove self.")
            return
        if admin_id not in admin_ids:
            bot.reply_to(message, f"⚠️ User `{admin_id}` not Admin.")
            return
        
        if remove_admin_db(admin_id):
            bot.reply_to(message, f"✅ Admin `{admin_id}` removed.")
            try:
                bot.send_message(admin_id, "ℹ️ You are no longer an Admin.")
            except Exception:
                pass
        else:
            bot.reply_to(message, f"❌ Failed to remove admin `{admin_id}`.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter Admin ID to remove or /cancel.")
        bot.register_next_step_handler(msg, process_remove_admin_id)
    except Exception as e:
        logger.error(f"Error removing admin: {e}")
        bot.reply_to(message, "Error.")

# --- List Admins Button ---
@bot.callback_query_handler(func=lambda call: call.data == 'list_admins')
def handle_list_admins_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    admin_list_str = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(list(admin_ids)))
    if not admin_list_str:
        admin_list_str = "(No Owner/Admins configured!)"
    
    bot.edit_message_text(
        f"👑 Current Admins:\n\n{admin_list_str}", 
        call.message.chat.id,
        call.message.message_id, 
        reply_markup=create_admin_panel(), 
        parse_mode='Markdown'
    )

# ==========================================
# ✅ COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"🎉 New user!\n👤 Name: {user_name}\n✳️ User: @{user_username or 'N/A'}\n🆔 ID: `{user_id}`", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"⚠️ Failed to notify owner about new user: {e}")

    current_files = get_user_file_count(user_id)
    user_status = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else "🆓 User")
    
    welcome_msg_text = (f"〽️ Welcome, {user_name}!\n\n"
                        f"🆔 Your User ID: `{user_id}`\n"
                        f"✳️ Username: `@{user_username or 'Not set'}`\n"
                        f"🔰 Your Status: {user_status}\n"
                        f"📁 Files Uploaded: {current_files} (Unlimited)\n\n"
                        f"🤖 Host & run Python (`.py`) or JS (`.js`) scripts.\n"
                        f"   Upload single scripts or `.zip` archives.\n\n"
                        f"⚠️ **All uploads require admin approval!**\n\n"
                        f"📦 Use `/install` to install packages\n"
                        f"📤 Use `/start` to access your files\n\n"
                        f"👇 Use buttons or type commands.")
    
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='Markdown')

@bot.message_handler(commands=['install'])
def command_install_packages(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "❌ You don't have any files uploaded yet!\nUpload a bot first.")
        return
    
    user_folder = get_user_folder(user_id)
    req_file_path = os.path.join(user_folder, 'requirements.txt')
    
    if os.path.exists(req_file_path):
        install_packages_for_user(message, user_folder, req_file_path)
    else:
        bot.send_message(chat_id, "📝 No `requirements.txt` found. Installing common packages...", parse_mode='Markdown')
        install_packages_for_user(message, user_folder, None)

@bot.message_handler(commands=['pending'])
def command_pending_uploads(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    pending_list = [data for data in pending_uploads.values() if data['status'] == 'pending']
    
    if not pending_list:
        bot.reply_to(message, "📭 No pending uploads.")
        return
    
    msg = "📥 **Pending Uploads:**\n\n"
    for data in pending_list[:10]:
        msg += f"🆔 User: `{data['user_id']}`\n"
        msg += f"📁 File: `{data['file_name']}`\n"
        msg += f"📂 Type: `{data['file_type']}`\n"
        msg += f"🕐 Time: `{data['timestamp']}`\n"
        msg += "─" * 10 + "\n"
    
    if len(pending_list) > 10:
        msg += f"\n... and {len(pending_list) - 10} more pending uploads."
    
    bot.reply_to(message, msg, parse_mode='Markdown')

# --- Install Packages Helper ---
def install_packages_for_user(message, user_folder, req_file_path=None):
    chat_id = message.chat.id
    
    status_msg = bot.send_message(chat_id, "📦 **Installing Packages...**\n⏳ Please wait...", parse_mode='Markdown')
    
    packages_to_install = []
    
    if req_file_path and os.path.exists(req_file_path):
        try:
            with open(req_file_path, 'r') as f:
                packages_to_install = [line.strip() for line in f.readlines() if line.strip() and not line.startswith('#')]
            bot.edit_message_text(f"📦 **Installing from requirements.txt**\n📄 Found {len(packages_to_install)} packages...", chat_id, status_msg.message_id, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error reading requirements.txt: {e}")
    
    if not packages_to_install:
        packages_to_install = ['requests', 'flask', 'psutil']
        bot.edit_message_text("📦 **Installing Default Packages...**", chat_id, status_msg.message_id, parse_mode='Markdown')
    
    installed = []
    failed = []
    
    for i, package in enumerate(packages_to_install):
        try:
            progress = f"📦 Installing packages...\n🔄 {i+1}/{len(packages_to_install)}: `{package}`"
            bot.edit_message_text(progress, chat_id, status_msg.message_id, parse_mode='Markdown')
            
            command = [sys.executable, '-m', 'pip', 'install', package]
            result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
            
            if result.returncode == 0:
                installed.append(package)
            else:
                failed.append(package)
                
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Error installing {package}: {e}")
            failed.append(package)
    
    final_msg = f"✅ **Package Installation Complete!**\n\n"
    final_msg += f"✅ Installed: {len(installed)} packages\n"
    if installed:
        final_msg += f"📦 `{', '.join(installed[:10])}`" + ("..." if len(installed) > 10 else "") + "\n"
    final_msg += f"\n❌ Failed: {len(failed)} packages\n"
    if failed:
        final_msg += f"⚠️ `{', '.join(failed[:5])}`" + ("..." if len(failed) > 5 else "") + "\n"
    
    bot.edit_message_text(final_msg, chat_id, status_msg.message_id, parse_mode='Markdown')

# --- Reply Keyboard Handlers ---
@bot.message_handler(func=lambda message: message.text == '📤 Upload File')
def reply_upload_file(message):
    bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\n\n⚠️ **File will be sent for admin approval first!**")

@bot.message_handler(func=lambda message: message.text == '📂 Check Files')
def reply_check_files(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_files_list = user_files.get(user_id, [])
    
    if not user_files_list:
        bot.reply_to(message, "📂 Your files:\n\n(No files uploaded)")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type, _ in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    bot.send_message(chat_id, "📂 Your files:\nClick to manage.", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '⚡ Bot Speed')
def reply_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start_time = time.time()
    
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        
        if user_id == OWNER_ID:
            user_level = "👑 Owner"
        elif user_id in admin_ids:
            user_level = "🛡️ Admin"
        else:
            user_level = "🆓 User"
        
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     f"👤 Your Level: {user_level}")
        
        bot.reply_to(message, speed_msg)
    except Exception as e:
        logger.error(f"Error in speed reply: {e}")
        bot.reply_to(message, "Error in speed test.")

@bot.message_handler(func=lambda message: message.text == '📊 Statistics')
def reply_stats(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())
    pending_count = len([p for p in pending_uploads.values() if p['status'] == 'pending'])
    running_scripts = len(bot_scripts)
    
    stats_msg = (f"📊 Bot Statistics:\n\n"
                 f"👥 Total Users: {total_users}\n"
                 f"📂 Total File Records: {total_files_records}\n"
                 f"📥 Pending Uploads: {pending_count}\n"
                 f"🟢 Running Scripts: {running_scripts}\n")
    
    if user_id in admin_ids:
        stats_msg += f"\n🔒 Bot Status: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}"
    
    bot.reply_to(message, stats_msg)

@bot.message_handler(func=lambda message: message.text == '📢 Broadcast')
def reply_broadcast(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    msg = bot.reply_to(message, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

@bot.message_handler(func=lambda message: message.text == '🔒 Lock Bot')
def reply_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    global bot_locked
    bot_locked = True
    bot.reply_to(message, "🔒 Bot locked.")

@bot.message_handler(func=lambda message: message.text == '🟢 Running All Code')
def reply_run_all(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    bot.reply_to(message, "⏳ Starting all scripts...")
    
    started_count = 0
    
    for target_user_id, files_for_user in list(user_files.items()):
        if not files_for_user:
            continue
        user_folder = get_user_folder(target_user_id)
        for file_name, file_type, _ in files_for_user:
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, message)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, message)).start()
                            started_count += 1
                        time.sleep(0.7)
                    except Exception as e:
                        logger.error(f"Error starting {file_name}: {e}")
    
    bot.send_message(message.chat.id, f"✅ All Users' Scripts - Processing Complete!\n\n▶️ Attempted to start: {started_count} scripts.")

@bot.message_handler(func=lambda message: message.text == '👑 Admin Panel')
def reply_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

@bot.message_handler(func=lambda message: message.text == '📥 Pending Uploads')
def reply_pending_uploads(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    pending_list = [data for data in pending_uploads.values() if data['status'] == 'pending']
    
    if not pending_list:
        bot.reply_to(message, "📭 No pending uploads.")
        return
    
    msg = "📥 **Pending Uploads:**\n\n"
    for data in pending_list[:10]:
        msg += f"🆔 User: `{data['user_id']}`\n"
        msg += f"📁 File: `{data['file_name']}`\n"
        msg += f"📂 Type: `{data['file_type']}`\n"
        msg += f"🕐 Time: `{data['timestamp']}`\n"
        msg += "─" * 10 + "\n"
    
    if len(pending_list) > 10:
        msg += f"\n... and {len(pending_list) - 10} more pending uploads."
    
    bot.reply_to(message, msg, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '📢 Updates Channel')
def reply_updates(message):
    bot.reply_to(message, f"📢 Join our updates channel:\n{UPDATE_CHANNEL}")

@bot.message_handler(func=lambda message: message.text == '📞 Contact Owner')
def reply_contact(message):
    bot.reply_to(message, f"📞 Contact Owner:\nhttps://t.me/{YOUR_USERNAME.replace('@', '')}")

# ==========================================
# ✅ CLEANUP
# ==========================================
def cleanup():
    logger.warning("Shutdown. Cleaning up processes...")
    for key in list(bot_scripts.keys()):
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
atexit.register(cleanup)

# ==========================================
# ✅ MAIN EXECUTION
# ==========================================
if __name__ == '__main__':
    print("="*40)
    print("🤖 Bot Starting Up...")
    print(f"🐍 Python: {sys.version.split()[0]}")
    print(f"🔧 Base Dir: {BASE_DIR}")
    print(f"📁 Upload Dir: {UPLOAD_BOTS_DIR}")
    print(f"📊 Data Dir: {IROTECH_DIR}")
    print(f"🔑 Owner ID: {OWNER_ID}")
    print(f"🛡️ Admins: {admin_ids}")
    print("="*40)
    
    try:
        init_db()
        load_data()
        
        keep_alive()
        
        print("✅ Bot is ready! Starting polling...")
        print("Press Ctrl+C to stop.")
        
        while True:
            try:
                bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
            except requests.exceptions.ReadTimeout:
                print("⏳ Read timeout, retrying...")
                time.sleep(5)
            except requests.exceptions.ConnectionError:
                print("🔌 Connection error, retrying...")
                time.sleep(15)
            except Exception as e:
                print(f"❌ Polling error: {e}")
                traceback.print_exc()
                time.sleep(30)
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        traceback.print_exc()
