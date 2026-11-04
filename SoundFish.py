import os
import sys
import ctypes


# ---------------------------------------------------------------------------
# 1. ПРАВА АДМИНИСТРАТОРА — проверяем самым первым, ДО тяжёлых импортов.
#    Раньше программа сначала грузила все библиотеки, а потом перезапускалась.
# ---------------------------------------------------------------------------
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


if not is_admin():
    try:
        extra = " ".join(f'"{arg}"' for arg in sys.argv[1:])
        if getattr(sys, "frozen", False):
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, extra, None, 1)
        else:
            script_path = os.path.abspath(sys.argv[0])
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{script_path}" {extra}',
                os.path.dirname(script_path), 1
            )
    except Exception as e:
        print(f"Ошибка запроса прав: {e}")
    sys.exit()

# ---------------------------------------------------------------------------
# 2. Защита от второго экземпляра (без создания лишнего окна Tk)
# ---------------------------------------------------------------------------
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "SoundFish_Studio_SingleInstance_Mutex")
if ctypes.windll.kernel32.GetLastError() == 183:
    ctypes.windll.user32.MessageBoxW(0, "Приложение SoundFish Studio уже запущено!", "Внимание", 0x30)
    sys.exit(0)

# Иконка в панели задач Windows
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("soundfish.studio.mediaplayer.v2")
except Exception:
    pass

import threading

# ---------------------------------------------------------------------------
# 3. ТЯЖЁЛЫЕ БИБЛИОТЕКИ ГРУЗИМ В ФОНЕ — окно появляется сразу, не дожидаясь их.
# ---------------------------------------------------------------------------
pygame = None
keyboard = None
MP3 = None
WAVE = None
ID3 = None
_audio_ready = threading.Event()


def _load_audio_backend():
    global pygame, keyboard, MP3, WAVE, ID3
    try:
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        import pygame as _pygame
        _pygame.mixer.init()
        pygame = _pygame
    except Exception as e:
        print(f"Аудио недоступно: {e}")
    try:
        import keyboard as _keyboard
        keyboard = _keyboard
    except Exception as e:
        print(f"Горячие клавиши недоступны: {e}")
    try:
        from mutagen.mp3 import MP3 as _MP3
        from mutagen.wave import WAVE as _WAVE
        from mutagen.id3 import ID3 as _ID3
        MP3, WAVE, ID3 = _MP3, _WAVE, _ID3
    except Exception as e:
        print(f"Чтение тегов недоступно: {e}")
    finally:
        _audio_ready.set()


threading.Thread(target=_load_audio_backend, daemon=True, name="audio-loader").start()


class _MusicProxy:
    """pygame.mixer.music, который сам дожидается фоновой загрузки и не падает, если звука нет."""

    def __getattr__(self, name):
        _audio_ready.wait(15)
        backend = None
        if pygame is not None:
            backend = getattr(getattr(pygame, "mixer", None), "music", None)
        if backend is None:
            return lambda *args, **kwargs: 0
        return getattr(backend, name)


music = _MusicProxy()


def audio_ok():
    return _audio_ready.is_set() and pygame is not None


# ---------------------------------------------------------------------------
# 4. Лёгкие импорты
# ---------------------------------------------------------------------------
import io
import json
import math
import queue
import random
import shutil
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

ctk.set_appearance_mode("dark")

LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


# ---------------------------------------------------------------------------
# 5. Иконка. ГЛАВНОЕ: картинка наушников берётся из твоего icon.ico и
#    НЕ перезаписывается. Рисуем такую же только если файла нет совсем.
# ---------------------------------------------------------------------------
def _resource_dir():
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def _find_icon():
    for folder in (_resource_dir(), os.getcwd()):
        path = os.path.join(folder, "icon.ico")
        if os.path.exists(path):
            return path
    return None


def _draw_default_icon():
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size, size], radius=30, fill=(234, 179, 8, 255))
    white = (255, 255, 255, 255)
    draw.arc([30, 45, 175, 140], start=160, end=350, fill=white, width=22)
    draw.ellipse([45, 95, 170, 190], fill=white)
    draw.ellipse([180, 55, 215, 160], fill=white)
    draw.ellipse([210, 55, 235, 160], fill=white)
    draw.rectangle([165, 75, 185, 95], fill=white)
    return img


def load_app_icon():
    path = _find_icon()
    if path:
        try:
            with Image.open(path) as im:
                sizes = sorted(im.info.get("sizes") or {im.size})
                im.size = sizes[-1]          # самый крупный кадр
                im.load()
                return im.convert("RGBA")
        except Exception as e:
            print(f"Не удалось прочитать icon.ico: {e}")
    img = _draw_default_icon()
    try:
        img.save(os.path.join(_resource_dir(), "icon.ico"), format="ICO",
                 sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        pass
    return img


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def center_square(img):
    w, h = img.size
    m = min(w, h)
    left, top = (w - m) // 2, (h - m) // 2
    return img.crop((left, top, left + m, top + m))


def round_image(img, size, radius):
    """Квадратная обложка со скруглёнными углами (с суперсэмплингом для гладких краёв)."""
    img = center_square(img).resize((size, size), LANCZOS).convert("RGBA")
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1], radius=radius * 4, fill=255)
    img.putalpha(mask.resize((size, size), LANCZOS))
    return img


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix_rgb(c1, c2, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a + (b - a) * k) for a, b in zip(c1, c2))


def _mix(c1, c2, k):
    return "#%02x%02x%02x" % _mix_rgb(c1, c2, k)


MODIFIERS = ("ctrl", "alt", "shift", "windows")
KEY_PRETTY = {
    "right": "→", "left": "←", "up": "↑", "down": "↓",
    "space": "Space", "esc": "Esc", "ctrl": "Ctrl", "alt": "Alt",
    "shift": "Shift", "windows": "Win",
}


class SoundFishApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.attributes("-alpha", 0.0)   # окно «проявится» плавно, когда интерфейс готов
        self.title("SoundFish Studio")
        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        win_w, win_h = 980, 720
        pos_x = max(0, (self.winfo_screenwidth() - int(win_w * scale)) // 2)
        pos_y = max(0, (self.winfo_screenheight() - int(win_h * scale)) // 2 - 20)
        self.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.minsize(850, 620)

        # Системные директории C:\Program Files\SoundFish
        self.base_dir = r"C:\Program Files\SoundFish"
        self.music_dir = os.path.join(self.base_dir, "music")
        self.album_dir = os.path.join(self.base_dir, "album")
        self.settings_dir = os.path.join(self.base_dir, "settings")
        
        self.albums_config_file = os.path.join(self.settings_dir, "albums.json")
        self.app_settings_file = os.path.join(self.settings_dir, "settings.json")

        self.init_storage_folders()

        # Загрузка пользовательских настроек (если есть)
        self.app_settings = self.load_app_settings()
        self.volume = int(self.app_settings.get("volume", 70))

        # Кэш длительностей треков (чтобы список не читал теги при каждом запуске)
        self.duration_cache_file = os.path.join(self.settings_dir, "durations.json")
        self.duration_cache = self._load_duration_cache()
        self._album_img_cache = {}

        # Локализация
        self.current_lang = self.app_settings.get("language", "Русский")
        self.translations = {
            "Русский": {
                "media_library": "Ваша медиатека",
                "albums_title": "Альбомы",
                "create_album": "+ Создать альбом",
                "add_files": "+ Добавить файлы",
                "select_track": "Выберите трек",
                "settings": "Настройки",
                "audio_volume": "Громкость аудио",
                "color_style": "Цветовая стилизация",
                "language": "Язык интерфейса",
                "global_bind": "Глобальный бинд (Play / Pause)",
                "hotkeys_title": "Горячие клавиши (работают в любом окне)",
                "hk_hint": "Нажми на кнопку, затем нужную комбинацию. Esc — отмена.",
                "hk_play_pause": "⏯  Играть / Пауза",
                "hk_next": "⏭  Следующий трек",
                "hk_prev": "⏮  Предыдущий трек",
                "hk_reset": "Сбросить",
                "hk_conflict": "Эта комбинация уже занята:\n{}",
                "hk_need_key": "Добавь к Ctrl / Alt / Shift обычную клавишу, например Ctrl + Alt + →.",
                "toast_toggle": "Показывать уведомление о треке",
                "toast_next": "Следующий трек",
                "toast_prev": "Предыдущий трек",
                "toast_play": "Играет",
                "toast_pause": "Пауза",
                "search_ph": "Поиск песни",
                "dlg_ok": "Понятно",
                "dlg_del_track_title": "Удалить трек?",
                "dlg_del_track_msg": "Файл будет удалён из медиатеки и с диска. Это действие нельзя отменить.",
                "dlg_del_album_title": "Удалить альбом?",
                "dlg_del_album_msg": "Сами треки останутся в медиатеке.",
                "dlg_album_empty_title": "Альбом пуст",
                "dlg_album_empty_msg": "В этом альбоме пока нет треков. Добавь их через «Редактировать».",
                "dlg_hotkey_title": "Эта клавиша не подходит",
                "alb_new_title": "Новый альбом",
                "alb_edit_title": "Редактировать альбом",
                "alb_cover": "Обложка",
                "alb_cover_none": "Фото не выбрано",
                "alb_cover_set": "Обложка выбрана",
                "alb_pick": "🖼  Выбрать фото",
                "alb_change": "🖼  Сменить фото",
                "alb_remove": "Убрать",
                "alb_name": "Название альбома",
                "alb_name_ph": "Мой альбом",
                "alb_tracks": "Песни в альбоме",
                "alb_selected": "Выбрано: {} из {}",
                "alb_all": "Все",
                "alb_none": "Сбросить",
                "alb_no_tracks": "В медиатеке пока нет песен",
                "alb_create_btn": "Создать",
                "alb_save_btn": "Сохранить",
                "alb_default_name": "Новый альбом",
                "current_key": "Текущая клавиша: {}",
                "assigned_key": "Назначено: {}",
                "press_keys": "Нажмите комбинацию клавиш...",
                "about_app": "О приложении",
                "about_text": "🎵 SoundFish Studio v2.3\n\n• Быстрый запуск, поиск по трекам (Ctrl+F), длительности и обложки из MP3.\n• Глобальные горячие клавиши: пауза, следующий и предыдущий трек.\n• Премиальный локальный медиаплеер нового поколения.\n• Улучшенный интеллектуальный случайный порядок воспроизведения (без повторов).\n• Полный офлайн-режим и автосохранение настроек в папку программы.\n\n📁 Системные хранилища:\nМузыка: {}\nАльбомы: {}\nНастройки: {}",
                "delete_title": "Подтверждение",
                "delete_confirm": "Удалить этот трек из медиатеки?\n\n\"{}\"",
                "delete_album_confirm": "Удалить альбом?\n\n\"{}\"",
                "delete_btn": "Удалить",
                "cancel_btn": "Отмена",
                "open_app": "Открыть SoundFish",
                "exit_app": "Выход"
            },
            "English": {
                "media_library": "Your Media Library",
                "albums_title": "Albums",
                "create_album": "+ Create Album",
                "add_files": "+ Add Files",
                "select_track": "Select a track",
                "settings": "Settings",
                "audio_volume": "Audio Volume",
                "color_style": "Color Theme",
                "language": "Interface Language",
                "global_bind": "Global Keybind (Play / Pause)",
                "hotkeys_title": "Hotkeys (work in any window)",
                "hk_hint": "Click a button, then press the combination. Esc cancels.",
                "hk_play_pause": "⏯  Play / Pause",
                "hk_next": "⏭  Next track",
                "hk_prev": "⏮  Previous track",
                "hk_reset": "Reset",
                "hk_conflict": "This combination is already used by:\n{}",
                "hk_need_key": "Add a regular key to Ctrl / Alt / Shift, e.g. Ctrl + Alt + →.",
                "toast_toggle": "Show track notification",
                "toast_next": "Next track",
                "toast_prev": "Previous track",
                "toast_play": "Now playing",
                "toast_pause": "Paused",
                "search_ph": "Search song",
                "dlg_ok": "Got it",
                "dlg_del_track_title": "Delete track?",
                "dlg_del_track_msg": "The file will be removed from your library and from disk. This can't be undone.",
                "dlg_del_album_title": "Delete album?",
                "dlg_del_album_msg": "The tracks themselves stay in your library.",
                "dlg_album_empty_title": "Album is empty",
                "dlg_album_empty_msg": "There are no tracks in this album yet. Add some via “Edit”.",
                "dlg_hotkey_title": "This key can't be used",
                "alb_new_title": "New album",
                "alb_edit_title": "Edit album",
                "alb_cover": "Cover",
                "alb_cover_none": "No photo selected",
                "alb_cover_set": "Cover selected",
                "alb_pick": "🖼  Choose photo",
                "alb_change": "🖼  Change photo",
                "alb_remove": "Remove",
                "alb_name": "Album name",
                "alb_name_ph": "My album",
                "alb_tracks": "Songs in the album",
                "alb_selected": "Selected: {} of {}",
                "alb_all": "All",
                "alb_none": "Clear",
                "alb_no_tracks": "Your library has no songs yet",
                "alb_create_btn": "Create",
                "alb_save_btn": "Save",
                "alb_default_name": "New album",
                "current_key": "Current Key: {}",
                "assigned_key": "Assigned: {}",
                "press_keys": "Press key combination...",
                "about_app": "About Application",
                "about_text": "🎵 SoundFish Studio v2.3\n\n• Fast startup, track search (Ctrl+F), durations and MP3 cover art.\n• Global hotkeys: play/pause, next and previous track.\n• Next-generation premium local media player.\n• Improved smart shuffle algorithm (no repeating tracks).\n• Full offline mode with auto-saving to program directory.\n\n📁 Storage Paths:\nMusic: {}\nAlbums: {}\nSettings: {}",
                "delete_title": "Confirmation",
                "delete_confirm": "Delete this track from media library?\n\n\"{}\"",
                "delete_album_confirm": "Delete this album?\n\n\"{}\"",
                "delete_btn": "Delete",
                "cancel_btn": "Cancel",
                "open_app": "Open SoundFish",
                "exit_app": "Exit"
            }
        }

        # Иконка берётся из icon.ico (файл не перезаписывается)
        self.icon_img = load_app_icon()
        try:
            icon_file = _find_icon()
            if icon_file:
                self.iconbitmap(icon_file)
        except Exception:
            pass
        self.logo_small = ctk.CTkImage(light_image=self.icon_img, dark_image=self.icon_img, size=(30, 30))
        self.logo_cover = ctk.CTkImage(light_image=self.icon_img, dark_image=self.icon_img, size=(48, 48))
        self.logo_toast = ctk.CTkImage(light_image=self.icon_img, dark_image=self.icon_img, size=(52, 52))
        self.cover_ctk = self.logo_cover
        self.current_cover_pil = None

        self.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

        self.playlist = []
        self.song_widgets = []
        
        self.albums = []  
        self.current_playing_album_idx = None  

        self.current_index = -1
        self.is_playing = False
        self.is_shuffled = False
        self.is_looped = False
        self.song_length = 0
        
        # Переменные для умного шаффла (Smart Shuffle)
        self.shuffled_queue = []
        self.shuffle_history = []

        # Глобальные горячие клавиши: действие -> комбинация
        self.default_hotkeys = {
            "play_pause": "space",
            "next": "ctrl+alt+right",
            "prev": "ctrl+alt+left",
        }
        self.hotkeys = dict(self.default_hotkeys)
        saved_hotkeys = self.app_settings.get("hotkeys")
        if isinstance(saved_hotkeys, dict):
            for action in self.default_hotkeys:
                value = saved_hotkeys.get(action)
                if isinstance(value, str) and value.strip():
                    self.hotkeys[action] = value.strip().lower()
        elif isinstance(self.app_settings.get("hotkey"), str):
            # миграция со старой версии настроек (был один бинд)
            self.hotkeys["play_pause"] = self.app_settings["hotkey"].lower()

        self.hotkey_handlers = {}
        self.hotkey_queue = queue.Queue()   # сюда клавиатурный поток кладёт события
        self._last_hotkey_time = {}
        self.is_listening = False
        self.bind_buttons = {}
        self.bind_labels = {}
        self.settings_cards = []

        # Уведомление-тост
        self.toast_enabled = bool(self.app_settings.get("toast", True))
        self.toast_win = None
        self.toast_id = 0
        self.toast_pos = (0, 0)

        # Эквалайзер
        self.viz_count = 44
        self.viz_levels = [0.0] * self.viz_count
        self.viz_items = []
        self._viz_heights = []
        self._viz_colors = []
        self._viz_palette = []
        self._viz_idle_color = "#000000"

        # Список треков / воспроизведение / служебное
        self._song_render_id = 0
        self._rendering = False
        self.song_titles = []
        self.song_dur_labels = []
        self._row_delete_factory = lambda p: (lambda: None)
        self._filter_text = ""
        self._filter_job = None
        self._seeking = False
        self.play_offset = 0.0
        self._last_sec = -1
        self._settings_job = None
        self._audio_applied = False
        self._modal = None
        self._modal_id = 0

        self.themes = {
            "Gold / Dark Luxury": {"bg": "#0F0F0F", "panel": "#171717", "card": "#242424", "card_hover": "#303030", "accent": "#EAB308", "accent_hover": "#FACC15", "text": "#FFFFFF", "subtext": "#A3A3A3"},
            "Emerald (Spotify Style)": {"bg": "#121212", "panel": "#181818", "card": "#212121", "card_hover": "#2a2a2a", "accent": "#1DB954", "accent_hover": "#1ed760", "text": "#FFFFFF", "subtext": "#B3B3B3"},
            "Cyber Violet": {"bg": "#0D0B14", "panel": "#141120", "card": "#1E1A2E", "card_hover": "#28233D", "accent": "#A855F7", "accent_hover": "#C084FC", "text": "#FFFFFF", "subtext": "#A1A1AA"},
            "Sunset Red": {"bg": "#120B0B", "panel": "#1A1010", "card": "#261717", "card_hover": "#362020", "accent": "#F43F5E", "accent_hover": "#FB7185", "text": "#FFFFFF", "subtext": "#FDA4AF"}
        }
        
        theme_name = self.app_settings.get("theme", "Gold / Dark Luxury")
        self.current_theme = self.themes.get(theme_name, self.themes["Gold / Dark Luxury"])

        self.sidebar_expanded = False
        self.sidebar_animating = False
        self.sidebar_pos = -80
        self.active_frame = None
        self.active_frame_name = None
        self.tray_icon = None

        self.load_albums_data()          # только чтение JSON, без рисования
        self.setup_ui()                  # строим только видимую вкладку «Медиатека»
        self.apply_theme()
        self.process_hotkey_queue()
        self.update_progress()
        self.animate_visualizer()
        self.load_all_songs()
        self.update_idletasks()
        self._fade_in()                  # окно проявляется уже готовым
        self.after(120, self._show_search_placeholder)
        self.after(30, self._build_deferred_frames)   # остальные вкладки — в фоне, пока окно открыто

    def tr(self, key):
        return self.translations.get(self.current_lang, {}).get(key, key)

    def init_storage_folders(self):
        os.makedirs(self.music_dir, exist_ok=True)
        os.makedirs(self.album_dir, exist_ok=True)
        os.makedirs(self.settings_dir, exist_ok=True)

    def load_app_settings(self):
        if os.path.exists(self.app_settings_file):
            try:
                with open(self.app_settings_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_app_settings(self):
        try:
            data = {
                "language": self.current_lang,
                "theme": list(self.themes.keys())[list(self.themes.values()).index(self.current_theme)],
                "volume": int(self.volume),
                "hotkeys": self.hotkeys,
                "toast": self.toast_enabled
            }
            with open(self.app_settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Ошибка сохранения настроек: {e}")

    def load_albums_data(self):
        if os.path.exists(self.albums_config_file):
            try:
                with open(self.albums_config_file, "r", encoding="utf-8") as f:
                    self.albums = json.load(f)
            except Exception:
                self.albums = []
        self.render_albums()

    def save_albums_data(self):
        try:
            with open(self.albums_config_file, "w", encoding="utf-8") as f:
                json.dump(self.albums, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Ошибка сохранения альбомов: {e}")

    def setup_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_bar = ctk.CTkFrame(self, height=60, corner_radius=0, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=10)

        self.btn_menu = ctk.CTkButton(
            self.top_bar, text="☰", width=45, height=40,
            fg_color="transparent", font=("Segoe UI", 20, "bold"),
            command=self.toggle_sidebar, hover=False
        )
        self.btn_menu.pack(side="left", padx=(10, 5), pady=10)

        self.lbl_logo = ctk.CTkLabel(self.top_bar, text="", image=self.logo_small, width=34, height=34)
        self.lbl_logo.pack(side="left", padx=(6, 0))

        self.lbl_title = ctk.CTkLabel(self.top_bar, text="SOUND", font=("Segoe UI", 18, "bold"))
        self.lbl_title.pack(side="left", padx=(8, 0))

        self.lbl_title2 = ctk.CTkLabel(self.top_bar, text="FISH", font=("Segoe UI", 18, "bold"))
        self.lbl_title2.pack(side="left", padx=(0, 10))

        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 15))
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=80, corner_radius=16)
        self.sidebar.place(x=-80, y=60, relheight=0.88)

        self.btn_lib = ctk.CTkButton(
            self.sidebar, text="🎵", width=55, height=55, corner_radius=12,
            font=("Segoe UI Emoji", 22), command=lambda: self.show_frame("library")
        )
        self.btn_lib.pack(pady=(15, 5))

        self.btn_all_songs = ctk.CTkButton(
            self.sidebar, text="🎶", width=55, height=55, corner_radius=12,
            font=("Segoe UI Emoji", 22), command=self.load_all_songs
        )
        self.btn_all_songs.pack(pady=5)

        self.btn_albums = ctk.CTkButton(
            self.sidebar, text="💿", width=55, height=55, corner_radius=12,
            font=("Segoe UI Emoji", 22), command=lambda: self.show_frame("albums")
        )
        self.btn_albums.pack(pady=5)

        self.btn_settings = ctk.CTkButton(
            self.sidebar, text="⚙️", width=55, height=55, corner_radius=12,
            font=("Segoe UI Emoji", 22), command=lambda: self.show_frame("settings")
        )
        self.btn_settings.pack(pady=5)

        self.btn_info = ctk.CTkButton(
            self.sidebar, text="ℹ️", width=55, height=55, corner_radius=12,
            font=("Segoe UI Emoji", 22), command=lambda: self.show_frame("info")
        )
        self.btn_info.pack(pady=5)

        self.frames = {}
        self.create_library_frame()

        self.show_frame("library")

    def toggle_sidebar(self):
        if self.sidebar_animating:
            return
        self.sidebar_animating = True
        self.sidebar.lift()
        self._animate_sidebar(-80 if self.sidebar_expanded else 15)

    def _animate_sidebar(self, target):
        diff = target - self.sidebar_pos
        if abs(diff) <= 3:
            self.sidebar_pos = target
            self.sidebar.place(x=target, y=60)
            self.sidebar_expanded = target > 0
            self.sidebar_animating = False
            return
        step = int(diff * 0.38) or (1 if diff > 0 else -1)
        self.sidebar_pos += step
        self.sidebar.place(x=self.sidebar_pos, y=60)
        self.after(12, lambda: self._animate_sidebar(target))

    def show_frame(self, name):
        self._ensure_frame(name)
        target_frame = self.frames[name]
        if self.active_frame == target_frame:
            return

        if self.active_frame:
            self.active_frame.grid_remove()

        target_frame.grid(row=0, column=0, sticky="nsew")
        self.active_frame = target_frame
        self.active_frame_name = name

        self.update_sidebar_colors()

        if self.sidebar_expanded:
            self.toggle_sidebar()

    def _ensure_frame(self, name, recolor=True):
        """Вкладки строятся по требованию: так окно открывается заметно быстрее."""
        if name in self.frames:
            return
        builder = {
            "library": self.create_library_frame,
            "albums": self.create_albums_frame,
            "settings": self.create_settings_frame,
            "info": self.create_info_frame,
        }[name]
        builder()
        if recolor:
            self.apply_theme()

    def _build_deferred_frames(self):
        pending = [n for n in ("albums", "settings", "info") if n not in self.frames]
        if pending:
            self._ensure_frame(pending[0], recolor=False)
            self.after(25, self._build_deferred_frames)
        else:
            self.apply_theme()

    def _fade_in(self, step=0):
        try:
            alpha = min(1.0, (step + 1) / 8)
            self.attributes("-alpha", alpha)
            if alpha < 1.0:
                self.after(14, lambda: self._fade_in(step + 1))
        except Exception:
            pass

    def update_sidebar_colors(self):
        t = self.current_theme
        buttons = [
            ("library", self.btn_lib),
            ("albums", self.btn_albums),
            ("settings", self.btn_settings),
            ("info", self.btn_info)
        ]
        for name, btn in buttons:
            if name == self.active_frame_name:
                btn.configure(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000")
            else:
                btn.configure(fg_color="transparent", hover_color=t["card_hover"], text_color=t["text"])

        self.btn_all_songs.configure(fg_color="transparent", hover_color=t["card_hover"], text_color=t["text"])

    def create_library_frame(self):
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["library"] = frame
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        top_lib = ctk.CTkFrame(frame, fg_color="transparent")
        top_lib.grid(row=0, column=0, sticky="ew", pady=(0, 15))

        self.lbl_lib_title = ctk.CTkLabel(top_lib, text=self.tr("media_library"), font=("Segoe UI", 22, "bold"))
        self.lbl_lib_title.pack(side="left")

        self.count_pill = ctk.CTkLabel(
            top_lib, text="", height=26, corner_radius=13,
            fg_color=self.current_theme["card"], text_color=self.current_theme["accent"],
            font=("Segoe UI", 11, "bold")
        )
        self.count_pill.pack(side="left", padx=14)

        self.btn_add = ctk.CTkButton(
            top_lib, text=self.tr("add_files"), height=38, font=("Segoe UI", 13, "bold"),
            corner_radius=20, command=self.add_song
        )
        self.btn_add.pack(side="right")

        self.search_entry = ctk.CTkEntry(
            top_lib, width=250, height=38, corner_radius=20, border_width=1,
            placeholder_text=self.tr("search_ph"), placeholder_text_color="#FFFFFF",
            text_color="#FFFFFF", font=("Segoe UI", 13)
        )
        self.search_entry.pack(side="right", padx=(0, 10))
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.search_entry.bind("<Escape>", self._clear_search)
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())

        self.song_listbox = ctk.CTkScrollableFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        self.song_listbox.grid(row=1, column=0, sticky="nsew")

        self.build_visualizer(frame)

        self.controls_frame = ctk.CTkFrame(frame, height=120, corner_radius=20, fg_color=self.current_theme["panel"])
        self.controls_frame.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.controls_frame.pack_propagate(False)

        player_layout = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        player_layout.pack(fill="both", expand=True, padx=20, pady=10)

        track_info_box = ctk.CTkFrame(player_layout, fg_color="transparent")
        track_info_box.pack(side="left", fill="y", anchor="w")

        self.lbl_cover_art = ctk.CTkLabel(
            track_info_box, text="", image=self.logo_cover, width=48, height=48, corner_radius=10,
            fg_color=self.current_theme["card"]
        )
        self.lbl_cover_art.pack(side="left", padx=(0, 12))

        labels_sub_box = ctk.CTkFrame(track_info_box, fg_color="transparent")
        labels_sub_box.pack(side="left", fill="y", anchor="w")

        self.lbl_now_playing = ctk.CTkLabel(
            labels_sub_box, text=self.tr("select_track"), font=("Segoe UI", 13, "bold"), anchor="w"
        )
        self.lbl_now_playing.pack(anchor="w", pady=(4, 0))

        self.lbl_artist = ctk.CTkLabel(
            labels_sub_box, text="SoundFish Player", font=("Segoe UI", 11), text_color="gray", anchor="w"
        )
        self.lbl_artist.pack(anchor="w")

        center_box = ctk.CTkFrame(player_layout, fg_color="transparent")
        center_box.pack(side="left", fill="both", expand=True, padx=20)

        btns_box = ctk.CTkFrame(center_box, fg_color="transparent")
        btns_box.pack(anchor="center")

        self.btn_shuffle = ctk.CTkButton(
            btns_box, text="🔀", width=36, height=36, fg_color="transparent",
            font=("Segoe UI Emoji", 14), command=self.toggle_shuffle, hover=False
        )
        self.btn_shuffle.pack(side="left", padx=5)

        self.btn_loop = ctk.CTkButton(
            btns_box, text="🔁", width=36, height=36, fg_color="transparent",
            font=("Segoe UI Emoji", 14), command=self.toggle_loop, hover=False
        )
        self.btn_loop.pack(side="left", padx=5)

        self.btn_prev = ctk.CTkButton(
            btns_box, text="⏮", width=36, height=36, fg_color="transparent",
            font=("Arial", 16), command=self.prev_song, hover=False
        )
        self.btn_prev.pack(side="left", padx=5)

        self.btn_play = ctk.CTkButton(
            btns_box, text="▶", width=44, height=44, corner_radius=22,
            font=("Arial", 18, "bold"), command=self.play_pause
        )
        self.btn_play.pack(side="left", padx=10)

        self.btn_next = ctk.CTkButton(
            btns_box, text="⏭", width=36, height=36, fg_color="transparent",
            font=("Arial", 16), command=self.next_song, hover=False
        )
        self.btn_next.pack(side="left", padx=5)

        progress_box = ctk.CTkFrame(center_box, fg_color="transparent")
        progress_box.pack(fill="x", pady=(5, 0))

        self.lbl_curr_time = ctk.CTkLabel(progress_box, text="00:00", font=("Consolas", 11), text_color="gray")
        self.lbl_curr_time.pack(side="left", padx=5)

        self.slider_progress = ctk.CTkSlider(
            progress_box, from_=0, to=100, command=self.on_seek, height=12
        )
        self.slider_progress.set(0)
        self.slider_progress.pack(side="left", fill="x", expand=True, padx=8)
        self.slider_progress.bind("<ButtonRelease-1>", self._on_seek_release)

        self.lbl_total_time = ctk.CTkLabel(progress_box, text="00:00", font=("Consolas", 11), text_color="gray")
        self.lbl_total_time.pack(side="right", padx=5)

    def create_albums_frame(self):
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["albums"] = frame
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        top_albums = ctk.CTkFrame(frame, fg_color="transparent")
        top_albums.grid(row=0, column=0, sticky="ew", pady=(0, 15))

        self.lbl_albums_title = ctk.CTkLabel(top_albums, text=self.tr("albums_title"), font=("Segoe UI", 22, "bold"))
        self.lbl_albums_title.pack(side="left")

        self.btn_create_album = ctk.CTkButton(
            top_albums, text=self.tr("create_album"), height=38, font=("Segoe UI", 13, "bold"),
            corner_radius=20, command=self.create_album_dialog
        )
        self.btn_create_album.pack(side="right")

        self.albums_scroll = ctk.CTkScrollableFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        self.albums_scroll.grid(row=1, column=0, sticky="nsew")
        self.render_albums()

    def create_settings_frame(self):
        frame = ctk.CTkScrollableFrame(self.main_container, fg_color="transparent")
        self.frames["settings"] = frame

        self.lbl_settings_title = ctk.CTkLabel(frame, text=self.tr("settings"), font=("Segoe UI", 24, "bold"))
        self.lbl_settings_title.pack(anchor="w", pady=(10, 20))

        card_vol = ctk.CTkFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        card_vol.pack(fill="x", pady=10)

        self.lbl_vol_title = ctk.CTkLabel(card_vol, text=self.tr("audio_volume"), font=("Segoe UI", 15, "bold"))
        self.lbl_vol_title.pack(anchor="w", padx=20, pady=(15, 5))
        
        vol_box = ctk.CTkFrame(card_vol, fg_color="transparent")
        vol_box.pack(fill="x", padx=20, pady=(0, 15))

        saved_vol = self.volume
        self.slider_volume = ctk.CTkSlider(vol_box, from_=0, to=100, command=self.change_volume, height=14)
        self.slider_volume.set(saved_vol)
        self.slider_volume.pack(side="left", fill="x", expand=True)

        self.lbl_volume = ctk.CTkLabel(vol_box, text=f"{saved_vol}%", font=("Segoe UI", 13, "bold"), width=50)
        self.lbl_volume.pack(side="right", padx=(10, 0))

        card_theme = ctk.CTkFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        card_theme.pack(fill="x", pady=10)

        self.lbl_theme_title = ctk.CTkLabel(card_theme, text=self.tr("color_style"), font=("Segoe UI", 15, "bold"))
        self.lbl_theme_title.pack(anchor="w", padx=20, pady=(15, 5))
        
        current_theme_name = self.app_settings.get("theme", "Gold / Dark Luxury")
        self.theme_menu = ctk.CTkOptionMenu(
            card_theme, values=list(self.themes.keys()), command=self.change_theme, width=240, height=36, corner_radius=10
        )
        self.theme_menu.set(current_theme_name)
        self.theme_menu.pack(anchor="w", padx=20, pady=(0, 15))

        card_lang = ctk.CTkFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        card_lang.pack(fill="x", pady=10)

        self.lbl_lang_title = ctk.CTkLabel(card_lang, text=self.tr("language"), font=("Segoe UI", 15, "bold"))
        self.lbl_lang_title.pack(anchor="w", padx=20, pady=(15, 5))
        self.lang_menu = ctk.CTkOptionMenu(
            card_lang, values=list(self.translations.keys()), command=self.change_language, width=240, height=36, corner_radius=10
        )
        self.lang_menu.set(self.current_lang)
        self.lang_menu.pack(anchor="w", padx=20, pady=(0, 15))

        card_hotkey = ctk.CTkFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        card_hotkey.pack(fill="x", pady=10)

        self.lbl_bind_title = ctk.CTkLabel(card_hotkey, text=self.tr("hotkeys_title"), font=("Segoe UI", 15, "bold"))
        self.lbl_bind_title.pack(anchor="w", padx=20, pady=(15, 2))

        self.lbl_bind_hint = ctk.CTkLabel(
            card_hotkey, text=self.tr("hk_hint"), font=("Segoe UI", 11),
            text_color=self.current_theme["subtext"]
        )
        self.lbl_bind_hint.pack(anchor="w", padx=20, pady=(0, 8))

        for action, label_key in (("play_pause", "hk_play_pause"), ("next", "hk_next"), ("prev", "hk_prev")):
            row = ctk.CTkFrame(card_hotkey, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=4)

            lbl = ctk.CTkLabel(row, text=self.tr(label_key), font=("Segoe UI", 13), anchor="w")
            lbl.pack(side="left")

            btn = ctk.CTkButton(
                row, text=self.fmt_combo(self.hotkeys[action]), width=220, height=36,
                corner_radius=10, font=("Segoe UI", 13, "bold"),
                command=lambda a=action: self.start_binding(a)
            )
            btn.pack(side="right")

            self.bind_buttons[action] = btn
            self.bind_labels[action] = (lbl, label_key)

        footer = ctk.CTkFrame(card_hotkey, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(10, 15))

        self.toast_var = ctk.BooleanVar(value=self.toast_enabled)
        self.sw_toast = ctk.CTkSwitch(
            footer, text=self.tr("toast_toggle"), variable=self.toast_var,
            command=self.on_toast_toggle, font=("Segoe UI", 12)
        )
        self.sw_toast.pack(side="left")

        self.btn_reset_binds = ctk.CTkButton(
            footer, text=self.tr("hk_reset"), width=110, height=30, corner_radius=8,
            fg_color="transparent", border_width=1, font=("Segoe UI", 12),
            command=self.reset_hotkeys
        )
        self.btn_reset_binds.pack(side="right")

        self.settings_cards = [card_vol, card_theme, card_lang, card_hotkey]

    def create_info_frame(self):
        frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["info"] = frame

        self.lbl_info_title = ctk.CTkLabel(frame, text=self.tr("about_app"), font=("Segoe UI", 24, "bold"))
        self.lbl_info_title.pack(anchor="w", pady=(10, 20))

        card = ctk.CTkFrame(frame, corner_radius=16, fg_color=self.current_theme["panel"])
        card.pack(fill="both", expand=True)

        self.lbl_info_content = ctk.CTkLabel(
            card, text=self.tr("about_text").format(self.music_dir, self.album_dir, self.settings_dir),
            font=("Segoe UI", 14), justify="left", wraplength=650
        )
        self.lbl_info_content.pack(padx=25, pady=25, anchor="nw")

    # --- КРАСИВЫЕ ОКНА-СООБЩЕНИЯ: по центру плеера, с затемнением фона ---
    def _modal_bounds(self):
        return (self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height())

    def _modal_center(self, bounds, size):
        """Центр окна плеера; карточка не выходит за пределы экрана, даже если плеер маленький."""
        px, py, pw, ph = bounds
        w, h = size
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = min(max(0, x), max(0, sw - w))
            y = min(max(0, y), max(0, sh - h - 48))
        except Exception:
            pass
        return (x, y)

    def _open_modal(self, build, on_escape=None, on_enter=None, close_on_scrim=True):
        """Показывает карточку строго по центру окна плеера. build(card) наполняет её содержимым."""
        self._close_modal(animate=False)
        t = self.current_theme
        self.update_idletasks()
        bounds = self._modal_bounds()
        px, py, pw, ph = bounds
        self._modal_id += 1
        tid = self._modal_id
        escape = on_escape or self._close_modal

        # затемнение поверх плеера
        scrim = tk.Toplevel(self)
        scrim.withdraw()
        scrim.overrideredirect(True)
        scrim.configure(bg="#000000")
        scrim.geometry(f"{pw}x{ph}+{px}+{py}")
        try:
            scrim.attributes("-alpha", 0.0)
            scrim.transient(self)
        except Exception:
            pass

        # сама карточка (скруглённые углы через прозрачный ключевой цвет)
        key_color = "#010203"
        win = tk.Toplevel(self)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=key_color)
        try:
            win.attributes("-transparentcolor", key_color)
            win.attributes("-alpha", 0.0)
            win.transient(self)
        except Exception:
            pass

        card = ctk.CTkFrame(win, corner_radius=22, fg_color=t["panel"], border_width=2, border_color=t["accent"])
        card.pack(padx=3, pady=3)
        build(card)

        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        pos = self._modal_center(bounds, (w, h))
        win.geometry(f"+{pos[0]}+{pos[1] + 14}")

        self._modal = {
            "tid": tid, "scrim": scrim, "win": win, "geom": bounds,
            "size": (w, h), "pos": pos, "closing": False, "actions": [],
        }

        def on_scrim_click(_event=None):
            if close_on_scrim:
                escape()
            else:
                win.lift()

        scrim.bind("<Button-1>", on_scrim_click)
        scrim.bind("<FocusIn>", lambda e: win.lift())
        win.bind("<Escape>", lambda e: escape())
        if on_enter:
            win.bind("<Return>", lambda e: on_enter())

        scrim.deiconify()
        win.deiconify()
        scrim.lift()
        win.lift()
        self.after(40, lambda: self._modal_focus(tid))
        self._modal_fade(tid, 0, False)
        self._modal_poll(tid)

    def _modal_focus(self, tid):
        m = self._modal
        if m and m["tid"] == tid:
            try:
                m["win"].focus_force()
            except Exception:
                pass

    def _modal_fade(self, tid, step, closing):
        m = self._modal
        if not m or m["tid"] != tid:
            return
        p = min(1.0, (step + 1) / 8)
        ease = 1 - (1 - p) ** 3
        k = (1 - ease) if closing else ease
        try:
            m["scrim"].attributes("-alpha", 0.55 * k)
            m["win"].attributes("-alpha", k)
            if not closing:
                x, y = m["pos"]
                m["win"].geometry(f"+{x}+{y + int(14 * (1 - ease))}")
        except Exception:
            return
        if p < 1.0:
            self.after(14, lambda: self._modal_fade(tid, step + 1, closing))
        elif closing:
            self._destroy_modal(tid)

    def _modal_poll(self, tid):
        """Если плеер подвинули или растянули — карточка и затемнение следуют за ним."""
        m = self._modal
        if not m or m["tid"] != tid:
            return
        try:
            bounds = self._modal_bounds()
            if bounds != m["geom"] and not m["closing"]:
                m["geom"] = bounds
                px, py, pw, ph = bounds
                m["scrim"].geometry(f"{pw}x{ph}+{px}+{py}")
                w, h = m["size"]
                m["pos"] = self._modal_center(bounds, (w, h))
                m["win"].geometry(f"+{m['pos'][0]}+{m['pos'][1]}")
                m["scrim"].lift()
                m["win"].lift()
        except Exception:
            pass
        self.after(150, lambda: self._modal_poll(tid))

    def _close_modal(self, animate=True):
        m = self._modal
        if not m or m["closing"]:
            return
        if animate:
            m["closing"] = True
            self._modal_fade(m["tid"], 0, True)
        else:
            self._destroy_modal(m["tid"])

    def _destroy_modal(self, tid):
        m = self._modal
        if not m or m["tid"] != tid:
            return
        for key in ("win", "scrim"):
            try:
                m[key].destroy()
            except Exception:
                pass
        self._modal = None
        try:
            self.focus_force()
        except Exception:
            pass

    def _message_card(self, title, message, detail=None, icon="ℹ", tone="info", buttons=None, default=0):
        """Красивое сообщение. buttons: [(текст, стиль, действие)], стиль: danger / primary / ghost.
        Enter нажимает кнопку с индексом default, Esc и клик по фону закрывают окно."""
        t = self.current_theme
        buttons = buttons or [(self.tr("dlg_ok"), "primary", None)]
        tone_color = {"danger": "#F43F5E", "warning": "#F59E0B"}.get(tone, t["accent"])
        badge_text = "#FFFFFF" if tone == "danger" else "#000000"
        styles = {
            "danger": dict(fg_color="#F43F5E", hover_color="#E11D48", text_color="#FFFFFF", border_width=0),
            "primary": dict(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000", border_width=0),
            "ghost": dict(fg_color=t["card"], hover_color=t["card_hover"], text_color=t["text"],
                          border_width=1, border_color=t["card_hover"]),
        }
        runners = []

        def make_runner(callback):
            def run():
                m = self._modal
                if m is None or m["closing"]:
                    return
                self._close_modal()
                if callback:
                    callback()
            return run

        def build(card):
            card.configure(border_color=tone_color)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(padx=36, pady=(28, 24))

            ctk.CTkLabel(
                inner, text=icon, width=64, height=64, corner_radius=32,
                fg_color=tone_color, text_color=badge_text, font=("Segoe UI Emoji", 28)
            ).pack(pady=(0, 14))

            ctk.CTkLabel(inner, text=title, font=("Segoe UI", 20, "bold"), text_color=t["text"]).pack()
            ctk.CTkLabel(
                inner, text=message, font=("Segoe UI", 13), text_color=t["subtext"],
                wraplength=340, justify="center"
            ).pack(pady=(8, 0))

            if detail:
                chip = ctk.CTkFrame(inner, corner_radius=12, fg_color=t["card"])
                chip.pack(fill="x", pady=(16, 0))
                ctk.CTkLabel(
                    chip, text=detail, font=("Consolas", 12), text_color=t["text"],
                    wraplength=320, justify="center"
                ).pack(padx=14, pady=10)

            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(pady=(24, 0))
            for text, style, callback in buttons:
                run = make_runner(callback)
                runners.append(run)
                ctk.CTkButton(
                    row, text=text, width=150, height=42, corner_radius=12,
                    font=("Segoe UI", 13, "bold"), command=run, **styles[style]
                ).pack(side="left", padx=7)

        self._open_modal(build, on_enter=lambda: runners[min(default, len(runners) - 1)]())
        self._modal["actions"] = runners

    def _default_album_art(self, size):
        """Фирменная заглушка обложки (виниловая пластинка), если фото не выбрано."""
        t = self.current_theme
        key = ("default_art", size, t["accent"], t["card_hover"])
        cached = self._album_img_cache.get(key)
        if cached is not None:
            return cached
        px = size * 3
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        plate = _hex_to_rgb(t["card_hover"])
        accent = _hex_to_rgb(t["accent"])
        d.rounded_rectangle([0, 0, px - 1, px - 1], radius=int(px * 0.2), fill=plate + (255,))
        c = px / 2
        r = px * 0.40
        d.ellipse([c - r, c - r, c + r, c + r], fill=(14, 14, 14, 255))
        for k in (0.31, 0.23):                       # бороздки
            rr = px * k
            d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=(46, 46, 46, 255), width=max(1, px // 90))
        rl = px * 0.14
        d.ellipse([c - rl, c - rl, c + rl, c + rl], fill=accent + (255,))
        rh = px * 0.035
        d.ellipse([c - rh, c - rh, c + rh, c + rh], fill=plate + (255,))
        img = img.resize((size * 2, size * 2), LANCZOS)
        art = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
        self._album_img_cache[key] = art
        return art

    def _album_form(self, index=None):
        """Одно красивое окно и для создания, и для редактирования альбома."""
        editing = index is not None
        album = self.albums[index] if editing else None
        t = self.current_theme

        state = {"path": album.get("icon_path") if editing else None}
        refs = {}
        checkbox_vars = {}

        all_tracks = []
        if os.path.exists(self.music_dir):
            all_tracks = sorted(
                (os.path.join(self.music_dir, f) for f in os.listdir(self.music_dir)
                 if f.lower().endswith((".mp3", ".wav"))),
                key=lambda p: os.path.basename(p).lower()
            )
        for p in all_tracks:
            checkbox_vars[p] = ctk.BooleanVar(value=bool(editing and p in album["tracks"]))

        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        list_height = int(max(100, min(230, self.winfo_height() / scale - 440)))

        def refresh_cover():
            art = self.load_album_image(state["path"], size=(88, 88)) if state["path"] else None
            refs["preview"].configure(image=art or self._default_album_art(88))
            if art:
                refs["status"].configure(text=self.tr("alb_cover_set"), text_color="#22c55e")
                refs["btn_pick"].configure(text=self.tr("alb_change"))
                refs["btn_clear"].pack(side="left", padx=(8, 0))
            else:
                state["path"] = None
                refs["status"].configure(text=self.tr("alb_cover_none"), text_color=t["subtext"])
                refs["btn_pick"].configure(text=self.tr("alb_pick"))
                refs["btn_clear"].pack_forget()

        def choose_img():
            m = self._modal
            file = filedialog.askopenfilename(
                parent=m["win"] if m else self,
                filetypes=[("Изображения", "*.png *.jpg *.jpeg")]
            )
            if m:
                self._modal_focus(m["tid"])
            if not file:
                return
            dest = os.path.join(self.album_dir, f"cover_{random.randint(1000, 9999)}_{os.path.basename(file)}")
            try:
                shutil.copy(file, dest)
                state["path"] = dest
            except Exception:
                state["path"] = file
            refresh_cover()

        def clear_img():
            state["path"] = None
            refresh_cover()

        def update_counter():
            chosen = sum(1 for v in checkbox_vars.values() if v.get())
            refs["counter"].configure(text=f"  {self.tr('alb_selected').format(chosen, len(checkbox_vars))}  ")

        def set_all(value):
            for v in checkbox_vars.values():
                v.set(value)
            for chk in refs.get("checks", []):
                if value:
                    chk.select()
                else:
                    chk.deselect()
            update_counter()

        def save():
            name = refs["entry"].get().strip() or (album["name"] if editing else self.tr("alb_default_name"))
            chosen = [p for p, v in checkbox_vars.items() if v.get()]
            if editing:
                album["icon_path"] = state["path"]
                album["name"] = name
                album["tracks"] = chosen
                self.save_albums_data()
                self.render_albums()
                if self.current_playing_album_idx == index:
                    self.render_album_tracks_ui(index)
            else:
                self.albums.append({"name": name, "icon_path": state["path"], "tracks": chosen})
                self.save_albums_data()
                self.render_albums()
            self._close_modal()

        def build(card):
            tid = self._modal_id
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(padx=30, pady=(24, 26))

            # --- заголовок
            head = ctk.CTkFrame(body, fg_color="transparent")
            head.pack(fill="x")
            ctk.CTkLabel(
                head, text=self.tr("alb_edit_title" if editing else "alb_new_title"),
                font=("Segoe UI", 20, "bold"), text_color=t["text"]
            ).pack(side="left")
            ctk.CTkButton(
                head, text="✕", width=34, height=34, corner_radius=17, fg_color=t["card"],
                hover_color=t["card_hover"], text_color=t["subtext"], font=("Segoe UI", 14, "bold"),
                command=self._close_modal
            ).pack(side="right")

            # --- обложка
            cover_row = ctk.CTkFrame(body, corner_radius=16, fg_color=t["card"])
            cover_row.pack(fill="x", pady=(18, 0))
            refs["preview"] = ctk.CTkLabel(cover_row, text="", width=88, height=88, image=self._default_album_art(88))
            refs["preview"].pack(side="left", padx=14, pady=14)

            info = ctk.CTkFrame(cover_row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=(0, 14), pady=14)
            ctk.CTkLabel(info, text=self.tr("alb_cover"), font=("Segoe UI", 13, "bold"),
                         text_color=t["text"], anchor="w").pack(anchor="w")
            refs["status"] = ctk.CTkLabel(info, text="", font=("Segoe UI", 11), text_color=t["subtext"], anchor="w")
            refs["status"].pack(anchor="w", pady=(2, 10))

            btn_row = ctk.CTkFrame(info, fg_color="transparent")
            btn_row.pack(anchor="w")
            refs["btn_pick"] = ctk.CTkButton(
                btn_row, text="", height=34, corner_radius=10, fg_color=t["accent"],
                hover_color=t["accent_hover"], text_color="#000000", font=("Segoe UI", 12, "bold"),
                command=choose_img
            )
            refs["btn_pick"].pack(side="left")
            refs["btn_clear"] = ctk.CTkButton(
                btn_row, text=self.tr("alb_remove"), width=80, height=34, corner_radius=10,
                fg_color="transparent", hover_color=t["card_hover"], text_color=t["subtext"],
                border_width=1, border_color=t["card_hover"], font=("Segoe UI", 12), command=clear_img
            )

            # --- название
            ctk.CTkLabel(body, text=self.tr("alb_name"), font=("Segoe UI", 13, "bold"),
                         text_color=t["text"], anchor="w").pack(anchor="w", pady=(18, 6))
            refs["entry"] = ctk.CTkEntry(
                body, height=42, corner_radius=12, border_width=1, border_color=t["accent"],
                fg_color=t["card"], text_color="#FFFFFF", placeholder_text=self.tr("alb_name_ph"),
                placeholder_text_color="#FFFFFF", font=("Segoe UI", 14)
            )
            refs["entry"].pack(fill="x")
            if editing:
                refs["entry"].insert(0, album["name"])

            # --- песни
            th = ctk.CTkFrame(body, fg_color="transparent")
            th.pack(fill="x", pady=(18, 6))
            ctk.CTkLabel(th, text=self.tr("alb_tracks"), font=("Segoe UI", 13, "bold"),
                         text_color=t["text"]).pack(side="left")
            refs["counter"] = ctk.CTkLabel(
                th, text="", height=24, corner_radius=12, fg_color=t["card"],
                text_color=t["accent"], font=("Segoe UI", 11, "bold")
            )
            refs["counter"].pack(side="left", padx=10)
            for text_key, value in (("alb_none", False), ("alb_all", True)):
                ctk.CTkButton(
                    th, text=self.tr(text_key), width=74, height=26, corner_radius=8,
                    fg_color="transparent", hover_color=t["card_hover"], text_color=t["subtext"],
                    font=("Segoe UI", 11), command=lambda v=value: set_all(v)
                ).pack(side="right", padx=(4, 0))

            scroll = ctk.CTkScrollableFrame(body, width=400, height=list_height, corner_radius=14, fg_color=t["card"])
            scroll.pack(fill="x")

            refs["checks"] = []
            if not all_tracks:
                ctk.CTkLabel(scroll, text=self.tr("alb_no_tracks"), text_color=t["subtext"]).pack(pady=16)
            else:
                def add_rows(start, first=False):
                    if not first:
                        m = self._modal
                        if not m or m["tid"] != tid:
                            return
                    for p in all_tracks[start:start + 10]:
                        title = os.path.splitext(os.path.basename(p))[0]
                        if len(title) > 46:
                            title = title[:43] + "..."
                        chk = ctk.CTkCheckBox(
                            scroll, text=title, variable=checkbox_vars[p], command=update_counter,
                            font=("Segoe UI", 12), text_color=t["text"], fg_color=t["accent"],
                            hover_color=t["accent_hover"], checkmark_color="#000000",
                            border_color=t["subtext"], corner_radius=6, checkbox_width=22, checkbox_height=22
                        )
                        chk.pack(anchor="w", pady=5, padx=8)
                        refs["checks"].append(chk)
                    if start + 10 < len(all_tracks):
                        self.after(8, lambda: add_rows(start + 10))

                add_rows(0, first=True)       # остальные строки достраиваются сразу после показа окна

            # --- кнопки
            foot = ctk.CTkFrame(body, fg_color="transparent")
            foot.pack(pady=(22, 0))
            ctk.CTkButton(
                foot, text=self.tr("alb_save_btn" if editing else "alb_create_btn"), width=170, height=44,
                corner_radius=12, fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000",
                font=("Segoe UI", 14, "bold"), command=save
            ).pack(side="left", padx=7)
            ctk.CTkButton(
                foot, text=self.tr("cancel_btn"), width=130, height=44, corner_radius=12,
                fg_color=t["card"], hover_color=t["card_hover"], text_color=t["text"],
                border_width=1, border_color=t["card_hover"], font=("Segoe UI", 14, "bold"),
                command=self._close_modal
            ).pack(side="left", padx=7)

            refresh_cover()
            update_counter()

        self._open_modal(build, on_enter=save, close_on_scrim=False)
        self._modal["actions"] = [save, self._close_modal]
        if not editing:
            self.after(140, lambda: refs["entry"].focus_set())

    def create_album_dialog(self):
        self._album_form(None)

    def edit_album_dialog(self, index):
        self._album_form(index)

    def load_album_image(self, img_path, size=(48, 48)):
        if not img_path or not os.path.exists(img_path):
            return None
        try:
            key = (img_path, size, os.path.getmtime(img_path))
            cached = self._album_img_cache.get(key)
            if cached is not None:
                return cached
            with Image.open(img_path) as pil_img:
                pil_img = center_square(pil_img.convert("RGBA")).resize((size[0] * 2, size[1] * 2), LANCZOS)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
            self._album_img_cache[key] = ctk_img
            return ctk_img
        except Exception:
            return None

    def render_albums(self):
        if not hasattr(self, "albums_scroll"):
            return
        for widget in self.albums_scroll.winfo_children():
            widget.destroy()

        for idx, album in enumerate(self.albums):
            card = ctk.CTkFrame(self.albums_scroll, corner_radius=12, fg_color=self.current_theme["card"])
            card.pack(fill="x", pady=6, padx=5)

            ctk_img = self.load_album_image(album.get("icon_path")) or self._default_album_art(48)
            lbl_icon = ctk.CTkLabel(card, image=ctk_img, text="")
            lbl_icon.pack(side="left", padx=10, pady=6)

            lbl_title = ctk.CTkLabel(card, text=f"{album['name']} ({len(album['tracks'])} треков)", font=("Segoe UI", 14, "bold"))
            lbl_title.pack(side="left", padx=10)

            btn_play_album = ctk.CTkButton(
                card, text="▶ Слушать", width=80, fg_color=self.current_theme["accent"], text_color="#000",
                command=lambda i=idx: self.play_album(i)
            )
            btn_play_album.pack(side="right", padx=8)

            btn_delete_album = ctk.CTkButton(
                card, text="🗑", width=32, fg_color="transparent", hover_color="#3A1C1C", text_color="#F43F5E",
                font=("Segoe UI Emoji", 13), command=lambda i=idx: self.confirm_delete_album_dialog(i)
            )
            btn_delete_album.pack(side="right", padx=4)

            btn_edit = ctk.CTkButton(
                card, text="⚙ Редактировать", width=120, corner_radius=8,
                fg_color=self.current_theme["card_hover"], hover_color=self.current_theme["panel"],
                text_color=self.current_theme["text"], border_width=1, border_color=self.current_theme["accent"],
                command=lambda i=idx: self.edit_album_dialog(i)
            )
            btn_edit.pack(side="right", padx=4)

    def confirm_delete_album_dialog(self, index):
        album_name = self.albums[index]["name"]
        self._message_card(
            self.tr("dlg_del_album_title"), self.tr("dlg_del_album_msg"), detail=album_name,
            icon="🗑", tone="danger", default=1,
            buttons=[
                (self.tr("delete_btn"), "danger", lambda: self.delete_album(index)),
                (self.tr("cancel_btn"), "ghost", None),
            ],
        )

    def delete_album(self, index):
        if 0 <= index < len(self.albums):
            if self.current_playing_album_idx == index:
                self.current_playing_album_idx = None
            elif self.current_playing_album_idx is not None and self.current_playing_album_idx > index:
                self.current_playing_album_idx -= 1
            del self.albums[index]
            self.save_albums_data()
            self.render_albums()

    # --- РЕНДЕР ТРЕКОВ АЛЬБОМА В ПЛЕЙЛИСТЕ С ВОЗМОЖНОСТЬЮ УДАЛЕНИЯ ИЗ АЛЬБОМА ---
    def render_album_tracks_ui(self, album_index):
        album = self.albums[album_index]
        self._reset_song_list(
            list(album["tracks"]),
            lambda p, a=album_index: (lambda: self.remove_track_from_album(a, p))
        )

    def remove_track_from_album(self, album_index, file_path):
        """Удаление песни только из альбома (файл в медиатеке сохраняется)"""
        if 0 <= album_index < len(self.albums):
            album = self.albums[album_index]
            if file_path in album["tracks"]:
                album["tracks"].remove(file_path)
                self.save_albums_data()
                self.render_albums()
                
                # Обновляем текущее окно прослушивания
                if self.current_playing_album_idx == album_index:
                    if file_path in self.playlist:
                        song_idx = self.playlist.index(file_path)
                        if song_idx == self.current_index:
                            music.stop()
                            self.is_playing = False
                            self.btn_play.configure(text="▶")
                            self.lbl_now_playing.configure(text=self.tr("select_track"))
                            self.current_index = -1
                    
                    self.render_album_tracks_ui(album_index)

    def play_album(self, index):
        album = self.albums[index]
        if not album["tracks"]:
            self._message_card(
                self.tr("dlg_album_empty_title"), self.tr("dlg_album_empty_msg"), icon="💿", tone="info"
            )
            return

        self.current_playing_album_idx = index
        self.lbl_lib_title.configure(text=f"Альбом: {album['name']}")
        self.render_album_tracks_ui(index)
        self.show_frame("library")
        self.play_song(0)

    def change_language(self, choice):
        self.current_lang = choice
        self.update_ui_language()
        self.save_app_settings()

    def update_ui_language(self):
        if self.current_playing_album_idx is None:
            self.lbl_lib_title.configure(text=self.tr("media_library"))
        else:
            alb_name = self.albums[self.current_playing_album_idx]['name']
            self.lbl_lib_title.configure(text=f"Альбом: {alb_name}")

        self.btn_add.configure(text=self.tr("add_files"))
        self.search_entry.configure(placeholder_text=self.tr("search_ph"))
        self._update_count()

        if self.current_index == -1:
            self.lbl_now_playing.configure(text=self.tr("select_track"))

        if "albums" in self.frames:
            self.lbl_albums_title.configure(text=self.tr("albums_title"))
            self.btn_create_album.configure(text=self.tr("create_album"))

        if "settings" in self.frames:
            self.lbl_settings_title.configure(text=self.tr("settings"))
            self.lbl_vol_title.configure(text=self.tr("audio_volume"))
            self.lbl_theme_title.configure(text=self.tr("color_style"))
            self.lbl_lang_title.configure(text=self.tr("language"))
            self.lbl_bind_title.configure(text=self.tr("hotkeys_title"))
            self.lbl_bind_hint.configure(text=self.tr("hk_hint"))
            for _action, (lbl, label_key) in self.bind_labels.items():
                lbl.configure(text=self.tr(label_key))
            self.sw_toast.configure(text=self.tr("toast_toggle"))
            self.btn_reset_binds.configure(text=self.tr("hk_reset"))
            self.refresh_bind_buttons()

        if "info" in self.frames:
            self.lbl_info_title.configure(text=self.tr("about_app"))
            self.lbl_info_content.configure(text=self.tr("about_text").format(self.music_dir, self.album_dir, self.settings_dir))

    def change_volume(self, value):
        val = int(value)
        self.volume = val
        self.lbl_volume.configure(text=f"{val}%")
        if audio_ok():
            music.set_volume(val / 100.0)
        self.request_save_settings()

    def request_save_settings(self):
        """Запись настроек с задержкой: ползунок громкости больше не пишет файл на каждый пиксель."""
        if self._settings_job is not None:
            self.after_cancel(self._settings_job)
        self._settings_job = self.after(500, self._flush_settings)

    def _flush_settings(self):
        self._settings_job = None
        self.save_app_settings()

    def confirm_delete_dialog(self, file_path):
        self._message_card(
            self.tr("dlg_del_track_title"), self.tr("dlg_del_track_msg"), detail=os.path.basename(file_path),
            icon="🗑", tone="danger", default=1,       # Enter = «Отмена», чтобы случайно не удалить
            buttons=[
                (self.tr("delete_btn"), "danger", lambda: self.delete_song(file_path)),
                (self.tr("cancel_btn"), "ghost", None),
            ],
        )

    # --- СПИСОК ТРЕКОВ ---
    def load_all_songs(self):
        if not os.path.exists(self.music_dir):
            return

        self.current_playing_album_idx = None
        self.lbl_lib_title.configure(text=self.tr("media_library"))

        names = sorted(
            (f for f in os.listdir(self.music_dir) if f.lower().endswith((".mp3", ".wav"))),
            key=str.lower
        )
        paths = [os.path.join(self.music_dir, f) for f in names]
        self._reset_song_list(paths, lambda p: (lambda: self.confirm_delete_dialog(p)))
        self.show_frame("library")

    def _reset_song_list(self, paths, delete_factory):
        """Полностью меняет список. Первые строки рисуются сразу, остальные — пачками (окно не зависает)."""
        current_path = None
        if 0 <= self.current_index < len(self.playlist):
            current_path = self.playlist[self.current_index]

        self._song_render_id += 1
        self._rendering = False
        for w in self.song_listbox.winfo_children():
            w.destroy()
        self.song_widgets = []
        self.song_titles = []
        self.song_dur_labels = []

        self.playlist = list(paths)
        self.current_index = self.playlist.index(current_path) if current_path in self.playlist else -1
        self._row_delete_factory = delete_factory

        self._filter_text = ""
        self._clear_search_entry()

        self.reset_shuffle_queues()
        self._update_count()
        self._start_row_rendering()
        self._start_duration_worker(self.playlist)

    def _start_row_rendering(self):
        if self._rendering:
            return
        self._rendering = True
        self._render_song_batch(self._song_render_id, first=True)

    def _render_song_batch(self, token, first=False):
        if token != self._song_render_id:
            return
        built = len(self.song_widgets)
        total = len(self.playlist)
        end = min(built + (8 if first else 6), total)
        for i in range(built, end):
            self._add_song_row(i)
        if end < total:
            self.after(6, lambda: self._render_song_batch(token))
        else:
            self._rendering = False

    def _row_text(self, i):
        mark = "▶" if i == self.current_index else f"{i + 1:02d}"
        return f"  {mark}   {self.song_titles[i]}"

    def _dur_text(self, path):
        entry = self.duration_cache.get(path)
        if entry and entry[1] > 0:
            return fmt_time(entry[1])
        return ""

    def _row_visible(self, title):
        return not self._filter_text or self._filter_text in title.lower()

    def _add_song_row(self, i):
        t = self.current_theme
        path = self.playlist[i]
        playing = (i == self.current_index)
        self.song_titles.append(os.path.splitext(os.path.basename(path))[0])

        frame = ctk.CTkFrame(self.song_listbox, corner_radius=12, fg_color=t["card_hover"] if playing else t["card"])
        frame.grid_columnconfigure(0, weight=1)

        btn_song = ctk.CTkButton(
            frame, text=self._row_text(i), anchor="w", fg_color="transparent",
            hover_color=t["card_hover"], text_color=t["accent"] if playing else t["text"],
            font=("Segoe UI", 13), command=lambda n=i: self.play_song(n)
        )
        btn_song.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=6)

        lbl_dur = ctk.CTkLabel(frame, text=self._dur_text(path), width=52, font=("Consolas", 12), text_color=t["subtext"])
        lbl_dur.grid(row=0, column=1, padx=(0, 4))

        btn_delete = ctk.CTkButton(
            frame, text="🗑", width=36, height=36, fg_color="transparent",
            hover_color="#3A1C1C", text_color="#F43F5E", font=("Segoe UI Emoji", 13),
            command=self._row_delete_factory(path)
        )
        btn_delete.grid(row=0, column=2, padx=(0, 8), pady=6)

        self.song_widgets.append((frame, btn_song))
        self.song_dur_labels.append(lbl_dur)
        if self._row_visible(self.song_titles[i]):
            frame.pack(fill="x", pady=4, padx=5)

    def _style_row(self, i, playing):
        t = self.current_theme
        frame, btn = self.song_widgets[i]
        frame.configure(fg_color=t["card_hover"] if playing else t["card"])
        btn.configure(text=self._row_text(i), text_color=t["accent"] if playing else t["text"])

    def _remove_row(self, idx):
        if idx < len(self.song_widgets):
            frame, _ = self.song_widgets[idx]
            frame.destroy()
            del self.song_widgets[idx]
            del self.song_titles[idx]
            del self.song_dur_labels[idx]
        del self.playlist[idx]
        if self.current_index > idx:
            self.current_index -= 1
        for i in range(idx, len(self.song_widgets)):
            _, btn = self.song_widgets[i]
            btn.configure(text=self._row_text(i), command=lambda n=i: self.play_song(n))
        self._update_count()

    def _count_text(self, n):
        if self.current_lang == "Русский":
            if n % 10 == 1 and n % 100 != 11:
                word = "трек"
            elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
                word = "трека"
            else:
                word = "треков"
        else:
            word = "track" if n == 1 else "tracks"
        return f"♪ {n} {word}"

    def _update_count(self):
        if hasattr(self, "count_pill"):
            self.count_pill.configure(text=f"  {self._count_text(len(self.playlist))}  ")

    # --- поиск ---
    def _on_search_key(self, _event=None):
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(150, self._apply_filter)

    def _clear_search(self, _event=None):
        self._clear_search_entry()
        self._apply_filter()
        self.focus_set()
        self.after(50, self._show_search_placeholder)

    def _clear_search_entry(self):
        """Очищает поле поиска, не ломая белую подсказку «Поиск песни»."""
        try:
            if self.search_entry.get():          # get() пустой, если сейчас показана подсказка
                self.search_entry.delete(0, "end")
        except Exception:
            pass
        self._show_search_placeholder()

    def _show_search_placeholder(self):
        """customtkinter сам возвращает подсказку только после потери фокуса — делаем это сразу."""
        try:
            entry = self.search_entry
            if entry.get() == "" and self.focus_get() is not entry._entry:
                entry._activate_placeholder()
        except Exception:
            pass

    def _apply_filter(self):
        self._filter_job = None
        self._filter_text = self.search_entry.get().strip().lower()
        for frame, _ in self.song_widgets:
            frame.pack_forget()
        for i, (frame, _) in enumerate(self.song_widgets):
            if self._row_visible(self.song_titles[i]):
                frame.pack(fill="x", pady=4, padx=5)

    # --- длительности (читаются в фоне и кэшируются на диск) ---
    def _load_duration_cache(self):
        try:
            with open(self.duration_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2}
        except Exception:
            pass
        return {}

    def _save_duration_cache(self):
        try:
            with open(self.duration_cache_file, "w", encoding="utf-8") as f:
                json.dump(dict(self.duration_cache), f, ensure_ascii=False)
        except Exception:
            pass

    def _cached_length(self, path):
        entry = self.duration_cache.get(path)
        if entry:
            try:
                if abs(entry[0] - os.path.getmtime(path)) < 1:
                    return entry[1]
            except OSError:
                pass
        return None

    def _read_length(self, path):
        _audio_ready.wait(15)
        try:
            low = path.lower()
            if low.endswith(".mp3") and MP3 is not None:
                return MP3(path).info.length
            if low.endswith(".wav") and WAVE is not None:
                return WAVE(path).info.length
        except Exception:
            pass
        return 0

    def get_track_length(self, path):
        cached = self._cached_length(path)
        if cached is not None:
            return cached
        length = self._read_length(path)
        try:
            self.duration_cache[path] = [os.path.getmtime(path), length]
        except OSError:
            pass
        return length

    def _start_duration_worker(self, paths):
        threading.Thread(
            target=self._duration_worker, args=(list(paths), self._song_render_id), daemon=True
        ).start()

    def _duration_worker(self, paths, token):
        changed = False
        for p in paths:
            if token != self._song_render_id:
                break
            length = self._cached_length(p)
            if length is None:
                length = self._read_length(p)
                try:
                    self.duration_cache[p] = [os.path.getmtime(p), length]
                except OSError:
                    continue
                changed = True
            self.hotkey_queue.put(("dur", p, length, token))
        if changed:
            self._save_duration_cache()

    def _apply_duration(self, path, length, token):
        if token != self._song_render_id or length <= 0:
            return
        try:
            idx = self.playlist.index(path)
        except ValueError:
            return
        if idx < len(self.song_dur_labels):
            self.song_dur_labels[idx].configure(text=fmt_time(length))

    def append_song_to_ui(self, file_path):
        if file_path in self.playlist:
            return
        self.playlist.append(file_path)
        self.reset_shuffle_queues()
        self._update_count()
        self._start_row_rendering()
        self._start_duration_worker([file_path])

    def add_song(self):
        files = filedialog.askopenfilenames(filetypes=[("Аудиофайлы", "*.mp3 *.wav")])
        added = []
        for f in files:
            destination_path = os.path.join(self.music_dir, os.path.basename(f))
            try:
                if os.path.abspath(f) != os.path.abspath(destination_path):
                    if not os.path.exists(destination_path):
                        shutil.copy(f, destination_path)
                added.append(destination_path)
            except Exception:
                pass

        if self.current_playing_album_idx is not None:
            self.load_all_songs()          # из альбома возвращаемся в общую медиатеку
        else:
            for path in added:
                self.append_song_to_ui(path)

    def delete_song(self, file_path):
        """Полное удаление песни из всей программы и диска"""
        if file_path in self.playlist:
            idx = self.playlist.index(file_path)
            
            if idx == self.current_index:
                music.stop()
                self.is_playing = False
                self.btn_play.configure(text="▶")
                self.lbl_now_playing.configure(text=self.tr("select_track"))
                self.current_index = -1

            self._remove_row(idx)
            self.reset_shuffle_queues()

        # Также вычищаем трек из всех альбомов, где он был
        for album in self.albums:
            if file_path in album["tracks"]:
                album["tracks"].remove(file_path)
        self.save_albums_data()
        self.render_albums()

        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    # --- ГОРЯЧИЕ КЛАВИШИ ---
    def fmt_combo(self, combo):
        parts = [p for p in combo.split("+") if p]
        return " + ".join(KEY_PRETTY.get(p, p.capitalize() if len(p) > 1 else p.upper()) for p in parts)

    def refresh_bind_buttons(self):
        for action, btn in self.bind_buttons.items():
            btn.configure(text=self.fmt_combo(self.hotkeys[action]))

    def register_hotkeys(self):
        if keyboard is None:
            return
        self.unregister_hotkeys()
        for action, combo in self.hotkeys.items():
            if not combo:
                continue
            try:
                self.hotkey_handlers[action] = keyboard.add_hotkey(
                    combo, lambda a=action: self._on_hotkey(a)
                )
            except Exception as e:
                print(f"Ошибка хоткея ({action}: {combo}): {e}")

    def unregister_hotkeys(self):
        for handler in list(self.hotkey_handlers.values()):
            try:
                keyboard.remove_hotkey(handler)
            except Exception:
                pass
        self.hotkey_handlers = {}

    def _on_hotkey(self, action):
        """Вызывается в потоке keyboard — только кладём событие в очередь."""
        now = time.monotonic()
        if now - self._last_hotkey_time.get(action, 0) < 0.25:
            return  # защита от автоповтора при зажатой клавише
        self._last_hotkey_time[action] = now
        self.hotkey_queue.put(("action", action))

    def _on_audio_ready(self):
        """Вызывается один раз, когда фоновая загрузка звука и клавиатуры закончилась."""
        if audio_ok():
            music.set_volume(self.volume / 100.0)
        self.register_hotkeys()

    def process_hotkey_queue(self):
        """Разбор очереди в главном потоке tkinter (безопасно для интерфейса)."""
        if not self._audio_applied and _audio_ready.is_set():
            self._audio_applied = True
            try:
                self._on_audio_ready()
            except Exception as e:
                print(f"Ошибка инициализации звука: {e}")
        try:
            while True:
                kind, *payload = self.hotkey_queue.get_nowait()
                try:
                    if kind == "action":
                        self.run_hotkey_action(payload[0])
                    elif kind == "captured":
                        self._apply_captured_key(*payload)
                    elif kind == "dur":
                        self._apply_duration(*payload)
                    elif kind == "restore":
                        self.restore_from_tray()
                    elif kind == "quit":
                        self.quit_app()
                        return
                except Exception as e:
                    print(f"Ошибка обработки события: {e}")
        except queue.Empty:
            pass
        self.after(50, self.process_hotkey_queue)

    def current_track_title(self):
        if 0 <= self.current_index < len(self.playlist):
            return os.path.splitext(os.path.basename(self.playlist[self.current_index]))[0]
        return ""

    def run_hotkey_action(self, action):
        if self.is_listening or not self.playlist:
            return
        if action == "play_pause":
            self.play_pause()
            icon, key = ("⏸", "toast_play") if self.is_playing else ("▶", "toast_pause")
        elif action == "next":
            self.next_song()
            icon, key = "⏭", "toast_next"
        elif action == "prev":
            self.prev_song()
            icon, key = "⏮", "toast_prev"
        else:
            return
        self.show_toast(icon, self.tr(key), self.current_track_title(), self.current_cover_pil)

    def start_binding(self, action):
        if self.is_listening or keyboard is None:
            return
        self.is_listening = True
        self.unregister_hotkeys()  # чтобы старые бинды не срабатывали во время записи
        self.bind_buttons[action].configure(text=self.tr("press_keys"))
        threading.Thread(target=self._capture_hotkey_thread, args=(action,), daemon=True).start()

    def _capture_hotkey_thread(self, action):
        pressed = []
        cancelled = False

        while True:
            try:
                event = keyboard.read_event()
            except Exception:
                cancelled = True
                break

            if event.event_type == keyboard.KEY_DOWN:
                name = (event.name or "").lower()
                if "ctrl" in name:
                    name = "ctrl"
                elif "alt" in name:
                    name = "alt"
                elif "shift" in name:
                    name = "shift"
                elif "windows" in name:
                    name = "windows"
                if name == "esc" and not pressed:
                    cancelled = True
                    break
                if name and name not in pressed:
                    pressed.append(name)
            elif event.event_type == keyboard.KEY_UP and pressed:
                break

        mods = [k for k in MODIFIERS if k in pressed]
        others = [k for k in pressed if k not in MODIFIERS]
        combo = "+".join(mods + others)
        self.hotkey_queue.put(("captured", action, combo, cancelled))

    def _apply_captured_key(self, action, combo, cancelled):
        self.is_listening = False
        error = None

        if not cancelled and combo:
            parts = combo.split("+")
            if all(p in MODIFIERS for p in parts):
                error = self.tr("hk_need_key")
            else:
                clash = next((a for a, c in self.hotkeys.items() if a != action and c == combo), None)
                if clash:
                    label = self.tr(self.bind_labels[clash][1]).strip()
                    error = self.tr("hk_conflict").format(label)
                else:
                    self.hotkeys[action] = combo
                    self.save_app_settings()

        self.register_hotkeys()
        self.refresh_bind_buttons()

        if error:
            self._message_card(
                self.tr("dlg_hotkey_title"), error, icon="⚠", tone="warning"
            )

    def reset_hotkeys(self):
        if self.is_listening:
            return
        self.hotkeys = dict(self.default_hotkeys)
        self.register_hotkeys()
        self.refresh_bind_buttons()
        self.save_app_settings()

    def on_toast_toggle(self):
        self.toast_enabled = bool(self.toast_var.get())
        self.save_app_settings()

    # --- ВСПЛЫВАЮЩЕЕ УВЕДОМЛЕНИЕ (TOAST) ---
    def show_toast(self, icon, title, subtitle="", cover=None):
        if not self.toast_enabled:
            return
        t = self.current_theme
        self.toast_id += 1
        tid = self.toast_id

        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except Exception:
                pass
            self.toast_win = None

        key_color = "#010203"  # этот цвет становится прозрачным -> скруглённые углы
        win = tk.Toplevel(self)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=key_color)
        try:
            win.attributes("-topmost", True)
            win.attributes("-transparentcolor", key_color)
            win.attributes("-alpha", 0.0)
        except Exception:
            pass

        card = ctk.CTkFrame(win, corner_radius=18, fg_color=t["panel"], border_width=2, border_color=t["accent"])
        card.pack(padx=2, pady=2)

        if cover is not None:
            self._toast_img = ctk.CTkImage(light_image=cover, dark_image=cover, size=(52, 52))
        else:
            self._toast_img = self.logo_toast
        badge = ctk.CTkLabel(card, text="", image=self._toast_img, width=52, height=52)
        badge.pack(side="left", padx=(12, 10), pady=12)

        box = ctk.CTkFrame(card, fg_color="transparent")
        box.pack(side="left", padx=(0, 22), pady=12)

        ctk.CTkLabel(box, text=f"{icon}  {title}", font=("Segoe UI", 11, "bold"), text_color=t["accent"], anchor="w").pack(anchor="w")
        name = subtitle if len(subtitle) <= 34 else subtitle[:31] + "..."
        ctk.CTkLabel(box, text=name or "SoundFish", font=("Segoe UI", 14, "bold"), text_color=t["text"], anchor="w").pack(anchor="w")

        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = win.winfo_screenwidth() - w - 28
        y = win.winfo_screenheight() - h - 110

        self.toast_win = win
        self.toast_pos = (x, y)
        win.geometry(f"+{x}+{y + 14}")
        win.deiconify()
        self._toast_in(win, tid, 0)

    def _toast_in(self, win, tid, step):
        if tid != self.toast_id:
            return
        try:
            p = min(1.0, step / 8)
            ease = 1 - (1 - p) ** 3
            win.attributes("-alpha", 0.97 * ease)
            x, y = self.toast_pos
            win.geometry(f"+{x}+{y + int(14 * (1 - ease))}")
            if p < 1.0:
                self.after(16, lambda: self._toast_in(win, tid, step + 1))
            else:
                self.after(2200, lambda: self._toast_out(win, tid, 0))
        except Exception:
            pass

    def _toast_out(self, win, tid, step):
        if tid != self.toast_id:
            return
        try:
            p = min(1.0, step / 10)
            win.attributes("-alpha", 0.97 * (1 - p))
            if p < 1.0:
                self.after(20, lambda: self._toast_out(win, tid, step + 1))
            else:
                win.destroy()
                if self.toast_win is win:
                    self.toast_win = None
        except Exception:
            pass

    # --- ЭКВАЛАЙЗЕР ---
    def build_visualizer(self, parent):
        t = self.current_theme
        self.viz_frame = ctk.CTkFrame(parent, height=72, corner_radius=16, fg_color=t["panel"])
        self.viz_frame.grid(row=2, column=0, sticky="ew", pady=(15, 0))
        self.viz_frame.pack_propagate(False)

        self.viz_canvas = tk.Canvas(self.viz_frame, bg=t["panel"], highlightthickness=0, bd=0)
        self.viz_canvas.pack(fill="both", expand=True, padx=18, pady=12)
        self.viz_canvas.bind("<Configure>", self._viz_resize)
        self._rebuild_viz_palette()

    def _rebuild_viz_palette(self):
        """32 готовых цвета вместо расчёта цвета для каждого столбика в каждом кадре."""
        t = self.current_theme
        c_panel, c_accent, c_hover = (_hex_to_rgb(t[k]) for k in ("panel", "accent", "accent_hover"))
        self._viz_palette = [
            _mix(c_panel, _mix_rgb(c_accent, c_hover, k / 31), 0.30 + 0.70 * min(1.0, (k / 31) * 1.4))
            for k in range(32)
        ]
        self._viz_idle_color = _mix(c_panel, c_accent, 0.25)
        self._viz_colors = [None] * len(self.viz_items)

    def _viz_resize(self, event):
        canvas = self.viz_canvas
        canvas.delete("all")
        self.viz_items = []
        # Чем шире окно, тем больше столбиков — на весь экран они не становятся толстыми
        n = max(24, min(120, int(event.width / 20)))
        self.viz_count = n
        self.viz_levels = [0.0] * n
        slot = max(event.width, 1) / n
        bw = max(2.0, slot * 0.55)
        for i in range(n):
            x = slot * (i + 0.5)
            item = canvas.create_line(
                x, event.height / 2, x, event.height / 2,
                width=bw, capstyle=tk.ROUND, fill=self.current_theme["panel"]
            )
            self.viz_items.append(item)
        self._viz_heights = [0.0] * n
        self._viz_colors = [None] * n

    def animate_visualizer(self):
        delay = 250
        try:
            # "zoomed" — окно развёрнуто на весь экран; прячем анимацию только в трее / свёрнутом окне
            if self.state() not in ("withdrawn", "iconic") and self.active_frame_name == "library" and self.viz_items and self._viz_palette:
                playing = self.is_playing
                delay = 33 if playing else 90
                canvas = self.viz_canvas
                W, H = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
                n = len(self.viz_items)
                slot = W / n
                bw = max(2.0, slot * 0.55)
                cy = H / 2
                now = time.time()
                beat = max(0.0, math.sin(now * 4.2)) ** 3
                palette = self._viz_palette

                for i, item in enumerate(self.viz_items):
                    if playing:
                        pos = i / max(n - 1, 1)
                        envelope = 1 - 0.55 * abs(pos - 0.5) * 2
                        wave = 0.5 + 0.5 * math.sin(now * 5.0 + i * 0.6) * math.sin(now * 2.3 + i * 0.21)
                        target = envelope * (0.22 + 0.5 * wave * random.uniform(0.55, 1.0) + 0.18 * beat)
                    else:
                        target = 0.06 + 0.03 * math.sin(now * 1.5 + i * 0.4)

                    lvl = self.viz_levels[i] + (target - self.viz_levels[i]) * 0.3
                    lvl = max(0.0, min(1.0, lvl))
                    self.viz_levels[i] = lvl

                    h = max(bw, lvl * H * 0.95)
                    if abs(h - self._viz_heights[i]) >= 0.6:       # не трогаем холст, если ничего не изменилось
                        self._viz_heights[i] = h
                        half = max(0.0, h / 2 - bw / 2)
                        x = slot * (i + 0.5)
                        canvas.coords(item, x, cy - half, x, cy + half)

                    color = palette[int(lvl * 31)] if playing else self._viz_idle_color
                    if color != self._viz_colors[i]:
                        self._viz_colors[i] = color
                        canvas.itemconfigure(item, fill=color)
        except Exception:
            pass
        self.after(delay, self.animate_visualizer)

    def extract_cover(self, path):
        """Достаёт встроенную обложку из MP3 (ID3-тег APIC). Если её нет — вернёт None."""
        if ID3 is None or not path.lower().endswith(".mp3"):
            return None
        try:
            tags = ID3(path)
            for key in tags.keys():
                if key.startswith("APIC"):
                    img = Image.open(io.BytesIO(tags[key].data)).convert("RGB")
                    return round_image(img, 104, 22)
        except Exception:
            pass
        return None

    def _set_cover(self, path):
        pil = self.extract_cover(path)
        self.current_cover_pil = pil
        if pil is not None:
            self.cover_ctk = ctk.CTkImage(light_image=pil, dark_image=pil, size=(48, 48))
        else:
            self.cover_ctk = self.logo_cover      # нет обложки — показываем наушники из icon.ico
        self.lbl_cover_art.configure(image=self.cover_ctk)

    def play_song(self, index, start_pos=0):
        if not (0 <= index < len(self.playlist)):
            return

        old = self.current_index
        self.current_index = index
        file_path = self.playlist[index]

        if old != -1 and old != index and old < len(self.song_widgets):
            self._style_row(old, playing=False)
        if index < len(self.song_widgets):
            self._style_row(index, playing=True)

        title = os.path.splitext(os.path.basename(file_path))[0]
        self.lbl_now_playing.configure(text=title if len(title) < 32 else title[:29] + "...")
        self._set_cover(file_path)

        self.song_length = self.get_track_length(file_path)
        self.slider_progress.configure(to=max(self.song_length, 1))
        self.slider_progress.set(start_pos)
        self.lbl_total_time.configure(text=fmt_time(self.song_length))
        self.lbl_curr_time.configure(text=fmt_time(start_pos))

        self.play_offset = float(start_pos)
        self._last_sec = -1
        try:
            music.load(file_path)
            music.play(start=start_pos)
        except Exception as e:
            print(f"Не удалось воспроизвести {file_path}: {e}")
        self.is_playing = True
        self.btn_play.configure(text="❚❚")

    def play_pause(self):
        if not self.playlist:
            return
        if self.current_index == -1:
            self.play_song(0)
            return

        if self.is_playing:
            music.pause()
            self.btn_play.configure(text="▶")
        else:
            music.unpause()
            self.btn_play.configure(text="❚❚")
        self.is_playing = not self.is_playing

    def on_seek(self, value):
        """Пока тянем ползунок — только обновляем время, звук не дёргаем."""
        self._seeking = True
        self.lbl_curr_time.configure(text=fmt_time(float(value)))

    def _on_seek_release(self, _event=None):
        """Перематываем один раз — когда ползунок отпустили (раньше трек «заикался» при перетаскивании)."""
        if not self._seeking:
            return
        self._seeking = False
        if self.current_index != -1 and self.song_length > 0:
            pos = float(self.slider_progress.get())
            music.play(start=pos)
            self.play_offset = pos
            self._last_sec = -1
            if not self.is_playing:
                music.pause()

    def update_progress(self):
        try:
            if self.is_playing and not self._seeking and audio_ok():
                if self.song_length > 0:
                    pos = min(self.play_offset + max(0, music.get_pos()) / 1000.0, self.song_length)
                    self.slider_progress.set(pos)
                    sec = int(pos)
                    if sec != self._last_sec:
                        self._last_sec = sec
                        self.lbl_curr_time.configure(text=fmt_time(sec))
                if not music.get_busy():          # трек закончился сам
                    self.next_song()
        except Exception as e:
            print(f"Ошибка прогресса: {e}")
        self.after(250, self.update_progress)

    # --- УМНЫЙ РАНДОМ (SMART SHUFFLE - БЕЗ ПОВТОРОВ) ---
    def reset_shuffle_queues(self):
        if not self.playlist:
            self.shuffled_queue = []
            return
        self.shuffled_queue = list(range(len(self.playlist)))
        random.shuffle(self.shuffled_queue)
        if self.current_index in self.shuffled_queue and len(self.shuffled_queue) > 1:
            self.shuffled_queue.remove(self.current_index)
            self.shuffled_queue.append(self.current_index)

    def next_song(self):
        if not self.playlist:
            return
        if self.is_looped:
            self.play_song(self.current_index)
            return

        if self.is_shuffled:
            if not self.shuffled_queue:
                self.reset_shuffle_queues()
            
            nxt = self.shuffled_queue.pop(0)
            self.shuffle_history.append(self.current_index)
            self.play_song(nxt)
        else:
            nxt = (self.current_index + 1) % len(self.playlist)
            self.play_song(nxt)

    def prev_song(self):
        if not self.playlist:
            return

        if self.is_shuffled and self.shuffle_history:
            prev = self.shuffle_history.pop()
            self.shuffled_queue.insert(0, self.current_index)
            self.play_song(prev)
        else:
            prev = (self.current_index - 1) % len(self.playlist)
            self.play_song(prev)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        if self.is_shuffled:
            self.reset_shuffle_queues()
        self.btn_shuffle.configure(text_color=self.current_theme["accent"] if self.is_shuffled else self.current_theme["subtext"])

    def toggle_loop(self):
        self.is_looped = not self.is_looped
        self.btn_loop.configure(text_color=self.current_theme["accent"] if self.is_looped else self.current_theme["subtext"])

    def change_theme(self, choice):
        self.current_theme = self.themes[choice]
        self.apply_theme()
        self.save_app_settings()

    def apply_theme(self):
        t = self.current_theme
        self.configure(fg_color=t["bg"])
        self.top_bar.configure(fg_color="transparent")
        self.sidebar.configure(fg_color=t["panel"])

        self.lbl_title.configure(text_color=t["text"])
        self.lbl_title2.configure(text_color=t["accent"])
        self.btn_menu.configure(text_color=t["text"])

        self.update_sidebar_colors()

        self.song_listbox.configure(fg_color=t["panel"])
        self.controls_frame.configure(fg_color=t["panel"])
        self.btn_add.configure(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000")
        self.search_entry.configure(
            fg_color=t["card"], border_color=t["accent"], text_color="#FFFFFF",
            placeholder_text_color="#FFFFFF"
        )
        self._show_search_placeholder()
        self.count_pill.configure(fg_color=t["card"], text_color=t["accent"])

        self.lbl_cover_art.configure(fg_color=t["card"])
        self.btn_play.configure(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000")
        self.btn_prev.configure(text_color=t["text"])
        self.btn_next.configure(text_color=t["text"])
        self.btn_shuffle.configure(text_color=t["accent"] if self.is_shuffled else t["subtext"])
        self.btn_loop.configure(text_color=t["accent"] if self.is_looped else t["subtext"])
        self.slider_progress.configure(progress_color=t["accent"], button_color=t["accent"], button_hover_color=t["accent_hover"])

        self.viz_frame.configure(fg_color=t["panel"])
        self.viz_canvas.configure(bg=t["panel"])
        self._rebuild_viz_palette()

        if "albums" in self.frames:
            self.albums_scroll.configure(fg_color=t["panel"])
            self.btn_create_album.configure(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000")
            self.render_albums()

        if "settings" in self.frames:
            self.slider_volume.configure(progress_color=t["accent"], button_color=t["accent"], button_hover_color=t["accent_hover"])
            for btn in self.bind_buttons.values():
                btn.configure(fg_color=t["accent"], hover_color=t["accent_hover"], text_color="#000000")
            self.sw_toast.configure(progress_color=t["accent"], button_hover_color=t["accent_hover"])
            self.btn_reset_binds.configure(border_color=t["accent"], text_color=t["accent"], hover_color=t["card_hover"])
            self.lbl_bind_hint.configure(text_color=t["subtext"])
            for card in self.settings_cards:
                card.configure(fg_color=t["panel"])
            self.theme_menu.configure(fg_color=t["accent"], button_color=t["accent"], button_hover_color=t["accent_hover"], text_color="#000000")
            self.lang_menu.configure(fg_color=t["accent"], button_color=t["accent"], button_hover_color=t["accent_hover"], text_color="#000000")

        for i, (frame, btn) in enumerate(self.song_widgets):
            playing = (i == self.current_index)
            frame.configure(fg_color=t["card_hover"] if playing else t["card"])
            btn.configure(hover_color=t["card_hover"], text_color=t["accent"] if playing else t["text"])
            if i < len(self.song_dur_labels):
                self.song_dur_labels[i].configure(text_color=t["subtext"])

    def minimize_to_tray(self):
        self.withdraw()
        if self.tray_icon is not None:
            return
        try:
            import pystray
        except Exception as e:
            print(f"Трей недоступен: {e}")
            self.deiconify()
            return

        menu = pystray.Menu(
            pystray.MenuItem(self.tr("open_app"), lambda: self.hotkey_queue.put(("restore",)), default=True),
            pystray.MenuItem(self.tr("exit_app"), lambda: self.hotkey_queue.put(("quit",)))
        )
        tray_img = self.icon_img.resize((64, 64), LANCZOS)
        self.tray_icon = pystray.Icon("SoundFish", tray_img, "SoundFish Studio", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def restore_from_tray(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.deiconify()

    def quit_app(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self._settings_job is not None:
            try:
                self.after_cancel(self._settings_job)
            except Exception:
                pass
            self._settings_job = None
        self.save_app_settings()
        if audio_ok():
            try:
                music.stop()
            except Exception:
                pass
        self.unregister_hotkeys()
        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    try:
        app = SoundFishApp()
        app.mainloop()
    except Exception as e:
        import traceback
        print("Произошла ошибка при запуске:\n")
        traceback.print_exc()
        input("\nНажмите Enter, чтобы закрыть окно...")