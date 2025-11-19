import os
import logging
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler
)
from TikTokApi import TikTokApi
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
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
        
        if len(self.data[user_key]) < initial_length:
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
    """Класс для работы с TikTok API"""
    
    def __init__(self):
        self.api = None
    
    async def initialize(self):
        """Инициализация TikTok API"""
        try:
            self.api = TikTokApi()
            await self.api.create_sessions(num_sessions=1, sleep_after=3)
            logger.info("TikTok API инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации TikTok API: {e}")
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечение ID видео из URL"""
        patterns = [
            r'(?:vm\.tiktok\.com|vt\.tiktok\.com)/([A-Za-z0-9]+)',
            r'tiktok\.com/@[\w.-]+/video/(\d+)',
            r'tiktok\.com/.*?/video/(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def get_video_stats(self, video_id: str) -> Optional[Dict]:
        """Получение статистики видео"""
        try:
            if not self.api:
                await self.initialize()
            
            video = self.api.video(id=video_id)
            video_data = await video.info()
            
            stats = video_data.get('stats', {})
            
            return {
                'views': stats.get('playCount', 0),
                'likes': stats.get('diggCount', 0),
                'shares': stats.get('shareCount', 0),
                'favorites': stats.get('collectCount', 0),
                'comments': stats.get('commentCount', 0)
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики видео {video_id}: {e}")
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
        "/set `<ссылка>` - Добавить видео для отслеживания\n"
        "/stats - Показать статистику всех отслеживаемых видео\n"
        "/remove - Удалить видео из отслеживания\n\n"
        "📊 *Что я отслеживаю:*\n"
        "• Просмотры (уведомления каждые 50K)\n"
        "• Лайки\n"
        "• Репосты\n"
        "• Добавления в избранное\n\n"
        "Начните с команды /set и укажите ссылку на ваше TikTok видео!"
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /set"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Пожалуйста, укажите ссылку на TikTok видео\n\n"
            "Пример: `/set https://www.tiktok.com/@username/video/1234567890`",
            parse_mode='Markdown'
        )
        return
    
    video_url = context.args[0]
    video_id = tiktok_monitor.extract_video_id(video_url)
    
    if not video_id:
        await update.message.reply_text(
            "❌ Не удалось распознать ссылку на TikTok видео.\n\n"
            "Убедитесь, что ссылка имеет формат:\n"
            "• `https://www.tiktok.com/@username/video/1234567890`\n"
            "• `https://vm.tiktok.com/ZMabcdefg/`",
            parse_mode='Markdown'
        )
        return
    
    # Отправка сообщения о загрузке
    loading_msg = await update.message.reply_text("⏳ Получаю информацию о видео...")
    
    # Получение начальной статистики
    stats = await tiktok_monitor.get_video_stats(video_id)
    
    if not stats:
        await loading_msg.edit_text(
            "❌ Не удалось получить информацию о видео.\n"
            "Проверьте, что видео существует и доступно публично."
        )
        return
    
    # Добавление видео
    if tracker.add_video(user_id, video_url, video_id):
        tracker.update_video_stats(user_id, video_id, stats)
        
        message = (
            "✅ *Видео добавлено для отслеживания!*\n\n"
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
        
        # Получение актуальной статистики
        stats = await tiktok_monitor.get_video_stats(video_id)
        
        if stats:
            tracker.update_video_stats(user_id, video_id, stats)
            
            # Расчет прогресса до следующего уведомления
            current_views = stats['views']
            next_milestone = ((current_views // 50000) + 1) * 50000
            progress = current_views % 50000
            progress_percent = (progress / 50000) * 100
            
            message_parts.append(
                f"\n*{idx}. Видео* `{video_id}`\n"
                f"👁 Просмотры: *{stats['views']:,}*\n"
                f"❤️ Лайки: *{stats['likes']:,}*\n"
                f"🔄 Репосты: *{stats['shares']:,}*\n"
                f"⭐ Избранное: *{stats['favorites']:,}*\n"
                f"📈 До следующей вехи: *{next_milestone - current_views:,}* ({progress_percent:.1f}%)\n"
            )
        else:
            message_parts.append(
                f"\n*{idx}. Видео* `{video_id}`\n"
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

async def check_videos_task(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка всех отслеживаемых видео"""
    logger.info("Запуск проверки видео...")
    
    all_videos = tracker.get_all_tracked_videos()
    
    for user_id, video in all_videos:
        video_id = video['video_id']
        last_views = video['last_views']
        last_notified = video['notified_at_views']
        
        # Получение текущей статистики
        stats = await tiktok_monitor.get_video_stats(video_id)
        
        if not stats:
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
                    f"Видео `{video_id}` достигло *{current_milestone:,}* просмотров!\n\n"
                    f"📊 *Текущая статистика:*\n"
                    f"👁 Просмотры: *{stats['views']:,}*\n"
                    f"❤️ Лайки: *{stats['likes']:,}*\n"
                    f"🔄 Репосты: *{stats['shares']:,}*\n"
                    f"⭐ Избранное: *{stats['favorites']:,}*\n\n"
                    f"🔗 [Открыть видео]({video['video_url']})"
                )
                
                await context.bot.send_message(
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

async def post_init(application: Application):
    """Инициализация после запуска бота"""
    # Инициализация TikTok API
    await tiktok_monitor.initialize()
    
    # Настройка планировщика
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_videos_task,
        'interval',
        minutes=CHECK_INTERVAL_MINUTES,
        args=[application]
    )
    scheduler.start()
    
    logger.info(f"Планировщик запущен (интервал: {CHECK_INTERVAL_MINUTES} минут)")

def main():
    """Главная функция запуска бота"""
    # Создание приложения
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("set", set_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Запуск бота
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()