import os
import logging
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
import asyncio
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler
)
import requests

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://your-app.onrender.com')
PORT = int(os.getenv('PORT', '10000'))
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL', '72'))  # Изменено на 72 минуты для 20 запросов/день
RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY', '8a8054913emsha5bb222aa3d3a45p158b8bjsn77f2a167e65f')
DATA_FILE = 'tracked_videos.json'

class VideoTracker:
    """Класс для хранения и управления отслеживаемыми видео"""
    
    def __init__(self):
        self.data: Dict = self.load_data()
    
    def load_data(self) -> Dict:
        """Загрузка данных из файла"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
        return {}
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    def add_video(self, user_id: int, video_url: str, video_id: str):
        """Добавление видео для отслеживания"""
        user_key = str(user_id)
        if user_key not in self.data:
            self.data[user_key] = []
        
        # Проверка, не добавлено ли уже это видео
        for video in self.data[user_key]:
            if video['video_id'] == video_id:
                return False
        
        self.data[user_key].append({
            'video_id': video_id,
            'video_url': video_url,
            'added_at': datetime.now().isoformat(),
            'last_views': 0,
            'last_likes': 0,
            'last_shares': 0,
            'last_favorites': 0,
            'notified_at_views': 0
        })
        self.save_data()
        return True
    
    def remove_video(self, user_id: int, video_id: str) -> bool:
        """Удаление видео из отслеживания"""
        user_key = str(user_id)
        if user_key not in self.data:
            return False
        
        initial_length = len(self.data[user_key])
        self.data[user_key] = [
            v for v in self.data[user_key] if v['video_id'] != video_id
        ]
        
        if len(self.data[user_key]) == 0:
            del self.data[user_key]
        
        if initial_length > len(self.data.get(str(user_id), [])):
            self.save_data()
            return True
        return False
    
    def get_user_videos(self, user_id: int) -> List[Dict]:
        """Получение всех видео пользователя"""
        return self.data.get(str(user_id), [])
    
    def update_video_stats(self, user_id: int, video_id: str, stats: Dict):
        """Обновление статистики видео"""
        user_key = str(user_id)
        if user_key not in self.data:
            return
        
        for video in self.data[user_key]:
            if video['video_id'] == video_id:
                video['last_views'] = stats.get('views', 0)
                video['last_likes'] = stats.get('likes', 0)
                video['last_shares'] = stats.get('shares', 0)
                video['last_favorites'] = stats.get('favorites', 0)
                break
        
        self.save_data()
    
    def get_all_tracked_videos(self) -> List[tuple]:
        """Получение всех отслеживаемых видео"""
        result = []
        for user_id, videos in self.data.items():
            for video in videos:
                result.append((int(user_id), video))
        return result

class TikTokMonitor:
    """Класс для работы с TikTok через RapidAPI"""
    
    def __init__(self):
        self.api_key = RAPIDAPI_KEY
        self.api_host = "tiktok-scraper2.p.rapidapi.com"
        self.headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.api_host
        }
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечение ID видео из URL"""
        # Обработка коротких ссылок
        if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
            try:
                response = requests.head(url, allow_redirects=True, timeout=10)
                url = response.url
                logger.info(f"Получен редирект на: {url}")
            except Exception as e:
                logger.warning(f"Не удалось получить редирект: {e}")
        
        # Паттерны для извлечения ID
        patterns = [
            r'tiktok\.com/@[\w.-]+/video/(\d+)',
            r'tiktok\.com/.*?/video/(\d+)',
            r'(?:vm\.tiktok\.com|vt\.tiktok\.com)/([A-Za-z0-9]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def get_video_stats(self, video_id: str, video_url: str) -> Optional[Dict]:
        """Получение статистики видео через RapidAPI"""
        try:
            # RapidAPI TikTok Scraper2 endpoint
            api_url = "https://tiktok-scraper2.p.rapidapi.com/video/info"
            
            querystring = {"video_url": video_url}
            
            logger.info(f"Запрос к RapidAPI для: {video_id}")
            response = requests.get(api_url, headers=self.headers, params=querystring, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Ответ API: {json.dumps(data, indent=2)[:500]}")
                
                # Извлекаем статистику из ответа
                stats = self._extract_stats_from_response(data)
                
                if stats and any(v > 0 for v in stats.values()):
                    logger.info(f"✅ Статистика для {video_id}: views={stats['views']}, likes={stats['likes']}")
                    return stats
                else:
                    logger.warning(f"Статистика получена, но все значения нулевые")
                    return None
            
            elif response.status_code == 429:
                logger.error(f"❌ Превышен лимит запросов RapidAPI (429)")
                return None
            
            elif response.status_code == 403:
                logger.error(f"❌ Ошибка доступа к RapidAPI (403) - проверьте ключ")
                return None
            
            else:
                logger.error(f"❌ Ошибка API: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}", exc_info=True)
            return None
    
    def _extract_stats_from_response(self, data: dict) -> Optional[Dict]:
        """Извлечение статистики из ответа RapidAPI"""
        try:
            # Разные форматы ответа API
            stats_data = None
            
            # Формат 1: data.stats
            if 'data' in data and isinstance(data['data'], dict):
                if 'stats' in data['data']:
                    stats_data = data['data']['stats']
                elif 'play_count' in data['data'] or 'playCount' in data['data']:
                    stats_data = data['data']
            
            # Формат 2: прямо stats в корне
            elif 'stats' in data:
                stats_data = data['stats']
            
            # Формат 3: video_info или itemInfo
            elif 'video_info' in data:
                stats_data = data['video_info'].get('stats')
            elif 'itemInfo' in data:
                item_struct = data['itemInfo'].get('itemStruct', {})
                stats_data = item_struct.get('stats')
            
            if not stats_data:
                logger.warning("Не удалось найти stats в ответе API")
                return None
            
            # Извлекаем значения (поддерживаем разные форматы ключей)
            result = {
                'views': (
                    stats_data.get('playCount') or 
                    stats_data.get('play_count') or 
                    stats_data.get('viewCount') or 
                    stats_data.get('view_count') or 0
                ),
                'likes': (
                    stats_data.get('diggCount') or 
                    stats_data.get('digg_count') or 
                    stats_data.get('likeCount') or 
                    stats_data.get('like_count') or 0
                ),
                'shares': (
                    stats_data.get('shareCount') or 
                    stats_data.get('share_count') or 0
                ),
                'favorites': (
                    stats_data.get('collectCount') or 
                    stats_data.get('collect_count') or 0
                )
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка парсинга ответа API: {e}")
            return None

# Глобальные объекты
tracker = VideoTracker()
tiktok_monitor = TikTokMonitor()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = (
        "🎵 *Добро пожаловать в TikTok Monitor Bot!*\n\n"
        "Я помогу вам отслеживать статистику ваших TikTok видео и буду присылать "
        "уведомления каждые 50,000 просмотров!\n\n"
        "📋 *Доступные команды:*\n\n"
        "/set `<ссылка>` \\- Добавить видео для отслеживания\n"
        "/stats \\- Показать статистику всех отслеживаемых видео\n"
        "/remove \\- Удалить видео из отслеживания\n\n"
        "📊 *Что я отслеживаю:*\n"
        "• Просмотры \\(уведомления каждые 50K\\)\n"
        "• Лайки\n"
        "• Репосты\n"
        "• Добавления в избранное\n\n"
        f"⏱ Проверка статистики каждые {CHECK_INTERVAL_MINUTES} минут\n\n"
        "Начните с команды /set и укажите ссылку на ваше TikTok видео\\!"
    )
    await update.message.reply_text(welcome_message, parse_mode='MarkdownV2')

async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /set"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Пожалуйста, укажите ссылку на TikTok видео\n\n"
            "Пример: /set https://www.tiktok.com/@username/video/1234567890"
        )
        return
    
    video_url = context.args[0]
    video_id = tiktok_monitor.extract_video_id(video_url)
    
    if not video_id:
        await update.message.reply_text(
            "❌ Не удалось распознать ссылку на TikTok видео.\n\n"
            "Убедитесь, что ссылка имеет формат:\n"
            "• https://www.tiktok.com/@username/video/1234567890\n"
            "• https://vm.tiktok.com/ZMabcdefg/"
        )
        return
    
    # Отправка сообщения о загрузке
    loading_msg = await update.message.reply_text("⏳ Получаю информацию о видео...")
    
    # Получение начальной статистики
    stats = await tiktok_monitor.get_video_stats(video_id, video_url)
    
    if not stats:
        await loading_msg.edit_text(
            "❌ Не удалось получить информацию о видео.\n\n"
            "Возможные причины:\n"
            "• Видео недоступно или приватное\n"
            "• Превышен лимит запросов API (20/день)\n"
            "• Проблемы с RapidAPI\n\n"
            "Попробуйте позже или проверьте ссылку."
        )
        return
    
    # Добавление видео
    if tracker.add_video(user_id, video_url, video_id):
        tracker.update_video_stats(user_id, video_id, stats)
        
        message = (
            f"✅ *Видео добавлено для отслеживания!*\n\n"
            f"🔗 ID: `{video_id}`\n\n"
            f"📊 *Текущая статистика:*\n"
            f"👁 Просмотры: *{stats['views']:,}*\n"
            f"❤️ Лайки: *{stats['likes']:,}*\n"
            f"🔄 Репосты: *{stats['shares']:,}*\n"
            f"⭐ Избранное: *{stats['favorites']:,}*\n\n"
            f"⏱ Проверка каждые {CHECK_INTERVAL_MINUTES} минут\n"
            f"🔔 Уведомление каждые 50,000 просмотров!"
        )
        await loading_msg.edit_text(message, parse_mode='Markdown')
    else:
        await loading_msg.edit_text("⚠️ Это видео уже отслеживается!")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats"""
    user_id = update.effective_user.id
    videos = tracker.get_user_videos(user_id)
    
    if not videos:
        await update.message.reply_text(
            "📭 У вас нет отслеживаемых видео.\n\n"
            "Используйте команду /set чтобы добавить видео."
        )
        return
    
    loading_msg = await update.message.reply_text("⏳ Обновляю статистику...")
    
    message_parts = ["📊 *Статистика ваших видео:*\n"]
    
    for idx, video in enumerate(videos, 1):
        video_id = video['video_id']
        video_url = video['video_url']
        
        # Получение актуальной статистики
        stats = await tiktok_monitor.get_video_stats(video_id, video_url)
        
        if stats:
            tracker.update_video_stats(user_id, video_id, stats)
            
            # Расчет прогресса до следующего уведомления
            current_views = stats['views']
            next_milestone = ((current_views // 50000) + 1) * 50000
            progress = current_views % 50000
            progress_percent = (progress / 50000) * 100
            
            message_parts.append(
                f"\n*{idx}. Видео* `{video_id[:12]}...`\n"
                f"👁 Просмотры: *{stats['views']:,}*\n"
                f"❤️ Лайки: *{stats['likes']:,}*\n"
                f"🔄 Репосты: *{stats['shares']:,}*\n"
                f"⭐ Избранное: *{stats['favorites']:,}*\n"
                f"📈 До вехи: *{next_milestone - current_views:,}* ({progress_percent:.1f}%)\n"
            )
        else:
            message_parts.append(
                f"\n*{idx}. Видео* `{video_id[:12]}...`\n"
                f"❌ Не удалось получить статистику\n"
                f"(возможно превышен лимит API)\n"
            )
    
    await loading_msg.edit_text(''.join(message_parts), parse_mode='Markdown')

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /remove"""
    user_id = update.effective_user.id
    videos = tracker.get_user_videos(user_id)
    
    if not videos:
        await update.message.reply_text(
            "📭 У вас нет отслеживаемых видео для удаления."
        )
        return
    
    # Создание клавиатуры с кнопками
    keyboard = []
    for video in videos:
        video_id = video['video_id']
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 Удалить {video_id[:15]}...",
                callback_data=f"remove_{video_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите видео для удаления:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "cancel":
        await query.edit_message_text("❌ Отменено")
        return
    
    if data.startswith("remove_"):
        video_id = data.replace("remove_", "")
        
        if tracker.remove_video(user_id, video_id):
            await query.edit_message_text(
                f"✅ Видео `{video_id}` удалено из отслеживания",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Видео не найдено")

async def check_videos_task(application: Application):
    """Периодическая проверка всех отслеживаемых видео"""
    logger.info("🔍 Запуск проверки видео...")
    
    all_videos = tracker.get_all_tracked_videos()
    logger.info(f"📹 Найдено {len(all_videos)} видео для проверки")
    
    for user_id, video in all_videos:
        video_id = video['video_id']
        video_url = video['video_url']
        last_views = video['last_views']
        last_notified = video['notified_at_views']
        
        # Получение текущей статистики
        stats = await tiktok_monitor.get_video_stats(video_id, video_url)
        
        if not stats:
            logger.warning(f"⚠️ Не удалось получить статистику для {video_id}")
            continue
        
        current_views = stats['views']
        tracker.update_video_stats(user_id, video_id, stats)
        
        # Проверка, достигнут ли новый рубеж в 50,000
        current_milestone = (current_views // 50000) * 50000
        last_milestone = (last_notified // 50000) * 50000
        
        if current_milestone > last_milestone and current_milestone > 0:
            # Отправка уведомления
            try:
                message = (
                    f"🎉 *Поздравляем! Новая веха достигнута!*\n\n"
                    f"Видео `{video_id[:15]}...` достигло *{current_milestone:,}* просмотров!\n\n"
                    f"📊 *Текущая статистика:*\n"
                    f"👁 Просмотры: *{stats['views']:,}*\n"
                    f"❤️ Лайки: *{stats['likes']:,}*\n"
                    f"🔄 Репосты: *{stats['shares']:,}*\n"
                    f"⭐ Избранное: *{stats['favorites']:,}*\n\n"
                    f"🔗 [Открыть видео]({video_url})"
                )
                
                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                
                # Обновление последнего уведомления
                video['notified_at_views'] = current_milestone
                tracker.save_data()
                
                logger.info(f"✅ Уведомление отправлено пользователю {user_id} для видео {video_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления: {e}")
    
    logger.info("✅ Проверка видео завершена")

async def periodic_check(application: Application):
    """Бесконечный цикл периодических проверок"""
    await asyncio.sleep(60)  # Ждем минуту перед первой проверкой
    
    while True:
        try:
            await check_videos_task(application)
        except Exception as e:
            logger.error(f"❌ Ошибка в периодической проверке: {e}")
        
        # Ждем указанное количество минут
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)

async def health_check(request):
    """Эндпоинт для проверки здоровья сервиса"""
    return web.Response(text="OK", status=200)

async def webhook_handler(request):
    """Обработчик webhook запросов от Telegram"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        return web.Response(status=500)

async def setup_webhook(app_instance):
    """Настройка webhook"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        await app_instance.bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Ошибка установки webhook: {e}")

# Глобальная переменная для приложения
application = None

async def start_background_tasks(app):
    """Запуск фоновых задач"""
    logger.info("🚀 Запуск фоновых задач...")
    app['check_task'] = asyncio.create_task(periodic_check(application))

async def cleanup_background_tasks(app):
    """Очистка фоновых задач"""
    logger.info("🛑 Остановка фоновых задач...")
    if 'check_task' in app:
        app['check_task'].cancel()
        try:
            await app['check_task']
        except asyncio.CancelledError:
            pass

def main():
    """Главная функция запуска бота"""
    global application
    
    logger.info("=" * 60)
    logger.info("🎵 TikTok Monitor Bot - Запуск")
    logger.info("=" * 60)
    logger.info(f"🌐 Порт: {PORT}")
    logger.info(f"🔗 Webhook URL: {WEBHOOK_URL}")
    logger.info(f"⏱ Интервал проверки: {CHECK_INTERVAL_MINUTES} минут")
    logger.info(f"🔑 RapidAPI: {'✅ Настроен' if RAPIDAPI_KEY != 'YOUR_KEY' else '❌ Не настроен'}")
    logger.info("=" * 60)
    
    # Создание приложения Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("set", set_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Инициализация бота
    logger.info("🔧 Инициализация бота...")
    asyncio.get_event_loop().run_until_complete(application.initialize())
    asyncio.get_event_loop().run_until_complete(setup_webhook(application))
    
    # Создание веб-сервера
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    app.router.add_post('/webhook', webhook_handler)
    
    # Запуск фоновых задач
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    logger.info(f"🚀 Запуск веб-сервера на 0.0.0.0:{PORT}")
    
    # Запуск веб-сервера
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    main()
