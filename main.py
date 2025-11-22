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
from bs4 import BeautifulSoup

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
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL', '10'))
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
    """Класс для работы с TikTok через web scraping"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечение ID видео из URL"""
        # Обработка коротких ссылок
        if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
            try:
                response = requests.head(url, headers=self.headers, allow_redirects=True, timeout=10)
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
        """Получение статистики видео"""
        try:
            # Если короткая ссылка, получаем полный URL
            if 'vm.tiktok.com' in video_url or 'vt.tiktok.com' in video_url:
                response = requests.head(video_url, headers=self.headers, allow_redirects=True, timeout=10)
                video_url = response.url
                logger.info(f"Полный URL: {video_url}")
            
            # Получаем страницу
            response = requests.get(video_url, headers=self.headers, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"Ошибка загрузки страницы: {response.status_code}")
                return None
            
            html = response.text
            
            # Ищем JSON данные в script тегах
            stats = self._extract_stats_from_html(html)
            
            if stats and any(v > 0 for v in stats.values()):
                logger.info(f"Статистика для {video_id}: {stats}")
                return stats
            
            logger.warning(f"Не удалось извлечь статистику для {video_id}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return None
    
    def _extract_stats_from_html(self, html: str) -> Optional[Dict]:
        """Извлечение статистики из HTML"""
        stats = {
            'views': 0,
            'likes': 0,
            'shares': 0,
            'favorites': 0
        }
        
        # Ищем JSON в script тегах
        try:
            # Паттерн для SIGI_STATE или __UNIVERSAL_DATA_FOR_REHYDRATION__
            json_patterns = [
                r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>',
                r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.*?});',
                r'window\[\'SIGI_STATE\'\]\s*=\s*({.*?});',
            ]
            
            for pattern in json_patterns:
                matches = re.finditer(pattern, html, re.DOTALL)
                for match in matches:
                    try:
                        json_str = match.group(1)
                        json_data = json.loads(json_str)
                        
                        # Рекурсивный поиск статистики
                        found_stats = self._find_stats_in_json(json_data)
                        if found_stats and any(v > 0 for v in found_stats.values()):
                            return found_stats
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        logger.debug(f"Ошибка парсинга JSON: {e}")
                        continue
        
        except Exception as e:
            logger.debug(f"Ошибка поиска JSON: {e}")
        
        # Если JSON не найден, используем регулярные выражения
        patterns_map = {
            'views': [
                r'"playCount["\']?\s*:\s*["\']?(\d+)',
                r'"viewCount["\']?\s*:\s*["\']?(\d+)',
                r'playCount[&quot;]*:(\d+)',
            ],
            'likes': [
                r'"diggCount["\']?\s*:\s*["\']?(\d+)',
                r'"likeCount["\']?\s*:\s*["\']?(\d+)',
                r'diggCount[&quot;]*:(\d+)',
            ],
            'shares': [
                r'"shareCount["\']?\s*:\s*["\']?(\d+)',
                r'shareCount[&quot;]*:(\d+)',
            ],
            'favorites': [
                r'"collectCount["\']?\s*:\s*["\']?(\d+)',
                r'collectCount[&quot;]*:(\d+)',
            ]
        }
        
        for key, patterns in patterns_map.items():
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    try:
                        stats[key] = int(match.group(1))
                        break
                    except (ValueError, IndexError):
                        continue
        
        return stats if any(v > 0 for v in stats.values()) else None
    
    def _find_stats_in_json(self, data, depth=0, max_depth=10) -> Optional[Dict]:
        """Рекурсивный поиск статистики в JSON"""
        if depth > max_depth or data is None:
            return None
        
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                return None
        
        if isinstance(data, dict):
            # Прямой поиск stats
            if 'stats' in data:
                stats_data = data['stats']
                if isinstance(stats_data, dict):
                    result = {
                        'views': stats_data.get('playCount', 0) or stats_data.get('viewCount', 0),
                        'likes': stats_data.get('diggCount', 0) or stats_data.get('likeCount', 0),
                        'shares': stats_data.get('shareCount', 0),
                        'favorites': stats_data.get('collectCount', 0)
                    }
                    if any(v > 0 for v in result.values()):
                        return result
            
            # Прямой поиск счетчиков
            if any(k in data for k in ['playCount', 'viewCount', 'diggCount', 'likeCount']):
                result = {
                    'views': data.get('playCount', 0) or data.get('viewCount', 0),
                    'likes': data.get('diggCount', 0) or data.get('likeCount', 0),
                    'shares': data.get('shareCount', 0),
                    'favorites': data.get('collectCount', 0)
                }
                if any(v > 0 for v in result.values()):
                    return result
            
            # Рекурсивный поиск в известных ключах
            search_keys = ['itemInfo', 'itemStruct', 'videoInfo', 'video', 'item', 'ItemModule', 'Detail']
            for key in search_keys:
                if key in data:
                    result = self._find_stats_in_json(data[key], depth + 1, max_depth)
                    if result and any(v > 0 for v in result.values()):
                        return result
            
            # Поиск в остальных ключах (ограниченно)
            for key, value in list(data.items())[:20]:
                if key not in search_keys and isinstance(value, (dict, list)):
                    result = self._find_stats_in_json(value, depth + 1, max_depth)
                    if result and any(v > 0 for v in result.values()):
                        return result
        
        elif isinstance(data, list):
            for item in data[:10]:  # Ограничиваем количество элементов
                result = self._find_stats_in_json(item, depth + 1, max_depth)
                if result and any(v > 0 for v in result.values()):
                    return result
        
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
            "❌ Не удалось получить информацию о видео.\n"
            "Проверьте, что видео существует и доступно публично.\n\n"
            "Примечание: TikTok может блокировать автоматические запросы. "
            "Попробуйте позже или используйте другую ссылку."
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
            f"Вы получите уведомление каждые 50,000 просмотров!"
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
    logger.info("Запуск проверки видео...")
    
    all_videos = tracker.get_all_tracked_videos()
    logger.info(f"Найдено {len(all_videos)} видео для проверки")
    
    for user_id, video in all_videos:
        video_id = video['video_id']
        video_url = video['video_url']
        last_views = video['last_views']
        last_notified = video['notified_at_views']
        
        # Получение текущей статистики
        stats = await tiktok_monitor.get_video_stats(video_id, video_url)
        
        if not stats:
            logger.warning(f"Не удалось получить статистику для {video_id}")
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
                
                logger.info(f"Уведомление отправлено пользователю {user_id} для видео {video_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления: {e}")
    
    logger.info("Проверка видео завершена")

