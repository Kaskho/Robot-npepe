import os
import logging
import time
from flask import Flask, request, abort
import telebot
from bot_logic import BotLogic
from config import Config
from waitress import serve

logging.basicConfig(
    level=logging.INFO,
    # FIX: Changed non-standard quotes (’...’) to standard single quotes ('...')
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
Logger = logging.getLogger(__name__)
App = Flask(__name__)
Bot = None
Bot_logic = None

# Initialize Bot
# FIX: Changed 'Try' to lowercase 'try'
try: 
    # FIX: Changed 'If' to lowercase 'if'
    if all([Config.BOT_TOKEN(), Config.WEBHOOK_BASE_URL(), Config.DATABASE_URL()]):
        # FIX: Ensure 'Bot' instance is used consistently (was 'bot')
        Bot = telebot.TeleBot(Config.BOT_TOKEN(), threaded=False)
        Bot_logic = BotLogic(Bot) 
    # FIX: Changed 'Else' to lowercase 'else'
    else:
        # NOTE: Keeping this message in English for consistency
        Logger.critical("FATAL: Essential environment variables not found.")
except Exception as e:
    # NOTE: Keeping this message in English for consistency
    Logger.critical(f"Error occurred during bot initialization: {e}", exc_info=True)

# Webhook for Telegram
@App.route(f'/{Config.BOT_TOKEN()}', methods=['POST'])
def webhook():
    if Bot_logic and request.headers.get('content-type') == 'application/json':
        try:
            Bot_logic.check_and_run_schedules()
            Json_string = request.get_data().decode('utf-8')
            Update = telebot.types.Update.de_json(Json_string)
            Bot.process_new_updates([Update])
        except Exception as e:
            Logger.error(f"Exception in webhook: {e}", exc_info=True)
        return "OK", 200
    else:
        abort(403)

# New, minimal Health Check Endpoint
@App.route('/health', methods=['GET'])
def health_check():
    Logger.info("Ping 'Health Check' received.")
    if Bot_logic:
        Bot_logic.check_and_run_schedules()
    # Returns "204 No Content"
    return "", 204

# Home page
@App.route('/')
def index():
    return " 🐸  NPEPE Telegram Bot is live — webhook activated.", 200

# Function to run the server
if __name__ == "__main__":
    Port = int(os.environ.get("PORT", 10000))
    if Bot and Bot_logic:
        Webhook_url = f"{Config.WEBHOOK_BASE_URL()}/{Config.BOT_TOKEN()}"
        Logger.info("Starting bot and setting webhook...")
        try:
            Bot.remove_webhook()
            time.sleep(0.5)
            Success = Bot.set_webhook(url=Webhook_url)
            if Success:
                Logger.info("✅ Webhook successfully set.")
            else:
                Logger.error("❌ Failed to set webhook.")
        except Exception as e:
            Logger.error(f"Error configuring webhook: {e}", exc_info=True)
        
        serve(App, host="0.0.0.0", port=Port)
    else:
        Logger.error("Bot was not initialized. Running in degraded server mode.")
        serve(App, host="0.0.0.0", port=Port)
