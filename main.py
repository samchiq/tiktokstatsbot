import os
import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import urllib.parse

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://tiktokstatsbot.onrender.com')
PORT = int(os.getenv('PORT', 10000))
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 5400))  # 90 минут в секундах

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('tiktok_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monitored_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            video_id TEXT NOT NULL,
            video_url TEXT NOT NULL,
            last_views INTEGER DEFAULT 0,
            last_likes INTEGER DEFAULT 0,
            last_comments INTEGER DEFAULT 0,
            last_shares INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, video_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    return conn

# Глобальное соединение с БД
db_conn = init_db()

class TikTokMonitor:
    def __init__(self):
        self.session = None
        
    async def get_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
        
    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_video_stats(self, video_id):
        """Получение статистики видео через RapidAPI"""
        if not RAPIDAPI_KEY:
            logger.error("RAPIDAPI_KEY не настроен")
            return None
            
        url = "https://tiktok-scraper2.p.rapidapi.com/video/info"
        querystring = {"video_id": video_id}
        
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": "tiktok-scraper2.p.rapidapi.com"
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers, params=querystring)
                response.raise_for_status()
                data = response.json()
                
                logger.info(f"Ответ API для {video_id}: {data}")
                
                # Обработка разных форматов ответа
                stats = None
                if isinstance(data, dict):
                    if 'data' in data and isinstance(data['data'], dict):
                        stats = data['data'].get('stats', {})
                    elif 'stats' in data:
                        stats = data['stats']
                    else:
                        stats = data
                
                if not stats:
                    logger.warning("Не удалось найти stats в ответе API")
                    return None
                
                # Извлечение статистики
                views = stats.get('playCount') or stats.get('views') or 0
                likes = stats.get('diggCount') or stats.get('likes') or 0
                comments = stats.get('commentCount') or stats.get('comments') or 0
                shares = stats.get('shareCount') or stats.get('shares') or 0

                result = {
                    'views': int(views) if views else 0,
                    'likes': int(likes) if likes else 0,
                    'comments': int(comments) if comments else 0,
                    'shares': int(shares) if shares else 0
                }
                
                # Проверка на нулевые значения
                if all(value == 0 for value in result.values()):
                    logger.warning("Все статистические данные нулевые")
                    return None
                    
                return result
                
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            return None

    def extract_video_id(self, url):
        """Извлечение ID видео из URL TikTok"""
        try:
            parsed = urllib.parse.urlparse(url)
            if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
                # Для коротких ссылок нужен async метод
                return None  # Будет обработано отдельно
            
            path_parts = parsed.path.split('/')
            if 'video' in path_parts:
                video_index = path_parts.index('video')
                if video_index + 1 < len(path_parts):
                    return path_parts[video_index + 1].split('?')[0]
                    
            return None
        except Exception as e:
            logger.error(f"Ошибка извлечения ID: {e}")
            return None

    async def get_redirect_video_id(self, short_url):
        """Получение ID из короткой ссылки"""
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(short_url)
                final_url = str(response.url)
                return self.extract_video_id(final_url)
        except Exception as e:
            logger.error(f"Ошибка получения ID из короткой ссылки: {e}")
            return None

# Инициализация монитора
tiktok_monitor = TikTokMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = """
🎵 TikTok Stats Monitor Bot

Отправьте мне ссылку на видео TikTok, и я буду отслеживать его статистику.

Команды:
/start - показать это сообщение
/list - список отслеживаемых видео
/help - помощь

Просто отправьте ссылку на видео TikTok!
    """
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📖 Помощь по боту

Как использовать:
1. Отправьте ссылку на видео TikTok
2. Бот начнет отслеживать статистику
3. Получайте обновления каждые 90 минут

Форматы ссылок:
• https://vm.tiktok.com/ZSJxxxxxxxx/
• https://www.tiktok.com/@user/video/1234567890123456789
• https://vt.tiktok.com/ZSJxxxxxxxx/

