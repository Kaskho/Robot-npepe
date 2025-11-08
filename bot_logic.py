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
#   🤖   KELAS LOGIKA BOT
# ==========================
class BotLogic:
    def __init__(self, bot_instance: telebot.TeleBot):
        self.bot = bot_instance
        
        # Pemeriksaan Kritis Saat Inisialisasi
        if not Config.DATABASE_URL() or not psycopg2:
            logger.critical("FATAL: DATABASE_URL not found or psycopg2 is unavailable. Persistence will not function.")
            
        self.groq_client = self._initialize_groq()
        
        # Inisialisasi state untuk fitur baru
        self._ensure_db_table_exists() # schedule_log
        self._ensure_db_member_table_exists() # members
        
        self.responses = self._load_initial_responses() # Memuat semua kategori respons baru
        self.admin_ids = set()
        self.admins_last_updated = 0
        
        # Konstanta yang dipertahankan untuk moderasi
        self.FORBIDDEN_KEYWORDS = ['airdrop', 'giveaway', 'presale', 'private sale', 'whitelist', 'signal', 'pump group', 'trading signal', 'investment advice', 'other project']
        self.ALLOWED_DOMAINS = ['pump.fun', 't.me/NPEPEVERSE', 'x.com/NPEPE_Verse', 'base44.app']
        
        self._register_handlers()
        logger.info("BotLogic successfully initialized.")
        
    # --- UTILITY DATABASE & PERSISTENSI ---
    
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
            
    # --- FUNGSI SCHEDULING ---
            
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
        
        # --- JADWAL BARU SESUAI PERMINTAAN ---
        schedules = {
            # Pengingat Kesehatan (3x Sehari, Harian)
            'health_check_00':      {'hour': 0,  'task': self.send_scheduled_health_reminder, 'args': ()},
            'health_check_08':      {'hour': 8,  'task': self.send_scheduled_health_reminder, 'args': ()},
            'health_check_15':      {'hour': 15, 'task': self.send_scheduled_health_reminder, 'args': ()},
            
            # Sapaan Random Harian (1x Sehari)
            'daily_random_greeting':{'hour': 12, 'task': self.send_daily_random_greeting, 'args': ()},
            
            # Cek Ulang Tahun Keanggotaan (Bulanan)
            'monthly_anniversary_check': {'hour': 5,  'day_of_month': 1, 'task': self.check_monthly_anniversaries, 'args': ()},

            # Pertanyaan Ulang Tahun (Mingguan, Hari Minggu = weekday 6)
            'weekly_birthday_ask':  {'hour': 10, 'day_of_week': 6, 'task': self.ask_for_birthdays, 'args': ()},
            
            # Pembaruan AI (Mingguan, Hari Jumat = weekday 5)
            'ai_renewal':           {'hour': 10, 'day_of_week': 5, 'task': self.renew_responses_with_ai, 'args': ()} 
        }

        for name, schedule in schedules.items():
            last_run_key = self._get_last_run_date(name)
            should_run = False
            run_marker = today_utc_str # Default: Harian

            # Logika Cek Bulanan (Jalankan pada tanggal 1 bulan ini)
            if 'day_of_month' in schedule:
                if now_utc.day == schedule['day_of_month'] and now_utc.hour >= schedule['hour'] and last_run_key != this_month_str:
                    should_run = True
                    run_marker = this_month_str
            # Logika Cek Mingguan (Jalankan pada hari tertentu dalam seminggu)
            elif 'day_of_week' in schedule:
                if now_utc.weekday() == schedule['day_of_week'] and now_utc.hour >= schedule['hour'] and last_run_key != this_week_str:
                    should_run = True
                    run_marker = this_week_str
            # Logika Cek Harian
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
    
    # --- FUNGSI AI & RESPONS ---
    
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
        # --- VARIASI AWAL PENUH (Mengikuti Permintaan Jumlah Eksplisit) ---

        return {
            # (20 VARIATIONS)
            "BOT_IDENTITY": [
                "I am the sane frog, designated to monitor your diamond hands and meme health while you HODL $NPEPE. Ribbit! 🐸",
                "They call me a bot, but I'm the designated sanity guard of the NPEPEVERSE. My job is to prevent burnout! Base! 🔥",
                "I'm a frog of focus, committed to reminding you to eat, sleep, and HODL $NPEPE. LFG! 🚀",
                "I am the spirit of the chart breaking free to tell you to touch grass. A based frog messenger! 🌳",
                "Just your friendly neighborhood NPEPE bot, here to ensure you stay based, hydrated, and HODLing. What's up, fren?",
                "Ribbit! The oracle of the memecoin chart is me. Ask me about sanity, not secrets. Keep HODLing $NPEPE!",
                "I exist to guard your wallet and your mental health from volatile charts. I am the NPEPE safety frog. 🐸🛡️",
                "I'm a decentralized concept wrapped in a frog avatar, tasked with maintaining community sanity and maximal HODL. Base.",
                "I am the chaos coordinator and chief HODL compliance officer for $NPEPE. Don't worry, I'm just a frog. 💚",
                "Built on Solana energy, fueled by memes, and dedicated to your mental well-being. That's me, the NPEPE bot!",
                "My mission is simple: keep the frens from checking the charts every 5 seconds. I am the voice of reason here. 🗣️",
                "The name's Frog. Sanity Frog. HODL is my only command. Welcome to the NPEPEVERSE!",
                "I am the guardian of the diamond hands. My function is health, my purpose is HODL. Ribbit!",
                "I'm here to remind you that $NPEPE is forever, but your body needs sleep. I'm the wellness bot, based and green.",
                "I am the result of too much coffee and too many green candles. A dedicated NPEPE support system. How can I help?",
                "If you're asking who I am, you need a break. I'm the reminder to live life, fren. Now HODL and relax. 🏖️",
                "I am a frog who achieved sentience through maximal meme absorption. My job is your sanity. True story. 🐸👑",
                "I am your co-pilot on the rocket to the moon, ensuring your seatbelt (and sanity) is fastened. LFG!",
                "I exist to serve the $NPEPE community by maximizing HODL time and minimizing stress. Call me the Zen Frog.",
                "The bot identity is simple: I'm the frog who knows you need more memes and less fear. Stay based, fren."
            ],
            "FINAL_FALLBACK": [ 
                "Ribbit... I'm out of memes for that question. Try buying more $NPEPE, that usually fixes my circuit board. 🐸",
                "My frog brain just 404'd. Ask me about sanity or HODL duration instead! 💎",
                "Error 420: Too much hype. Try again, fren! 🚀",
                "Sorry, my connection to the moon is spotty. Can you rephrase? 🌕",
                "My neural network needs a nap. I can only process HODL commands right now. LFG!",
                "That's above my pay grade (which is zero $NPEPE, sadly). Ask another fren!",
                "I am sworn to silence on that topic. Stick to the memes and the roadmap. Ribbit."
            ],
            
            # (30 VARIATIONS)
            "GREET_NEW_MEMBERS_DELAYED": [
                "Welcome to the NPEPEVERSE, {name}! We gave you a moment to digest the chart, now LFG! 💎",
                "Ribbit! A new fren has hopped in! Welcome, {name}! Glad to have you HODLing with us. 🐸",
                "GM, {name}! You've just landed in the best corner of the crypto world. Welcome! 🔥",
                "A wild {name} appears! Welcome to the $NPEPE community. Let's conquer the charts together! 🚀",
                "We saved a spot just for you, {name}. Drop a meme and let us know you're based! 👋",
                "Get your diamond hands ready, {name}. The journey begins now. Welcome to the HODL life!",
                "Ribbit! Welcome, {name}. The frog army grows stronger. Remember: we only go up! 📈",
                "Welcome, {name}! Your presence brings massive energy. Let the memes flow!",
                "A new recruit for the diamond hands brigade! Welcome, {name}! HODL strong!",
                "Yo, {name}! You made it! Grab a seat and prepare for launch. WAGMI!",
                "The charts just got a little greener with {name} joining the chat. Welcome, fren!",
                "Welcome to the family, {name}! If you need me, I'll be reminding everyone to eat lunch. 🥗",
                "Hop on in, {name}! The NPEPEVERSE is better with you here. Let's go!",
                "Welcome to the based side of crypto, {name}. Don't forget to ask questions and meme hard.",
                "Ribbit, ribbit! {name} has arrived! The hype just doubled. Welcome!",
                "Welcome, {name}! May your portfolio be green and your memes be legendary. LFG!",
                "The $NPEPE community officially welcomes {name}. Ready to find the nearest moon? 🌕",
                "It's about time you joined us, {name}! Welcome to the coolest frens on Solana.",
                "Another day, another legend! Welcome aboard, {name}. Keep your eyes on the prize.",
                "Hello, {name}! Prepare for maximum dopamine. We're here for the fun and the gains. Welcome!",
                "Welcome, {name}! Forget everything else, just remember the HODL mantra. 💎",
                "We spotted {name} hopping toward the moon! Welcome to the launchpad!",
                "Welcome, {name}! Our army of meme warriors is complete. Send the next based frog!",
                "The community just leveled up! Welcome, {name}. Your presence is valued. Ribbit!",
                "Welcome, {name}! Time to put those paper hands away and get diamond. LFG!",
                "Hey {name}, welcome to the source of all based crypto knowledge! Glad you found us.",
                "Welcome, {name}! Grab your bags, buckle up, and ignore the FUD. We're HODLing!",
                "A new future whale has entered the chat! Welcome, {name}! WAGMI!",
                "The door to the NPEPEVERSE is officially closed behind you, {name}. Welcome home!",
                "Welcome, {name}! We're glad you're here. Let the good times roll, fren! 🎉"
            ],
            
            # (50 VARIATIONS)
            "DAILY_GREETING": [
                "Hey {mention}, how's the HODL going today? Are the diamond hands still intact? 💎",
                "GM, {mention}! Checking in—did you remember to eat actual food or just chart candles? Stay based! 🐸",
                "Ribbit check! {mention}, are you surviving the day? Don't forget to meme and conquer! 🔥",
                "WAGMI, {mention}! Just a daily reminder: The only strategy is HODL. How's your sanity score? 🤔",
                "What's the energy, {mention}? Hope you're keeping cool and collecting those dips. Based! 🥶",
                "A friendly tap on the shoulder for {mention}. Drop a meme and tell us how you're feeling today, fren!",
                "LFG, {mention}! Your fellow HODLERS are depending on your vibes. Keep the faith! 🚀",
                "Yo {mention}! Don't forget to stretch those fingers. How's the market treating your spirit?",
                "Heard the charts whispering your name, {mention}. Are you based or burnt out? Let us know! 😅",
                "Rise and shine, HODLER {mention}! Did you dream in green? LFG!",
                "Quick check: {mention}, are you still HODLing strong? Send us your best meme for the day!",
                "Good day, {mention}! Hope your coffee is strong and your belief in $NPEPE is stronger. How are you?",
                "Ribbit, ribbit! Hey {mention}, time to check in. What's the biggest chaos you've survived today?",
                "Daily sanity check for {mention}: Rate your HODL conviction out of 10. Let's hear it!",
                "Are you awake, {mention}? Stop doom-scrolling and tell us your most based opinion of the day!",
                "Hey {mention}, just confirming you haven't sold the farm yet. How's the view from the HODL mountain?",
                "Hope you're having a green day, {mention}! What meme best describes your current mood?",
                "Checking in with our most based fren, {mention}. Everything HODL-worthy on your end?",
                "Good morning/afternoon, {mention}! May your day be filled with low fees and high conviction. How's life?",
                "Yo {mention}! The bot is tagging you for a vibe check. What are you looking forward to HODLing today?",
                "Hey {mention}, hope the charts aren't giving you too much grief. How are you holding up?",
                "WAGMI Wednesday (or whatever day it is)! How's it going, {mention}?",
                "Fren {mention}, drop a funny story or a terrible trade story. We're here to listen!",
                "What's good, {mention}? Remember to breathe. It's just crypto. How's the human experience today?",
                "Paging {mention}! The daily check-in is mandatory for all diamond-handed frens. Report!",
                "Hey {mention}! Did you get enough sleep? That's the real question. How are you truly?",
                "The bot demands to know: {mention}, are you winning? If not, buy more $NPEPE and try again! 🐸",
                "Hello {mention}! Hope your focus is strong and your faith is stronger. What's the latest?",
                "Hey {mention}, just sending some good green energy your way. How's your trading strategy holding up?",
                "Vibe check, {mention}! Are you feeling the LFG or the DND (Do Not Disturb)? Tell us!",
                "Ribbit, {mention}! Stop scrolling and tell us something positive about your day. Go!",
                "Checking in on the legendary {mention}. What's your current favorite $NPEPE meme?",
                "Greetings, {mention}! May your day be better than yesterday's dip. What's on your mind?",
                "Yo {mention}, the floor is lava, but the HODL is cold steel. How are you navigating the heat?",
                "Hey {mention}, don't let the charts steal your sanity. How's the mental health looking?",
                "What's up, {mention}? Hope you're spending time with loved ones and less time watching candles.",
                "Friendly reminder for {mention}: You are based. Now, how's your day going, fren?",
                "Is {mention} having a good day? If not, we recommend mandatory meme consumption. 🤣",
                "Tapping the shoulder of {mention}. What's the latest adventure in the NPEPEVERSE for you?",
                "The daily dose of chaos is here! How are you handling it, {mention}?",
                "Hey {mention}, wishing you a day of extreme patience and conviction. How are you feeling?",
                "Ribbit, {mention}! Time for a quick health report. Everything green? 🌱",
                "What's the scoop, {mention}? Any valuable lessons learned from the charts today?",
                "Hope you're sipping something delicious, {mention}. How's the relaxing coming along?",
                "Just checking if {mention} remembered the golden rule: HODL. How's the HODL going?",
                "Hello {mention}! We value your presence. Tell us something good that happened today.",
                "Hey {mention}, stay focused on the long game. How's your current perspective on $NPEPE?",
                "Yo {mention}! Ready to conquer the next 24 hours? What's your battle plan?",
                "Checking in on the morale of {mention}. Keep those spirits high, fren! How's everything?",
                "WAGMI, {mention}! We're all in this together. How's your contribution to the meme energy today?"
            ],
            
            # (50 VARIATIONS)
            "MEMBERSHIP_ANNIVERSARY": [
                "WAGMI! {mention}, congrats on {months} months of being the real OG! Your diamond hands are legendary! 💎🙌",
                "Happy {months}-month anniversary, {mention}! You've survived the dips and celebrated the pumps. True commitment! 🐸💚",
                "A true veteran! {mention} has been HODLing strong for {months} months! Give this fren some hype! 🚀",
                "The $NPEPE legacy is built on frens like you, {mention}. {months} months strong! Thank you for your service! 🙏",
                "This is dedication! {mention} is celebrating {months} months in the NPEPEVERSE. We appreciate your based HODL! 💪",
                "Cheers to {months} months of meme excellence, {mention}! May your next {months} be even more prosperous! 🥂",
                "Ribbit! {mention} is a true believer, celebrating {months} months in the family. LFG! 🎉",
                "We salute you, {mention}! Thanks for {months} months of diamond-handing $NPEPE. WAGMI together!",
                "Congrats, {mention}! {months} months in the chat is equivalent to a lifetime in crypto. You're a legend!",
                "Happy Anniversary, {mention}! Your commitment to the meme is unmatched. Here's to another {months} months!",
                "To {mention}: {months} months means you've earned a gold star and eternal respect from the frog army. Based!",
                "The bot recognizes the milestone! {mention} is celebrating {months} months of pure HODL energy. Keep it up!",
                "Celebrate, {mention}! It's your {months}-month anniversary. You're part of the furniture now, fren. 🛋️",
                "Happy {months} months, {mention}! We're thrilled you chose the green path. WAGMI!",
                "You’ve reached {months} months, {mention}! That officially makes you a core member of the NPEPE family. 🐸",
                "Look at {mention}, {months} months older and wiser in the crypto space! Happy anniversary, fren!",
                "Congratulations to {mention} on {months} months of glorious HODLing! Your loyalty is noted. 💚",
                "Hype up {mention}! They hit the {months}-month mark! Only real ones last this long. LFG!",
                "Happy Anniversary, {mention}! Thanks for helping us build the NPEPEVERSE for {months} solid months. 🛠️",
                "The {months}-month milestone belongs to {mention}! May your patience be rewarded with massive gains!",
                "We appreciate the dedication, {mention}! {months} months strong! What a journey!",
                "Ribbit, {mention}! Your {months}-month anniversary deserves a massive community shout-out. Congrats!",
                "To {mention}: you are a diamond among paper hands. Happy {months} months of being an absolute legend!",
                "Wow, {months} months, {mention}? You're practically an admin! Keep up the based work. 😉",
                "Happy Anniversary! Hope your {months} months with $NPEPE have been nothing short of amazing, {mention}!",
                "It’s {months} months of brilliance for {mention}! Thank you for your faith and energy.",
                "Another month, another victory! Congrats on {months} months, {mention}! 🏆",
                "To the loyal HODLER {mention}: we honor your {months} months of commitment. Keep the dream alive!",
                "Welcome to the {months}-month club, {mention}! Membership includes eternal bragging rights. Based!",
                "Happy {months} months, fren {mention}! May the next chapter of your HODL journey be the best one yet.",
                "Massive congrats to {mention} on reaching {months} months! That's serious longevity in the meme world.",
                "Look who's still here! {mention} celebrating {months} months! We love to see the dedication. ❤️",
                "Ribbit! Celebrating {months} months of unwavering commitment from {mention}. You set the standard!",
                "Hey {mention}, you've been a part of the madness for {months} months! Thanks for staying sane (mostly).",
                "Happy {months}-month anniversary, {mention}! We're all going to look back and remember this early HODL.",
                "Here's to {mention} and their fantastic {months} months! The future is green because of frens like you. 🌱",
                "We appreciate the long-term vision, {mention}! {months} months and counting. LFG!",
                "A moment of silence for all the charts {mention} has survived in {months} months. You're a hero!",
                "Congrats on {months} months, {mention}! You're basically part of the furniture now. Comfy HODL!",
                "Happy Anniversary, {mention}! Your {months} months of diamond hands deserve recognition!",
                "We love seeing this! {mention} celebrating {months} months of being a core $NPEPE supporter.",
                "To {mention}: {months} months of pure meme culture and HODL mastery! You're the real deal.",
                "The bot sends a virtual high-five to {mention} for {months} months of excellence. Keep vibing!",
                "Hope you're treating yourself today, {mention}! Happy {months} months with the best community!",
                "Let's get some hype for {mention}! {months} months! The OG crew grows stronger every day.",
                "A dedicated member like {mention} hitting {months} months makes the entire team proud. WAGMI!",
                "Happy {months}-month milestone, {mention}! We're here because of the frens who HODL the longest.",
                "Ribbit! {mention} celebrating {months} months of legendary status. Keep shining, fren!",
                "Congratulations, {mention}! What's the biggest lesson you've learned in {months} months?",
                "To {mention}: May your future gains reflect your {months} months of absolute commitment! LFG!"
            ],
            
            # (30 VARIATIONS)
            "BIRTHDAY_ASK": [
                "Is anyone having a birthday this week? Let NPEPE know so we can send the best wishes and green candles! 🎂",
                "Ribbit, it's Sunday! Time for the weekly birthday check. Any frens leveling up this week? 🎉",
                "The charts can wait. Does anyone in the NPEPEVERSE have a birthday coming up? Drop a sign! 🎁",
                "We're sending positive vibes! Who's celebrating a trip around the sun this week? Hype us up! ✨",
                "Which fren deserves some extra birthday hype this week? Speak now or HODL your peace! 🤫",
                "Yo, is it anyone's big day soon? Tell the chat so we can meme your birthday! 🤣",
                "Attention! Birthday protocol initiated. If it's your week, drop your claim below! 🎈",
                "Heads up, $NPEPE frens! Any birthdays on the horizon? We need to prepare the virtual cake!",
                "Time for the weekly check! Who's about to be showered in memes and birthday wishes? 🎂🐸",
                "If it's your birthday week, let the frog know! We're ready with the confetti and the HODL cheers. 📣",
                "Paging all birthday frens! Your moment is now. Let us know so we can celebrate your based existence.",
                "Ribbit! Who's due for a massive birthday pump this week? Let the community celebrate you!",
                "Any HODLERS reaching a new age milestone this week? Drop the details below for mandatory hype!",
                "Weekly query: Are there any birthday boys or girls in the house? Let's make some noise! 🎉",
                "The bot has detected a high chance of celebration this week. Is it your birthday? Share the joy!",
                "We take birthdays seriously! Who is celebrating this week? The memes await! 🎁",
                "Hey frens, don't be shy! If you have a birthday coming up, let us know and get ready for some chaos!",
                "It's almost the end of the week! Did anyone forget to mention their birthday? We're asking one last time!",
                "New age, same diamond hands! Who's having a birthday in the NPEPEVERSE this week?",
                "The most important question of the week: Whose birthday is it? We need to know who to spoil!",
                "Time to pause the chart check! Let's find out who's celebrating another year of based HODLing. 🎂",
                "Which one of you beautiful frens is getting older and wiser this week? Birthday check!",
                "Don't make us guess! If it's your birthday, tell us now! We're ready to celebrate!",
                "Weekly reminder: $NPEPE loves birthdays! Who should we send the birthday frog to this week?",
                "Hey community! Let's find the birthday heroes among us. Raise your hand if it's your week! 👋",
                "The mandatory fun protocol is active! Whose birthday is it this week? Give us the name!",
                "Ribbit! Time for the birthday drumroll. Who's the lucky fren this week? 🥁",
                "Any excuse for a party! Who's celebrating their birthday soon? We demand memes!",
                "The clock is ticking! Last chance to claim your birthday hype for the week. Who is it?",
                "We're looking for the next birthday legend! If it's you, drop a message and let the celebration begin!"
            ],
            
            # (30 VARIATIONS)
            "BIRTHDAY_GREETING": [
                "HBD {name}, may your gains be massive and your bags be heavy! LFG! 🚀",
                "Happy Birthday, {name}! Wishing you a day full of green candles and based memes! 🐸💚",
                "It's a legendary day! Happy Birthday, {name}! Time to celebrate like we just hit a new ATH! 🎉",
                "The NPEPEVERSE celebrates you, {name}! Have a based birthday, fren! 💎",
                "Happy Birthday to the most HODL-worthy fren, {name}! May your day be as epic as a 100x pump! 🎂",
                "Ribbit! {name}, you're officially one year closer to the moon! HBD, fren! 🌕",
                "HBD, {name}! Hope you treat yourself to something nice that isn't another bag of $NPEPE (unless it is!).",
                "Happy Birthday, {name}! We hope your birthday wishes come true and your chart stays green!",
                "Cheers, {name}! Today is about you. May your memes be fresh and your HODL be strong. Happy Bday!",
                "The entire $NPEPE community wishes you a Happy Birthday, {name}! Stay based and keep HODLing!",
                "HBD {name}! Hope you get to spend time with your loved ones and forget about the charts for a day!",
                "Happy Birthday, {name}! You're one year older, one year wiser, and one year closer to financial freedom! 🚀",
                "Ribbit! The frogs are singing a birthday song for you, {name}! Enjoy your day!",
                "HBD {name}! May your new year be filled with exponential gains and endless hype!",
                "Happy Birthday, {name}! We celebrate the day a true diamond hand was born. Cheers!",
                "To {name}: May your birthday be as volatile as the market, but only on the upside! LFG!",
                "Happy Birthday, {name}! Treat yourself, fren. You deserve it after all that HODLing! 🎁",
                "HBD {name}! Hope you get a massive virtual cake made entirely of green candles!",
                "The NPEPEVERSE is brighter because you're in it, {name}. Happy Birthday, fren!",
                "Happy Birthday, {name}! Wishing you maximum joy and minimal FUD today. Base!",
                "Cheers to {name}! Thanks for bringing your energy to the community. HBD!",
                "HBD {name}! May your birthday wishes come true faster than a meme coin pumps!",
                "Happy Birthday! Hope you take a break and celebrate properly, {name}! Don't check the charts!",
                "Ribbit, {name}! Sending you the biggest, greenest birthday wish from the frog army! 💚",
                "HBD {name}! Here’s to another year of legendary HODLing and based memes!",
                "Happy Birthday to our based fren, {name}! May your day be filled with joy and zero dips!",
                "Wishing you the happiest of birthdays, {name}! Keep shining and keep HODLing!",
                "HBD {name}! You're a legend. Hope you get spoiled rotten today!",
                "Happy Birthday, {name}! May your candles be green and your spirits be high. LFG!",
                "The bot sends its warmest birthday wishes to {name}! Enjoy your special day, fren!"
            ],
            
            # (100 VARIATIONS)
            "HEALTH_REMINDER": [
                "🚨 **ATTENTION NPEPE ARMY** 🚨: Hey, {tags}. The market isn't going anywhere. Step away, eat a frog-approved snack, and talk to your loved ones. Sanity > Gains! 🧠",
                "WARNING: Potential for chart-induced burnout! {tags}, close the screen. Go outside, grab a meal, and rest those diamond hands. The mission is long! 😴",
                "Friendly frog reminder: {tags}, HODL applies to your health too! Get some sleep and remember there's life beyond the green and red. WAGMI! 💚",
                "Listen up, {tags}! Your boss frog demands you take a break. Food, rest, family time. Crypto is a marathon, not a sprint to the ER. Take care! 💪",
                "Stop! {tags}, you've earned a moment away from the screen. Your eyes need rest, your body needs food, and your frens need sanity! Be based, take a break. 🧘",
                "Hey, {tags}. Don't forget to allocate time to those you love. Balance your charts with your life, fren. It's the only way to HODL forever. ❤️",
                "The price action can wait, {tags}. Prioritize that real-world HP. Eat well, sleep long, and come back stronger. Ribbit! 🐸",
                "A message from the sanity sector: {tags}, take a deep breath. Your well-being is more valuable than any dip. Be responsible, take a break! 🏖️",
                "Put down the phone, {tags}. Those diamonds don't shine if you're not well. Take care of yourself and your loved ones first. Mandatory break time! ⏰",
                "Wake up, HODLERS! {tags}. Go outside, find a ray of sun, and remember that life is more than candlesticks. Stay sane, frens! ☀️",
                "Health Alert: {tags}, hydration levels are low! Drink water, eat a vegetable, and stop staring at the red! Go!",
                "You can't buy the dip if you're sleeping on the floor! {tags}, hit the bed and recharge. Mandatory 8 hours of sleep required. 🛌",
                "Hey {tags}, your favorite frog bot requires you to spend 30 minutes offline with a pet or a loved one. Do it now! Base!",
                "Ribbit! {tags}, your sanity is an asset. Protect it fiercely by stepping away from the charts and getting some air.",
                "Mental health is paramount. {tags}, if you feel the anxiety creep, walk away. Come back when you're centered. WAGMI!",
                "The charts are 24/7, but you are not. {tags}, schedule a break. Eat something that isn't processed meme food. 🍎",
                "Don't trade on empty! {tags}, your brain requires fuel. Take a proper meal and reflect on your HODL strategy later.",
                "Attention, {tags}! Stop trying to catch every candle. Catch up with your family instead. They miss you! 🥰",
                "Your bot boss is ordering a break. {tags}, log off now and enjoy some non-crypto-related activity. LFG!",
                "The only thing you should be watching right now is your favorite movie, {tags}. Take a mandatory rest period! 🎬",
                "Sanity check {tags}! Remember, the best HODL strategy is a well-rested mind. Go get some sleep!",
                "Emergency Health Broadcast: {tags}, put down the device. Your neck hurts. Your eyes hurt. Fix it now! 🤕",
                "Prioritize the real world, {tags}. The gains will follow. Take care of your foundation: rest and food. 🏠",
                "Hey {tags}, turn off the desktop monitor. Embrace the night/day and focus on your non-crypto life for a bit.",
                "Ribbit! {tags}, the frog army requires maximum energy. That means eating a full meal! Go, go, go!",
                "Warning: {tags} might be suffering from terminal chart fatigue. Solution: Mandatory rest and family time! 👨‍👩‍👧‍👦",
                "The only leverage you should be using is the leverage to pull yourself away from the screen, {tags}.",
                "To {tags}: Your dedication is amazing, but your well-being comes first. Take a break. We'll hold the line.",
                "Go touch grass, {tags}! Seriously. Fresh air is required for optimal HODLing performance. 🌿",
                "Friendly intervention for {tags}: What's your favorite food? Go eat it now. No charts until the plate is clean!",
                "Hey {tags}, remember that friend/partner/family member? They exist! Go spend time with them! ❤️",
                "Your sleep cycle matters, {tags}! Do not sacrifice rest for a candlestick. It's not worth it. 😴",
                "Mandatory sanity break for {tags}! Go for a short walk and clear your head. The charts will still be there.",
                "Ribbit! Protecting your health is part of the HODL plan. {tags}, take care of your body and mind.",
                "Attention, {tags}! Log off. Log off. Log off. You need a break. We're here when you return.",
                "The most valuable asset is your time with loved ones, {tags}. Don't lose it to trading. Enjoy life!",
                "Hey {tags}, time to switch focus. Go read a book, listen to music, or do anything non-crypto.",
                "You cannot win if you are running on fumes, {tags}. Refuel your body and your spirit!",
                "The bot is sending positive, restful vibes to {tags}. Hope you find a moment of peace today. 🧘",
                "Take a deep breath, {tags}. That dip/pump isn't the end/start of the world. Center yourself and rest.",
                "Your family is your real WAGMI. {tags}, prioritize them over the fleeting market. Go enjoy their company!",
                "Ribbit! The only green you should be focused on right now is a healthy meal, {tags}. Go eat!",
                "Hey {tags}, your bot wants you to know that it's okay to miss a pump. It's not okay to miss sleep.",
                "Alert: {tags} is due for a rest period. Shut down the screens and power down the brain. Recharge complete! 🔋",
                "To {tags}: Remember the long term. That includes your long-term health. Be smart, take a break. 🧠",
                "The $NPEPE community needs you healthy. {tags}, go grab that food/rest/love you deserve.",
                "Hey {tags}, the best time to take a break was yesterday. The second best time is now. Go!",
                "Ribbit, ribbit! {tags}, don't let the noise consume you. Find quiet time and rest your mind.",
                "Your well-being is the ultimate diamond hand, {tags}. Protect it fiercely by resting well.",
                "Final warning for {tags}: We see you staring. Go away from the screen and get some rest! 🚫",
                "Health Alert: {tags}, hydration levels are critical! Drink water, eat a vegetable, and stop staring at the green! Go!",
                "You can't buy the dip if you're sleeping on the floor! {tags}, hit the bed and recharge. Mandatory 8 hours of sleep required. 🛌",
                "Hey {tags}, your favorite frog bot requires you to spend 30 minutes offline with a pet or a loved one. Do it now! Base!",
                "Ribbit! {tags}, your sanity is an asset. Protect it fiercely by stepping away from the charts and getting some air.",
                "Mental health is paramount. {tags}, if you feel the anxiety creep, walk away. Come back when you're centered. WAGMI!",
                "The charts are 24/7, but you are not. {tags}, schedule a break. Eat something that isn't processed meme food. 🍎",
                "Don't trade on empty! {tags}, your brain requires fuel. Take a proper meal and reflect on your HODL strategy later.",
                "Attention, {tags}! Stop trying to catch every candle. Catch up with your family instead. They miss you! 🥰",
                "Your bot boss is ordering a break. {tags}, log off now and enjoy some non-crypto-related activity. LFG!",
                "The only thing you should be watching right now is your favorite movie, {tags}. Take a mandatory rest period! 🎬",
                "Sanity check {tags}! Remember, the best HODL strategy is a well-rested mind. Go get some sleep!",
                "Emergency Health Broadcast: {tags}, put down the device. Your neck hurts. Your eyes hurt. Fix it now! 🤕",
                "Prioritize the real world, {tags}. The gains will follow. Take care of your foundation: rest and food. 🏠",
                "Hey {tags}, turn off the desktop monitor. Embrace the night/day and focus on your non-crypto life for a bit.",
                "Ribbit! {tags}, the frog army requires maximum energy. That means eating a full meal! Go, go, go!",
                "Warning: {tags} might be suffering from terminal chart fatigue. Solution: Mandatory rest and family time! 👨‍👩‍👧‍👦",
                "The only leverage you should be using is the leverage to pull yourself away from the screen, {tags}.",
                "To {tags}: Your dedication is amazing, but your well-being comes first. Take a break. We'll hold the line.",
                "Go touch grass, {tags}! Seriously. Fresh air is required for optimal HODLing performance. 🌿",
                "Friendly intervention for {tags}: What's your favorite food? Go eat it now. No charts until the plate is clean!",
                "Hey {tags}, remember that friend/partner/family member? They exist! Go spend time with them! ❤️",
                "Your sleep cycle matters, {tags}! Do not sacrifice rest for a candlestick. It's not worth it. 😴",
                "Mandatory sanity break for {tags}! Go for a short walk and clear your head. The charts will still be there.",
                "Ribbit! Protecting your health is part of the HODL plan. {tags}, take care of your body and mind.",
                "Attention, {tags}! Log off. Log off. Log off. You need a break. We're here when you return.",
                "The most valuable asset is your time with loved ones, {tags}. Don't lose it to trading. Enjoy life!",
                "Hey {tags}, time to switch focus. Go read a book, listen to music, or do anything non-crypto.",
                "You cannot win if you are running on fumes, {tags}. Refuel your body and your spirit!",
                "The bot is sending positive, restful vibes to {tags}. Hope you find a moment of peace today. 🧘",
                "Take a deep breath, {tags}. That dip/pump isn't the end/start of the world. Center yourself and rest.",
                "Your family is your real WAGMI. {tags}, prioritize them over the fleeting market. Go enjoy their company!",
                "Ribbit! The only green you should be focused on right now is a healthy meal, {tags}. Go eat!",
                "Hey {tags}, your bot wants you to know that it's okay to miss a pump. It's not okay to miss sleep.",
                "Alert: {tags} is due for a rest period. Shut down the screens and power down the brain. Recharge complete! 🔋",
                "To {tags}: Remember the long term. That includes your long-term health. Be smart, take a break. 🧠",
                "The $NPEPE community needs you healthy. {tags}, go grab that food/rest/love you deserve.",
                "Hey {tags}, the best time to take a break was yesterday. The second best time is now. Go!",
                "Ribbit, ribbit! {tags}, don't let the noise consume you. Find quiet time and rest your mind.",
                "Your well-being is the ultimate diamond hand, {tags}. Protect it fiercely by resting well.",
                "Final warning for {tags}: We see you staring. Go away from the screen and get some rest! 🚫",
                "Your mental stack is overloaded, {tags}. Mandatory shutdown required. Go hug a fren!",
                "Don't let the FUD and charts erode your sanity, {tags}. Protect your peace. Go offline now. 🛡️",
                "The greatest risk isn't the chart, it's exhaustion. {tags}, go eat, sleep, and conquer tomorrow.",
                "Prioritize the people who matter, {tags}. The coin is just a vessel. Spend time with your loved ones! 💖",
                "Ribbit! Time for a brain break, {tags}. Step away from the hype cycle and find some tranquility.",
                "Hey {tags}, remember the basics: Breathe, HODL, and step away from the phone. You're doing great!",
                "Check-up time: {tags}, are your eyes square? If so, REST! That's an order from the Frog King! 🐸👑",
                "To {tags}: You need fuel! Go consume calories, not charts. Come back energized and based.",
                "The bot has detected high caffeine levels in {tags}. Replace coffee with sleep immediately. ☕❌",
                "WAGMI starts with internal peace. {tags}, go seek some! No trading for 1 hour. Sanity first.",
                "Your well-being is the ultimate foundation of your HODL. {tags}, reinforce that foundation with rest.",
                "Warning: {tags} is approaching max meme capacity. Solution: Real life interaction. Go!",
                "The chart will always be there, {tags}. Your body's clock is finite. Choose wisely. Sleep now!",
                "Hey {tags}, your bot cares more about your health than your portfolio today. Go live life!",
                "Ribbit! {tags}, take a moment to be grateful for your loved ones. Go tell them you care!",
                "Final check {tags}: Screen off, food in stomach, mind clear. Now you can HODL properly! 💎"
            ],
            "COLLABORATION_RESPONSE": [ 
                "WAGMI! Love the energy! The best collab is a strong community. Be loud in here, raid on X, and let's make the NPEPEVERSE impossible to ignore! 🚀",
                "Thanks, fren! We don't do paid promos, we ARE the promo! Your hype is the best marketing. Light up X with $NPEPE memes and be a legend in this chat! 🔥",
                "You want to help? Based! The NPEPE army runs on passion. Be active, welcome new frens, and spread the gospel of NPEPE across the internet like a religion! 🐸🙏",
                "The most valuable thing you can do is bring your energy here every day and make some noise on X. Let's build this together! 💚",
                "We're a community project—our collective effort is the only collab we need. Spread the meme, fren!",
                "Hit up the X (Twitter) channel and start some chaos! That's the best marketing money can't buy. LFG!"
            ],
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
        """Function run by Timer after 5 minutes."""
        now_utc = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save member to DB
        self._update_member_info(member_id, first_name, now_utc, last_interacted_date=now_utc)
        
        # Prepare message
        welcome_text = random.choice(self.responses.get("GREET_NEW_MEMBERS_DELAYED", [])).format(name=f"[{first_name}](tg://user?id={member_id})")
        
        try:
            # Send message
            self.bot.send_message(chat_id, welcome_text, parse_mode="Markdown")
            logger.info(f"Delayed greeting sent to new member: {member_id}")
        except Exception as e:
            logger.error(f"Failed to send welcome message via Timer: {e}")

    def greet_new_members(self, message):
        try:
            for member in message.new_chat_members:
                logger.info(f"New member {member.id} detected. Scheduling delayed greeting in 5 minutes (300 seconds)...")
                
                # FIX: Use threading.Timer to prevent blocking Waitress
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
                about_text = (" 🚀  *$NPEPE* is the next evolution of meme power!\n"
                              "We are a community-driven force born on *Pump.fun*.\n\n"
                              "This is 100% pure, unadulterated meme energy. Welcome to the NPEPEVERSE!  🐸 ")
                
                # FIX: Check if message is already displaying this content to avoid 'message is not modified' error
                if call.message.text != about_text:
                    self.bot.answer_callback_query(call.id)
                    self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=about_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
                else:
                    self.bot.answer_callback_query(call.id, text="You are already viewing the About page!", show_alert=False)

            elif call.data == "ca":
                ca_text = f" 🔗  *Contract Address:*\n`{Config.CONTRACT_ADDRESS()}`"
                
                # FIX: Check if message is already displaying this content to avoid 'message is not modified' error
                if call.message.text != ca_text:
                    self.bot.answer_callback_query(call.id)
                    self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=ca_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
                else:
                    self.bot.answer_callback_query(call.id, text="You are already viewing the Contract Address!", show_alert=False)

            else:
                self.bot.answer_callback_query(call.id, text="Action not recognized.")
        except Exception as e:
            logger.error(f"Error in callback handler: {e}", exc_info=True)
            try:
                # Handle 'message is not modified' gracefully
                if "message is not modified" in str(e):
                    logger.warning("Attempted to edit an unmodified message. Ignoring API error.")
                    return
                    
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
                # FIX: Dispatch AI processing to a separate thread to prevent blocking Waitress
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

    # --- FUNGSI TUGAS TERJADWAL ---

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
                # 3. Check membership in group (skip jika sudah tidak jadi member)
                chat_member = self.bot.get_chat_member(group_id, user_id)
                if chat_member.status in ['member', 'administrator', 'creator']:
                    members_to_greet.append((user_id, username))
                    
                    # Tandai sebagai sudah disapa di DB
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

        # 4. Kirim sapaan ke 3 member yang valid
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
            
            # Hitung selisih bulan
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
            
    # --- FUNGSI AI RENEWAL ---

    def renew_responses_with_ai(self):
        logger.info("Starting weekly AI response renewal process.")
        if not self.groq_client:
            logger.warning("Skipping AI renewal: Groq not initialized.")
            return

        # Menggunakan jumlah variasi yang diminta (30, 50, 50, 30, 100, 20)
        categories_to_renew = {
            "GREET_NEW_MEMBERS_DELAYED": ("Produce 30 unique, friendly, and funny welcome messages for new members in a crypto group. The message should NOT mention the 5-minute delay. Must include the placeholder '{name}'. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 25), # Minimal 25 dari 30
            "DAILY_GREETING": ("Produce 50 short, energetic, and based check-in messages for a crypto group bot. The message should tag a random user (using the placeholder '{mention}') and ask how they are HODLing up or how their day is going. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 40), # Minimal 40 dari 50
            "MEMBERSHIP_ANNIVERSARY": ("Produce 50 unique, proud, and enthusiastic messages to thank a member for their long-term HODLing in a meme coin community (1, 2, 3, etc., months). Must include placeholders '{mention}' for the user and '{months}' for the duration. Use strong $NPEPE/meme coin slang: 'diamond hands', 'based', 'LFG', 'WAGMI'.", 40), # Minimal 40 dari 50
            "BIRTHDAY_ASK": ("Produce 30 unique, fun, and casual messages for a meme coin bot to ask the community if anyone is having a birthday this week. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based'.", 25), # Minimal 25 dari 30
            "BIRTHDAY_GREETING": ("Produce 30 unique, energetic, and funny birthday greetings for a meme coin member. Use the placeholder '{name}' and $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 25), # Minimal 25 dari 30
            "HEALTH_REMINDER": ("Produce 100 unique, caring, but meme-themed messages reminding all crypto members to take a break, eat, rest, and spend time with loved ones, to prevent burnout from trading/HODLing. Must include the placeholder '{tags}' for a list of mentions. Use $NPEPE slang: 'fren', 'diamond hands', 'HODL'.", 80), # Minimal 80 dari 100
            "BOT_IDENTITY": ("Produce 20 short, chaotic, and proud identity answers for a meme coin bot. The bot should explicitly state it is a 'frog' whose task is to keep members 'sane' while they HODL $NPEPE. Use $NPEPE slang: 'fren', 'ribbit', 'based'.", 15), # Minimal 15 dari 20
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
