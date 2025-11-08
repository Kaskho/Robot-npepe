# Menggunakan Python 3.11 sebagai dasar
FROM python:3.11-slim

# Mengatur direktori kerja
WORKDIR /app

# Menyalin dan menginstal semua yang dibutuhkan
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Menjalankan bot
CMD ["python3", "main.py"]