async def periodic_check(application: Application):
    """Бесконечный цикл периодических проверок"""
    await asyncio.sleep(60)  # Ждем минуту перед первой проверкой
    
    while True:
        try:
            await check_videos_task(application)
        except Exception as e:
            logger.error(f"Ошибка в периодической проверке: {e}")
        
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
    logger.info("Запуск фоновых задач...")
    app['check_task'] = asyncio.create_task(periodic_check(application))

async def cleanup_background_tasks(app):
    """Очистка фоновых задач"""
    logger.info("Остановка фоновых задач...")
    if 'check_task' in app:
        app['check_task'].cancel()
        try:
            await app['check_task']
        except asyncio.CancelledError:
            pass

def main():
    """Главная функция запуска бота"""
    global application
    
    logger.info("=" * 50)
    logger.info("Запуск TikTok Monitor Bot")
    logger.info("=" * 50)
    logger.info(f"Порт: {PORT}")
    logger.info(f"Webhook URL: {WEBHOOK_URL}")
    logger.info(f"Интервал проверки: {CHECK_INTERVAL_MINUTES} минут")
    
    # Создание приложения Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("set", set_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Инициализация бота
    logger.info("Инициализация бота...")
    asyncio.get_event_loop().run_until_complete(application.initialize())
    asyncio.get_event_loop().run_until_complete(setup_webhook(application))
    
    # Создание веб-сервера
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)  # Дополнительный эндпоинт для корня
    app.router.add_post('/webhook', webhook_handler)
    
    # Запуск фоновых задач
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    logger.info(f"🚀 Запуск веб-сервера на 0.0.0.0:{PORT}")
    
    # Запуск веб-сервера
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    main()
