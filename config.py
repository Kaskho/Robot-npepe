import os

class Config:
    """
    Kelas untuk mengelola semua variabel konfigurasi dari environment.
    Memisahkannya ke file sendiri mencegah error import sirkular dan membuat
    konfigurasi lebih mudah dikelola.
    """
    @staticmethod
    def BOT_TOKEN(): return os.environ.get("BOT_TOKEN")
    
    @staticmethod
    def WEBHOOK_BASE_URL(): return os.environ.get("WEBHOOK_BASE_URL")
    
    @staticmethod
    def GROQ_API_KEY(): return os.environ.get("GROQ_API_KEY")
    
    @staticmethod
    def GROUP_CHAT_ID(): return os.environ.get("GROUP_CHAT_ID")
    
    @staticmethod
    def GROUP_OWNER_ID(): return os.environ.get("GROUP_OWNER_ID")
    
    @staticmethod
    def DATABASE_URL(): return os.environ.get("DATABASE_URL")
    
    @staticmethod
    def CONTRACT_ADDRESS(): return os.environ.get("CONTRACT_ADDRESS", "BJ65ym9UYPkcfLSUuE9j4uXYuiG6TgA4pFn393Eppump")
    
    @staticmethod
    def PUMP_FUN_LINK(): return f"https://pump.fun/{Config.CONTRACT_ADDRESS()}"
    
    @staticmethod
    def WEBSITE_URL(): return os.environ.get("WEBSITE_URL", "https://next-npepe-launchpad-2b8b3071.base44.app")
    
    @staticmethod
    def TELEGRAM_URL(): return os.environ.get("TELEGRAM_URL", "https://t.me/NPEPEVERSE")
    
    @staticmethod
    def TWITTER_URL(): return os.environ.get("TWITTER_URL", "https://x.com/NPEPE_Verse")