Команды:
/list - показать все отслеживаемые видео
/stats [ссылка] - получить текущую статистику
    """
    await update.message.reply_text(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    text = update.message.text.strip()
    
    if not any(domain in text for domain in ['tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com']):
        await update.message.reply_text("❌ Пожалуйста, отправьте действительную ссылку на видео TikTok.")
        return

    # Обработка коротких ссылок
    if 'vm.tiktok.com' in text or 'vt.tiktok.com' in text:
        video_id = await tiktok_monitor.get_redirect_video_id(text)
    else:
        video_id = tiktok_monitor.extract_video_id(text)
    
    if not video_id:
        await update.message.reply_text("❌ Не удалось извлечь ID видео из ссылки.")
        return

    # Проверяем существование видео
    loading_msg = await update.message.reply_text("🔍 Проверяем видео...")
    
    stats = await tiktok_monitor.get_video_stats(video_id)
    if not stats:
        await loading_msg.edit_text("❌ Не удалось получить статистику видео. Проверьте ссылку или попробуйте позже (возможно превышен лимит API).")
        return

    # Сохраняем видео для отслеживания
    chat_id = update.message.chat_id
    
    try:
        cursor = db_conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO monitored_videos 
            (chat_id, video_id, video_url, last_views, last_likes, last_comments, last_shares)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (chat_id, video_id, text, stats['views'], stats['likes'], stats['comments'], stats['shares']))
        
        db_conn.commit()
        
        # Сохраняем историю статистики
        cursor.execute('''
            INSERT INTO video_stats (video_id, views, likes, comments, shares)
            VALUES (?, ?, ?, ?, ?)
        ''', (video_id, stats['views'], stats['likes'], stats['comments'], stats['shares']))
        
        db_conn.commit()
        
        # Создаем клавиатуру с действиями
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{video_id}")],
            [InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{video_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        stats_text = f"""
✅ Видео добавлено для отслеживания!

📊 Текущая статистика:
👁️ Просмотры: {stats['views']:,}
❤️ Лайки: {stats['likes']:,}
💬 Комментарии: {stats['comments']:,}
↩️ Репосты: {stats['shares']:,}

