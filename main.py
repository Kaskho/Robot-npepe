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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
Logger = logging.getLogger(__name__)
App = Flask(__name__)
Bot = None
Bot_logic = None

# Initialize Bot
try:
    if all([Config.BOT_TOKEN(), Config.WEBHOOK_BASE_URL(), Config.DATABASE_URL()]):
        # Set use_class_middlewares=True for proper handling of async tasks
        Bot = telebot.TeleBot(Config.BOT_TOKEN(), threaded=False, use_class_middlewares=True)
        Bot_logic = BotLogic(Bot)
    else:
        Logger.critical("FATAL: Essential environment variables not found.")
except Exception as e:
    Logger.critical(f"An error occurred during bot initialization: {e}", exc_info=True)

# Webhook for Telegram
@App.route(f'/{Config.BOT_TOKEN()}', methods=['POST'])
def webhook():
    if Bot_logic and request.headers.get('content-type') == 'application/json':
        try:
            # Check and run scheduled tasks using webhook requests
            Bot_logic.check_and_run_schedules() 
            
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            Bot.process_new_updates([update])
        except Exception as e:
            Logger.error(f"Exception in webhook: {e}", exc_info=True)
        return "OK", 200
    else:
        abort(403)

# New, minimal Health Check Endpoint
@App.route('/health', methods=['GET'])
def health_check():
    Logger.info("Ping 'Health Check' received.")
    # Removed Bot_logic.check_and_run_schedules() to keep this endpoint lightweight
    # Returns "204 No Content"
    return "", 204

# Home page
@App.route('/')
def index():
    return " 🐸  NPEPE Telegram Bot is alive — webhook activated.", 200

# Function to run the server
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    if Bot and Bot_logic:
        webhook_url = f"{Config.WEBHOOK_BASE_URL()}/{Config.BOT_TOKEN()}"
        Logger.info("Starting bot and setting webhook...")
        try:
            Bot.remove_webhook()
            time.sleep(0.5)
            success = Bot.set_webhook(url=webhook_url)
            if success:
                Logger.info("✅ Webhook successfully set.")
            else:
                Logger.error("❌ Failed to set webhook.")
        except Exception as e:
            Logger.error(f"Error configuring webhook: {e}", exc_info=True)
        
        serve(App, host="0.0.0.0", port=port)
    else:
        Logger.error("Bot not initialized. Running in degraded server mode.")
        serve(App, host="0.0.0.0", port=port)
