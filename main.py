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
from telegram.request import HTTPXRequest

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://your-app.onrender.com')
port_str = os.getenv('PORT') or '10000'
PORT = int(port_str)
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
    """Класс для работы с TikTok через TikTokApi библиотеку"""
    
    def __init__(self):
        self.api_class = None
        self.api_available = False
        self.ms_token = os.getenv('TIKTOK_MS_TOKEN', None)  # ms_token из cookies браузера
        try:
            from TikTokApi import TikTokApi
            self.api_class = TikTokApi
            self.api_available = True
            if not self.ms_token:
                logger.warning("TIKTOK_MS_TOKEN не установлен. TikTokApi может работать ограниченно.")
            logger.info("TikTokApi инициализирован успешно")
        except Exception as e:
            logger.warning(f"TikTokApi недоступен: {e}. Используется простой метод получения данных.")
            self.api_available = False
        
        # Headers для fallback методов
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечение ID видео из URL"""
        # Обработка коротких ссылок - сначала получаем редирект
        if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
            try:
                # Получаем редирект на полную страницу
                response = requests.head(url, headers=self.headers, allow_redirects=True, timeout=10)
                url = response.url
            except Exception as e:
                logger.warning(f"Не удалось получить редирект для {url}: {e}")
                # Пытаемся извлечь ID из короткой ссылки
                match = re.search(r'(?:vm\.tiktok\.com|vt\.tiktok\.com)/([A-Za-z0-9]+)', url)
                if match:
                    return match.group(1)
        
        # Паттерны для полных ссылок
        patterns = [
            r'tiktok\.com/@[\w.-]+/video/(\d+)',
            r'tiktok\.com/.*?/video/(\d+)',
            r'(?:vm\.tiktok\.com|vt\.tiktok\.com)/([A-Za-z0-9]+)',  # На случай, если редирект не сработал
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def get_video_stats(self, video_id: str, video_url: str) -> Optional[Dict]:
        """Получение статистики видео через TikTokApi"""
        if not self.api_available or not self.api_class:
            logger.warning(f"TikTokApi недоступен для {video_id}")
            return None
        
        try:
            # TikTokApi 6.3.0 требует async context manager
            async with self.api_class() as api:
                # Создаем сессии для работы с API
                ms_tokens = [self.ms_token] if self.ms_token else []
                await api.create_sessions(
                    ms_tokens=ms_tokens,
                    num_sessions=1,
                    sleep_after=2,
                    browser=os.getenv("TIKTOK_BROWSER", "chromium")
                )
                
                # Получаем видео по URL
                video = api.video(url=video_url)
                
                # Получаем информацию о видео
                video_data = await video.info()
                
                # Извлекаем статистику из ответа
                stats = None
                
                # Проверяем различные форматы ответа
                if isinstance(video_data, dict):
                    # Прямой доступ к stats
                    if 'stats' in video_data:
                        stats = video_data['stats']
                    # Через itemInfo.itemStruct.stats (наиболее вероятный формат)
                    elif 'itemInfo' in video_data:
                        item_info = video_data['itemInfo']
                        if isinstance(item_info, dict) and 'itemStruct' in item_info:
                            item_struct = item_info['itemStruct']
                            if isinstance(item_struct, dict) and 'stats' in item_struct:
                                stats = item_struct['stats']
                    # Через videoInfo.stats
                    elif 'videoInfo' in video_data:
                        video_info = video_data['videoInfo']
                        if isinstance(video_info, dict) and 'stats' in video_info:
                            stats = video_info['stats']
                
                # Если stats не найдено, пытаемся найти в самом объекте video_data
                if not stats and isinstance(video_data, dict):
                    # Прямые ключи статистики в корне
                    if any(key in video_data for key in ['playCount', 'viewCount', 'diggCount', 'likeCount']):
                        stats = video_data
                
                # Также пробуем получить через метод stats() объекта video
                if not stats:
                    try:
                        stats_data = await video.stats()
                        if isinstance(stats_data, dict):
                            stats = stats_data
                    except Exception as e:
                        logger.debug(f"Метод stats() не доступен: {e}")
                
                # Извлекаем значения статистики
                if stats and isinstance(stats, dict):
                    result = {
                        'views': stats.get('playCount') or stats.get('viewCount') or 0,
                        'likes': stats.get('diggCount') or stats.get('likeCount') or 0,
                        'shares': stats.get('shareCount') or 0,
                        'favorites': stats.get('collectCount') or 0,
                    }
                    
                    # Проверяем, что получили хотя бы одну статистику
                    if any(v > 0 for v in result.values()):
                        logger.info(f"Получена статистика для {video_id}: views={result['views']}, likes={result['likes']}")
                        return result
                    else:
                        logger.warning(f"Статистика найдена, но все значения нулевые для {video_id}")
                else:
                    logger.warning(f"Не удалось извлечь статистику из ответа TikTokApi для {video_id}. Формат ответа: {type(video_data)}")
                    
        except Exception as e:
            logger.error(f"Ошибка получения данных через TikTokApi для {video_id}: {e}", exc_info=True)
        
        return None
    
    # Удаляем старые методы парсинга - используем только TikTokApi
        """Парсинг страницы видео для получения статистики"""
        try:
            # Если это короткая ссылка, получаем редирект
            if 'vm.tiktok.com' in video_url or 'vt.tiktok.com' in video_url:
                response = requests.head(video_url, headers=self.headers, allow_redirects=True, timeout=10)
                video_url = response.url
                logger.info(f"Получен редирект на: {video_url}")
            
            response = requests.get(video_url, headers=self.headers, timeout=15, allow_redirects=True)
            
            if response.status_code != 200:
                logger.error(f"Не удалось загрузить страницу: {response.status_code}")
                return None
            
            # Ищем JSON данные в HTML
            html = response.text
            
            # Попытка найти JSON данные в script тегах
            stats = None
            
            # Ищем JSON в разных script тегах
            script_patterns = [
                r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                r'<script[^>]*>window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.*?});</script>',
                r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.*?});',
                r'<script[^>]*>.*?"stats".*?({.*?})</script>',
            ]
            
            for pattern in script_patterns:
                script_matches = re.finditer(pattern, html, re.DOTALL | re.IGNORECASE)
                for match in script_matches:
                    try:
                        json_str = match.group(1) if match.groups() else match.group(0)
                        # Пробуем распарсить как JSON
                        try:
                            json_data = json.loads(json_str)
                            stats = self._extract_stats_from_json(json_data)
                            if stats and any(v > 0 for v in stats.values()):
                                logger.info(f"Найдена статистика в JSON: {stats}")
                                break
                        except json.JSONDecodeError:
                            # Пробуем найти JSON объект внутри строки
                            json_obj_match = re.search(r'\{[^{}]*"stats"[^{}]*\}', json_str)
                            if json_obj_match:
                                try:
                                    json_data = json.loads(json_obj_match.group(0))
                                    stats = self._extract_stats_from_json(json_data)
                                    if stats and any(v > 0 for v in stats.values()):
                                        logger.info(f"Найдена статистика в частичном JSON: {stats}")
                                        break
                                except json.JSONDecodeError:
                                    continue
                    except Exception as e:
                        logger.debug(f"Ошибка парсинга script: {e}")
                        continue
                
                if stats and any(v > 0 for v in stats.values()):
                    break
            
            # Если не нашли в JSON, используем регулярные выражения
            if not stats or all(v == 0 for v in stats.values()):
                views_patterns = [
                    r'"playCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"viewCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"playCount":(\d+(?:[,\s]\d+)*)',
                    r'playCount&quot;:(\d+(?:[,\s]\d+)*)',
                    r'"stats"[^}]*"playCount":(\d+(?:[,\s]\d+)*)',
                    r'playCount["\']?\s*:\s*(\d+(?:[,\s]\d+)*)',
                    r'(\d+(?:[,\s]\d+)*)\s*(?:views|просмотров)',
                ]
                
                likes_patterns = [
                    r'"diggCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"likeCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"diggCount":(\d+(?:[,\s]\d+)*)',
                    r'diggCount&quot;:(\d+(?:[,\s]\d+)*)',
                    r'"stats"[^}]*"diggCount":(\d+(?:[,\s]\d+)*)',
                    r'diggCount["\']?\s*:\s*(\d+(?:[,\s]\d+)*)',
                    r'(\d+(?:[,\s]\d+)*)\s*(?:likes|лайков)',
                ]
                
                shares_patterns = [
                    r'"shareCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"shareCount":(\d+(?:[,\s]\d+)*)',
                    r'shareCount&quot;:(\d+(?:[,\s]\d+)*)',
                    r'"stats"[^}]*"shareCount":(\d+(?:[,\s]\d+)*)',
                    r'shareCount["\']?\s*:\s*(\d+(?:[,\s]\d+)*)',
                    r'(\d+(?:[,\s]\d+)*)\s*(?:shares|репостов)',
                ]
                
                favorites_patterns = [
                    r'"collectCount["\']?\s*:\s*["\']?(\d+(?:[,\s]\d+)*)',
                    r'"collectCount":(\d+(?:[,\s]\d+)*)',
                    r'collectCount&quot;:(\d+(?:[,\s]\d+)*)',
                    r'"stats"[^}]*"collectCount":(\d+(?:[,\s]\d+)*)',
                    r'collectCount["\']?\s*:\s*(\d+(?:[,\s]\d+)*)',
                    r'(\d+(?:[,\s]\d+)*)\s*(?:favorites|избранное)',
                ]
                
                def extract_stat(patterns):
                    for pattern in patterns:
                        match = re.search(pattern, html, re.IGNORECASE)
                        if match:
                            try:
                                return int(match.group(1).replace(',', '').replace('.', ''))
                            except ValueError:
                                continue
                    return 0
                
                stats = {
                    'views': extract_stat(views_patterns),
                    'likes': extract_stat(likes_patterns),
                    'shares': extract_stat(shares_patterns),
                    'favorites': extract_stat(favorites_patterns),
                }
            
            # Проверяем, что хотя бы одна статистика найдена
            if not stats or all(v == 0 for v in stats.values()):
                logger.warning(f"Не удалось извлечь статистику из HTML для {video_url}")
                logger.debug(f"Размер HTML: {len(html)} символов")
                # Сохраняем часть HTML для отладки (первые 5000 символов)
                logger.debug(f"Начало HTML: {html[:5000]}")
                return None  # Не возвращаем тестовые данные
            
            return stats
            
        except Exception as e:
            logger.error(f"Ошибка парсинга страницы: {e}")
            return None
    
    def _extract_stats_from_json(self, data) -> Optional[Dict]:
        """Рекурсивный поиск статистики в JSON структуре"""
        if data is None:
            return None
            
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        
        if not isinstance(data, dict):
            if isinstance(data, list):
                for item in data:
                    result = self._extract_stats_from_json(item)
                    if result and any(v > 0 for v in result.values()):
                        return result
            return None
        
        # Ищем ключи со статистикой
        stats = {}
        
        # Пробуем найти статистику в различных форматах
        if 'stats' in data:
            stats_data = data['stats']
            if isinstance(stats_data, dict):
                stats['views'] = stats_data.get('playCount') or stats_data.get('viewCount') or 0
                stats['likes'] = stats_data.get('diggCount') or stats_data.get('likeCount') or 0
                stats['shares'] = stats_data.get('shareCount') or 0
                stats['favorites'] = stats_data.get('collectCount') or 0
            elif isinstance(stats_data, list) and len(stats_data) > 0:
                # Если stats это список, берем первый элемент
                result = self._extract_stats_from_json(stats_data[0])
                if result:
                    stats.update(result)
        
        # Прямые ключи в корневом объекте
        if 'playCount' in data or 'viewCount' in data:
            stats['views'] = data.get('playCount') or data.get('viewCount') or 0
        if 'diggCount' in data or 'likeCount' in data:
            stats['likes'] = data.get('diggCount') or data.get('likeCount') or 0
        if 'shareCount' in data:
            stats['shares'] = data.get('shareCount') or 0
        if 'collectCount' in data:
            stats['favorites'] = data.get('collectCount') or 0
        
        # Ищем в объекте videoInfo или itemInfo
        for key in ['videoInfo', 'itemInfo', 'itemStruct', 'video', 'item']:
            if key in data and isinstance(data[key], dict):
                result = self._extract_stats_from_json(data[key])
                if result and any(v > 0 for v in result.values()):
                    return result
        
        # Если нашли хотя бы одну статистику, возвращаем
        if stats and any(v > 0 for v in stats.values()):
            return stats
        
        # Рекурсивно ищем в дочерних объектах (ограничиваем глубину)
        for key, value in list(data.items())[:20]:  # Ограничиваем количество ключей для производительности
            if isinstance(value, (dict, list)) and key not in ['stats', 'videoInfo', 'itemInfo']:
                result = self._extract_stats_from_json(value)
                if result and any(v > 0 for v in result.values()):
                    return result
        
        return stats if stats else None

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
    stats = await tiktok_monitor.get_video_stats(video_id, video_url)
    
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

async def check_videos_task(application: Application):
    """Периодическая проверка всех отслеживаемых видео"""
    logger.info("Запуск проверки видео...")
    
    all_videos = tracker.get_all_tracked_videos()
    
    for user_id, video in all_videos:
        video_id = video['video_id']
        video_url = video['video_url']
        last_views = video['last_views']
        last_notified = video['notified_at_views']
        
        # Получение текущей статистики
        stats = await tiktok_monitor.get_video_stats(video_id, video_url)
        
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
    webhook_url = f"{WEBHOOK_URL}/webhook"
    await app_instance.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook установлен: {webhook_url}")

# Глобальная переменная для приложения
application = None

async def start_background_tasks(app):
    """Запуск фоновых задач"""
    app['check_task'] = asyncio.create_task(periodic_check(application))

async def cleanup_background_tasks(app):
    """Очистка фоновых задач"""
    app['check_task'].cancel()
    await app['check_task']

def main():
    """Главная функция запуска бота"""
    global application
    
    # Создание приложения Telegram с увеличенными таймаутами
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30,
    )
    
    application = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("set", set_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Инициализация бота
    asyncio.get_event_loop().run_until_complete(application.initialize())
    asyncio.get_event_loop().run_until_complete(setup_webhook(application))
    
    # Создание веб-сервера
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_post('/webhook', webhook_handler)
    
    # Запуск фоновых задач
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    logger.info(f"Сервер запускается на порту {PORT}...")
    logger.info(f"Проверка видео каждые {CHECK_INTERVAL_MINUTES} минут")
    
    # Запуск веб-сервера
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    main()
