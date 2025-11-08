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
    logging.info("DIAGNOSTICS: 'psycopg2' library SUCCESSFULLY imported.")
except ImportError as e:
    psycopg2 = None
    logging.critical(f"DIAGNOSTICS: CRITICAL - FAILED to import 'psycopg2'. Persistence will be disabled. Error: {e}")

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
            logger.critical("FATAL: DATABASE_URL not found or psycopg2 unavailable. Persistence will not function.")
            
        self.groq_client = self._initialize_groq()
        self.responses = self._load_initial_responses()
        self.admin_ids = set()
        self.admins_last_updated = 0
    
        # New Bot Constants
        self.MEMBER_GREET_DELAY = 300 # 5 minutes (300 seconds)
        self.RANDOM_MEMBER_MENTION_COUNT = 3 # 3 users per day
        self.MEMBER_MENTION_COOLDOWN = 60 # Cooldown between mentions in a single run

        # Existing Constants (cooldown/reply chance are removed)
        self.HYPE_KEYWORDS = ['buy', 'bought', 'pump', 'moon', 'lfg', 'send it', 'green', 'bullish', 'rocket', 'diamond', 'hodl', 'ape', 'lets go', 'ath']
        self.FORBIDDEN_KEYWORDS = ['airdrop', 'giveaway', 'presale', 'private sale', 'whitelist', 'signal', 'pump group', 'trading signal', 
'investment advice', 'other project']
        self.ALLOWED_DOMAINS = ['pump.fun', 't.me/NPEPEVERSE', 'x.com/NPEPE_Verse', 'base44.app']
        
        self._ensure_db_table_exists() # schedule_log
        self._ensure_member_table_exists() # group_members
        self._populate_initial_members() # Run once on startup to mark existing members as OG
        self._register_handlers()
        logger.info("BotLogic successfully initialized.")

    # --- Database and Persistence Functions ---

    def _get_db_connection(self):
        db_url = Config.DATABASE_URL()
        if not db_url or not psycopg2:
            logger.warning("DATABASE_URL is not set or psycopg2 is not installed. Persistence disabled.")
            return None
        try:
            # Use conn.cursor(cursor_factory=psycopg2.extras.DictCursor) for dictionary access if needed
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
                
    def _ensure_member_table_exists(self):
        conn = self._get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    # 'join_date' is used for anniversary calculations
                    # 'last_mention' for tracking random mention cycles
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS group_members (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            first_name TEXT,
                            join_date TEXT,
                            last_mention TEXT
                        )
                    """)
                conn.commit()
                logger.info("Database table 'group_members' is ready.")
            except Exception as e:
                logger.error(f"Failed to create member table: {e}")
            finally:
                conn.close()
                
    def _populate_initial_members(self):
        """
        Fetches existing group members and marks them as 'Pre-Bot/OG' in the DB.
        Executed once on initialization.
        """
        group_id = Config.GROUP_CHAT_ID()
        if not group_id or not self.bot:
            return
            
        conn = self._get_db_connection()
        if not conn: return
        
        # Marker for OG/Pre-Bot members
        og_join_date = '1970-01-01 00:00:00'
        
        try:
            # NOTE: get_chat_members() can be slow/rate-limited for very large groups.
            # We assume it completes successfully for initial deployment.
            members = self.bot.get_chat_members(group_id)
            members_added = 0
            
            with conn.cursor() as cursor:
                for member in members:
                    if member.user.is_bot:
                        continue
                        
                    # Check if member already exists
                    cursor.execute("SELECT user_id FROM group_members WHERE user_id = %s", (member.user.id,))
                    if cursor.fetchone() is None:
                        # Insert as OG member
                        cursor.execute("""
                            INSERT INTO group_members (user_id, username, first_name, join_date) 
                            VALUES (%s, %s, %s, %s)
                        """, (
                            member.user.id, 
                            member.user.username, 
                            member.user.first_name, 
                            og_join_date
                        ))
                        members_added += 1
                        
            conn.commit()
            logger.info(f"Initial population complete. Added {members_added} existing members as OG.")
        except Exception as e:
            logger.error(f"Failed to populate initial members: {e}")
            try: conn.rollback()
            except: pass
        finally:
            if conn: conn.close()
            
    def _add_or_update_member(self, member):
        """Adds a new member or updates an existing one (for new joins)."""
        conn = self._get_db_connection()
        if not conn: return
        try:
            now_str = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
            with conn.cursor() as cursor:
                # Check if the member exists
                cursor.execute("SELECT join_date FROM group_members WHERE user_id = %s", (member.id,))
                result = cursor.fetchone()
                
                if result is None:
                    # New member, record accurate join date
                    join_date = now_str
                    cursor.execute("""
                        INSERT INTO group_members (user_id, username, first_name, join_date) 
                        VALUES (%s, %s, %s, %s) 
                        ON CONFLICT (user_id) DO NOTHING
                    """, (member.id, member.username, member.first_name, join_date))
                else:
                    # Existing member, only update username/first_name
                    cursor.execute("""
                        UPDATE group_members SET username = %s, first_name = %s 
                        WHERE user_id = %s
                    """, (member.username, member.first_name, member.id))
            conn.commit()
            logger.debug(f"Member {member.id} added/updated.")
        except Exception as e:
            logger.error(f"Failed to add/update member {member.id}: {e}")
            try: conn.rollback()
            except: pass
        finally:
            if conn: conn.close()

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
        
    def _get_next_member_to_mention(self):
        conn = self._get_db_connection()
        if not conn: return None
        try:
            with conn.cursor() as cursor:
                # Select members who are NOT bots (user_id not in a list of known bot IDs) 
                # and have the oldest last_mention date.
                cursor.execute("""
                    SELECT user_id, first_name
                    FROM group_members
                    WHERE first_name NOT LIKE '%%Bot%%' AND username NOT LIKE '%%bot%%'
                    ORDER BY last_mention ASC NULLS FIRST
                    LIMIT 1
                """)
                result = cursor.fetchone()
                
                if result:
                    user_id, first_name = result
                    # Mark as mentioned
                    now_str = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
                    cursor.execute("UPDATE group_members SET last_mention = %s WHERE user_id = %s", (now_str, user_id))
                    conn.commit()
                    return {'user_id': user_id, 'first_name': first_name}
                return None
        except Exception as e:
            logger.error(f"Failed to get next member for mention: {e}")
            return None
        finally:
            if conn: conn.close()
            
    def _get_members_by_join_month(self, months_ago):
        """Retrieves members celebrating their X-month anniversary today."""
        conn = self._get_db_connection()
        if not conn: return []
        
        try:
            today = self._get_current_utc_time()
            members_data = []
            
            with conn.cursor() as cursor:
                # Select members where join_date is NOT the OG date
                # And the DAY part of join_date matches today's day
                cursor.execute("""
                    SELECT user_id, first_name, join_date
                    FROM group_members
                    WHERE join_date != '1970-01-01 00:00:00'
                    AND EXTRACT(DAY FROM join_date::date) = %s
                """, (today.day,))
                
                for row in cursor.fetchall():
                    user_id, first_name, join_date_str = row
                    
                    # The join_date from the DB might have a fractional part, so we split it off
                    join_date = datetime.strptime(join_date_str.split('.')[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)

                    # Calculate the difference in months
                    months_diff = (today.year - join_date.year) * 12 + today.month - join_date.month
                    
                    if months_diff == months_ago:
                        members_data.append({'user_id': user_id, 'first_name': first_name})
                        
            return members_data
        except Exception as e:
            logger.error(f"Failed to get members by join month {months_ago}: {e}")
            return []
        finally:
            if conn: conn.close()

    def _get_pre_bot_members(self):
        """Retrieves members marked as OG/Pre-Bot."""
        conn = self._get_db_connection()
        if not conn: return []
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT user_id, first_name
                    FROM group_members
                    WHERE join_date = '1970-01-01 00:00:00'
                """)
                return [{'user_id': user_id, 'first_name': first_name} for user_id, first_name in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get pre-bot members: {e}")
            return []
        finally:
            if conn: conn.close()
            
    def _get_all_active_members(self):
        conn = self._get_db_connection()
        if not conn: return []
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, first_name FROM group_members")
                return [{'user_id': user_id, 'first_name': first_name} for user_id, first_name in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get all active members: {e}")
            return []
        finally:
            if conn: conn.close()

    # --- Scheduler Functions ---

    def check_and_run_schedules(self):
        """Checks and runs scheduled tasks based on current UTC time and last run log."""
        now_utc = self._get_current_utc_time()
        today_utc_str = now_utc.strftime('%Y-%m-%d')
        
        schedules = {
            # Daily Tasks (Welfare/Mention)
            'daily_member_mention':         {'hour': 0, 'task': self.mention_random_members, 'args': ()},
            'daily_welfare_reminder_utc08': {'hour': 8, 'task': self.send_welfare_reminder, 'args': ()},
            'daily_welfare_reminder_utc15': {'hour': 15, 'task': self.send_welfare_reminder, 'args': ()},
            
            # Monthly/Weekly Tasks (Run once per period, day/hour is just a check point)
            'monthly_anniversary_check':    {'hour': 5, 'task': self.check_monthly_anniversaries, 'args': ()},
            'weekly_birthday_chat':         {'hour': 5, 'day_of_week': 0, 'task': self.send_weekly_birthday_chat, 'args': ()}, # Sunday (0)
            'ai_renewal':                   {'hour': 10, 'day_of_week': 5, 'task': self.renew_responses_with_ai, 'args': ()} # Friday (5)
        }
        
        for name, schedule in schedules.items():
            last_run_date = self._get_last_run_date(name)
            should_run = False
            is_weekly = 'day_of_week' in schedule
            
            if is_weekly:
                iso_key = now_utc.strftime('%Y-W%U') # Week marker
                if (now_utc.weekday() == schedule['day_of_week'] and now_utc.hour >= schedule['hour'] and last_run_date != iso_key):
                    should_run = True
            else:
                # For daily tasks, check if the hour is passed and hasn't run today
                if (now_utc.hour >= schedule['hour'] and last_run_date != today_utc_str):
                    should_run = True
                # Special handling for monthly checks to run only once per month. 
                # We reuse the daily logic, but the task itself checks if it already ran this month.
                if 'monthly' in name and last_run_date and datetime.strptime(last_run_date, '%Y-%m-%d').month == now_utc.month:
                    should_run = False # Already ran this month
            
            if should_run:
                try:
                    logger.info(f"Running scheduled task: {name} at {now_utc.isoformat()}")
                    schedule['task'](*schedule.get('args', ()))
                    run_marker = iso_key if is_weekly else today_utc_str
                    self._update_last_run_date(name, run_marker)
                except Exception as e:
                    logger.error(f"Error running scheduled task {name}: {e}", exc_info=True)

    # --- New Scheduled Task Implementations ---

    def mention_random_members(self):
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return

        for _ in range(self.RANDOM_MEMBER_MENTION_COUNT):
            member_data = self._get_next_member_to_mention()
            if member_data:
                user_id, first_name = member_data['user_id'], member_data['first_name']
                try:
                    first_name_clean = (first_name or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                    mention = f"[{first_name_clean}](tg://user?id={user_id})"
                    
                    # Use new greeting: `DAILY_MEMBER_CHECK`
                    message = random.choice(self.responses.get("DAILY_MEMBER_CHECK", [])).format(name=mention)
                    
                    self.bot.send_message(group_id, message, parse_mode="Markdown")
                    logger.info(f"Mentioned random member {user_id} in group {group_id}.")
                    time.sleep(self.MEMBER_MENTION_COOLDOWN) # Cooldown
                except Exception as e:
                    logger.error(f"Failed to mention member {user_id} in group {group_id}: {e}")
            else:
                logger.info("No more members to mention in the current cycle.")
                break 

    def check_monthly_anniversaries(self):
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # === 1. Monthly Member Check (1, 2, 3, ... months) ===
        for month in range(1, 13): 
            anniversary_members = self._get_members_by_join_month(month)
            if anniversary_members:
                logger.info(f"Found {len(anniversary_members)} members with {month}-month anniversary.")
                
                member_mentions = []
                for member in anniversary_members:
                    first_name_clean = (member['first_name'] or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                    member_mentions.append(f"[{first_name_clean}](tg://user?id={member['user_id']})")
                
                mention_list = ", ".join(member_mentions)
                
                message_template = random.choice(self.responses.get("ANNIVERSARY_GREETING", [])).format(
                    months=month, 
                    names=mention_list
                )
                
                try:
                    self.bot.send_message(group_id, message_template, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send anniversary message for {month} months: {e}")

        # === 2. Pre-Bot Member Check (OGs) ===
        pre_bot_members = self._get_pre_bot_members()
        if pre_bot_members:
            logger.info(f"Found {len(pre_bot_members)} pre-bot members for special greeting.")

            pre_bot_mentions = []
            for member in pre_bot_members:
                first_name_clean = (member['first_name'] or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                pre_bot_mentions.append(f"[{first_name_clean}](tg://user?id={member['user_id']})")
            
            mention_list_pre_bot = ", ".join(pre_bot_mentions)

            # Use 'OG' as a placeholder for the template to differentiate
            message_template_pre_bot = random.choice(self.responses.get("ANNIVERSARY_GREETING", [])).format(
                months='OG', 
                names=mention_list_pre_bot
            )
            
            try:
                self.bot.send_message(group_id, message_template_pre_bot, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send pre-bot anniversary message: {e}")
                
    def send_weekly_birthday_chat(self):
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        all_members = self._get_all_active_members()
        if not all_members: return
        
        member_mentions = []
        for member in all_members:
            first_name_clean = (member['first_name'] or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
            member_mentions.append(f"[{first_name_clean}](tg://user?id={member['user_id']})")
        
        mention_list = ", ".join(member_mentions)
        
        # Use new chat: `BIRTHDAY_CHAT`
        message_template = random.choice(self.responses.get("BIRTHDAY_CHAT", [])).format(names=mention_list)
        
        try:
            self.bot.send_message(group_id, message_template, parse_mode="Markdown")
            logger.info("Sent weekly birthday chat and tagged all members.")
        except Exception as e:
            logger.error(f"Failed to send weekly birthday chat: {e}")

    def send_welfare_reminder(self):
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        all_members = self._get_all_active_members()
        if not all_members: return
        
        member_mentions = []
        for member in all_members:
            first_name_clean = (member['first_name'] or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
            member_mentions.append(f"[{first_name_clean}](tg://user?id={member['user_id']})")
            
        mention_list = ", ".join(member_mentions)
        
        # Use new reminder: `WELFARE_REMINDER`
        reminder_template = random.choice(self.responses.get("WELFARE_REMINDER", [])).format(names=mention_list)
        
        try:
            self.bot.send_message(group_id, reminder_template, parse_mode="Markdown")
            logger.info("Sent daily welfare reminder and tagged all members.")
        except Exception as e:
            logger.error(f"Failed to send welfare reminder: {e}")

    # --- Groq and AI Functions ---
    
    def _initialize_groq(self):
        # [span_0](start_span)... (same as original) [cite: 30-31]
        api_key = Config.GROQ_API_KEY()
        if not api_key or not groq or not httpx:
            logger.warning("Groq unavailable or GROQ_API_KEY missing. AI features disabled.")
            return None
        try:
            client = groq.Groq(api_key=api_key, http_client=httpx.Client(timeout=15.0))
            logger.info("Groq client successfully initialized.")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
            return None

    def _load_initial_responses(self):
        """Loads default responses, prioritizing English and new categories."""
        return {
            # New Identity for the 'Sanity Frog'
            "BOT_IDENTITY": [
                "I'm the designated 'sanity frog' for the NPEPEVERSE. My only job is to ensure you stay based, diamond-handed, and sane while trading. Ribbit! 🐸",
                "Call me the Chief Welfare Officer. I'm here to remind you to eat, sleep, and HODL. What more do you need? LFG! 🔥"
            ],
            # New Daily Check-in (50 variations, must include {name})
            "DAILY_MEMBER_CHECK": [
                "Hey {name}! Just checking in, fren. Did you eat your green candle today? WAGMI! 💚",
                "Ribbit {name}! How's the view from your diamond hands? Are we still on course for the moon? 🚀"
            ],
            # New Anniversary Greetings (50 variations, must include {months} or be OG, and {names})
            "ANNIVERSARY_GREETING": [
                "BIG HYPE for our legends celebrating {months} months: {names}! Thanks for being based. Let's get these gains! 💎",
                "Frog Salute to {names} for {months} months of pure HODL energy! You are the NPEPEVERSE. LFG! 🐸",
                "A true OG salute to {names}! You were here before the webhook was warm. Thank you for your diamond hands! 🔥"
            ],
            # New Weekly Birthday Chat (20 variations, must include {names})
            "BIRTHDAY_CHAT": [
                "Attention, NPEPEVERSE: {names}! Is it anyone's birthday week? If so, wish them a based day! And if not, buy more NPEPE anyway! 🎂",
                "Weekly Vibe Check, frens, including {names}! Any birthday legends out there? Wishing everyone a week of green! 💚"
            ],
            # New Welfare Reminder (100 variations, must include {names})
            "WELFARE_REMINDER": [
                "Hey {names}, put the charts down for 5 mins. Go hydrate, touch grass, and hug your loved ones. Then come back and HODL harder! 💪",
                "To all the diamond hands, especially {names}: The market is 24/7, but you're not. Rest up, fren. See you at the next ATH! 🚀"
            ],
            # Existing responses (kept for fallback/other functions)
            "WHO_IS_OWNER": [ "My dev? Think Satoshi Nakamoto, but with way more memes. A mysterious legend who dropped some based code and vanished into the hype. 🐸👻 ", "The dev is busy. I'm the caretaker. Any complaints can be submitted to me in the form of a 100x pump. 📈 " ],
            "FINAL_FALLBACK": [ "My circuits are fried from too much hype. Try asking that again, or maybe just buy more $NPEPE? That usually fixes things. 🐸 ", "Ribbit... what was that? I was busy staring at the chart. Could you rephrase for this simple frog bot? 📈 " ],
            "GREET_NEW_MEMBERS": [ " 🐸  Welcome to the NPEPEVERSE, {name}! We're a frenly bunch. LFG! 🚀 ", "Ribbit! A new fren has appeared! Welcome, {name}! Glad to have you hopping with us. 🐸💚 " ],
            "HYPE": [ "Let's go, NPEPE army! Time to make some noise! 🚀 ", "Who's feeling bullish today?! 🔥 " ],
            "WISDOM": [ "The greatest gains are not in the chart, but in the strength of the community. WAGMI. 🐸💚 ", "Fear is temporary, HODLing is forever. Stay strong, fren." ],
            "COLLABORATION_RESPONSE": [ "WAGMI! Love the energy! The best collab is a strong community. Be loud in here, raid on X, and let's make the NPEPEVERSE impossible to ignore! 🚀 ", "Thanks, fren! We don't do paid promos, we ARE the promo! Your hype is the best marketing. Light up X with $NPEPE memes and be a legend in this chat! 🔥 " ],
        }
    
    def renew_responses_with_ai(self):
        logger.info("Starting weekly AI response renewal process.")
        if not self.groq_client:
            logger.warning("Skipping AI renewal: Groq not initialized.")
            return
            
        categories_to_renew = {
            # 20 Variasi
            "BOT_IDENTITY": ("Produce 20 short, enthusiastic responses for a meme coin bot when asked 'what it is'. Use slang: 'sanity frog', 'CWO', 'based', 'ribbit'. Must focus on keeping members sane/focused on HODL.", 15),
            
            # 50 Variasi
            "DAILY_MEMBER_CHECK": ("Produce 50 short, friendly messages for a meme coin bot to randomly check on a user. Must include the placeholder '{name}'. Use slang: 'fren', 'WAGMI', 'green candle'.", 40),
            
            # 50 Variasi
            "ANNIVERSARY_GREETING": ("Produce 50 short, enthusiastic 'thank you' messages for users. HALF should be for monthly join anniversaries (include placeholders '{months}' and '{names}'). The OTHER HALF should be for 'pre-bot' members (only include placeholder '{names}' and refer to them as OGs or founders). Use slang: 'based', 'HODL', 'legend'.", 40),

            # 20 Variasi
            "BIRTHDAY_CHAT": ("Produce 20 short, casual chat messages for a bot to send on a Monday, asking if anyone has a birthday that week. Must include the placeholder '{names}' to tag all members. Use meme slang.", 15),

            # 100 Variasi
            "WELFARE_REMINDER": ("Produce 100 short, caring but meme-themed messages reminding users to rest, eat, or spend time with family. Must include the placeholder '{names}' to tag all members. Use slang: 'diamond hands', 'touch grass'.", 80),
            
            # 20 Variasi
            "GREET_NEW_MEMBERS": ("Produce 20 unique welcome messages for new members in a crypto group. Must include the placeholder '{name}'. Friendly and exciting.", 10),
            
            # 100 Variasi
            "HYPE": ("Produce 100 short hype messages for a meme coin bot. Funny, enthusiastic, use slang like LFG, WAGMI, ribbit, fren. Each 5-30 words.", 50),
            
            # 20 Variasi
            "WISDOM": ("Produce 20 wise, motivational quotes for a crypto community. Meme-themed, short, inspiring about HODL, community, moon.", 10),

        }
        
        for category, (prompt, min_count) in categories_to_renew.items():
            try:
                logger.info(f"Requesting AI to renew category: {category}...")
                completion = self.groq_client.chat.completions.create(
                    messages=[{"role": "system", "content": prompt}],
                    model="llama3-8b-8192", temperature=1.0, max_tokens=2000
                )
                text = completion.choices[0].message.content
                # Split lines by newline or numbered list format
                new_lines = [line.strip() for line in re.split(r'\n|\d+\.', text) if line.strip() and len(line) > 5]
                
                # Enforce placeholder for specific categories
                if category == "GREET_NEW_MEMBERS":
                    new_lines = [line for line in new_lines if '{name}' in line]
                if category in ["DAILY_MEMBER_CHECK", "ANNIVERSARY_GREETING", "BIRTHDAY_CHAT", "WELFARE_REMINDER"]:
                    new_lines = [line for line in new_lines if '{names}' in line or ('{months}' in line and category == "ANNIVERSARY_GREETING")]
                    
                if len(new_lines) >= min_count:
                    self.responses[category] = new_lines
                    logger.info(f" ✅  Category '{category}' successfully renewed by AI with {len(new_lines)} new entries.")
                else:
                    logger.warning(f" ⚠️  AI renewal for '{category}' only produced {len(new_lines)} lines (needed {min_count}); renewal skipped.")
            except Exception as e:
                logger.error(f" ❌  Failed to renew category '{category}' with AI: {e}", exc_info=True)

    # --- Telegram Handlers ---

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
        # [cite_start]... (same as original) [cite: 129-130]
        now = time.time()
        if now - self.admins_last_updated > 600:
            try:
                admins = self.bot.get_chat_administrators(chat_id)
                self.admin_ids = {admin.user.id for admin in admins if admin and admin.user}
                self.admins_last_updated = now
            except Exception as e:
                logger.error(f"Could not update admin list: {e}")
                
    def _is_spam_or_ad(self, message):
        # [cite_start]... (same as original, but ensure all logic is in English context) [cite: 131-133]
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
        
    def greet_new_members(self, message):
        try:
            new_members = message.new_chat_members
            for member in new_members:
                # Add/Update member to DB immediately
                self._add_or_update_member(member)
                
                if member.is_bot: continue 
                
                # Use threading.Timer for the 5-minute delay (300 seconds)
                def delayed_greeting(chat_id, member_id, first_name):
                    try:
                        first_name_clean = (first_name or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                        # Use the GREET_NEW_MEMBERS response category
                        welcome_text = random.choice(self.responses.get("GREET_NEW_MEMBERS", [])).format(name=f"[{first_name_clean}](tg://user?id={member_id})")
                        self.bot.send_message(chat_id, welcome_text, parse_mode="Markdown")
                        logger.info(f"Delayed welcome sent to new member: {member_id}")
                    except Exception as e:
                        logger.error(f"Failed to send delayed welcome message to {member_id}: {e}")

                threading.Timer(self.MEMBER_GREET_DELAY, 
                                delayed_greeting, 
                                args=(message.chat.id, member.id, member.first_name)).start()
                
            logger.info(f"New members detected, delayed greeting scheduled for {len(new_members)} members.")
        except Exception as e:
            logger.error(f"Error in greet_new_members: {e}", exc_info=True)
            
    def send_welcome(self, message):
        # [cite_start]... (same as original)[span_0](end_span)
        welcome_text = (" 🐸  *Welcome to the official NextPepe ($NPEPE) Bot!* 🔥 \n\n"
                        "I am the spirit of the NPEPEVERSE, here to guide you. Use the buttons below or ask me anything!")
        try:
            self.bot.reply_to(message, welcome_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send /start: {e}")
            
    def handle_callback_query(self, call):
        # [span_1](start_span)... (same as original) [cite: 138-141]
        try:
            if call.data == "hype":
                hype_text = random.choice(self.responses.get("HYPE", ["LFG!"]))
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
        # [cite_start]... (same as original)[span_1](end_span)
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
            
            # --- Anti-Spam / Admin Exemption ---
            if message.chat.type in ['group', 'supergroup']:
                # [cite_start]... (Admin/Owner exemption logic remains) [cite: 143-146]
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
            # --- End Anti-Spam ---
      
            text = (message.text or message.caption or "")
            if not text: return
            
            lower_text = text.lower().strip()
            chat_id = message.chat.id
            
            # --- Owner Mention Response ---
            if (Config.GROUP_OWNER_ID() and message.entities and message.chat.type in ['group', 'supergroup']):
                for entity in message.entities:
                    if getattr(entity, 'type', None) == 'text_mention' and getattr(entity, 'user', None):
                        if str(entity.user.id) == str(Config.GROUP_OWNER_ID()):
                            self.bot.send_message(chat_id, random.choice(self.responses.get("WHO_IS_OWNER", [])))
                            return
            
            # --- Hardcoded Keyword Responses ---
            if any(kw in lower_text for kw in ["ca", "contract", "address"]):
                self.bot.send_message(chat_id, f"Here is the contract address, fren:\n\n`{Config.CONTRACT_ADDRESS()}`", 
parse_mode="Markdown")
                return
            if any(kw in lower_text for kw in ["how to buy", "where to buy", "buy npepe"]):
                self.bot.send_message(chat_id, " 💰  You can buy *$NPEPE* on Pump.fun! The portal to the moon is one click away!  🚀 ", parse_mode="Markdown", reply_markup=self.main_menu_keyboard())
                return
            
            # Identity Response (Uses the new 'Sanity Frog' answers)
            if any(kw in lower_text for kw in ["what are you", "what is this bot", "are you a bot", "what kind of bot"]):
                logger.info("Identity question detected, responding...")
                # Removed time.sleep(20)
                self.bot.send_message(chat_id, random.choice(self.responses.get("BOT_IDENTITY", [])))
                return
                
            if any(kw in lower_text for kw in ["owner", "dev", "creator", "in charge", "who made you"]):
                logger.info("Owner question detected, responding...")
                # Removed time.sleep(20)
                self.bot.send_message(chat_id, random.choice(self.responses.get("WHO_IS_OWNER", [])))
                return
            
            if any(kw in lower_text for kw in ["collab", "partner", "promote", "help grow", "shill", "marketing"]):
                self.bot.send_message(chat_id, random.choice(self.responses.get("COLLABORATION_RESPONSE", [])))
                return
                
            # --- AI Response to Questions ---
            if self.groq_client and self._is_a_question(text):
                thinking_message = None
                try:
                    thinking_message = self.bot.send_message(chat_id, " 🐸  The NPEPE oracle is consulting the memes...")
                    system_prompt = (
                        "You are a crypto community bot for $NPEPE. Funny, enthusiastic, chaotic. "
                        "Use slang: ‘fren’, ‘WAGMI’, ‘HODL’, ‘based’, ‘LFG’, ‘ribbit’. Keep answers short."
                    )
                    chat_completion = self.groq_client.chat.completions.create(
                        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
                        model="llama3-8b-8192", temperature=0.7, max_tokens=150
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
                return
            
            # --- REMOVED: Random Hype Reply Logic ---
            # All other text is ignored to prevent spam, unless it's an AI question/hardcoded keyword.

        except Exception as e:
            logger.error(f"FATAL ERROR processing message: {e}", exc_info=True)
