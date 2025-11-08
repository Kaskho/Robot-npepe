import os
import logging
import random
import time
import json
import re
from datetime import datetime, timezone, timedelta
import threading

# --- Third-Party Libraries ---
try:
    import psycopg2
    logging.info("DIAGNOSTIC: 'psycopg2' library SUCCESSFULLY imported.")
except ImportError as e:
    psycopg2 = None
    logging.critical(f"DIAGNOSTIC: CRITICAL - FAILED to import 'psycopg2'. Persistence will be disabled. Error: {e}")
try:
    import groq
    import httpx
except ImportError:
    groq = None
    httpx = None
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config

# ==========================
#   🔧   LOGGING CONFIGURATION
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==========================
#   🤖   BOT LOGIC CLASS
# ==========================
class BotLogic:
    def __init__(self, bot_instance: telebot.TeleBot):
        self.bot = bot_instance
        
        # Critical Check on Initialization
        if not Config.DATABASE_URL() or not psycopg2:
            logger.critical("FATAL: DATABASE_URL not found or psycopg2 is unavailable. Persistence will not function.")
            
        self.groq_client = self._initialize_groq()
        
        # Initialize state for new features
        self._ensure_db_table_exists() # schedule_log
        self._ensure_db_member_table_exists() # members
        
        self.responses = self._load_initial_responses() # Load all new response categories
        self.admin_ids = set()
        self.admins_last_updated = 0
        
        # Constants retained for moderation
        self.FORBIDDEN_KEYWORDS = ['airdrop', 'giveaway', 'presale', 'private sale', 'whitelist', 'signal', 'pump group', 'trading signal', 'investment advice', 'other project']
        self.ALLOWED_DOMAINS = ['pump.fun', 't.me/NPEPEVERSE', 'x.com/NPEPE_Verse', 'base44.app']
        
        self._register_handlers()
        logger.info("BotLogic successfully initialized.")
        
    # --- UTILITY DATABASE & PERSISTENCE FUNCTIONS ---
    
    def _get_db_connection(self):
        db_url = Config.DATABASE_URL()
        if not db_url or not psycopg2:
            logger.warning("DATABASE_URL is not set or psycopg2 is not installed. Persistence disabled.")
            return None
        try:
            return psycopg2.connect(db_url)
        except Exception as e:
            logger.error(f"DB connection failed: {e}")
            return None
            
    def _ensure_db_table_exists(self): 
        conn = self._get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("CREATE TABLE IF NOT EXISTS schedule_log (task_name TEXT PRIMARY KEY, last_run_date TEXT)")
                conn.commit()
                logger.info("Database table 'schedule_log' is ready.")
            except Exception as e:
                logger.error(f"Failed to create schedule table: {e}")
            finally:
                conn.close()
    
    def _ensure_db_member_table_exists(self):
        conn = self._get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS members (
                            user_id BIGINT PRIMARY KEY, 
                            username TEXT, 
                            joined_date TEXT, 
                            last_interacted_date TEXT, 
                            last_thanked_month INTEGER DEFAULT 0
                        )
                    """)
                conn.commit()
                logger.info("Database table 'members' is ready.")
            except Exception as e:
                logger.error(f"Failed to create members table: {e}")
            finally:
                conn.close()

    def _update_member_info(self, user_id, username=None, joined_date=None, last_interacted_date=None, last_thanked_month=None):
        conn = self._get_db_connection()
        if not conn: return
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT username, joined_date, last_interacted_date, last_thanked_month FROM members WHERE user_id = %s", (user_id,))
                existing = cursor.fetchone()
                
                _username = username if username is not None else (existing[0] if existing else None)
                _joined_date = joined_date if joined_date is not None else (existing[1] if existing else None)
                _last_interacted_date = last_interacted_date if last_interacted_date is not None else (existing[2] if existing else None)
                _last_thanked_month = last_thanked_month if last_thanked_month is not None else (existing[3] if existing else 0)

                sql = """
                    INSERT INTO members (user_id, username, joined_date, last_interacted_date, last_thanked_month)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET 
                        username = EXCLUDED.username,
                        joined_date = EXCLUDED.joined_date,
                        last_interacted_date = EXCLUDED.last_interacted_date,
                        last_thanked_month = EXCLUDED.last_thanked_month
                """
                cursor.execute(sql, (user_id, _username, _joined_date, _last_interacted_date, _last_thanked_month))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update member DB for {user_id}: {e}")
            try: conn.rollback()
            except: pass
        finally:
            if conn: conn.close()

    def _get_all_active_members(self):
        conn = self._get_db_connection()
        if not conn: return []
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, username, joined_date, last_interacted_date, last_thanked_month FROM members")
                results = cursor.fetchall()
            return results
        except Exception as e:
            logger.error(f"Failed to get all members: {e}")
            return []
        finally:
            if conn: conn.close()
            
    # --- SCHEDULING FUNCTIONS ---
            
    def _get_last_run_date(self, task_name): 
        conn = self._get_db_connection()
        if not conn: return None
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT last_run_date FROM schedule_log WHERE task_name = %s", (task_name,))
                result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Failed to get last run date for {task_name}: {e}")
            return None
        finally:
            if conn: conn.close()
            
    def _update_last_run_date(self, task_name, run_date): 
        conn = self._get_db_connection()
        if not conn: return
        try:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO schedule_log (task_name, last_run_date) VALUES (%s, %s) ON CONFLICT (task_name) DO UPDATE SET last_run_date = EXCLUDED.last_run_date", (task_name, run_date))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update DB for {task_name}: {e}")
            try: conn.rollback()
            except: pass
        finally:
            if conn: conn.close()
            
    def _get_current_utc_time(self): 
        return datetime.now(timezone.utc)
        
    def check_and_run_schedules(self):
        now_utc = self._get_current_utc_time()
        today_utc_str = now_utc.strftime('%Y-%m-%d')
        this_month_str = now_utc.strftime('%Y-%m')
        this_week_str = now_utc.strftime('%Y-W%U')
        
        # --- NEW SCHEDULES AS REQUESTED ---
        schedules = {
            # Health Reminders (3x Daily, Daily)
            'health_check_00':      {'hour': 0,  'task': self.send_scheduled_health_reminder, 'args': ()},
            'health_check_08':      {'hour': 8,  'task': self.send_scheduled_health_reminder, 'args': ()},
            'health_check_15':      {'hour': 15, 'task': self.send_scheduled_health_reminder, 'args': ()},
            
            # Daily Random Greeting (1x Daily)
            'daily_random_greeting':{'hour': 12, 'task': self.send_daily_random_greeting, 'args': ()},
            
            # Membership Anniversary Check (Monthly)
            'monthly_anniversary_check': {'hour': 5,  'day_of_month': 1, 'task': self.check_monthly_anniversaries, 'args': ()},

            # Birthday Question (Weekly, Sunday = weekday 6)
            'weekly_birthday_ask':  {'hour': 10, 'day_of_week': 6, 'task': self.ask_for_birthdays, 'args': ()},
            
            # AI Renewal (Weekly, Friday = weekday 5)
            'ai_renewal':           {'hour': 10, 'day_of_week': 5, 'task': self.renew_responses_with_ai, 'args': ()} 
        }

        for name, schedule in schedules.items():
            last_run_key = self._get_last_run_date(name)
            should_run = False
            run_marker = today_utc_str # Default: Daily

            # Monthly Check Logic (Run on the 1st of the month)
            if 'day_of_month' in schedule:
                if now_utc.day == schedule['day_of_month'] and now_utc.hour >= schedule['hour'] and last_run_key != this_month_str:
                    should_run = True
                    run_marker = this_month_str
            # Weekly Check Logic (Run on specific day of the week)
            elif 'day_of_week' in schedule:
                if now_utc.weekday() == schedule['day_of_week'] and now_utc.hour >= schedule['hour'] and last_run_key != this_week_str:
                    should_run = True
                    run_marker = this_week_str
            # Daily Check Logic
            else:
                if now_utc.hour >= schedule['hour'] and last_run_key != today_utc_str:
                    should_run = True
            
            if should_run:
                try:
                    logger.info(f"Running scheduled task: {name} at {now_utc.isoformat()}")
                    schedule['task'](*schedule.get('args', ()))
                    self._update_last_run_date(name, run_marker)
                except Exception as e:
                    logger.error(f"Error running scheduled task {name}: {e}", exc_info=True)
    
    # --- AI & RESPONSE FUNCTIONS ---
    
    def _initialize_groq(self):
        api_key = Config.GROQ_API_KEY()
        if not api_key or not groq or not httpx:
            logger.warning("Groq is unavailable or GROQ_API_KEY is missing. AI features disabled.")
            return None
        try:
            client = groq.Groq(api_key=api_key, http_client=httpx.Client(timeout=15.0))
            logger.info("Groq client successfully initialized.")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
            return None
            
    def _load_initial_responses(self):
        # Initial default responses, fully in English, to be overwritten by AI
        return {
            "BOT_IDENTITY": [ "I am the frog assigned to guard the sanity of the NPEPE HODLERS. Ribbit!" ],
            "FINAL_FALLBACK": [ "Ribbit... try again, fren!" ],
            "GREET_NEW_MEMBERS_DELAYED": [ "Welcome 5 minutes late, {name}! LFG!" ],
            "DAILY_GREETING": [ "Hey {mention}, how's the HODL going today?" ],
            "MEMBERSHIP_ANNIVERSARY": [ "WAGMI! {mention}, congrats on {months} months with NPEPE!" ],
            "BIRTHDAY_ASK": [ "Is anyone having a birthday this week? Let NPEPE know!" ],
            "BIRTHDAY_GREETING": [ "HBD {name}, may your gains be massive!" ],
            "HEALTH_REMINDER": [ "Hey, {tags}. Don't forget to rest! The chart can wait." ],
            "COLLABORATION_RESPONSE": [ "WAGMI! Love the energy! The best collab is a strong community. Be loud in here, raid on X, and let's make the NPEPEVERSE impossible to ignore! 🚀" ],
        }
    
    # --- TELEGRAM HANDLERS ---
    
    def _register_handlers(self):
        self.bot.message_handler(content_types=['new_chat_members'])(self.greet_new_members)
        self.bot.message_handler(commands=['start', 'help'])(self.send_welcome)
        self.bot.callback_query_handler(func=lambda call: True)(self.handle_callback_query)
        self.bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'sticker', 'document'])(self.handle_all_text)
    
    def main_menu_keyboard(self):
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(" 🚀  About $NPEPE", callback_data="about"), InlineKeyboardButton(" 🔗  Contract Address", callback_data="ca"),
            InlineKeyboardButton(" 💰  Buy on Pump.fun", url=Config.PUMP_FUN_LINK()), InlineKeyboardButton(" 🌐  Website", url=Config.WEBSITE_URL()),
            InlineKeyboardButton(" ✈️  Telegram", url=Config.TELEGRAM_URL()), InlineKeyboardButton(" 🐦  Twitter", url=Config.TWITTER_URL()),
            InlineKeyboardButton(" 🐸  Hype Me Up!", callback_data="hype")
        )
        return keyboard
  
    def _update_admin_ids(self, chat_id): 
        now = time.time()
        if now - self.admins_last_updated > 600:
            try:
                admins = self.bot.get_chat_administrators(chat_id)
                self.admin_ids = {admin.user.id for admin in admins if admin and admin.user}
                self.admins_last_updated = now
            except Exception as e:
                logger.error(f"Could not update admin list: {e}")
                
    def _is_spam_or_ad(self, message): 
        text = (message.text or message.caption or "") if message else ""
        text_lower = text.lower()
        
        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword in text_lower:
                return True, f"Forbidden Keyword: {keyword}"
        
        if "http" in text_lower or "t.me" in text_lower:
            urls = re.findall(r'(https?://[^\s]+)|([\w\.-]+(?:\.[\w\.-]+)+)', text)
            urls_flat = [u[0] or u[1] for u in urls if u[0] or u[1]]
            for url in urls_flat:
                if not any(domain in url for domain in self.ALLOWED_DOMAINS):
                    return True, f"Unauthorized Link: {url}"
        
        solana_pattern = r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b'
        eth_pattern = r'\b0x[a-fA-F0-9]{40}\b'
        if re.search(solana_pattern, text) and Config.CONTRACT_ADDRESS() not in text:
            return True, "Potential Solana Contract Address"
        if re.search(eth_pattern, text):
            return True, "Potential EVM Contract Address"
            
        return False, None

    def _send_delayed_greeting(self, chat_id, member_id, first_name):
        """Fungsi yang akan dijalankan oleh Timer setelah 5 menit."""
        now_utc = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
        
        # Simpan member ke DB
        self._update_member_info(member_id, first_name, now_utc, last_interacted_date=now_utc)
        
        # Siapkan pesan
        welcome_text = random.choice(self.responses.get("GREET_NEW_MEMBERS_DELAYED", [])).format(name=f"[{first_name}](tg://user?id={member_id})")
        
        try:
            # Kirim pesan
            self.bot.send_message(chat_id, welcome_text, parse_mode="Markdown")
            logger.info(f"Delayed greeting sent to new member: {member_id}")
        except Exception as e:
            logger.error(f"Failed to send welcome message via Timer: {e}")

    def greet_new_members(self, message):
        try:
            for member in message.new_chat_members:
                logger.info(f"New member {member.id} detected. Scheduling delayed greeting in 5 minutes (300 seconds)...")
                
                # Gunakan threading.Timer untuk menunda operasi tanpa memblokir Waitress
                # FIX: Replaced time.sleep(300) with threading.Timer
                first_name = (member.first_name or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                
                timer = threading.Timer(
                    300, # 5 minutes delay
                    self._send_delayed_greeting, 
                    args=[message.chat.id, member.id, first_name]
                )
                timer.start()
                
        except Exception as e:
            logger.error(f"Error in greet_new_members: {e}", exc_info=True)

    def send_welcome(self, message):
        welcome_text = (" 🐸  *Welcome to the official NextPepe ($NPEPE) Bot!* 🔥 \n\n"
                        "I am the spirit of the NPEPEVERSE, here to guide you. Use the buttons below or ask me anything!")
        try:
            self.bot.reply_to(message, welcome_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send /start: {e}")
            
    def handle_callback_query(self, call): 
        try:
            if call.data == "hype":
                hype_text = "LFG! HODL tight, fren!" 
                self.bot.answer_callback_query(call.id, text=hype_text, show_alert=True)
            elif call.data == "about":
                self.bot.answer_callback_query(call.id)
                about_text = (" 🚀  *$NPEPE* is the next evolution of meme power!\n"
                              "We are a community-driven force born on *Pump.fun*.\n\n"
                              "This is 100% pure, unadulterated meme energy. Welcome to the NPEPEVERSE!  🐸 ")
                self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=about_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
            elif call.data == "ca":
                self.bot.answer_callback_query(call.id)
                ca_text = f" 🔗  *Contract Address:*\n`{Config.CONTRACT_ADDRESS()}`"
                self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=ca_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
            else:
                self.bot.answer_callback_query(call.id, text="Action not recognized.")
        except Exception as e:
            logger.error(f"Error in callback handler: {e}", exc_info=True)
            try:
                self.bot.answer_callback_query(call.id, text="Sorry, something went wrong!", show_alert=True)
            except:
                pass
            
    def _is_a_question(self, text): 
        if not text or not isinstance(text, str):
            return False
        txt = text.strip().lower()
        if txt.endswith('?'):
            return True
        question_words = ['what', 'how', 'when', 'where', 'why', 'who', 'can', 'could', 'is', 'are', 'do', 'does', 'explain']
        return any(txt.startswith(w) for w in question_words)

    def handle_all_text(self, message):
        try:
            if not message: return
            
            if message.chat.type in ['group', 'supergroup']:
                chat_id = message.chat.id
                user_id = message.from_user.id
                self._update_admin_ids(chat_id)
                
                is_exempt = user_id in self.admin_ids
                if Config.GROUP_OWNER_ID() and str(user_id) == str(Config.GROUP_OWNER_ID()):
                    is_exempt = True
                if not is_exempt:
                    is_spam, reason = self._is_spam_or_ad(message)
                    if is_spam:
                        try:
                            self.bot.delete_message(chat_id, message.message_id)
                            logger.info(f"Deleted message {message.message_id} from {user_id} reason: {reason}")
                        except Exception as e:
                            logger.error(f"Failed to delete spam message: {e}")
                        return
      
            text = (message.text or message.caption or "")
            if not text: return
            
            lower_text = text.lower().strip()
            chat_id = message.chat.id
            
            # Owner Tag Check
            if (Config.GROUP_OWNER_ID() and message.entities and message.chat.type in ['group', 'supergroup']):
                for entity in message.entities:
                    if getattr(entity, 'type', None) == 'text_mention' and getattr(entity, 'user', None):
                        if str(entity.user.id) == str(Config.GROUP_OWNER_ID()):
                            self.bot.send_message(chat_id, random.choice(self.responses.get("BOT_IDENTITY", []))) 
                            return
            
            # CA & Buy Check
            if any(kw in lower_text for kw in ["ca", "contract", "address"]):
                self.bot.send_message(chat_id, f"Here is the contract address, fren:\n\n`{Config.CONTRACT_ADDRESS()}`", parse_mode="Markdown")
                return
            if any(kw in lower_text for kw in ["how to buy", "where to buy", "buy npepe"]):
                self.bot.send_message(chat_id, " 💰  You can buy *$NPEPE* on Pump.fun! The portal to the moon is one click away!  🚀 ", parse_mode="Markdown", reply_markup=self.main_menu_keyboard())
                return
            
            # --- NEW BOT LOGIC ---
            
            # Bot Identity Response
            if any(kw in lower_text for kw in ["what are you", "what is this bot", "are you a bot", "what kind of bot", "who made you"]):
                logger.info("Bot identity question detected, responding immediately...")
                self.bot.send_message(chat_id, random.choice(self.responses.get("BOT_IDENTITY", [])))
                return
            
            # Birthday Response
            if any(kw in lower_text for kw in ["my birthday", "my bday", "it's my birthday", "my birthday this week"]) and message.chat.type in ['group', 'supergroup']:
                try:
                    self.bot.reply_to(message, random.choice(self.responses.get("BIRTHDAY_GREETING", [])).format(name=message.from_user.first_name), parse_mode="Markdown")
                    return
                except Exception as e:
                    logger.error(f"Failed to send birthday greeting: {e}")
                    
            # Collaboration Response (Retained)
            if any(kw in lower_text for kw in ["collab", "partner", "promote", "help grow", "shill", "marketing"]):
                self.bot.send_message(chat_id, random.choice(self.responses.get("COLLABORATION_RESPONSE", [])))
                return
            
            # AI Response for Questions
            if self.groq_client and self._is_a_question(text):
                # Dispatch AI processing to a separate thread to prevent blocking Waitress
                threading.Thread(target=self._process_ai_response, args=(chat_id, text)).start()
                return
            
        except Exception as e:
            logger.error(f"FATAL ERROR processing message: {e}", exc_info=True)

    def _process_ai_response(self, chat_id, text):
        """Dedicated function to handle the blocking AI request."""
        thinking_message = None
        try:
            thinking_message = self.bot.send_message(chat_id, " 🐸  The NPEPE oracle is consulting the memes...")
            system_prompt = (
                "You are a crypto community bot for $NPEPE. Funny, enthusiastic, chaotic. "
                "Use slang: ‘fren’, ‘WAGMI’, ‘HODL’, ‘based’, ‘LFG’, ‘ribbit’. Keep answers short."
            )
            chat_completion = self.groq_client.chat.completions.create(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
                model="llama3-8b-8192", 
                temperature=0.7, max_tokens=150
            )
            ai_response = chat_completion.choices[0].message.content
            try:
                self.bot.edit_message_text(ai_response, chat_id=chat_id, message_id=thinking_message.message_id)
            except Exception:
                self.bot.send_message(chat_id, ai_response)
        except Exception as e:
            logger.error(f"AI response error: {e}", exc_info=True)
            fallback = random.choice(self.responses.get("FINAL_FALLBACK", ["Sorry fren, can’t answer now."]))
            try:
                if thinking_message: self.bot.edit_message_text(fallback, chat_id=chat_id, message_id=thinking_message.message_id)
                else: self.bot.send_message(chat_id, fallback)
            except Exception as ex:
                logger.error(f"Failed to send fallback: {ex}")

    # --- NEW SCHEDULED TASK FUNCTIONS ---

    def send_daily_random_greeting(self):
        """Task 1: Greets 3 random members daily (turn-based mode)."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # 1. Get all members from DB
        all_members = self._get_all_active_members()
        if not all_members:
            logger.warning("No members in DB to greet.")
            return

        # 2. Sort by last_interacted_date (for rotation/turn)
        sorted_members = sorted(all_members, key=lambda x: datetime.strptime(x[3], '%Y-%m-%d %H:%M:%S') if x[3] else datetime.min)
        
        members_to_greet = []
        now_ts_str = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
        
        for i in range(3): # Try to greet 3 members
            if not sorted_members: break
            
            member_data = sorted_members.pop(0) 
            user_id, username, joined_date, last_interacted_date, last_thanked_month = member_data

            try:
                # 3. Check membership in group (skip if no longer a member)
                chat_member = self.bot.get_chat_member(group_id, user_id)
                if chat_member.status in ['member', 'administrator', 'creator']:
                    members_to_greet.append((user_id, username))
                    
                    # Mark as greeted in DB
                    self._update_member_info(user_id, last_interacted_date=now_ts_str)
                else:
                    logger.info(f"Member {user_id} is no longer active. Skipping.")
            except telebot.apihelper.ApiTelegramException as e:
                if "user not found in chat" in str(e):
                    logger.info(f"Member {user_id} left the group. Skipping.")
                else:
                    logger.error(f"Error checking membership for {user_id}: {e}")
            except Exception as e:
                logger.error(f"Error checking membership for {user_id}: {e}")

        # 4. Send greetings to 3 valid members
        if members_to_greet:
            message_parts = []
            for user_id, username in members_to_greet:
                greeting = random.choice(self.responses.get("DAILY_GREETING", [])).format(mention=f"[{username or 'Fren'}](tg://user?id={user_id})")
                message_parts.append(greeting)
            
            final_message = "\n\n---\n\n".join(message_parts)
            try:
                self.bot.send_message(group_id, final_message, parse_mode="Markdown")
                logger.info(f"Sent random daily greeting to {len(members_to_greet)} members.")
            except Exception as e:
                logger.error(f"Failed to send daily greeting: {e}", exc_info=True)
                
    def check_monthly_anniversaries(self):
        """Task 2: Sends thank you messages for membership anniversaries."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        now = self._get_current_utc_time()
        now_date = now.date()
        
        members_to_thank = []
        for user_id, username, joined_date_str, _, last_thanked_month in self._get_all_active_members():
            if not joined_date_str: continue
            
            # Calculate month difference
            joined_date = datetime.strptime(joined_date_str.split()[0], '%Y-%m-%d').date()
            month_diff = (now_date.year - joined_date.year) * 12 + now_date.month - joined_date.month
            
            # Check if: (1) It's been 1 month or more, AND (2) Today is their join date,
            # AND (3) They haven't been thanked for this membership month yet.
            if month_diff > 0 and now_date.day == joined_date.day and month_diff > last_thanked_month:
                members_to_thank.append((user_id, username, month_diff))

        if members_to_thank:
            message_parts = []
            for user_id, username, months in members_to_thank:
                mention = f"[{username or 'Fren'}](tg://user?id={user_id})"
                thanks_message = random.choice(self.responses.get("MEMBERSHIP_ANNIVERSARY", [])).format(mention=mention, months=months)
                message_parts.append(thanks_message)
                
                # Update DB: Mark as thanked for this month
                self._update_member_info(user_id, last_thanked_month=months)

            final_message = "\n\n---\n\n".join(message_parts)
            try:
                self.bot.send_message(group_id, final_message, parse_mode="Markdown")
                logger.info(f"Sent membership anniversary greetings for {len(members_to_thank)} members.")
            except Exception as e:
                logger.error(f"Failed to send membership anniversary greetings: {e}")
                
    def ask_for_birthdays(self):
        """Task 3: Asks for birthdays weekly."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        message = random.choice(self.responses.get("BIRTHDAY_ASK", []))
        try:
            self.bot.send_message(group_id, message, parse_mode="Markdown")
            logger.info("Sent weekly birthday question.")
        except Exception as e:
            logger.error(f"Failed to send birthday question: {e}")

    def send_scheduled_health_reminder(self):
        """Task 4: Reminds members to take care of their health 3x a day."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # Get all valid members in the group to tag
        all_members = self._get_all_active_members()
        
        # Filter for active members
        valid_members = []
        for user_id, username, _, _, _ in all_members:
             try:
                chat_member = self.bot.get_chat_member(group_id, user_id)
                if chat_member.status in ['member', 'administrator', 'creator']:
                    valid_members.append((user_id, username))
             except:
                continue

        # Take 5 random tags (limited for API compliance/spam reduction)
        tags_to_use = random.sample(valid_members, min(5, len(valid_members)))
        tags_list = [f"[{username or 'Fren'}](tg://user?id={user_id})" for user_id, username in tags_to_use]
        tags_string = " ".join(tags_list) if tags_list else "Frens"

        message_template = random.choice(self.responses.get("HEALTH_REMINDER", []))
        
        final_message = f"🚨 **ATTENTION NPEPE ARMY** 🚨\n\n{message_template.format(tags=tags_string)}"

        try:
            self.bot.send_message(group_id, final_message, parse_mode="Markdown")
            logger.info("Sent scheduled health reminder.")
        except Exception as e:
            logger.error(f"Failed to send health reminder: {e}", exc_info=True)
            
    # --- AI RENEWAL FUNCTION ---

    def renew_responses_with_ai(self):
        logger.info("Starting weekly AI response renewal process.")
        if not self.groq_client:
            logger.warning("Skipping AI renewal: Groq not initialized.")
            return

        categories_to_renew = {
            "GREET_NEW_MEMBERS_DELAYED": ("Produce 50 unique, friendly, and funny welcome messages for new members in a crypto group after a 5-minute delay. Must include the placeholder '{name}'. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 40),
            "DAILY_GREETING": ("Produce 50 short, energetic, and based check-in messages for a crypto group bot. The message should tag a random user (using the placeholder '{mention}') and ask how they are HODLing up or how their day is going. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 40),
            "MEMBERSHIP_ANNIVERSARY": ("Produce 50 unique, proud, and enthusiastic messages to thank a member for their long-term HODLing in a meme coin community (1, 2, 3, etc., months). Must include placeholders '{mention}' for the user and '{months}' for the duration. Use strong $NPEPE/meme coin slang: 'diamond hands', 'based', 'LFG', 'WAGMI'.", 40),
            "BIRTHDAY_ASK": ("Produce 30 unique, fun, and casual messages for a meme coin bot to ask the community if anyone is having a birthday this week. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based'.", 25),
            "BIRTHDAY_GREETING": ("Produce 30 unique, energetic, and funny birthday greetings for a meme coin member. Use the placeholder '{name}' and $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 25),
            "HEALTH_REMINDER": ("Produce 100 unique, caring, but meme-themed messages reminding all crypto members to take a break, eat, rest, and spend time with loved ones, to prevent burnout from trading/HODLing. Must include the placeholder '{tags}' for a list of mentions. Use $NPEPE slang: 'fren', 'diamond hands', 'HODL'.", 80),
            "BOT_IDENTITY": ("Produce 20 short, chaotic, and proud identity answers for a meme coin bot. The bot should explicitly state it is a 'frog' whose task is to keep members 'sane' while they HODL $NPEPE. Use $NPEPE slang: 'fren', 'ribbit', 'based'.", 15),
            "COLLABORATION_RESPONSE": ("Produce 20 enthusiastic responses for a meme coin bot when someone asks about collaboration or marketing. Focus on community effort (raids, memes) over paid promotion. Use slang.", 15),
        }
        
        for category, (prompt, min_count) in categories_to_renew.items():
            try:
                logger.info(f"Requesting AI update for category: {category}...")
                completion = self.groq_client.chat.completions.create(
                    messages=[{"role": "system", "content": prompt}],
                    model="llama3-8b-8192", temperature=1.0, max_tokens=2000
                )
                text = completion.choices[0].message.content
                new_lines = [line.strip() for line in re.split(r'\n|\d+\.', text) if line.strip() and len(line) > 5]
                
                # Filter for required placeholders
                placeholder_map = {
                    "GREET_NEW_MEMBERS_DELAYED": '{name}', 
                    "DAILY_GREETING": '{mention}', 
                    "MEMBERSHIP_ANNIVERSARY": '{mention}', 
                    "BIRTHDAY_GREETING": '{name}', 
                    "HEALTH_REMINDER": '{tags}'
                }
                
                placeholder = placeholder_map.get(category)
                if placeholder:
                    new_lines = [line for line in new_lines if placeholder in line]
                    
                if category == "MEMBERSHIP_ANNIVERSARY":
                    new_lines = [line for line in new_lines if '{months}' in line]

                if len(new_lines) >= min_count:
                    self.responses[category] = new_lines
                    logger.info(f" ✅  Category '{category}' successfully updated by AI with {len(new_lines)} new entries.")
                else:
                    logger.warning(f" ⚠️  AI update for '{category}' only produced {len(new_lines)} lines (needed {min_count}); update skipped.")
            except Exception as e:
                logger.error(f" ❌  Failed to update category '{category}' with AI: {e}", exc_info=True)