Бот будет проверять статистику каждые 90 минут.
        """.strip()
        
        await loading_msg.edit_text(stats_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Ошибка сохранения видео: {e}")
        await loading_msg.edit_text("❌ Ошибка при сохранении видео.")

async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список отслеживаемых видео"""
    chat_id = update.message.chat_id
    
    cursor = db_conn.cursor()
    cursor.execute('''
        SELECT video_id, video_url, last_views, last_likes, last_comments, last_shares
        FROM monitored_videos 
        WHERE chat_id = ?
    ''', (chat_id,))
    
    videos = cursor.fetchall()
    
    if not videos:
        await update.message.reply_text("📭 У вас нет отслеживаемых видео.")
        return
    
    message_text = "📋 Ваши отслеживаемые видео:\n\n"
    
    for i, (video_id, url, views, likes, comments, shares) in enumerate(videos, 1):
        message_text += f"{i}. {url[:50]}...\n"
        message_text += f"   👁️ {views:,} | ❤️ {likes:,} | 💬 {comments:,} | ↩️ {shares:,}\n\n"
    
    await update.message.reply_text(message_text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    chat_id = query.message.chat_id
    
    if data.startswith('refresh_'):
        video_id = data.split('_', 1)[1]
        await refresh_stats(query, video_id)
        
    elif data.startswith('delete_'):
        video_id = data.split('_', 1)[1]
        await delete_video(query, video_id)

async def refresh_stats(query, video_id):
    """Обновление статистики"""
    await query.edit_message_text("🔄 Обновляем статистику...")
    
    stats = await tiktok_monitor.get_video_stats(video_id)
    if not stats:
        await query.edit_message_text("❌ Не удалось обновить статистику. Возможно превышен лимит API.")
        return
    
    # Обновляем статистику в БД
    cursor = db_conn.cursor()
    cursor.execute('''
        UPDATE monitored_videos 
        SET last_views = ?, last_likes = ?, last_comments = ?, last_shares = ?
        WHERE video_id = ?
    ''', (stats['views'], stats['likes'], stats['comments'], stats['shares'], video_id))
    
    cursor.execute('''
        INSERT INTO video_stats (video_id, views, likes, comments, shares)
        VALUES (?, ?, ?, ?, ?)
    ''', (video_id, stats['views'], stats['likes'], stats['comments'], stats['shares']))
    
    db_conn.commit()
    
    # Обновляем клавиатуру
    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{video_id}")],
        [InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{video_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    stats_text = f"""
📊 Обновленная статистика:

👁️ Просмотры: {stats['views']:,}
❤️ Лайки: {stats['likes']:,}
💬 Комментарии: {stats['comments']:,}
↩️ Репосты: {stats['shares']:,}
    """.strip()
    
    await query.edit_message_text(stats_text, reply_markup=reply_markup)

async def delete_video(query, video_id):
    """Удаление видео из отслеживания"""
    cursor = db_conn.cursor()
    cursor.execute('DELETE FROM monitored_videos WHERE video_id = ?', (video_id,))
    db_conn.commit()
    
    await query.edit_message_text("✅ Видео удалено из отслеживания.")

async def check_videos_task(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача проверки статистики"""
    logger.info("🔍 Запуск проверки видео...")
    
    cursor = db_conn.cursor()
    cursor.execute('SELECT DISTINCT video_id FROM monitored_videos')
    videos = cursor.fetchall()
    
    logger.info(f"📹 Найдено {len(videos)} видео для проверки")
    
    for (video_id,) in videos:
        try:
            stats = await tiktok_monitor.get_video_stats(video_id)
            if stats:
                # Получаем всех пользователей, отслеживающих это видео
                cursor.execute('''
                    SELECT chat_id, last_views, last_likes, last_comments, last_shares 
                    FROM monitored_videos 
                    WHERE video_id = ?
                ''', (video_id,))
                
                tracked_videos = cursor.fetchall()
                
                for chat_id, old_views, old_likes, old_comments, old_shares in tracked_videos:
                    # Обновляем статистику
                    cursor.execute('''
                        UPDATE monitored_videos 
                        SET last_views = ?, last_likes = ?, last_comments = ?, last_shares = ?
                        WHERE chat_id = ? AND video_id = ?
                    ''', (stats['views'], stats['likes'], stats['comments'], stats['shares'], chat_id, video_id))
                    
                    # Сохраняем историю
                    cursor.execute('''
                        INSERT INTO video_stats (video_id, views, likes, comments, shares)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (video_id, stats['views'], stats['likes'], stats['comments'], stats['shares']))
                    
                    db_conn.commit()
                    
        except Exception as e:
            logger.error(f"Ошибка проверки видео {video_id}: {e}")
            continue
    
    logger.info("✅ Проверка видео завершена")

async def webhook_handler(request):
    """Обработчик вебхука от Telegram"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(text="Error", status=500)

async def health_check(request):
    """Проверка здоровья приложения"""
    return web.Response(text="Bot is running", status=200)

async def on_startup(app):
    """Действия при запуске приложения"""
    logger.info("🚀 Запуск фоновых задач...")
    
    # Запуск периодической проверки
    application.job_queue.run_repeating(
        check_videos_task, 
        interval=CHECK_INTERVAL, 
        first=10
    )

async def main():
    """Основная функция запуска"""
    global application
    
    # Инициализация бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_videos))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Инициализация бота
    await application.initialize()
    
    # Настройка вебхука
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")
    
    # Создание aiohttp приложения
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    # Запуск при запуске
    app.on_startup.append(on_startup)
    
    # Запуск сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"🚀 Запуск веб-сервера на 0.0.0.0:{PORT}")
    
    # Бесконечный цикл
    await asyncio.Future()

if __name__ == '__main__':
    # Логирование информации о запуске
    logger.info("=" * 60)
    logger.info("🎵 TikTok Monitor Bot - Запуск")
    logger.info("=" * 60)
    logger.info(f"🌐 Порт: {PORT}")
    logger.info(f"🔗 Webhook URL: {WEBHOOK_URL}")
    logger.info(f"⏱ Интервал проверки: {CHECK_INTERVAL} секунд")
    logger.info(f"🔑 RapidAPI: {'✅ Настроен' if RAPIDAPI_KEY else '❌ Не настроен'}")
    logger.info("=" * 60)
    
    # Запуск приложения
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    finally:
        # Закрытие соединений
        asyncio.run(tiktok_monitor.close_session())
        db_conn.close()
