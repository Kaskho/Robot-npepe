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

# Inisialisasi Bot
try:
    [span_11](start_span)if all([Config.BOT_TOKEN(), Config.WEBHOOK_BASE_URL(), Config.DATABASE_URL()]):[span_11](end_span)
        [span_12](start_span)Bot = telebot.TeleBot(Config.BOT_TOKEN(), threaded=False)[span_12](end_span)
        Bot_logic = BotLogic(Bot)
    else:
        Logger.critical("FATAL: Variabel lingkungan penting tidak ditemukan.")
except Exception as e:
    [span_13](start_span)Logger.critical(f"Terjadi error saat inisialisasi bot: {e}", exc_info=True)[span_13](end_span)

# Webhook untuk Telegram
[span_14](start_span)@App.route(f'/{Config.BOT_TOKEN()}', methods=['POST'])[span_14](end_span)
def webhook():
    [span_15](start_span)if Bot_logic and request.headers.get('content-type') == 'application/json':[span_15](end_span)
        try:
            Bot_logic.check_and_run_schedules()
            Json_string = request.get_data().decode('utf-8')
            Update = telebot.types.Update.de_json(json_string)
            Bot.process_new_updates([Update])
        except Exception as e:
            [span_16](start_span)Logger.error(f"Pengecualian di webhook: {e}", exc_info=True)[span_16](end_span)
        return "OK", 200
    else:
        [span_17](start_span)abort(403)[span_17](end_span)

# Endpoint Health Check yang baru dan sangat kecil
[span_18](start_span)@App.route('/health', methods=['GET'])[span_18](end_span)
def health_check():
    [span_19](start_span)Logger.info("Ping 'Health Check' diterima.")[span_19](end_span)
    if Bot_logic:
        # Panggil penjadwal untuk memastikan tugas harian berjalan
        [span_20](start_span)Bot_logic.check_and_run_schedules()[span_20](end_span) 
    # Mengembalikan respons “204 No Content”, yang benar-benar kosong.
    [span_21](start_span)return "", 204[span_21](end_span)

# Halaman utama
[span_22](start_span)@App.route('/')[span_22](end_span)
def index():
    [span_23](start_span)return " 🐸  Bot Telegram NPEPE hidup — webhook diaktifkan.", 200[span_23](end_span)

# Fungsi untuk menjalankan server
[span_24](start_span)if __name__ == "__main__":[span_24](end_span)
    [span_25](start_span)Port = int(os.environ.get("PORT", 10000))[span_25](end_span)
    if Bot and Bot_logic:
        [span_26](start_span)Webhook_url = f"{Config.WEBHOOK_BASE_URL()}/{Config.BOT_TOKEN()}"[span_26](end_span)
        [span_27](start_span)Logger.info("Memulai bot dan mengatur webhook...")[span_27](end_span)
        try:
            [span_28](start_span)Bot.remove_webhook()[span_28](end_span)
            [span_29](start_span)time.sleep(0.5)[span_29](end_span)
            [span_30](start_span)Success = Bot.set_webhook(url=Webhook_url)[span_30](end_span)
            if Success:
                [span_31](start_span)Logger.info("✅ Webhook berhasil diatur.")[span_31](end_span)
            else:
                [span_32](start_span)Logger.error("❌ Gagal mengatur webhook.")[span_32](end_span)
        except Exception as e:
            [span_33](start_span)Logger.error(f"Error saat mengkonfigurasi webhook: {e}", exc_info=True)[span_33](end_span)
        
        [span_34](start_span)serve(App, host="0.0.0.0", port=Port)[span_34](end_span)
    else:
        [span_35](start_span)Logger.error("Bot tidak diinisialisasi. Berjalan dalam mode server terdegradasi.")[span_35](end_span)
        [span_36](start_span)serve(App, host="0.0.0.0", port=Port)[span_36](end_span)
