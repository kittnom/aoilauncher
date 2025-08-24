import sys
import os
import ctypes
from ctypes import wintypes
import winreg
import win32com.client
import webbrowser
import subprocess
import re
import math
import time
from datetime import datetime
import threading
from typing import List, Dict, Tuple, Optional
import hashlib
import base64
import uuid
import secrets
import string
import requests
import shutil
import tempfile
from pathlib import Path
import sqlite3
import pickle
import configparser
from collections import deque

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QMenu,
    QSystemTrayIcon,
    QFrame,
    QScrollArea,
    QTextEdit,
    QSplitter,
    QComboBox,
    QSlider,
    QCheckBox,
    QTabWidget,
    QGroupBox,
    QProgressBar,
    QMessageBox,
    QFileDialog,
    QColorDialog,
    QFontDialog,
    QStackedWidget,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QSize, QTimer, QThread, pyqtSignal, QPropertyAnimation, 
    QEasingCurve, QRect, QPoint, QSettings, QStandardPaths,
    QMimeData, QUrl, QFileSystemWatcher, QProcess, QMutex
)
from PyQt6.QtGui import (
    QPalette, QColor, QIcon, QPixmap, QImage, QFont, QPainter,
    QLinearGradient, QBrush, QPen, QCursor, QClipboard, QAction,
    QShortcut, QKeySequence, QDrag, QMovie
)


# ---------------- Debug switch ----------------
DEBUG = True  # Enabled for crash analysis


def log(*a):
    if DEBUG:
        print(*a)


def debug_print(*a):
    """Safe debug function"""
    if DEBUG:
        try:
            print("[DEBUG]", *a)
        except Exception:
            pass


# ---------------- Win32 structures & constants ----------------
class SHFILEINFO(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTR = 0x000000010
SHGFI_SYSICONINDEX = 0x00004000
SHGFI_LINKOVERLAY = 0x000008000
FILE_ATTRIBUTE_NORMAL = 0x00000080

SHGetFileInfo = ctypes.windll.shell32.SHGetFileInfoW
SHGetFileInfo.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(SHFILEINFO),
    ctypes.c_uint,
    wintypes.UINT,
]
SHGetFileInfo.restype = wintypes.DWORD

ExtractIconEx = ctypes.windll.shell32.ExtractIconExW
ExtractIconEx.argtypes = [
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.HICON),
    wintypes.UINT,
]
ExtractIconEx.restype = wintypes.UINT

# IImageList / SHGetImageList (Explorer system imagelist)
try:
    SHGetImageList = ctypes.windll.shell32.SHGetImageList
    SHGetImageList.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_byte),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    SHGetImageList.restype = ctypes.c_long
except AttributeError:
    SHGetImageList = None


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def IID_IImageList():
    return GUID(
        0x46EB5926,
        0x582E,
        0x4017,
        (ctypes.c_ubyte * 8)(0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50),
    )


class IImageList(ctypes.Structure):
    pass


class IImageListVtbl(ctypes.Structure):
    _fields_ = [
        (
            "QueryInterface",
            ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ),
        ),
        ("AddRef", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
        ("Release", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
        ("Add", ctypes.c_void_p),
        ("ReplaceIcon", ctypes.c_void_p),
        ("SetOverlayImage", ctypes.c_void_p),
        ("Replace", ctypes.c_void_p),
        ("AddMasked", ctypes.c_void_p),
        ("Draw", ctypes.c_void_p),
        ("Remove", ctypes.c_void_p),
        (
            "GetIcon",
            ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(wintypes.HICON),
            ),
        ),
    ]


IImageList._fields_ = [("lpVtbl", ctypes.POINTER(IImageListVtbl))]

SHIL_SMALL = 0
SHIL_LARGE = 1
ILD_TRANSPARENT = 0x00000001


# ---------------- Cache ----------------
_ICON_CACHE = {}  # key: (path.lower(), small, tag) -> QIcon


# ---------------- Helpers ----------------
def _qicon_from_hicon(hicon: wintypes.HICON) -> QIcon:
    """Create QIcon from HICON - memory leak prevention"""
    if not hicon:
        return QIcon()
    try:
        image = QImage.fromHICON(hicon)
        # Always clean up HICON
        try:
            ctypes.windll.user32.DestroyIcon(hicon)
        except Exception:
            pass
        
        if image.isNull():
            return QIcon()
        return QIcon(QPixmap.fromImage(image))
    except Exception as e:
        debug_print(f"_qicon_from_hicon error: {e}")
        # Try to clean up HICON
        try:
            ctypes.windll.user32.DestroyIcon(hicon)
        except Exception:
            pass
        return QIcon()


def _parse_icon_location(loc: str):
    if not loc:
        return None, 0
    try:
        loc = os.path.expandvars(loc.strip().strip('"'))
        if "," in loc:
            pth, idx = loc.rsplit(",", 1)
            try:
                return pth.strip().strip('"'), int(idx.strip())
            except ValueError:
                return pth.strip().strip('"'), 0
        return loc, 0
    except Exception:
        return None, 0


def resolve_lnk(path: str):
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        sh = shell.CreateShortcut(path)
        target = sh.TargetPath
        icon_path, icon_index = _parse_icon_location(sh.IconLocation)
        if not icon_path and target:
            icon_path, icon_index = target, 0
        return target, icon_path, icon_index
    except Exception as e:
        log("resolve_lnk error:", e)
        return None, None, 0


# ---------------- Explorer system image list ----------------
def _get_system_imagelist_handle(small: bool):
    if SHGetImageList is None:
        return None
    size_flag = SHIL_SMALL if small else SHIL_LARGE
    pimagelist = ctypes.c_void_p()
    hr = SHGetImageList(size_flag, ctypes.byref(IID_IImageList()), ctypes.byref(pimagelist))
    if hr != 0 or not pimagelist:
        return None
    return ctypes.cast(pimagelist, ctypes.POINTER(IImageList))


def _get_sys_icon_index(path: str) -> int:
    sfi = SHFILEINFO()
    ok = SHGetFileInfo(
        path,
        0,
        ctypes.byref(sfi),
        ctypes.sizeof(sfi),
        SHGFI_SYSICONINDEX | SHGFI_LINKOVERLAY,
    )
    return sfi.iIcon if ok else -1


def _icon_from_system_imagelist(path: str, small: bool) -> QIcon:
    try:
        key = (path.lower(), small, "sys")
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
        idx = _get_sys_icon_index(path)
        if idx < 0:
            return QIcon()
        iml = _get_system_imagelist_handle(small)
        if not iml or not iml.contents:
            return QIcon()
        hicon = wintypes.HICON()
        hr = iml.contents.lpVtbl.contents.GetIcon(iml, idx, ILD_TRANSPARENT, ctypes.byref(hicon))
        if hr != 0 or not hicon:
            return QIcon()
        icon = _qicon_from_hicon(hicon)
        if not icon.isNull():
            _ICON_CACHE[key] = icon
        return icon
    except Exception as e:
        log("system imagelist err:", e)
        return QIcon()


# ---------------- Low-level extraction ----------------
def _extract_icon_from_module(module_path: str, index: int | None, small: bool) -> QIcon:
    try:
        if not os.path.exists(module_path):
            return QIcon()

        if index is not None:
            Large = (wintypes.HICON * 1)()
            Small = (wintypes.HICON * 1)()
            got = ExtractIconEx(module_path, int(index), Large, Small, 1)
            if got:
                h = Small[0] if small else Large[0]
                if h:
                    ico = _qicon_from_hicon(h)
                    if not ico.isNull():
                        return ico

        try:
            count = ExtractIconEx(module_path, -1, None, None, 0)
        except Exception:
            count = 0

        if not count or count <= 0:
            flags = SHGFI_ICON | (SHGFI_SMALLICON if small else SHGFI_LARGEICON)
            sfi = SHFILEINFO()
            res = SHGetFileInfo(module_path, 0, ctypes.byref(sfi), ctypes.sizeof(sfi), flags)
            if res and sfi.hIcon:
                return _qicon_from_hicon(sfi.hIcon)
            return QIcon()

        for i in range(min(count, 10)):
            Large = (wintypes.HICON * 1)()
            Small = (wintypes.HICON * 1)()
            got = ExtractIconEx(module_path, i, Large, Small, 1)
            if got:
                h = Small[0] if small else Large[0]
                if h:
                    ico = _qicon_from_hicon(h)
                    if not ico.isNull():
                        return ico

    except Exception as e:
        log("extract module err:", e)

    return QIcon()


def _icon_from_existing_file(path: str, small: bool) -> QIcon:
    flags = SHGFI_ICON | (SHGFI_SMALLICON if small else SHGFI_LARGEICON)
    sfi = SHFILEINFO()
    res = SHGetFileInfo(path, 0, ctypes.byref(sfi), ctypes.sizeof(sfi), flags)
    if res and sfi.hIcon:
        return _qicon_from_hicon(sfi.hIcon)
    return QIcon()


def _icon_from_extension(ext: str, small: bool) -> QIcon:
    if not ext.startswith("."):
        ext = "." + ext
    flags = SHGFI_ICON | SHGFI_USEFILEATTR | (SHGFI_SMALLICON if small else SHGFI_LARGEICON)
    sfi = SHFILEINFO()
    res = SHGetFileInfo("dummy" + ext, FILE_ATTRIBUTE_NORMAL, ctypes.byref(sfi), ctypes.sizeof(sfi), flags)
    if res and sfi.hIcon:
        return _qicon_from_hicon(sfi.hIcon)
    return QIcon()


def _registry_default_icon(file_path: str):
    try:
        _, ext = os.path.splitext(file_path)
        if not ext:
            return None, 0
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as k:
            file_type = winreg.QueryValue(k, None)
            if not file_type:
                return None, 0
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{file_type}\\DefaultIcon") as k:
            val = winreg.QueryValue(k, None)
            pth, idx = _parse_icon_location(val)
            return pth, idx
    except Exception:
        return None, 0


# ---------------- Public: icon_from_path ----------------
def icon_from_path(path: str, small: bool = True) -> QIcon:
    try:
        key = (path.lower(), small, "main")
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]

        lower = path.lower()
        exists = os.path.exists(path)

        if lower.endswith(".lnk"):
            target, icon_p, icon_i = resolve_lnk(path)
            debug_print(f"icon_from_path - .lnk: {path} -> target: {target}, icon: {icon_p}")

            # Try to get icon from the .lnk file itself first
            ico = _icon_from_system_imagelist(path, small)
            if not ico.isNull():
                _ICON_CACHE[key] = ico
                return ico

            # Try icon location from .lnk file
            if icon_p and os.path.exists(icon_p):
                ico = _extract_icon_from_module(icon_p, icon_i, small)
                if not ico.isNull():
                    _ICON_CACHE[key] = ico
                    return ico

            # Try target file icon
            if target and os.path.exists(target):
                if target.lower().endswith((".exe", ".dll", ".ico")):
                    ico = _extract_icon_from_module(target, None, small)
                    if not ico.isNull():
                        _ICON_CACHE[key] = ico
                        return ico

                ico = _icon_from_existing_file(target, small)
                if not ico.isNull():
                    _ICON_CACHE[key] = ico
                    return ico

            # Fallback to .lnk extension icon
            ico = _icon_from_extension(".lnk", small)
            if not ico.isNull():
                _ICON_CACHE[key] = ico
                return ico

        if lower.endswith((".exe", ".dll", ".ico")):
            if exists:
                ico = _icon_from_existing_file(path, small)
                if not ico.isNull():
                    _ICON_CACHE[key] = ico
                    return ico
            ico = _extract_icon_from_module(path, None, small)
            if not ico.isNull():
                _ICON_CACHE[key] = ico
                return ico

            _, ext = os.path.splitext(path)
            ico = _icon_from_extension(ext, small)
            _ICON_CACHE[key] = ico
            return ico

        if exists:
            ico = _icon_from_existing_file(path, small)
            if not ico.isNull():
                _ICON_CACHE[key] = ico
                return ico

        reg_p, reg_i = _registry_default_icon(path)
        if reg_p and os.path.exists(reg_p):
            ico = _extract_icon_from_module(reg_p, reg_i, small)
            if not ico.isNull():
                _ICON_CACHE[key] = ico
                return ico

        _, ext = os.path.splitext(path)
        ico = _icon_from_extension(ext, small)
        _ICON_CACHE[key] = ico
        return ico

    except Exception as e:
        debug_print(f"icon_from_path error: {e}")
        return QIcon()


# ---------------- Search worker ----------------
class SearchWorker(QThread):
    results_ready = pyqtSignal(list)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        results = []
        try:
            # Simple, reliable file search
            debug_print(f"Starting simple file search for: {self.query}")
            
            # Search in common locations
            search_locations = [
                os.path.expanduser("~\\Desktop"),
                os.path.expanduser("~\\Downloads"), 
                os.path.expanduser("~\\Documents"),
                os.path.expanduser("~\\OneDrive\\Desktop"),
                os.path.expanduser("~\\OneDrive\\Downloads"),
                os.path.expanduser("~\\OneDrive\\Documents"),
                "C:\\Program Files",
                "C:\\Program Files (x86)",
                "C:\\Users\\Public\\Desktop"
            ]
            
            query_lower = self.query.lower()
            
            for location in search_locations:
                if os.path.exists(location):
                    try:
                        debug_print(f"Searching in: {location}")
                        for root, dirs, files in os.walk(location):
                            # Limit depth for speed
                            if root.count(os.sep) - location.count(os.sep) > 2:
                                continue
                                
                            for file in files:
                                if query_lower in file.lower():
                                    full_path = os.path.join(root, file)
                                    # Only add executable files, shortcuts, and common file types
                                    if (file.lower().endswith(('.exe', '.lnk', '.msi', '.bat', '.cmd')) or
                                        file.lower().startswith(('chrome', 'firefox', 'edge', 'discord', 'steam', 'notepad', 'calc', 'paint', 'word', 'excel', 'powerpoint'))):
                                        results.append((file, full_path))
                                        debug_print(f"Found: {file} -> {full_path}")
                                        
                                        if len(results) >= 25:  # Limit results
                                            break
                            if len(results) >= 25:
                                break
                                
                    except Exception as e:
                        debug_print(f"Error searching {location}: {e}")
                        continue
            
            # Also search for installed programs in registry
            if len(results) < 10:
                try:
                    registry_results = self.registry_search()
                    results.extend(registry_results)
                    debug_print(f"Registry search added {len(registry_results)} results")
                except Exception as e:
                    debug_print(f"Registry search error: {e}")
            
            # Remove duplicates
            seen_paths = set()
            unique_results = []
            for name, path in results:
                if path not in seen_paths:
                    seen_paths.add(path)
                    unique_results.append((name, path))
            
            debug_print(f"Total results found: {len(unique_results)}")
            self.results_ready.emit(unique_results)
            
        except Exception as e:
            debug_print(f"Search error: {e}")
            self.results_ready.emit([])
    
    def registry_search(self):
        """Search Windows registry for installed programs"""
        results = []
        query_lower = self.query.lower()
        
        try:
            import winreg
            
            # Search in common registry locations
            registry_locations = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes\Applications"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
            ]
            
            for hkey, subkey in registry_locations:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                subkey_name = winreg.EnumKey(key, i)
                                if query_lower in subkey_name.lower():
                                    # Try to get the executable path
                                    try:
                                        with winreg.OpenKey(key, subkey_name) as subkey_handle:
                                            try:
                                                exe_path = winreg.QueryValue(subkey_handle, "")
                                                if exe_path and os.path.exists(exe_path):
                                                    results.append((subkey_name, exe_path))
                                            except:
                                                pass
                                    except:
                                        pass
                            except:
                                continue
                except Exception as e:
                    debug_print(f"Registry search error for {subkey}: {e}")
                    continue
                    
        except Exception as e:
            debug_print(f"Registry search error: {e}")
        
        return results


# ---------------- Global Hotkey System ----------------
class GlobalHotkey(QThread):
    hotkey_pressed = pyqtSignal(str)  # Signal emits hotkey string
    
    def __init__(self, parent_launcher=None):
        super().__init__()
        self.is_running = False
        self.parent_launcher = parent_launcher
        self.hotkeys = {}  # Dictionary to store registered hotkeys
        
    def register_hotkey(self, hotkey_string, hotkey_id):
        """Parse and register a hotkey - supports multi-key combinations"""
        try:
            import win32con
            import win32gui
            
            debug_print(f"Attempting to register: {hotkey_string}")
            
            # Parse hotkey string (e.g., "Q+Space", "Ctrl+A+B")
            modifiers = 0
            keys = []
            
            parts = hotkey_string.split('+')
            for part in parts:
                part = part.strip()
                if part == "Ctrl":
                    modifiers |= win32con.MOD_CONTROL
                elif part == "Alt":
                    modifiers |= win32con.MOD_ALT
                elif part == "Shift":
                    modifiers |= win32con.MOD_SHIFT
                elif part == "Win":
                    modifiers |= win32con.MOD_WIN
                else:
                    # This is a regular key
                    key_code = self.get_vk_code(part)
                    if key_code:
                        keys.append(key_code)
            
            # For multi-key combinations (like Q+Space), we can only register the first key with Windows
            # But we'll store the full combination and handle it in our message loop
            if keys:
                primary_key = keys[0]  # Use first non-modifier key
                
                try:
                    win32gui.RegisterHotKey(None, hotkey_id, modifiers, primary_key)
                    self.hotkeys[hotkey_id] = hotkey_string
                    debug_print(f"Registered hotkey: {hotkey_string} -> VK:{primary_key} with modifiers:{modifiers} (ID: {hotkey_id})")
                    return True
                except Exception as register_error:
                    debug_print(f"RegisterHotKey failed for {hotkey_string}: {register_error}")
                    # No fallback registration - if it fails, it fails
                    return False
            
            debug_print(f"Could not register hotkey: {hotkey_string}")
            return False
            
        except Exception as e:
            debug_print(f"Failed to register hotkey {hotkey_string}: {e}")
        
        return False
    
    def get_vk_code(self, key_name):
        """Get Windows VK code for any key name - UNIVERSAL SUPPORT"""
        try:
            import win32con
            
            # Comprehensive VK code mapping - MASSIVE DATABASE
            vk_map = {
                # Letters A-Z
                'A': 0x41, 'B': 0x42, 'C': 0x43, 'D': 0x44, 'E': 0x45, 'F': 0x46,
                'G': 0x47, 'H': 0x48, 'I': 0x49, 'J': 0x4A, 'K': 0x4B, 'L': 0x4C,
                'M': 0x4D, 'N': 0x4E, 'O': 0x4F, 'P': 0x50, 'Q': 0x51, 'R': 0x52,
                'S': 0x53, 'T': 0x54, 'U': 0x55, 'V': 0x56, 'W': 0x57, 'X': 0x58,
                'Y': 0x59, 'Z': 0x5A,
                
                # Numbers 0-9
                '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
                '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
                
                # Function keys F1-F24
                'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74, 'F6': 0x75,
                'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B,
                'F13': 0x7C, 'F14': 0x7D, 'F15': 0x7E, 'F16': 0x7F, 'F17': 0x80, 'F18': 0x81,
                'F19': 0x82, 'F20': 0x83, 'F21': 0x84, 'F22': 0x85, 'F23': 0x86, 'F24': 0x87,
                
                # Control keys
                'Space': 0x20, 'Tab': 0x09, 'Enter': 0x0D, 'Escape': 0x1B,
                'Backspace': 0x08, 'Delete': 0x2E, 'Insert': 0x2D,
                'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
                'Left': 0x25, 'Up': 0x26, 'Right': 0x27, 'Down': 0x28,
                'CapsLock': 0x14, 'NumLock': 0x90, 'ScrollLock': 0x91,
                'PrintScreen': 0x2C, 'Pause': 0x13, 'Menu': 0x5D,
                
                # Punctuation and symbols
                ';': 0xBA, '=': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE, '/': 0xBF,
                '`': 0xC0, '[': 0xDB, '\\': 0xDC, ']': 0xDD, "'": 0xDE,
                
                # Shifted symbols (common ones)
                '!': 0x31, '@': 0x32, '#': 0x33, '$': 0x34, '%': 0x35, '^': 0x36,
                '&': 0x37, '*': 0x38, '(': 0x39, ')': 0x30,
                '_': 0xBD, '+': 0xBB, '{': 0xDB, '}': 0xDD, '|': 0xDC,
                ':': 0xBA, '"': 0xDE, '<': 0xBC, '>': 0xBE, '?': 0xBF, '~': 0xC0,
                
                # Numpad keys
                'Numpad0': 0x60, 'Numpad1': 0x61, 'Numpad2': 0x62, 'Numpad3': 0x63,
                'Numpad4': 0x64, 'Numpad5': 0x65, 'Numpad6': 0x66, 'Numpad7': 0x67,
                'Numpad8': 0x68, 'Numpad9': 0x69, 'NumpadAdd': 0x6B, 'NumpadSubtract': 0x6D,
                'NumpadMultiply': 0x6A, 'NumpadDivide': 0x6F, 'NumpadDecimal': 0x6E,
                'NumpadEnter': 0x0D,
                
                # Media keys
                'VolumeUp': 0xAF, 'VolumeDown': 0xAE, 'VolumeMute': 0xAD,
                'MediaNext': 0xB0, 'MediaPrev': 0xB1, 'MediaStop': 0xB2, 'MediaPlay': 0xB3,
                
                # Browser keys
                'BrowserBack': 0xA6, 'BrowserForward': 0xA7, 'BrowserRefresh': 0xA8,
                'BrowserStop': 0xA9, 'BrowserSearch': 0xAA, 'BrowserFavorites': 0xAB,
                'BrowserHome': 0xAC,
                
                # Additional special keys
                'Sleep': 0x5F, 'Apps': 0x5D, 'Clear': 0x0C, 'Select': 0x29,
                'Execute': 0x2B, 'Help': 0x2F, 'Snapshot': 0x2C,
            }
            
            # Method 1: Direct mapping
            if key_name in vk_map:
                return vk_map[key_name]
            
            # Method 2: Try win32con VK_ constants
            vk_candidates = [
                f'VK_{key_name.upper()}',
                f'VK_{key_name}',
                key_name.upper(),
                key_name
            ]
            
            for candidate in vk_candidates:
                if hasattr(win32con, candidate):
                    vk_code = getattr(win32con, candidate)
                    debug_print(f"Found VK code for {key_name}: {candidate} = {vk_code}")
                    return vk_code
            
            # Method 3: Handle Key### format (from Qt unknown keys)
            if key_name.startswith('Key') and key_name[3:].isdigit():
                vk_code = int(key_name[3:])
                debug_print(f"Using raw VK code for {key_name}: {vk_code}")
                return vk_code
            
            # Method 4: Single character to ASCII
            if len(key_name) == 1:
                vk_code = ord(key_name.upper())
                debug_print(f"ASCII conversion for {key_name}: {vk_code}")
                return vk_code
            
            # Method 5: Try to extract number from name
            import re
            number_match = re.search(r'\d+', key_name)
            if number_match:
                vk_code = int(number_match.group())
                debug_print(f"Extracted VK code from {key_name}: {vk_code}")
                return vk_code
            
            # Method 6: Use a hash-based VK code (fallback)
            vk_code = hash(key_name) % 0xFF
            if vk_code < 0x08 or vk_code > 0xFE:  # Avoid reserved ranges
                vk_code = 0x80 + (hash(key_name) % 0x30)  # Safe range
            
            debug_print(f"Generated fallback VK code for {key_name}: {vk_code}")
            return vk_code
            
        except Exception as e:
            debug_print(f"VK code error for {key_name}: {e}")
            # Ultimate fallback
            return 0x80
        
    def run(self):
        """Monitor global hotkeys"""
        try:
            import win32con
            import win32gui
            
            # Get settings from parent launcher
            if self.parent_launcher and hasattr(self.parent_launcher, 'settings'):
                settings = self.parent_launcher.settings
                
                # Register main launcher hotkey only
                main_hotkey = settings.value("hotkey_global_hotkey", "Ctrl+Space")
                self.register_hotkey(main_hotkey, 1)
            else:
                # Fallback: register default main hotkey
                self.register_hotkey("Ctrl+Space", 1)
            
            self.is_running = True
            debug_print("Global hotkey system started")
            
            try:
                while self.is_running:
                    msg = win32gui.GetMessage(None, 0, 0)
                    if msg[1][1] == win32con.WM_HOTKEY:
                        hotkey_id = msg[1][2]
                        if hotkey_id in self.hotkeys:
                            hotkey_string = self.hotkeys[hotkey_id]
                            debug_print(f"Hotkey triggered: {hotkey_string}")
                            self.hotkey_pressed.emit(hotkey_string)
                    time.sleep(0.01)
            finally:
                # Unregister all hotkeys
                for hotkey_id in self.hotkeys:
                    try:
                        win32gui.UnregisterHotKey(None, hotkey_id)
                        debug_print(f"Unregistered hotkey ID: {hotkey_id}")
                    except Exception as e:
                        debug_print(f"Error unregistering hotkey {hotkey_id}: {e}")
                
        except ImportError:
            debug_print("Global hotkey not available - win32gui not found")
        except Exception as e:
            debug_print(f"Global hotkey error: {e}")
    
    def stop(self):
        """Stop hotkey monitoring"""
        self.is_running = False
        self.quit()


# Clipboard Manager disabled - no clipboard history tracking
class ClipboardManager:
    def __init__(self):
        pass  # Disabled
    
    def on_clipboard_changed(self):
        """Clipboard monitoring disabled"""
        pass
    
    def save_history(self):
        """Clipboard saving disabled"""
        pass
    
    def load_history(self):
        """Clipboard loading disabled"""
        pass
    
    def get_history(self) -> List[Tuple[str, str, str]]:
        """Clipboard history disabled - returns empty list"""
        return []


# ---------------- Text Processor ----------------
class TextProcessor:
    @staticmethod
    def process_text(text: str, operation: str) -> str:
        """Process text with various operations"""
        try:
            if operation == "uppercase":
                return text.upper()
            elif operation == "lowercase":
                return text.lower()
            elif operation == "title":
                return text.title()
            elif operation == "reverse":
                return text[::-1]
            elif operation == "base64_encode":
                return base64.b64encode(text.encode()).decode()
            elif operation == "base64_decode":
                return base64.b64decode(text.encode()).decode()
            elif operation == "url_encode":
                import urllib.parse
                return urllib.parse.quote(text)
            elif operation == "url_decode":
                import urllib.parse
                return urllib.parse.unquote(text)
            elif operation == "md5":
                return hashlib.md5(text.encode()).hexdigest()
            elif operation == "sha256":
                return hashlib.sha256(text.encode()).hexdigest()
            elif operation == "word_count":
                return f"Words: {len(text.split())}, Characters: {len(text)}"
            elif operation == "remove_spaces":
                return text.replace(" ", "")
            elif operation == "remove_newlines":
                return text.replace("\n", " ").replace("\r", "")
            elif operation == "json_format":
                import json
                return json.dumps(json.loads(text), indent=2)
            else:
                return text
        except Exception as e:
            return f"Error: {e}"
    
    @staticmethod
    def generate_password(length: int = 16, include_symbols: bool = True) -> str:
        """Generate secure password"""
        chars = string.ascii_letters + string.digits
        if include_symbols:
            chars += "!@#$%^&*"
        return ''.join(secrets.choice(chars) for _ in range(length))
    
    @staticmethod
    def generate_uuid() -> str:
        """Generate UUID"""
        return str(uuid.uuid4())


# ---------------- API Integrations ----------------
class APIIntegrator:
    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 5  # 5 second timeout
    
    def get_weather(self, city: str) -> Optional[str]:
        """Get weather information"""
        try:
            # Using OpenWeatherMap (you'll need API key)
            api_key = "your_api_key_here"  # Users should add their own
            if api_key == "your_api_key_here":
                return f"Weather for {city}: API key required for OpenWeatherMap"
            
            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
            response = self.session.get(url)
            data = response.json()
            
            if response.status_code == 200:
                temp = data['main']['temp']
                desc = data['weather'][0]['description']
                return f"{city}: {temp}°C, {desc}"
            else:
                return f"Weather error: {data.get('message', 'Unknown error')}"
        except Exception as e:
            return f"Weather error: {e}"
    
    def convert_currency(self, amount: float, from_curr: str, to_curr: str) -> Optional[str]:
        """Convert currency"""
        try:
            # Using a free API (no key required)
            url = f"https://api.exchangerate-api.com/v4/latest/{from_curr.upper()}"
            response = self.session.get(url)
            data = response.json()
            
            if to_curr.upper() in data['rates']:
                rate = data['rates'][to_curr.upper()]
                result = amount * rate
                return f"{amount} {from_curr.upper()} = {result:.2f} {to_curr.upper()}"
            else:
                return f"Currency {to_curr} not found"
        except Exception as e:
            return f"Currency error: {e}"
    
    def get_crypto_price(self, symbol: str) -> Optional[str]:
        """Get cryptocurrency price"""
        try:
            url = f"https://api.coindesk.com/v1/bpi/currentprice/{symbol}.json"
            response = self.session.get(url)
            data = response.json()
            
            if 'bpi' in data and symbol.upper() in data['bpi']:
                price = data['bpi'][symbol.upper()]['rate']
                return f"{symbol.upper()}: {price}"
            else:
                # Fallback to CoinGecko
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
                response = self.session.get(url)
                data = response.json()
                if symbol in data:
                    price = data[symbol]['usd']
                    return f"{symbol.upper()}: ${price}"
                return f"Crypto {symbol} not found"
        except Exception as e:
            return f"Crypto error: {e}"


# ---------------- AI Integration ----------------
class AIAssistant:
    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 10
        
        # Multiple AI service configurations
        self.services = {
            'openai': {
                'url': 'https://api.openai.com/v1/chat/completions',
                'model': 'gpt-3.5-turbo',
                'api_key': 'your_openai_api_key_here'
            },
            'anthropic': {
                'url': 'https://api.anthropic.com/v1/messages',
                'model': 'claude-3-sonnet-20240229',
                'api_key': 'your_anthropic_api_key_here'
            },
            'ollama': {
                'url': 'http://localhost:11434/api/generate',
                'model': 'llama2',
                'api_key': None  # Local Ollama doesn't need API key
            },
            'gemini': {
                'url': 'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent',
                'model': 'gemini-pro',
                'api_key': 'your_gemini_api_key_here'
            }
        }
        
        # Default service
        self.current_service = 'ollama'  # Start with local Ollama
        
        # Load settings
        self.load_ai_settings()
    
    def load_ai_settings(self):
        """Load AI settings from config file"""
        try:
            config = configparser.ConfigParser()
            if os.path.exists('aoi_ai_config.ini'):
                config.read('aoi_ai_config.ini')
                
                for service in self.services:
                    if service in config:
                        if 'api_key' in config[service]:
                            self.services[service]['api_key'] = config[service]['api_key']
                        if 'model' in config[service]:
                            self.services[service]['model'] = config[service]['model']
                        if 'url' in config[service]:
                            self.services[service]['url'] = config[service]['url']
                
                if 'general' in config and 'default_service' in config['general']:
                    self.current_service = config['general']['default_service']
                    
        except Exception as e:
            debug_print(f"AI settings load error: {e}")
    
    def save_ai_settings(self):
        """Save AI settings to config file"""
        try:
            config = configparser.ConfigParser()
            
            # Save service configurations
            for service, settings in self.services.items():
                config[service] = {}
                for key, value in settings.items():
                    if value is not None:
                        config[service][key] = str(value)
            
            # Save general settings
            config['general'] = {'default_service': self.current_service}
            
            with open('aoi_ai_config.ini', 'w') as f:
                config.write(f)
                
        except Exception as e:
            debug_print(f"AI settings save error: {e}")
    
    def query_ollama(self, prompt: str) -> Optional[str]:
        """Query local Ollama instance"""
        try:
            data = {
                "model": self.services['ollama']['model'],
                "prompt": prompt,
                "stream": False
            }
            
            response = self.session.post(self.services['ollama']['url'], json=data)
            if response.status_code == 200:
                result = response.json()
                return result.get('response', '').strip()
            else:
                return f"Ollama error: {response.status_code}"
                
        except requests.exceptions.ConnectionError:
            return "Ollama not running. Install and start Ollama locally."
        except Exception as e:
            return f"Ollama error: {e}"
    
    def query_openai(self, prompt: str) -> Optional[str]:
        """Query OpenAI GPT"""
        try:
            api_key = self.services['openai']['api_key']
            if api_key == 'your_openai_api_key_here':
                return "OpenAI API key required. Use 'ai config openai' to set it up."
            
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            
            data = {
                "model": self.services['openai']['model'],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.7
            }
            
            response = self.session.post(self.services['openai']['url'], headers=headers, json=data)
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content'].strip()
            else:
                return f"OpenAI error: {response.status_code}"
                
        except Exception as e:
            return f"OpenAI error: {e}"
    
    def query_anthropic(self, prompt: str) -> Optional[str]:
        """Query Anthropic Claude"""
        try:
            api_key = self.services['anthropic']['api_key']
            if api_key == 'your_anthropic_api_key_here':
                return "Anthropic API key required. Use 'ai config anthropic' to set it up."
            
            headers = {
                'x-api-key': api_key,
                'Content-Type': 'application/json',
                'anthropic-version': '2023-06-01'
            }
            
            data = {
                "model": self.services['anthropic']['model'],
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            response = self.session.post(self.services['anthropic']['url'], headers=headers, json=data)
            if response.status_code == 200:
                result = response.json()
                return result['content'][0]['text'].strip()
            else:
                return f"Anthropic error: {response.status_code}"
                
        except Exception as e:
            return f"Anthropic error: {e}"
    
    def query_gemini(self, prompt: str) -> Optional[str]:
        """Query Google Gemini"""
        try:
            api_key = self.services['gemini']['api_key']
            if api_key == 'your_gemini_api_key_here':
                return "Gemini API key required. Use 'ai config gemini' to set it up."
            
            url = f"{self.services['gemini']['url']}?key={api_key}"
            
            data = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
            
            response = self.session.post(url, json=data)
            if response.status_code == 200:
                result = response.json()
                return result['candidates'][0]['content']['parts'][0]['text'].strip()
            else:
                return f"Gemini error: {response.status_code}"
                
        except Exception as e:
            return f"Gemini error: {e}"
    
    def query_ai(self, prompt: str, service: str = None) -> str:
        """Query AI with specified or default service"""
        try:
            if service is None:
                service = self.current_service
            
            debug_print(f"Querying AI: {service} - {prompt[:50]}...")
            
            if service == 'ollama':
                return self.query_ollama(prompt)
            elif service == 'openai':
                return self.query_openai(prompt)
            elif service == 'anthropic':
                return self.query_anthropic(prompt)
            elif service == 'gemini':
                return self.query_gemini(prompt)
            else:
                return f"Unknown AI service: {service}"
                
        except Exception as e:
            return f"AI query error: {e}"
    
    def get_smart_suggestions(self, query: str, context: List[str] = None) -> List[str]:
        """Get AI-powered smart suggestions"""
        try:
            context_str = ""
            if context:
                context_str = f"Context: {', '.join(context[:5])}\n"
            
            prompt = f"""
{context_str}User is searching for: "{query}"

Suggest 3-5 relevant completions or related searches. Be concise and practical.
Format as a simple list, one suggestion per line.
Focus on: applications, files, system commands, or useful actions.

Examples:
- If query is "chrom" → suggest "chrome", "chrome.exe", "chrome browser"
- If query is "calc" → suggest "calculator", "calc.exe", "calculate 2+2"
- If query is "note" → suggest "notepad", "notepad.exe", "notes app"
"""
            
            response = self.query_ai(prompt)
            if response and not response.startswith(("error:", "Error:", "API key")):
                suggestions = [line.strip('- ').strip() for line in response.split('\n') if line.strip()]
                return suggestions[:5]
            
            return []
            
        except Exception as e:
            debug_print(f"AI suggestions error: {e}")
            return []
    
    def explain_result(self, item_name: str, item_path: str) -> str:
        """Get AI explanation of a search result"""
        try:
            prompt = f"""
Briefly explain what this file/application is:
Name: {item_name}
Path: {item_path}

Provide a 1-2 sentence explanation of what this is and what it does.
Be helpful and informative but concise.
"""
            
            response = self.query_ai(prompt)
            if response and not response.startswith(("error:", "Error:", "API key")):
                return response
            
            return "AI explanation not available"
            
        except Exception as e:
            return f"Explanation error: {e}"
    
    def process_natural_query(self, query: str) -> Optional[Dict]:
        """Process natural language queries"""
        try:
            prompt = f"""
User query: "{query}"

Analyze this query and determine the best action. Respond in JSON format:
{{
    "action": "search|command|calculation|web_search|ask_ai",
    "target": "specific target or search term",
    "confidence": 0.0-1.0
}}

Examples:
- "open calculator" → {{"action": "command", "target": "calculator", "confidence": 0.9}}
- "what's 15% of 200" → {{"action": "calculation", "target": "15% of 200", "confidence": 0.9}}
- "find chrome" → {{"action": "search", "target": "chrome", "confidence": 0.8}}
- "search google for python" → {{"action": "web_search", "target": "google python", "confidence": 0.9}}
- "what is machine learning" → {{"action": "ask_ai", "target": "what is machine learning", "confidence": 0.7}}
"""
            
            response = self.query_ai(prompt)
            if response and response.strip().startswith('{'):
                try:
                    import json
                    return json.loads(response.strip())
                except json.JSONDecodeError:
                    pass
            
            return None
            
        except Exception as e:
            debug_print(f"Natural query processing error: {e}")
            return None


# ---------------- Smart AI Commands ----------------
class AICommands:
    def __init__(self, ai_assistant: AIAssistant):
        self.ai = ai_assistant
    
    def handle_ai_config(self, args: List[str]) -> str:
        """Handle AI configuration commands"""
        if len(args) < 2:
            return "Usage: ai config <service> [key=value]"
        
        service = args[1].lower()
        if service not in self.ai.services:
            return f"Unknown service: {service}. Available: {list(self.ai.services.keys())}"
        
        if len(args) == 2:
            # Show current config
            config = self.ai.services[service]
            return f"{service} config:\n" + "\n".join([f"  {k}: {v}" for k, v in config.items()])
        
        # Set configuration
        for arg in args[2:]:
            if '=' in arg:
                key, value = arg.split('=', 1)
                if key in self.ai.services[service]:
                    self.ai.services[service][key] = value
                    self.ai.save_ai_settings()
                    return f"Set {service}.{key} = {value}"
        
        return "Invalid configuration format. Use key=value"
    
    def handle_ai_switch(self, service: str) -> str:
        """Switch AI service"""
        if service.lower() in self.ai.services:
            self.ai.current_service = service.lower()
            self.ai.save_ai_settings()
            return f"Switched to {service} AI service"
        else:
            return f"Unknown service: {service}. Available: {list(self.ai.services.keys())}"


# ---------------- File Operations ----------------
class FileOperations:
    @staticmethod
    def open_file_location(file_path: str) -> bool:
        """Open file location in explorer"""
        try:
            if os.path.exists(file_path):
                subprocess.run(['explorer', '/select,', file_path])
                return True
            return False
        except Exception as e:
            debug_print(f"Open location error: {e}")
            return False
    
    @staticmethod
    def copy_path_to_clipboard(file_path: str) -> bool:
        """Copy file path to clipboard"""
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(file_path)
            return True
        except Exception as e:
            debug_print(f"Copy path error: {e}")
            return False
    
    @staticmethod
    def delete_file(file_path: str) -> bool:
        """Delete file (move to recycle bin)"""
        try:
            import send2trash
            send2trash.send2trash(file_path)
            return True
        except ImportError:
            # Fallback to permanent delete
            try:
                os.remove(file_path)
                return True
            except Exception as e:
                debug_print(f"Delete error: {e}")
                return False
        except Exception as e:
            debug_print(f"Delete error: {e}")
            return False
    
    @staticmethod
    def get_file_info(file_path: str) -> Dict[str, str]:
        """Get file information"""
        try:
            if not os.path.exists(file_path):
                return {"error": "File not found"}
            
            stat = os.stat(file_path)
            size = stat.st_size
            
            # Format size
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024:
                    size_str = f"{size:.1f} {unit}"
                    break
                size /= 1024
            else:
                size_str = f"{size:.1f} TB"
            
            return {
                "size": size_str,
                "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "type": "File" if os.path.isfile(file_path) else "Directory"
            }
        except Exception as e:
            return {"error": str(e)}


# ---------------- Calculator & Math ----------------
class Calculator:
    @staticmethod
    def evaluate_expression(expr: str) -> Optional[str]:
        """Safely evaluate mathematical expressions"""
        try:
            # Only allow specific characters for security
            allowed_chars = set('0123456789+-*/().% ')
            if not all(c in allowed_chars for c in expr.replace(' ', '')):
                return None
            
            # Common math operations
            expr = expr.replace('×', '*').replace('÷', '/')
            expr = expr.replace('^', '**')  # Power operation
            
            # Safe eval
            result = eval(expr, {"__builtins__": {}}, {
                "sin": math.sin, "cos": math.cos, "tan": math.tan,
                "sqrt": math.sqrt, "log": math.log, "pi": math.pi,
                "e": math.e, "abs": abs, "round": round,
                "pow": pow, "max": max, "min": min
            })
            
            # Format result
            if isinstance(result, float):
                if result.is_integer():
                    return str(int(result))
                else:
                    return f"{result:.6f}".rstrip('0').rstrip('.')
            return str(result)
            
        except Exception:
            return None
    
    @staticmethod
    def parse_percentage(text: str) -> Optional[str]:
        """Calculate percentages"""
        try:
            # "15% of 200" format
            match = re.match(r'(\d+(?:\.\d+)?)%?\s+of\s+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
            if match:
                percentage, number = float(match.group(1)), float(match.group(2))
                result = (percentage / 100) * number
                return f"{percentage}% of {number} = {result:.2f}".rstrip('0').rstrip('.')
            
            # "200 + 15%" format
            match = re.match(r'(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)%', text)
            if match:
                number, percentage = float(match.group(1)), float(match.group(2))
                result = number + (number * percentage / 100)
                return f"{number} + {percentage}% = {result:.2f}".rstrip('0').rstrip('.')
                
            return None
        except Exception:
            return None


# ---------------- Web Search ----------------
class WebSearcher:
    SEARCH_ENGINES = {
        'google': 'https://www.google.com/search?q={}',
        'youtube': 'https://www.youtube.com/results?search_query={}',
        'stackoverflow': 'https://stackoverflow.com/search?q={}',
        'github': 'https://github.com/search?q={}',
        'wikipedia': 'https://en.wikipedia.org/wiki/Special:Search?search={}',
        'translate': 'https://translate.google.com/?text={}',
        'maps': 'https://maps.google.com/maps?q={}'
    }
    
    @staticmethod
    def parse_search(text: str) -> Optional[Tuple[str, str, str]]:
        """Parse search command"""
        text = text.strip().lower()
        
        for engine in WebSearcher.SEARCH_ENGINES:
            if text.startswith(f"{engine} "):
                query = text[len(engine)+1:]
                if query:
                    return engine, query, WebSearcher.SEARCH_ENGINES[engine].format(query.replace(' ', '+'))
        
        # URL check
        if text.startswith(('http://', 'https://', 'www.')):
            if not text.startswith('http'):
                text = 'https://' + text
            return 'url', text, text
            
        return None


# ---------------- System Commands ----------------
class SystemCommands:
    COMMANDS = {
        'shutdown': ('Shutdown Computer', 'shutdown /s /t 1'),
        'restart': ('Restart Computer', 'shutdown /r /t 1'),
        'sleep': ('Sleep Mode', 'rundll32.exe powrprof.dll,SetSuspendState 0,1,0'),
        'lock': ('Lock Computer', 'rundll32.exe user32.dll,LockWorkStation'),
        'logout': ('Log Out', 'shutdown /l'),
        'taskmanager': ('Task Manager', 'taskmgr'),
        'cmd': ('Command Prompt', 'cmd'),
        'powershell': ('PowerShell', 'powershell'),
        'control': ('Control Panel', 'control'),
        'calculator': ('Calculator', 'calc'),
        'notepad': ('Notepad', 'notepad'),
        'paint': ('Paint', 'mspaint'),
        'explorer': ('File Explorer', 'explorer'),
    }
    
    @staticmethod
    def parse_volume(text: str) -> Optional[Tuple[str, str]]:
        """Volume level control"""
        match = re.match(r'volume\s+(\d+)', text.lower())
        if match:
            level = int(match.group(1))
            if 0 <= level <= 100:
                return f'Volume level {level}%', f'nircmd.exe setsysvolume {int(level * 655.35)}'
        return None
    
    @staticmethod
    def execute_command(command: str) -> bool:
        """Execute system command"""
        try:
            debug_print(f"Executing system command: {command}")
            subprocess.Popen(command, shell=True)
            return True
        except Exception as e:
            debug_print(f"System command error: {e}")
            return False


# ---------------- Smart Suggestions ----------------
class SmartSuggestions:
    def __init__(self, settings):
        self.settings = settings
        self.usage_data = self.load_usage_data()
    
    def load_usage_data(self) -> Dict:
        """Load usage data from QSettings"""
        try:
            # Load from QSettings instead of JSON file
            apps_data = self.settings.value("usage_data/apps", {}, type=dict)
            searches_data = self.settings.value("usage_data/searches", {}, type=dict)
            last_used_data = self.settings.value("usage_data/last_used", {}, type=dict)
            
            return {
                "apps": apps_data,
                "searches": searches_data, 
                "last_used": last_used_data
            }
        except Exception as e:
            debug_print(f"Usage data load error: {e}")
            return {"apps": {}, "searches": {}, "last_used": {}}
    
    def save_usage_data(self):
        """Save usage data to QSettings"""
        try:
            # Save to QSettings instead of JSON file
            self.settings.setValue("usage_data/apps", self.usage_data.get("apps", {}))
            self.settings.setValue("usage_data/searches", self.usage_data.get("searches", {}))
            self.settings.setValue("usage_data/last_used", self.usage_data.get("last_used", {}))
            
            # Force sync to ensure data is saved
            self.settings.sync()
            
        except Exception as e:
            debug_print(f"Usage data save error: {e}")
    
    def record_usage(self, item_name: str, item_type: str = "app"):
        """Record usage"""
        try:
            now = datetime.now().isoformat()
            
            if item_type not in self.usage_data:
                self.usage_data[item_type] = {}
            
            if item_name not in self.usage_data[item_type]:
                self.usage_data[item_type][item_name] = {"count": 0, "last_used": now}
            
            self.usage_data[item_type][item_name]["count"] += 1
            self.usage_data[item_type][item_name]["last_used"] = now
            self.usage_data["last_used"][item_name] = now
            
            self.save_usage_data()
        except Exception as e:
            debug_print(f"Usage record error: {e}")
    
    def get_suggestions(self, query: str = "") -> List[Tuple[str, str, int]]:
        """Get smart suggestions"""
        suggestions = []
        try:
            # Most frequently used
            for app_name, data in self.usage_data.get("apps", {}).items():
                if not query or query.lower() in app_name.lower():
                    suggestions.append((app_name, "frequent", data["count"]))
            
            # Recently used
            recent_items = sorted(
                self.usage_data.get("last_used", {}).items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            
            for item_name, _ in recent_items:
                if not query or query.lower() in item_name.lower():
                    suggestions.append((item_name, "recent", 1000))  # High priority
            
            # Time-based suggestions
            current_hour = datetime.now().hour
            if 9 <= current_hour <= 17:  # Work hours
                work_apps = ["outlook", "teams", "excel", "word", "powerpoint", "chrome"]
                for app in work_apps:
                    if not query or query.lower() in app.lower():
                        suggestions.append((app, "work_time", 500))
            else:  # Evening hours
                leisure_apps = ["steam", "discord", "spotify", "vlc", "games"]
                for app in leisure_apps:
                    if not query or query.lower() in app.lower():
                        suggestions.append((app, "leisure_time", 300))
            
            # Sort by priority
            suggestions.sort(key=lambda x: x[2], reverse=True)
            return suggestions[:10]
            
        except Exception as e:
            debug_print(f"Suggestions error: {e}")
            return []


# ---------------- UI ----------------
class LauncherUI(QWidget):
    def __init__(self):
        super().__init__()
        
        # Settings - Initialize first so other components can use it
        self.settings = QSettings("AoiLauncher", "Settings")
        self.theme = self.settings.value("theme", "dark")
        
        self.search_timer = QTimer(singleShot=True)
        self.search_timer.timeout.connect(self.do_search)
        self.current_worker = None
        self.is_closing = False  # Close control
        
        # Core features
        self.calculator = Calculator()
        self.web_searcher = WebSearcher()
        self.system_commands = SystemCommands()
        self.smart_suggestions = SmartSuggestions(self.settings)
        
        # Advanced features
        self.clipboard_manager = ClipboardManager()
        self.text_processor = TextProcessor()
        self.api_integrator = APIIntegrator()
        self.file_operations = FileOperations()
        
        # AI Integration
        self.ai_assistant = AIAssistant()
        self.ai_commands = AICommands(self.ai_assistant)
        
        # Options window
        self.options_window = None
        
        # Global hotkey
        self.global_hotkey = None
        self.setup_global_hotkey()
        
        # Animations
        self.fade_animation = None
        
        debug_print("LauncherUI starting...")
        
        # Check and setup startup on first run
        self.setup_startup_on_first_run()
        
        self.initUI()
    
    def setup_startup_on_first_run(self):
        """Setup launcher to start with Windows on first run"""
        try:
            # Check if this is the first run
            first_run = self.settings.value("first_run", True, type=bool)
            
            if first_run:
                debug_print("First run detected, setting up startup...")
                
                # Add to Windows startup
                if self.add_to_startup():
                    debug_print("Successfully added to Windows startup")
                    # Mark as not first run anymore
                    self.settings.setValue("first_run", False)
                    
                    # Show welcome message
                    QTimer.singleShot(2000, self.show_welcome_message)
                else:
                    debug_print("Failed to add to Windows startup")
                    
        except Exception as e:
            debug_print(f"Startup setup error: {e}")
    
    def add_to_startup(self):
        """Add launcher to Windows startup registry"""
        try:
            import winreg
            
            # Get the current executable path
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                exe_path = sys.executable
            else:
                # Running as Python script
                exe_path = os.path.abspath(sys.argv[0])
                # Convert to .exe if it's a .py file
                if exe_path.endswith('.py'):
                    exe_path = exe_path.replace('.py', '.exe')
            
            # Registry key for current user startup
            startup_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            
            try:
                # Open registry key
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key, 0, winreg.KEY_SET_VALUE) as key:
                    # Add launcher to startup
                    winreg.SetValueEx(key, "AoiLauncher", 0, winreg.REG_SZ, f'"{exe_path}"')
                    debug_print(f"Added to startup: {exe_path}")
                    return True
                    
            except Exception as e:
                debug_print(f"Registry access error: {e}")
                return False
                
        except Exception as e:
            debug_print(f"Add to startup error: {e}")
            return False
    
    def show_welcome_message(self):
        """Show welcome message on first run"""
        try:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Welcome to Aoi Launcher!")
            msg.setText("🎉 Welcome to Aoi Launcher!")
            msg.setInformativeText(
                "Your launcher has been automatically added to Windows startup.\n\n"
                "Features:\n"
                "• Press Ctrl+Space to open\n"
                "• Type 'options' for settings\n"
                "• Use 'ai:' prefix for AI queries\n"
                "• Built-in calculator and web search\n\n"
                "Enjoy using Aoi Launcher! 🚀"
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.setStyleSheet("""
                QMessageBox {
                    background-color: rgba(40,40,40,200);
                    color: #ffffff;
                    font-family: 'Segoe UI', Arial;
                }
                QMessageBox QLabel {
                    color: #ffffff;
                }
                QPushButton {
                    background-color: #4a9eff;
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #5aafff;
                }
            """)
            msg.exec()
            
        except Exception as e:
            debug_print(f"Welcome message error: {e}")
    
    def remove_from_startup(self):
        """Remove launcher from Windows startup"""
        try:
            import winreg
            
            # Registry key for current user startup
            startup_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            
            try:
                # Open registry key
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key, 0, winreg.KEY_SET_VALUE) as key:
                    # Remove launcher from startup
                    winreg.DeleteValue(key, "AoiLauncher")
                    debug_print("Removed from Windows startup")
                    return True
                    
            except Exception as e:
                debug_print(f"Registry access error: {e}")
                return False
                
        except Exception as e:
            debug_print(f"Remove from startup error: {e}")
            return False
    
    def is_in_startup(self):
        """Check if launcher is currently in Windows startup"""
        try:
            import winreg
            
            # Registry key for current user startup
            startup_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            
            try:
                # Open registry key
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key, 0, winreg.KEY_READ) as key:
                    # Try to read the launcher value
                    winreg.QueryValueEx(key, "AoiLauncher")
                    debug_print("Launcher is in Windows startup")
                    return True
                    
            except FileNotFoundError:
                # Value not found
                debug_print("Launcher is not in Windows startup")
                return False
            except Exception as e:
                debug_print(f"Registry read error: {e}")
                return False
                
        except Exception as e:
            debug_print(f"Check startup status error: {e}")
            return False

    def setup_global_hotkey(self):
        """Setup global hotkey system"""
        try:
            self.global_hotkey = GlobalHotkey(self)
            self.global_hotkey.hotkey_pressed.connect(self.handle_global_hotkey)
            self.global_hotkey.start()
        except Exception as e:
            debug_print(f"Global hotkey setup error: {e}")
    
    def handle_global_hotkey(self, hotkey_string):
        """Handle global hotkey - only Ctrl+Space is active"""
        try:
            debug_print(f"Global hotkey received: {hotkey_string}")
            
            # Get saved hotkey settings
            main_hotkey = self.settings.value("hotkey_global_hotkey", "Ctrl+Space")
            
            if hotkey_string == main_hotkey:
                # Main launcher toggle
                self.toggle_launcher()
                
        except Exception as e:
            debug_print(f"Handle global hotkey error: {e}")
    
    def toggle_launcher(self):
        """Toggle launcher visibility"""
        try:
            if self.isVisible():
                self.hide_with_animation()
            else:
                # Always start fresh when showing
                self.show_with_animation()
        except Exception as e:
            debug_print(f"Toggle launcher error: {e}")
    
    def show_with_animation(self):
        """Show launcher with fade animation"""
        try:
            # Completely reset launcher state before showing
            self.reset_launcher_state()
            
            self.show()
            self.raise_()
            self.activateWindow()
            self.search_bar.setFocus()
            self.search_bar.clear()
            
            # Final safety check - ensure results are completely hidden
            if hasattr(self, 'result_list'):
                self.result_list.clear()
                self.result_list.hide()
                self.result_list.setVisible(False)
            
            # Ensure minimal window size
            self.resize(650, 100)
            self.center_on_screen()
            
            # Fade in animation
            self.setWindowOpacity(0.0)
            self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
            self.fade_animation.setDuration(200)
            self.fade_animation.setStartValue(0.0)
            self.fade_animation.setEndValue(1.0)
            self.fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.fade_animation.start()
            
            debug_print("Launcher shown with complete reset - no suggestions possible")
            
        except Exception as e:
            debug_print(f"Show animation error: {e}")
    
    def reset_launcher_state(self):
        """Completely reset launcher to initial state"""
        try:
            # Stop any ongoing searches
            if hasattr(self, 'current_worker') and self.current_worker and self.current_worker.isRunning():
                self.current_worker.quit()
                self.current_worker.wait(100)
                if self.current_worker.isRunning():
                    self.current_worker.terminate()
            
            # Clear search results and ensure they stay hidden
            if hasattr(self, 'result_list'):
                self.result_list.clear()
                self.result_list.hide()
                # Force hide to prevent any display
                self.result_list.setVisible(False)
            
            # Reset window size to minimal
            self.resize(650, 100)
            self.center_on_screen()
            
            # Clear search bar
            if hasattr(self, 'search_bar'):
                self.search_bar.clear()
            
            # Cancel any pending search timers
            if hasattr(self, 'search_timer') and self.search_timer.isActive():
                self.search_timer.stop()
            
            debug_print("Launcher state completely reset - no suggestions will appear")
            
        except Exception as e:
            debug_print(f"Reset launcher state error: {e}")
    
    def hide_with_animation(self):
        """Hide launcher with fade animation"""
        try:
            # Clear any ongoing searches and hide results before hiding
            if hasattr(self, 'current_worker') and self.current_worker and self.current_worker.isRunning():
                self.current_worker.quit()
                self.current_worker.wait(100)
            
            # Hide results and reset size
            self.result_list.hide()
            self.resize(650, 100)
            
            self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
            self.fade_animation.setDuration(150)
            self.fade_animation.setStartValue(1.0)
            self.fade_animation.setEndValue(0.0)
            self.fade_animation.setEasingCurve(QEasingCurve.Type.InCubic)
            self.fade_animation.finished.connect(self.hide)
            self.fade_animation.start()
        except Exception as e:
            debug_print(f"Hide animation error: {e}")
            self.hide()

    def event(self, e):
        if e.type() == e.Type.WindowDeactivate and not self.is_closing:
            self.hide_with_animation()
        return super().event(e)

    def initUI(self):
        self.setWindowTitle("Python Raycast Launcher")
        self.resize(650, 100)  # Start with minimal height - just search bar
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        self.setPalette(pal)

        self.search_bar = QLineEdit(placeholderText="🔍 Aoi Launcher - Calculate, search, ask AI, execute...")
        self.search_bar.setStyleSheet(
            """
            QLineEdit {
                padding: 15px;
                border-radius: 15px;
                background-color: rgba(40,40,40,200);
                color: #fff;
                font-size: 20px;
                font-weight: 500;
                border: 1px solid rgba(255,255,255,0.1);
            }
            QLineEdit:focus {
                border: 1px solid rgba(255,255,255,0.25);
                background-color: rgba(50,50,50,220);
            }
            """
        )
        
        # AI-powered auto-completion and search
        self.search_bar.textChanged.connect(self.on_text_changed)
        # ENTER → seçili öğeyi aç
        self.search_bar.returnPressed.connect(self.launch_selected)

        self.result_list = QListWidget()
        self.result_list.setStyleSheet(
            """
            QListWidget {
                background-color: rgba(28,28,30,180);
                border: none;
                color: #fff;
                font-size: 18px;
                border-radius: 15px;
                padding: 8px;
            }
            QListWidget::item {
                padding: 18px 15px;
                margin: 3px;
                border-radius: 12px;
                min-height: 20px;
            }
            QListWidget::item:hover {
                background-color: rgba(255,255,255,0.1);
            }
            QListWidget::item:selected {
                background-color: rgba(255,255,255,0.2);
            }
            """
        )
        self.result_list.setIconSize(QSize(48, 48))
        self.result_list.itemDoubleClicked.connect(self.launch_item)

        # Context menu for file operations
        self.result_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(self.show_context_menu)

        # ARROW KEYS + ENTER support
        self.result_list.keyPressEvent = self.list_key_press

        font = QFont()
        font.setPointSize(14)
        font.setWeight(QFont.Weight.Medium)
        self.result_list.setFont(font)

        layout = QVBoxLayout()
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.result_list)
        self.setLayout(layout)

        # Initially hide result list - only show search bar
        self.result_list.hide()

        self.center_on_screen()
    
    def on_text_changed(self, text):
        """Handle text changes - optimized for performance"""
        try:
            # If text is empty, hide results and reset to minimal size
            # NEVER show any suggestions or results when empty
            if not text.strip():
                self.result_list.hide()
                self.resize(650, 100)
                self.center_on_screen()
                
                # Stop any ongoing searches immediately
                if hasattr(self, 'current_worker') and self.current_worker and self.current_worker.isRunning():
                    self.current_worker.quit()
                    self.current_worker.wait(100)
                    if self.current_worker.isRunning():
                        self.current_worker.terminate()
                
                return
            
            # Start normal search timer for non-empty text
            self.search_timer.start(140)
            
            # No AI auto-suggestions to prevent performance issues
            # Users can use 'ai:' prefix for AI queries
                
        except Exception as e:
            debug_print(f"Text change error: {e}")
    
    def get_ai_suggestions(self, query: str):
        """Get AI-powered suggestions (disabled for performance)"""
        # Disabled to prevent performance issues
        # Users should use 'ai:' prefix for AI queries
        pass

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) // 2, (screen.height() - size.height()) // 4)

    def format_display_name(self, name: str) -> str:
        visible_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".mp3",
            ".wav",
            ".flac",
            ".mp4",
            ".mkv",
            ".avi",
            ".pdf",
            ".txt",
            ".docx",
            ".pptx",
            ".xlsx",
            ".exe",
            ".lnk",
        }
        _, ext = os.path.splitext(name)
        if ext.lower() in visible_exts:
            return name
        return os.path.splitext(name)[0]

    def populate_results(self, results: list):
        """Safe result population"""
        try:
            self.result_list.clear()
            debug_print(f"populate_results - {len(results)} results received")
            
            for i, (name, path) in enumerate(results):
                try:
                    item = QListWidgetItem()
                    item.setText(self.format_display_name(name))
                    item.setData(Qt.ItemDataRole.UserRole, path)
                    # Prevent app crash from icon loading errors
                    try:
                        item.setIcon(icon_from_path(path, small=True))
                    except Exception as icon_error:
                        debug_print(f"Icon loading error: {icon_error}")
                        item.setIcon(QIcon())  # Empty icon
                    self.result_list.addItem(item)
                    if DEBUG and i < 5:  # Debug first 5 results
                        debug_print(f"populate_results - {i+1}: {name} -> {path}")
                except Exception as e:
                    debug_print(f"populate_results item error: {e}")
                    continue
            
            # Show result list and resize window based on results
            if self.result_list.count() > 0:
                self.result_list.show()
                # Calculate new height: search bar + results + margins
                result_height = min(self.result_list.count() * 70, 400)  # Max 400px for results
                new_height = 100 + result_height + 50  # 100 for search bar, 50 for margins
                self.resize(650, new_height)
                self.center_on_screen()  # Recenter after resize
                
                # Auto-select first item
                self.result_list.setCurrentRow(0)
                debug_print(f"{self.result_list.count()} results found, first item selected, window resized to {new_height}px")
            else:
                # No results - hide list and resize to minimal
                self.result_list.hide()
                self.resize(650, 100)
                
        except Exception as e:
            debug_print(f"populate_results general error: {e}")
            self.result_list.clear()
            self.result_list.hide()
            self.resize(650, 100)

    def do_search(self):
        """Advanced smart search system"""
        try:
            q = self.search_bar.text().strip()
            debug_print(f"Searching for: '{q}' (from search_bar.text())")
            
            if not q:
                debug_print("Query is empty, returning early from do_search.")
                # Empty search - hide results and resize to minimal height
                # NEVER show any suggestions or default results
                self.result_list.clear()  # Clear any existing results
                self.result_list.hide()
                self.resize(650, 100)
                self.center_on_screen()
                
                # Stop any ongoing searches immediately
                if hasattr(self, 'current_worker') and self.current_worker and self.current_worker.isRunning():
                    debug_print("Stopping search worker for empty query...")
                    try:
                        self.current_worker.results_ready.disconnect()
                    except Exception:
                        pass
                    self.current_worker.quit()
                    self.current_worker.wait(100)
                    if self.current_worker.isRunning():
                        self.current_worker.terminate()
                
                return
            
            debug_print(f"Query is NOT empty ('{q}'), proceeding to handle_special_commands.")
            # Check special commands
            special_results = self.handle_special_commands(q)
            if special_results:
                debug_print(f"handle_special_commands returned {len(special_results)} results, populating custom results.")
                self.populate_custom_results(special_results)
                return
            debug_print(f"handle_special_commands returned no results for '{q}', proceeding to normal file search.")
                
            # Normal file search
            # Safely stop old worker
            if self.current_worker and self.current_worker.isRunning():
                debug_print("Stopping old worker...")
                try:
                    self.current_worker.results_ready.disconnect()
                except Exception:
                    pass
                self.current_worker.quit()
                self.current_worker.wait(1000)
                if self.current_worker.isRunning():
                    self.current_worker.terminate()
                    
            self.current_worker = SearchWorker(q)
            self.current_worker.results_ready.connect(self.populate_results)
            self.current_worker.start()
            debug_print("New worker started")
        except Exception as e:
            debug_print(f"do_search error: {e}")
            self.result_list.clear()
    
    def handle_special_commands(self, query: str) -> Optional[List[Dict]]:
        """Handle special commands - MEGA ENHANCED"""
        results = []
        query_lower = query.lower().strip()
        
        # Return None immediately for empty queries - no default suggestions
        if not query_lower:
            return None
        
        # 1. Mathematical calculations
        if any(char in query for char in '+-*/()='):
            calc_result = self.calculator.evaluate_expression(query)
            if calc_result:
                results.append({
                    'type': 'calculation',
                    'title': f"{query} = {calc_result}",
                    'subtitle': 'Calculation result (Press Enter to copy)',
                    'action': 'copy',
                    'data': calc_result
                })
        
        # 2. Percentage calculations
        percentage_result = self.calculator.parse_percentage(query)
        if percentage_result:
            results.append({
                'type': 'percentage',
                'title': percentage_result,
                'subtitle': 'Percentage calculation (Press Enter to copy)',
                'action': 'copy',
                'data': percentage_result
            })
        
        # 3. Text processing commands
        if query_lower.startswith(('encode ', 'decode ', 'hash ', 'text ')):
            parts = query.split(' ', 2)
            if len(parts) >= 3:
                operation = f"{parts[0]}_{parts[1]}"
                text = parts[2]
                
                operations = {
                    'encode_base64': 'base64_encode',
                    'decode_base64': 'base64_decode', 
                    'encode_url': 'url_encode',
                    'decode_url': 'url_decode',
                    'hash_md5': 'md5',
                    'hash_sha256': 'sha256',
                    'text_upper': 'uppercase',
                    'text_lower': 'lowercase',
                    'text_title': 'title',
                    'text_reverse': 'reverse'
                }
                
                if operation in operations:
                    result = self.text_processor.process_text(text, operations[operation])
                    results.append({
                        'type': 'text_processing',
                        'title': result,
                        'subtitle': f'Text processing: {operation.replace("_", " ")}',
                        'action': 'copy',
                        'data': result
                    })
        
        # 4. Generators
        if query_lower == 'generate password' or query_lower.startswith('password'):
            password = self.text_processor.generate_password()
            results.append({
                'type': 'generator',
                'title': password,
                'subtitle': 'Generated secure password (Press Enter to copy)',
                'action': 'copy',
                'data': password
            })
        
        if query_lower == 'generate uuid' or query_lower == 'uuid':
            uuid_str = self.text_processor.generate_uuid()
            results.append({
                'type': 'generator',
                'title': uuid_str,
                'subtitle': 'Generated UUID (Press Enter to copy)',
                'action': 'copy',
                'data': uuid_str
            })
        
        # 5. Clipboard history - DISABLED
        # Clipboard history feature has been disabled
        
        # 6. Weather
        if query_lower.startswith('weather '):
            city = query[8:].strip()
            if city:
                weather_info = self.api_integrator.get_weather(city)
                if weather_info:
                    results.append({
                        'type': 'weather',
                        'title': weather_info,
                        'subtitle': f'Weather information for {city}',
                        'action': 'copy',
                        'data': weather_info
                    })
        
        # 7. Currency conversion
        currency_match = re.match(r'(\d+(?:\.\d+)?)\s+(\w{3})\s+to\s+(\w{3})', query_lower)
        if currency_match:
            amount, from_curr, to_curr = currency_match.groups()
            currency_result = self.api_integrator.convert_currency(float(amount), from_curr, to_curr)
            if currency_result:
                results.append({
                    'type': 'currency',
                    'title': currency_result,
                    'subtitle': 'Currency conversion',
                    'action': 'copy',
                    'data': currency_result
                })
        
        # 8. Cryptocurrency prices
        if query_lower.endswith(' price') or query_lower.endswith(' crypto'):
            crypto = query_lower.replace(' price', '').replace(' crypto', '').strip()
            if crypto:
                crypto_info = self.api_integrator.get_crypto_price(crypto)
                if crypto_info:
                    results.append({
                        'type': 'crypto',
                        'title': crypto_info,
                        'subtitle': f'Cryptocurrency price for {crypto}',
                        'action': 'copy',
                        'data': crypto_info
                    })
        
        # 9. Color tools
        if query_lower.startswith('#') and len(query) == 7:
            # Hex color
            results.append({
                'type': 'color',
                'title': f'Color: {query.upper()}',
                'subtitle': 'Hex color code (Press Enter to copy)',
                'action': 'copy',
                'data': query.upper()
            })
        
        # 10. Web searches
        web_result = self.web_searcher.parse_search(query)
        if web_result:
            engine, search_query, url = web_result
            results.append({
                'type': 'web_search',
                'title': f"{engine.title()}: {search_query}" if engine != 'url' else url,
                'subtitle': f'Search on web: {search_query}' if engine != 'url' else 'Open URL',
                'action': 'open_url',
                'data': url
            })
        
        # 11. System commands
        if query_lower in self.system_commands.COMMANDS:
            desc, cmd = self.system_commands.COMMANDS[query_lower]
            results.append({
                'type': 'system_command',
                'title': desc,
                'subtitle': f'System command: {query_lower}',
                'action': 'system_command',
                'data': cmd
            })
        
        # 12. Volume control
        volume_result = self.system_commands.parse_volume(query)
        if volume_result:
            desc, cmd = volume_result
            results.append({
                'type': 'volume',
                'title': desc,
                'subtitle': 'Set volume level',
                'action': 'system_command',
                'data': cmd
            })
        
        # 13. Options Command
        if query_lower in ['options', 'aoioptions', 'settings', 'preferences']:
            results.append({
                'type': 'options',
                'title': 'Open Options Panel',
                'subtitle': 'Configure launcher settings, hotkeys, and preferences',
                'action': 'open_options',
                'data': 'options'
            })
        
        # 14. Startup Management Commands
        if query_lower in ['startup', 'start with windows', 'auto start']:
            is_in_startup = self.is_in_startup()
            if is_in_startup:
                results.append({
                    'type': 'startup_remove',
                    'title': 'Remove from Windows Startup',
                    'subtitle': 'Launcher will not start automatically with Windows',
                    'action': 'remove_startup',
                    'data': 'startup_remove'
                })
            else:
                results.append({
                    'type': 'startup_add',
                    'title': 'Add to Windows Startup',
                    'subtitle': 'Launcher will start automatically with Windows',
                    'action': 'add_startup',
                    'data': 'startup_add'
                })
        
        # 15. AI Commands
        if query_lower.startswith('ai '):
            ai_parts = query.split(' ', 2)
            
            if len(ai_parts) >= 2:
                if ai_parts[1] == 'config':
                    config_result = self.ai_commands.handle_ai_config(ai_parts)
                    results.append({
                        'type': 'ai_config',
                        'title': config_result,
                        'subtitle': 'AI Configuration',
                        'action': 'copy',
                        'data': config_result
                    })
                
                elif ai_parts[1] == 'switch' and len(ai_parts) >= 3:
                    switch_result = self.ai_commands.handle_ai_switch(ai_parts[2])
                    results.append({
                        'type': 'ai_switch',
                        'title': switch_result,
                        'subtitle': 'AI Service Switch',
                        'action': 'copy',
                        'data': switch_result
                    })
                
                elif ai_parts[1] == 'status':
                    status = f"Current AI: {self.ai_assistant.current_service}"
                    results.append({
                        'type': 'ai_status',
                        'title': status,
                        'subtitle': 'AI Service Status',
                        'action': 'copy',
                        'data': status
                    })
        
        # 16. AI Query Preparation (ai: prefix)
        if query_lower.startswith('ai:'):
            ai_query = query[3:].strip()
            if ai_query:
                results.append({
                    'type': 'ai_query_ready',
                    'title': f"Ask AI: {ai_query}",
                    'subtitle': f'Press Enter to get answer from {self.ai_assistant.current_service}',
                    'action': 'ai_query',
                    'data': ai_query
                })
        
        # 17. Natural language processing for simple commands (no AI calls)
        # Only process very specific patterns without calling AI
        if not results and len(query.split()) == 2:
            words = query_lower.split()
            
            # Simple "open X" patterns
            if words[0] == 'open':
                target = words[1]
                if target in self.system_commands.COMMANDS:
                    desc, cmd = self.system_commands.COMMANDS[target]
                    results.append({
                        'type': 'simple_command',
                        'title': desc,
                        'subtitle': f'Open {target}',
                        'action': 'system_command',
                        'data': cmd
                    })
                else:
                    # Simple search suggestion
                    results.append({
                        'type': 'simple_search',
                        'title': f"Search for: {target}",
                        'subtitle': 'Simple search suggestion',
                        'action': 'search',
                        'data': target
                    })
            
            # Simple "find X" patterns  
            elif words[0] in ['find', 'search']:
                target = words[1]
                results.append({
                    'type': 'simple_search',
                    'title': f"Search for: {target}",
                    'subtitle': 'Simple search suggestion',
                    'action': 'search',
                    'data': target
                })
        
        return results if results else None
    

    
    def populate_custom_results(self, results: List[Dict]):
        """Populate custom results"""
        try:
            self.result_list.clear()
            debug_print(f"populate_custom_results - {len(results)} custom results")
            if len(results) > 0:
                debug_print(f"populate_custom_results - First result: {results[0]}")
                debug_print("populate_custom_results - Call stack trace enabled - this should NOT happen with empty query!")
            
            for i, result in enumerate(results):
                try:
                    item = QListWidgetItem()
                    item.setText(result['title'])
                    item.setData(Qt.ItemDataRole.UserRole, result)
                    
                    # Custom icons
                    icon = QIcon()
                    if result['type'] == 'calculation':
                        # Calculator icon (use system icon)
                        try:
                            icon = icon_from_path("calc.exe", small=True)
                        except:
                            pass
                    elif result['type'] == 'web_search':
                        # Browser icon
                        try:
                            icon = icon_from_path("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", small=True)
                        except:
                            try:
                                icon = icon_from_path("msedge.exe", small=True)
                            except:
                                pass
                    elif result['type'] in ['system_command', 'volume']:
                        # System icon
                        try:
                            icon = icon_from_path("control.exe", small=True)
                        except:
                            pass
                    
                    item.setIcon(icon)
                    self.result_list.addItem(item)
                    
                    if DEBUG and i < 3:
                        debug_print(f"populate_custom_results - {i+1}: {result['title']}")
                        
                except Exception as e:
                    debug_print(f"populate_custom_results item error: {e}")
                    continue
            
            # Show result list and resize window based on results
            if self.result_list.count() > 0:
                self.result_list.show()
                # Calculate new height: search bar + results + margins
                result_height = min(self.result_list.count() * 70, 400)  # Max 400px for results
                new_height = 100 + result_height + 50  # 100 for search bar, 50 for margins
                self.resize(650, new_height)
                self.center_on_screen()  # Recenter after resize
                
                # Select first item
                self.result_list.setCurrentRow(0)
                debug_print(f"{self.result_list.count()} custom results found, window resized to {new_height}px")
            else:
                # No results - hide list and resize to minimal
                self.result_list.hide()
                self.resize(650, 100)
                
        except Exception as e:
            debug_print(f"populate_custom_results general error: {e}")
            self.result_list.clear()
            self.result_list.hide()
            self.resize(650, 100)

    def launch_item(self, item: QListWidgetItem):
        """Advanced item execution system"""
        try:
            data = item.data(Qt.ItemDataRole.UserRole)
            debug_print(f"launch_item - Data: {data}")
            
            # New format: Dictionary (special commands)
            if isinstance(data, dict):
                self.handle_custom_action(data)
                return
            
            # Old format: String (file path)
            path = data
            if path:
                # Special handling for .lnk files
                if path.lower().endswith('.lnk'):
                    debug_print(f"launch_item - Processing .lnk file: {path}")
                    try:
                        # Resolve .lnk file to get target
                        target, icon_path, icon_index = resolve_lnk(path)
                        debug_print(f"launch_item - .lnk target: {target}")
                        
                        if target and os.path.exists(target):
                            # Execute the target file
                            debug_print(f"launch_item - Executing .lnk target: {target}")
                            os.startfile(target)
                            debug_print("launch_item - .lnk target executed successfully!")
                            
                            # Record usage
                            filename = os.path.basename(target)
                            self.smart_suggestions.record_usage(filename, 'apps')
                            
                            if not self.is_closing:
                                self.hide()  # Close yerine hide kullan - arkaplanda kal
                            return
                        else:
                            # Try to execute .lnk file directly with shell
                            debug_print(f"launch_item - Trying to execute .lnk directly")
                            subprocess.Popen(['cmd', '/c', 'start', '', path], shell=True)
                            debug_print("launch_item - .lnk executed with shell!")
                            
                            if not self.is_closing:
                                self.hide()  # Close yerine hide kullan - arkaplanda kal
                            return
                            
                    except Exception as e:
                        debug_print(f"launch_item - .lnk execution error: {e}")
                        # Fall back to normal file execution
                
                # Fix Turkish character issues - comprehensive correction
                corrected_path = path
                # Kullanıcılar -> Users conversion
                corrected_path = corrected_path.replace("C:\\Kullanıcılar", "C:\\Users")
                # Masaüstü -> Desktop conversion
                corrected_path = corrected_path.replace("\\Masaüstü\\", "\\Desktop\\")
                # Belgeler -> Documents conversion  
                corrected_path = corrected_path.replace("\\Belgeler\\", "\\Documents\\")
                # İndirilenler -> Downloads conversion
                corrected_path = corrected_path.replace("\\İndirilenler\\", "\\Downloads\\")
                debug_print(f"launch_item - Corrected Path: {corrected_path}")
                
                # Try alternative paths too
                alternative_paths = []
                if "\\Masaüstü\\" in path:
                    # Alternative paths for Desktop
                    username = path.split("\\")[2]  # kittnom
                    filename = path.split("\\")[-1]
                    alternative_paths.extend([
                        f"C:\\Users\\{username}\\Desktop\\{filename}",
                        f"C:\\Users\\{username}\\OneDrive\\Desktop\\{filename}",
                        f"C:\\Users\\{username}\\OneDrive\\Masaüstü\\{filename}"
                    ])
                
                debug_print(f"launch_item - Alternative paths: {alternative_paths}")
                
                # Try all paths
                all_paths = [corrected_path, path] + alternative_paths
                
                for i, test_path in enumerate(all_paths):
                    debug_print(f"launch_item - Testing ({i+1}/{len(all_paths)}): {test_path}")
                    if os.path.exists(test_path):
                        try:
                            debug_print(f"launch_item - FOUND! Executing: {test_path}")
                            os.startfile(test_path)
                            debug_print("launch_item - Successfully executed!")
                            
                            # Record usage
                            filename = os.path.basename(test_path)
                            self.smart_suggestions.record_usage(filename, 'apps')
                            
                            if not self.is_closing:
                                self.hide()  # Close yerine hide kullan - arkaplanda kal
                            return
                        except Exception as e:
                            debug_print(f"launch_item - Execution error: {e}")
                            continue
                    else:
                        debug_print(f"launch_item - Does not exist: {test_path}")
                
                # Last resort: find file and determine real path
                debug_print("launch_item - Last resort: file search...")
                filename = os.path.basename(path)
                username = path.split("\\")[2] if len(path.split("\\")) > 2 else "kittnom"
                
                # Check common locations
                common_locations = [
                    f"C:\\Users\\{username}\\Desktop",
                    f"C:\\Users\\{username}\\OneDrive\\Desktop",
                    f"C:\\Users\\{username}\\OneDrive\\Masaüstü",
                    f"C:\\Users\\{username}\\Downloads",
                    f"C:\\Users\\{username}\\OneDrive\\İndirilenler",
                    f"C:\\Users\\{username}\\Documents",
                    f"C:\\Users\\{username}\\OneDrive\\Belgeler"
                ]
                
                for location in common_locations:
                    search_path = os.path.join(location, filename)
                    debug_print(f"launch_item - Searching location: {search_path}")
                    if os.path.exists(search_path):
                        try:
                            debug_print(f"launch_item - FILE FOUND! Executing: {search_path}")
                            os.startfile(search_path)
                            debug_print("launch_item - Successfully executed!")
                            if not self.is_closing:
                                self.hide()  # Close yerine hide kullan - arkaplanda kal
                            return
                        except Exception as e:
                            debug_print(f"launch_item - Execution error: {e}")
                
                debug_print(f"launch_item - File '{filename}' not found in any location!")
            else:
                debug_print("launch_item - Path not found!")
                
        except Exception as e:
            debug_print(f"launch_item general error: {e}")
            
        if not self.is_closing:
            self.hide()  # Close yerine hide kullan - arkaplanda kal
    
    def show_context_menu(self, position):
        """Show context menu for file operations"""
        try:
            item = self.result_list.itemAt(position)
            if not item:
                return
            
            data = item.data(Qt.ItemDataRole.UserRole)
            
            # Only show context menu for file paths, not custom commands
            if isinstance(data, str) and os.path.exists(data):
                menu = QMenu(self)
                
                # Open file location
                open_location_action = menu.addAction("📁 Open File Location")
                open_location_action.triggered.connect(lambda: self.file_operations.open_file_location(data))
                
                # Copy path
                copy_path_action = menu.addAction("📋 Copy Path")
                copy_path_action.triggered.connect(lambda: self.file_operations.copy_path_to_clipboard(data))
                
                # File info
                info_action = menu.addAction("ℹ️ Properties")
                info_action.triggered.connect(lambda: self.show_file_info(data))
                
                menu.addSeparator()
                
                # Delete file (if it's a file, not a directory)
                if os.path.isfile(data):
                    delete_action = menu.addAction("🗑️ Delete File")
                    delete_action.triggered.connect(lambda: self.delete_file_with_confirmation(data))
                
                menu.exec(self.result_list.mapToGlobal(position))
                
        except Exception as e:
            debug_print(f"Context menu error: {e}")
    
    def show_file_info(self, file_path: str):
        """Show file information dialog"""
        try:
            info = self.file_operations.get_file_info(file_path)
            
            if "error" in info:
                QMessageBox.warning(self, "File Info", f"Error: {info['error']}")
                return
            
            info_text = f"""
File: {os.path.basename(file_path)}
Path: {file_path}
Size: {info['size']}
Type: {info['type']}
Created: {info['created']}
Modified: {info['modified']}
            """.strip()
            
            QMessageBox.information(self, "File Information", info_text)
            
        except Exception as e:
            debug_print(f"File info error: {e}")
    
    def delete_file_with_confirmation(self, file_path: str):
        """Delete file with confirmation"""
        try:
            reply = QMessageBox.question(
                self, 
                "Delete File", 
                f"Are you sure you want to delete:\n{os.path.basename(file_path)}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                if self.file_operations.delete_file(file_path):
                    debug_print(f"File deleted: {file_path}")
                    # Refresh search results
                    self.do_search()
                else:
                    QMessageBox.warning(self, "Delete Error", "Failed to delete file")
            
        except Exception as e:
            debug_print(f"Delete confirmation error: {e}")
    
    def handle_custom_action(self, data: Dict):
        """Handle custom actions"""
        try:
            action = data.get('action')
            action_data = data.get('data')
            
            debug_print(f"handle_custom_action - Action: {action}, Data: {action_data}")
            
            if action == 'copy':
                # Copy to clipboard
                try:
                    clipboard = QApplication.clipboard()
                    clipboard.setText(str(action_data))
                    debug_print(f"Copied to clipboard: {action_data}")
                except Exception as e:
                    debug_print(f"Clipboard copy error: {e}")
                    
            elif action == 'open_url':
                # Open web URL
                try:
                    webbrowser.open(action_data)
                    debug_print(f"URL opened: {action_data}")
                    # Record web search usage
                    self.smart_suggestions.record_usage(data.get('title', 'web_search'), 'web')
                except Exception as e:
                    debug_print(f"URL open error: {e}")
                    
            elif action == 'system_command':
                # Execute system command
                try:
                    if self.system_commands.execute_command(action_data):
                        debug_print(f"System command successful: {action_data}")
                        # Record system command usage
                        self.smart_suggestions.record_usage(data.get('title', 'system_cmd'), 'system')
                    else:
                        debug_print(f"System command failed: {action_data}")
                except Exception as e:
                    debug_print(f"System command error: {e}")
                    
            elif action == 'search':
                # Start new search
                try:
                    self.search_bar.setText(action_data)
                    self.search_bar.setFocus()
                    return  # Don't close window
                except Exception as e:
                    debug_print(f"Search start error: {e}")
                    
            elif action == 'ai_query':
                # Process AI query on demand
                try:
                    debug_print(f"Processing AI query: {action_data}")
                    
                    # Show loading indicator
                    self.result_list.clear()
                    loading_item = QListWidgetItem("🤖 AI is thinking...")
                    loading_item.setData(Qt.ItemDataRole.UserRole, None)
                    self.result_list.addItem(loading_item)
                    
                    # Process AI query in background
                    QTimer.singleShot(100, lambda: self.process_ai_query_delayed(action_data))
                    return  # Don't close window
                    
                except Exception as e:
                    debug_print(f"AI query error: {e}")
                    
            elif action == 'open_options':
                # Open options window
                try:
                    debug_print("Opening options window...")
                    
                    if self.options_window is None or not self.options_window.isVisible():
                        self.options_window = OptionsWindow(self)
                        self.options_window.show()
                    else:
                        self.options_window.raise_()
                        self.options_window.activateWindow()
                    
                    # Don't close main launcher
                    return
                    
                except Exception as e:
                    debug_print(f"Options window error: {e}")
            
            elif action == 'add_startup':
                # Add to Windows startup
                try:
                    if self.add_to_startup():
                        debug_print("Successfully added to Windows startup")
                        # Show success message
                        QMessageBox.information(self, "Startup", "✅ Launcher added to Windows startup!")
                    else:
                        QMessageBox.warning(self, "Startup Error", "❌ Failed to add to Windows startup")
                except Exception as e:
                    debug_print(f"Add startup error: {e}")
                    QMessageBox.warning(self, "Startup Error", f"❌ Error: {e}")
            
            elif action == 'remove_startup':
                # Remove from Windows startup
                try:
                    if self.remove_from_startup():
                        debug_print("Successfully removed from Windows startup")
                        # Show success message
                        QMessageBox.information(self, "Startup", "✅ Launcher removed from Windows startup!")
                    else:
                        QMessageBox.warning(self, "Startup Error", "❌ Failed to remove from Windows startup")
                except Exception as e:
                    debug_print(f"Remove startup error: {e}")
                    QMessageBox.warning(self, "Startup Error", f"❌ Error: {e}")
            
            # Hide window for most actions (instead of closing)
            if action not in ['search', 'ai_query', 'open_options', 'add_startup', 'remove_startup'] and not self.is_closing:
                self.hide()  # Close yerine hide kullan - arkaplanda kal
                
        except Exception as e:
            debug_print(f"handle_custom_action general error: {e}")
            if not self.is_closing:
                self.hide()  # Close yerine hide kullan - arkaplanda kal
    
    def process_ai_query_delayed(self, query: str):
        """Process AI query in background with loading indicator"""
        try:
            debug_print(f"Starting AI query processing: {query}")
            
            # Get AI response
            ai_response = self.ai_assistant.query_ai(query)
            
            # Clear loading and show result
            self.result_list.clear()
            
            if ai_response and not ai_response.startswith(("error:", "Error:")):
                # Success response
                item = QListWidgetItem()
                
                # Split long responses for better display
                if len(ai_response) > 200:
                    title = ai_response[:180] + "..."
                    subtitle = f"Full answer • {self.ai_assistant.current_service} • Press Enter to copy"
                else:
                    title = ai_response
                    subtitle = f"AI Answer • {self.ai_assistant.current_service} • Press Enter to copy"
                
                item.setText(title)
                item.setData(Qt.ItemDataRole.UserRole, {
                    'type': 'ai_response',
                    'title': title,
                    'subtitle': subtitle,
                    'action': 'copy',
                    'data': ai_response
                })
                
                # AI icon
                try:
                    ai_icon = icon_from_path("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", small=True)
                except:
                    ai_icon = QIcon()
                item.setIcon(ai_icon)
                
                self.result_list.addItem(item)
                
                # Auto-select for easy copying
                self.result_list.setCurrentRow(0)
                
                debug_print(f"AI response ready: {ai_response[:50]}...")
                
            else:
                # Error response
                error_item = QListWidgetItem(f"❌ AI Error: {ai_response}")
                error_item.setData(Qt.ItemDataRole.UserRole, {
                    'type': 'ai_error',
                    'title': f"AI Error: {ai_response}",
                    'subtitle': 'AI service error',
                    'action': 'copy',
                    'data': ai_response
                })
                self.result_list.addItem(error_item)
                debug_print(f"AI error: {ai_response}")
            
        except Exception as e:
            debug_print(f"AI query processing error: {e}")
            
            # Show error in UI
            self.result_list.clear()
            error_item = QListWidgetItem(f"❌ Processing Error: {str(e)}")
            error_item.setData(Qt.ItemDataRole.UserRole, {
                'type': 'processing_error',
                'title': f"Processing Error: {str(e)}",
                'subtitle': 'Internal error',
                'action': 'copy',
                'data': str(e)
            })
            self.result_list.addItem(error_item)

    def launch_selected(self):
        """Safely execute selected item"""
        try:
            debug_print("launch_selected called")
            item = self.result_list.currentItem()
            debug_print(f"currentItem = {item}")
            if item:
                debug_print(f"Item found, executing: {item.text()}")
                self.launch_item(item)
            else:
                debug_print("No selected item found!")
        except Exception as e:
            debug_print(f"launch_selected error: {e}")

    def list_key_press(self, e):
        """Enhanced keyboard event handling - Turkish Enter support"""
        try:
            key = e.key()
            debug_print(f"Key pressed: {key} (0x{key:08x})")
            
            # Navigation keys
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                QListWidget.keyPressEvent(self.result_list, e)
            # ALL POSSIBLE ENTER KEYS (including Turkish)
            elif (key == Qt.Key.Key_Return or 
                  key == Qt.Key.Key_Enter or 
                  key == 70 or  # Turkish keyboard Enter
                  key == 16777220 or  # Qt.Key.Key_Return
                  key == 16777221 or  # Qt.Key.Key_Enter
                  key == 13):  # ASCII Enter
                debug_print("ENTER key detected!")
                self.launch_selected()
            # Escape key
            elif key == Qt.Key.Key_Escape:
                debug_print("ESCAPE key detected!")
                if not self.is_closing:
                    self.hide()  # Close yerine hide kullan - arkaplanda kal
            else:
                QListWidget.keyPressEvent(self.result_list, e)
        except Exception as ex:
            debug_print(f"list_key_press error: {ex}")
            QListWidget.keyPressEvent(self.result_list, e)


    def closeEvent(self, event):
        """Cleanup when application is closing"""
        self.is_closing = True
        debug_print("Application closing...")
        try:
            # Safely stop worker
            if self.current_worker and self.current_worker.isRunning():
                debug_print("Stopping worker...")
                try:
                    self.current_worker.results_ready.disconnect()
                except Exception:
                    pass
                self.current_worker.quit()
                self.current_worker.wait(500)  # Wait 500ms
                if self.current_worker.isRunning():
                    self.current_worker.terminate()
            
            # Stop global hotkey
            if self.global_hotkey and self.global_hotkey.isRunning():
                debug_print("Stopping global hotkey...")
                self.global_hotkey.stop()
                self.global_hotkey.wait(1000)
                if self.global_hotkey.isRunning():
                    self.global_hotkey.terminate()
                    
        except Exception as e:
            debug_print(f"closeEvent error: {e}")
        
        event.accept()


# ---------------- Professional Options/Settings Window ----------------
class OptionsWindow(QWidget):
    def __init__(self, parent_launcher):
        super().__init__()
        self.parent_launcher = parent_launcher
        self.ai_assistant = parent_launcher.ai_assistant
        self.settings = parent_launcher.settings
        
        # Store original values for cancel functionality
        self.original_values = {}
        
        # Window properties
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Setup UI
        self.initUI()
        self.load_current_settings()
        
        # Add shadow and blur effects
        self.setup_window_effects()
    
    def initUI(self):
        """Initialize the professional options UI"""
        self.setWindowTitle("Aoi Launcher - Settings")
        self.resize(900, 700)
        
        # Main container with modern layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create main container widget
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(25,25,35,0.95), stop:1 rgba(35,35,45,0.95));
                border-radius: 20px;
                border: none;
            }
        """)
        
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(30, 25, 30, 25)
        container_layout.setSpacing(25)
        
        # Professional title bar
        title_bar = self.create_title_bar()
        container_layout.addWidget(title_bar)
        
        # Main content area with sidebar
        content_widget = self.create_content_area()
        container_layout.addWidget(content_widget)
        
        # Professional action buttons
        button_bar = self.create_button_bar()
        container_layout.addWidget(button_bar)
        
        # Add container to main layout
        main_layout.addWidget(container)
        self.setLayout(main_layout)
        
        # Apply global styling
        self.apply_professional_styling()
        self.center_on_screen()
    
    def create_title_bar(self):
        """Create professional title bar"""
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(0, 0, 0, 0)
        
        # Icon and title
        icon_title_layout = QHBoxLayout()
        
        # App icon
        icon_label = QLabel("⚙️")
        icon_label.setStyleSheet("""
            QLabel {
                font-size: 28px;
                margin-right: 10px;
                border: none;
                background: transparent;
            }
        """)
        
        # Title text
        title_label = QLabel("Aoi Launcher Settings")
        title_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 24px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Arial';
                border: none;
                background: transparent;
            }
        """)
        
        # Subtitle
        subtitle_label = QLabel("Configure your launcher experience")
        subtitle_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 13px;
                margin-top: 5px;
                font-family: 'Segoe UI', 'Arial';
                border: none;
                background: transparent;
            }
        """)
        
        # Title section
        title_section = QVBoxLayout()
        title_section.setSpacing(2)
        title_text_layout = QHBoxLayout()
        title_text_layout.addWidget(icon_label)
        title_text_layout.addWidget(title_label)
        title_text_layout.addStretch()
        title_section.addLayout(title_text_layout)
        title_section.addWidget(subtitle_label)
        
        # Window controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)
        
        # Minimize button
        minimize_btn = QPushButton("─")
        minimize_btn.setFixedSize(32, 32)
        minimize_btn.setStyleSheet(self.get_window_button_style("#4a9eff"))
        minimize_btn.clicked.connect(self.showMinimized)
        
        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(self.get_window_button_style("#ff5f57"))
        close_btn.clicked.connect(self.close)
        
        controls_layout.addWidget(minimize_btn)
        controls_layout.addWidget(close_btn)
        
        # Combine everything
        title_layout.addLayout(title_section)
        title_layout.addStretch()
        title_layout.addLayout(controls_layout)
        
        return title_widget
    
    def create_content_area(self):
        """Create main content area with sidebar navigation"""
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(20)
        
        # Sidebar navigation
        sidebar = self.create_sidebar()
        content_layout.addWidget(sidebar)
        
        # Main settings panel
        self.settings_stack = QStackedWidget()
        self.create_settings_pages()
        content_layout.addWidget(self.settings_stack)
        
        # Set proportions (sidebar: content = 1:3)
        content_layout.setStretch(0, 1)
        content_layout.setStretch(1, 3)
        
        return content_widget
    
    def create_sidebar(self):
        """Create modern sidebar navigation"""
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("""
            QWidget {
                background: rgba(20,20,30,0.8);
                border-radius: 15px;
                border: none;
            }
        """)
        
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 20, 15, 20)
        sidebar_layout.setSpacing(8)
        
        # Navigation title
        nav_title = QLabel("Settings")
        nav_title.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
                margin-bottom: 10px;
                padding: 8px 12px;
                border: none;
                background: transparent;
            }
        """)
        sidebar_layout.addWidget(nav_title)
        
        # Navigation items
        self.nav_buttons = []
        nav_items = [
            ("🏠", "General", "Basic launcher settings"),
            ("🤖", "AI & APIs", "AI services and integrations"),
            ("🎨", "Appearance", "Themes and visual options"),
            ("⌨️", "Shortcuts", "Keyboard and hotkeys"),
            ("⚡", "Performance", "Advanced and performance"),
        ]
        
        for icon, title, description in nav_items:
            btn = self.create_nav_button(icon, title, description)
            self.nav_buttons.append(btn)
            sidebar_layout.addWidget(btn)
        
        # Set first button as active
        if self.nav_buttons:
            self.nav_buttons[0].setChecked(True)
        
        sidebar_layout.addStretch()
        
        # Version info
        version_label = QLabel("v2.0.0")
        version_label.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 11px;
                text-align: center;
                padding: 8px;
                border: none;
                background: transparent;
            }
        """)
        sidebar_layout.addWidget(version_label)
        
        return sidebar
    
    def create_nav_button(self, icon, title, description):
        """Create professional navigation button"""
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setFixedHeight(65)
        
        # Button content
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(12, 8, 12, 8)
        
        # Icon
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 18px; margin-right: 8px; border: none; background: transparent;")
        icon_label.setFixedWidth(30)
        
        # Text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600; font-size: 13px; border: none; background: transparent;")
        
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 10px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        text_layout.addWidget(title_label)
        text_layout.addWidget(desc_label)
        
        btn_layout.addWidget(icon_label)
        btn_layout.addLayout(text_layout)
        
        # Create widget for button content
        btn_widget = QWidget()
        btn_widget.setLayout(btn_layout)
        
        # Set button layout
        main_btn_layout = QVBoxLayout(btn)
        main_btn_layout.setContentsMargins(0, 0, 0, 0)
        main_btn_layout.addWidget(btn_widget)
        
        # Styling
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 10px;
                text-align: left;
                color: #ffffff;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.1);
                border: none;
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(100,150,255,0.3), stop:1 rgba(150,100,255,0.3));
                border: none;
            }
            QPushButton:pressed {
                border: none;
            }
        """)
        
        # Connect to page switching
        btn.clicked.connect(lambda checked, idx=len(self.nav_buttons): self.switch_page(idx))
        
        return btn
    
    def create_button_bar(self):
        """Create professional action button bar"""
        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 15, 0, 0)
        
        # Left side - utility buttons
        left_layout = QHBoxLayout()
        
        # Export settings
        export_btn = QPushButton("📤 Export")
        export_btn.setStyleSheet(self.get_action_button_style("#6c757d"))
        export_btn.clicked.connect(self.export_settings)
        
        # Import settings
        import_btn = QPushButton("📥 Import")
        import_btn.setStyleSheet(self.get_action_button_style("#6c757d"))
        import_btn.clicked.connect(self.import_settings)
        
        left_layout.addWidget(export_btn)
        left_layout.addWidget(import_btn)
        left_layout.addStretch()
        
        # Right side - main actions
        right_layout = QHBoxLayout()
        right_layout.setSpacing(12)
        
        # Reset to defaults
        reset_btn = QPushButton("🔄 Reset")
        reset_btn.setStyleSheet(self.get_action_button_style("#ff9500"))
        reset_btn.clicked.connect(self.reset_to_defaults)
        
        # Cancel
        cancel_btn = QPushButton("❌ Cancel")
        cancel_btn.setStyleSheet(self.get_action_button_style("#6c757d"))
        cancel_btn.clicked.connect(self.cancel_settings)
        
        # Apply & Save
        apply_btn = QPushButton("✅ Apply & Save")
        apply_btn.setStyleSheet(self.get_action_button_style("#28a745"))
        apply_btn.clicked.connect(self.apply_settings)
        apply_btn.setDefault(True)  # Make it the default button
        
        right_layout.addWidget(reset_btn)
        right_layout.addWidget(cancel_btn)
        right_layout.addWidget(apply_btn)
        
        # Combine layouts
        button_layout.addLayout(left_layout)
        button_layout.addLayout(right_layout)
        
        return button_widget
    
    def create_professional_section(self, title, controls):
        """Create a professional settings section"""
        section = QWidget()
        section.setStyleSheet("""
            QWidget {
                background: rgba(255,255,255,0.03);
                border-radius: 12px;
                border: none;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(15)
        
        # Section title
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
                margin-bottom: 8px;
                border: none;
                background: transparent;
            }
        """)
        layout.addWidget(title_label)
        
        # Add controls
        for control in controls:
            if control:
                layout.addWidget(control)
        
        return section
    
    def create_slider_setting(self, label, attr_name, min_val, max_val, default_val, suffix, description):
        """Create a professional slider setting"""
        container = QWidget()
        container.setStyleSheet("QWidget { border: none; background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        
        # Label and value
        header_layout = QHBoxLayout()
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-weight: 500; font-size: 14px; border: none; background: transparent;")
        
        value_label = QLabel(f"{default_val}{suffix}")
        value_label.setStyleSheet("color: #64a8ff; font-weight: 600; border: none; background: transparent;")
        value_label.setFixedWidth(60)
        
        header_layout.addWidget(label_widget)
        header_layout.addStretch()
        header_layout.addWidget(value_label)
        
        # Slider
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default_val)
        slider.setStyleSheet("""
            QSlider {
                border: none;
                background: transparent;
            }
            QSlider::groove:horizontal {
                background: rgba(255,255,255,0.1);
                height: 6px;
                border-radius: 3px;
                border: none;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #64a8ff, stop:1 #4a9eff);
                width: 18px;
                height: 18px;
                border-radius: 9px;
                margin-top: -6px;
                margin-bottom: -6px;
                border: none;
            }
            QSlider::handle:horizontal:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #74b8ff, stop:1 #5aafff);
            }
        """)
        
        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 12px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        # Connect slider to value label
        slider.valueChanged.connect(lambda v: value_label.setText(f"{v}{suffix}"))
        
        # Store references
        setattr(self, attr_name, slider)
        
        layout.addLayout(header_layout)
        layout.addWidget(slider)
        layout.addWidget(desc_label)
        
        return container
    
    def create_checkbox_setting(self, label, attr_name, default_val, description):
        """Create a professional checkbox setting"""
        container = QWidget()
        container.setStyleSheet("QWidget { border: none; background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        
        checkbox = QCheckBox(label)
        checkbox.setChecked(default_val)
        checkbox.setStyleSheet("""
            QCheckBox {
                font-weight: 500;
                font-size: 14px;
                spacing: 8px;
                border: none;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
            }
            QCheckBox::indicator:unchecked {
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.3);
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #64a8ff, stop:1 #4a9eff);
                border: 1px solid #64a8ff;
            }
            QCheckBox::indicator:checked:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #74b8ff, stop:1 #5aafff);
            }
        """)
        
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 12px; margin-left: 26px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        setattr(self, attr_name, checkbox)
        
        layout.addWidget(checkbox)
        layout.addWidget(desc_label)
        
        return container
    
    def create_combo_setting(self, label, attr_name, options, default_val, description):
        """Create a professional combo box setting"""
        container = QWidget()
        container.setStyleSheet("QWidget { border: none; background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        
        # Label
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-weight: 500; font-size: 14px; border: none; background: transparent;")
        
        # Combo box
        combo = QComboBox()
        combo.addItems(options)
        combo.setCurrentText(default_val)
        combo.setStyleSheet("""
            QComboBox {
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
                min-height: 20px;
            }
            QComboBox:hover {
                border: 1px solid rgba(100,168,255,0.5);
            }
            QComboBox:focus {
                border: 1px solid #64a8ff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #fff;
            }
        """)
        
        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 12px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        setattr(self, attr_name, combo)
        
        layout.addWidget(label_widget)
        layout.addWidget(combo)
        layout.addWidget(desc_label)
        
        return container
    
    def create_password_setting(self, label, attr_name, placeholder, description):
        """Create a professional password input setting"""
        container = QWidget()
        container.setStyleSheet("QWidget { border: none; background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        
        # Label
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-weight: 500; font-size: 14px; border: none; background: transparent;")
        
        # Password input
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.EchoMode.Password)
        password_input.setPlaceholderText(placeholder)
        password_input.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                padding: 10px 12px;
                font-size: 13px;
                min-height: 20px;
            }
            QLineEdit:hover {
                border: 1px solid rgba(100,168,255,0.5);
            }
            QLineEdit:focus {
                border: 1px solid #64a8ff;
                background: rgba(255,255,255,0.15);
            }
        """)
        
        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 12px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        setattr(self, attr_name, password_input)
        
        layout.addWidget(label_widget)
        layout.addWidget(password_input)
        layout.addWidget(desc_label)
        
        return container
    
    def create_hotkey_setting(self, label, attr_name, default_hotkey, description):
        """Create a professional hotkey recorder setting"""
        container = QWidget()
        container.setStyleSheet("QWidget { border: none; background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        
        # Label
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-weight: 500; font-size: 14px; border: none; background: transparent;")
        
        # Hotkey recorder button
        hotkey_btn = QPushButton(default_hotkey)
        hotkey_btn.setCheckable(True)
        hotkey_btn.setFixedHeight(40)
        hotkey_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                padding: 10px 15px;
                font-size: 13px;
                font-weight: 600;
                text-align: center;
            }
            QPushButton:hover {
                border: 1px solid rgba(100,168,255,0.5);
                background: rgba(255,255,255,0.15);
            }
            QPushButton:checked {
                border: 1px solid #64a8ff;
                background: rgba(100,168,255,0.2);
                color: #64a8ff;
            }
            QPushButton:pressed {
                background: rgba(100,168,255,0.3);
            }
        """)
        
        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #888; font-size: 12px; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        
        # Store reference and connect event
        setattr(self, attr_name, hotkey_btn)
        hotkey_btn.clicked.connect(lambda: self.start_hotkey_recording(hotkey_btn, attr_name))
        
        layout.addWidget(label_widget)
        layout.addWidget(hotkey_btn)
        layout.addWidget(desc_label)
        
        return container
    
    def start_hotkey_recording(self, button, attr_name):
        """Start recording hotkey"""
        if button.isChecked():
            button.setText("Press keys...")
            button.setFocus()
            # Store the button reference for key capture
            self.recording_button = button
            self.recording_attr = attr_name
            # Install event filter for key capture
            button.installEventFilter(self)
        else:
            # Reset to original text if cancelled
            original_text = self.settings.value(f"hotkey_{attr_name}", button.text())
            button.setText(original_text)
            if hasattr(self, 'recording_button'):
                delattr(self, 'recording_button')
    
    def eventFilter(self, obj, event):
        """Multi-key combination capture - supports Q+Space, A+B+C etc."""
        if hasattr(self, 'recording_button') and obj == self.recording_button:
            
            # Initialize pressed keys tracking if not exists
            if not hasattr(self, 'pressed_keys'):
                self.pressed_keys = set()
                self.pressed_key_names = set()
                self.key_timer = QTimer()
                self.key_timer.setSingleShot(True)
                self.key_timer.timeout.connect(self.finalize_hotkey_combination)
            
            if event.type() == event.Type.KeyPress:
                key = event.key()
                
                # Skip if key is already pressed (auto-repeat)
                if key in self.pressed_keys:
                    return True
                
                # Add to pressed keys
                self.pressed_keys.add(key)
                
                # Skip modifier-only keys for combination building
                modifier_keys = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta}
                if key not in modifier_keys:
                    # Convert key to readable name
                    key_name = self.get_readable_key_name(key, event.modifiers())
                    if key_name:
                        self.pressed_key_names.add(key_name)
                
                # Build current combination with modifiers + all pressed keys
                current_combination = []
                
                # Add active modifiers first
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    current_combination.append("Ctrl")
                if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    current_combination.append("Alt")
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    current_combination.append("Shift")
                if event.modifiers() & Qt.KeyboardModifier.MetaModifier:
                    current_combination.append("Win")
                
                # Add all pressed non-modifier keys (sorted for consistency)
                sorted_keys = sorted(list(self.pressed_key_names))
                current_combination.extend(sorted_keys)
                
                # Show current combination in real-time
                combination_str = "+".join(current_combination)
                self.recording_button.setText(combination_str)
                
                # Store the combination
                self.current_combination = combination_str
                
                # Reset timer - wait for more keys or finalize after delay
                self.key_timer.stop()
                self.key_timer.start(2000)  # 2 second delay for multi-key combos
                
                return True
                
            elif event.type() == event.Type.KeyRelease:
                key = event.key()
                
                # Remove from pressed keys
                if key in self.pressed_keys:
                    self.pressed_keys.remove(key)
                
                # Remove from key names (for non-modifiers)
                modifier_keys = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta}
                if key not in modifier_keys:
                    key_name = self.get_readable_key_name(key, event.modifiers())
                    if key_name in self.pressed_key_names:
                        self.pressed_key_names.remove(key_name)
                
                # If all keys are released, finalize after short delay
                if not self.pressed_keys:
                    self.key_timer.stop()
                    self.key_timer.start(500)  # 0.5 second after all keys released
                
                return True
        
        return super().eventFilter(obj, event)
    
    def get_readable_key_name(self, key, modifiers):
        """Convert Qt key code to readable name"""
        # Comprehensive key mapping
        key_map = {
            # Letters
            Qt.Key.Key_A: "A", Qt.Key.Key_B: "B", Qt.Key.Key_C: "C", Qt.Key.Key_D: "D", Qt.Key.Key_E: "E",
            Qt.Key.Key_F: "F", Qt.Key.Key_G: "G", Qt.Key.Key_H: "H", Qt.Key.Key_I: "I", Qt.Key.Key_J: "J",
            Qt.Key.Key_K: "K", Qt.Key.Key_L: "L", Qt.Key.Key_M: "M", Qt.Key.Key_N: "N", Qt.Key.Key_O: "O",
            Qt.Key.Key_P: "P", Qt.Key.Key_Q: "Q", Qt.Key.Key_R: "R", Qt.Key.Key_S: "S", Qt.Key.Key_T: "T",
            Qt.Key.Key_U: "U", Qt.Key.Key_V: "V", Qt.Key.Key_W: "W", Qt.Key.Key_X: "X", Qt.Key.Key_Y: "Y",
            Qt.Key.Key_Z: "Z",
            
            # Numbers
            Qt.Key.Key_0: "0", Qt.Key.Key_1: "1", Qt.Key.Key_2: "2", Qt.Key.Key_3: "3", Qt.Key.Key_4: "4",
            Qt.Key.Key_5: "5", Qt.Key.Key_6: "6", Qt.Key.Key_7: "7", Qt.Key.Key_8: "8", Qt.Key.Key_9: "9",
            
            # Special keys
            Qt.Key.Key_Space: "Space", Qt.Key.Key_Tab: "Tab", Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter",
            Qt.Key.Key_Escape: "Escape", Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Delete: "Delete",
            Qt.Key.Key_Insert: "Insert", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End",
            Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown",
            Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right", Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down",
            
            # Punctuation
            Qt.Key.Key_Semicolon: ";", Qt.Key.Key_Equal: "=", Qt.Key.Key_Comma: ",", Qt.Key.Key_Minus: "-",
            Qt.Key.Key_Period: ".", Qt.Key.Key_Slash: "/", Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_Backslash: "\\",
            Qt.Key.Key_BracketRight: "]", Qt.Key.Key_Apostrophe: "'", Qt.Key.Key_QuoteLeft: "`",
            
            # System keys
            Qt.Key.Key_CapsLock: "CapsLock", Qt.Key.Key_NumLock: "NumLock", Qt.Key.Key_ScrollLock: "ScrollLock",
            Qt.Key.Key_Print: "PrintScreen", Qt.Key.Key_Pause: "Pause", Qt.Key.Key_Menu: "Menu",
        }
        
        # Function keys
        for i in range(1, 25):
            try:
                f_key = getattr(Qt.Key, f'Key_F{i}')
                key_map[f_key] = f'F{i}'
            except AttributeError:
                break
        
        # Check for numpad
        if modifiers & Qt.KeyboardModifier.KeypadModifier:
            numpad_map = {
                Qt.Key.Key_0: "Numpad0", Qt.Key.Key_1: "Numpad1", Qt.Key.Key_2: "Numpad2",
                Qt.Key.Key_3: "Numpad3", Qt.Key.Key_4: "Numpad4", Qt.Key.Key_5: "Numpad5",
                Qt.Key.Key_6: "Numpad6", Qt.Key.Key_7: "Numpad7", Qt.Key.Key_8: "Numpad8",
                Qt.Key.Key_9: "Numpad9", Qt.Key.Key_Plus: "NumpadAdd", Qt.Key.Key_Minus: "NumpadSubtract",
                Qt.Key.Key_Asterisk: "NumpadMultiply", Qt.Key.Key_Slash: "NumpadDivide",
                Qt.Key.Key_Period: "NumpadDecimal", Qt.Key.Key_Enter: "NumpadEnter"
            }
            if key in numpad_map:
                return numpad_map[key]
        
        # Try direct mapping
        if key in key_map:
            return key_map[key]
        
        # Try to find Qt key name
        for attr_name in dir(Qt.Key):
            if attr_name.startswith('Key_') and getattr(Qt.Key, attr_name) == key:
                return attr_name[4:]  # Remove 'Key_' prefix
        
        # Fallback
        return f"Key{key}"
    
    def finalize_hotkey_combination(self):
        """Finalize and save the recorded hotkey combination"""
        if hasattr(self, 'current_combination') and hasattr(self, 'recording_button'):
            combination = self.current_combination
            
            # Accept ANY combination - no restrictions!
            self.recording_button.setText(combination)
            self.recording_button.setChecked(False)
            
            # Save to settings
            self.settings.setValue(f"hotkey_{self.recording_attr}", combination)
            
            # Cleanup all recording state
            self.recording_button.removeEventFilter(self)
            if hasattr(self, 'key_timer'):
                self.key_timer.stop()
                delattr(self, 'key_timer')
            delattr(self, 'recording_button')
            delattr(self, 'recording_attr')
            delattr(self, 'current_combination')
            if hasattr(self, 'pressed_keys'):
                delattr(self, 'pressed_keys')
            if hasattr(self, 'pressed_key_names'):
                delattr(self, 'pressed_key_names')
            
            debug_print(f"Multi-key hotkey saved: {combination}")
    
    def create_info_section(self, title, info_items):
        """Create an info section with bullet points"""
        section = self.create_professional_section(title, [])
        
        # Get the layout from the section
        section_layout = section.layout()
        
        # Add info items
        for item in info_items:
            info_label = QLabel(item)
            info_label.setStyleSheet("""
                QLabel {
                    color: #ccc;
                    font-size: 13px;
                    margin: 2px 0px;
                    padding-left: 10px;
                }
            """)
            section_layout.addWidget(info_label)
        
        return section
    
    def create_action_section(self, title, actions):
        """Create a section with action buttons"""
        section = self.create_professional_section(title, [])
        section_layout = section.layout()
        
        for button_text, callback, description in actions:
            action_container = QWidget()
            action_layout = QHBoxLayout(action_container)
            action_layout.setContentsMargins(0, 5, 0, 5)
            
            # Action button
            action_btn = QPushButton(button_text)
            action_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.1);
                    border: 2px solid rgba(255,255,255,0.2);
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-weight: 500;
                    min-width: 120px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.2);
                    border: 2px solid rgba(255,255,255,0.4);
                }
                QPushButton:pressed {
                    background: rgba(255,255,255,0.3);
                }
            """)
            action_btn.clicked.connect(callback)
            
            # Description
            desc_label = QLabel(description)
            desc_label.setStyleSheet("color: #888; font-size: 12px;")
            desc_label.setWordWrap(True)
            
            action_layout.addWidget(action_btn)
            action_layout.addWidget(desc_label, 1)
            
            section_layout.addWidget(action_container)
        
        return section
    
    def setup_window_effects(self):
        """Setup professional window effects"""
        try:
            # Add drop shadow effect
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(30)
            shadow.setColor(QColor(0, 0, 0, 100))
            shadow.setOffset(0, 8)
            self.setGraphicsEffect(shadow)
        except Exception as e:
            debug_print(f"Window effects error: {e}")
    
    def switch_page(self, index):
        """Switch to selected settings page"""
        try:
            # Update button states
            for i, btn in enumerate(self.nav_buttons):
                btn.setChecked(i == index)
            
            # Switch page
            self.settings_stack.setCurrentIndex(index)
            
        except Exception as e:
            debug_print(f"Page switch error: {e}")
    
    def create_settings_pages(self):
        """Create all settings pages"""
        try:
            # Page 0: General
            general_page = self.create_professional_general_page()
            self.settings_stack.addWidget(general_page)
            
            # Page 1: AI & APIs
            ai_page = self.create_professional_ai_page()
            self.settings_stack.addWidget(ai_page)
            
            # Page 2: Appearance
            appearance_page = self.create_professional_appearance_page()
            self.settings_stack.addWidget(appearance_page)
            
            # Page 3: Shortcuts
            shortcuts_page = self.create_professional_shortcuts_page()
            self.settings_stack.addWidget(shortcuts_page)
            
            # Page 4: Performance
            performance_page = self.create_professional_performance_page()
            self.settings_stack.addWidget(performance_page)
            
        except Exception as e:
            debug_print(f"Settings pages error: {e}")
    
    def create_professional_general_page(self):
        """Create professional general page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(25)
        
        title = QLabel("General Settings")
        title.setStyleSheet("color: #fff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        
        # Add settings sections
        search_section = self.create_professional_section("🔍 Search Performance", [
            self.create_slider_setting("Search Delay", "search_delay", 50, 1000, 140, "ms", "Time to wait before starting search"),
            self.create_slider_setting("Max Results", "max_results", 5, 100, 50, "results", "Maximum number of search results"),
        ])
        layout.addWidget(search_section)
        
        window_section = self.create_professional_section("🪟 Window Behavior", [
            self.create_slider_setting("Window Opacity", "window_opacity", 50, 100, 95, "%", "Transparency level"),
            self.create_checkbox_setting("Auto-hide on focus lost", "auto_hide", True, "Hide when clicking outside"),
        ])
        layout.addWidget(window_section)
        
        # Startup settings
        startup_section = self.create_professional_section("🚀 Startup Settings", [
            self.create_checkbox_setting("Start with Windows", "start_with_windows", True, "Automatically start launcher when Windows boots"),
        ])
        layout.addWidget(startup_section)
        
        layout.addStretch()
        return page
    
    def create_professional_ai_page(self):
        """Create professional AI page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(25)
        
        title = QLabel("AI & API Services")
        title.setStyleSheet("color: #fff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        
        service_section = self.create_professional_section("🤖 AI Service", [
            self.create_combo_setting("Current AI Service", "ai_service", 
                                     ["ollama", "openai", "anthropic", "gemini"],
                                     self.ai_assistant.current_service, "Select AI service")
        ])
        layout.addWidget(service_section)
        
        api_section = self.create_professional_section("🔑 API Keys", [
            self.create_password_setting("OpenAI API Key", "openai_key", "sk-...", "OpenAI GPT API key"),
            self.create_password_setting("Anthropic API Key", "anthropic_key", "sk-ant-...", "Anthropic Claude API key"),
            self.create_password_setting("Gemini API Key", "gemini_key", "AI...", "Google Gemini API key"),
        ])
        layout.addWidget(api_section)
        
        layout.addStretch()
        return page
    
    def create_professional_appearance_page(self):
        """Create professional appearance page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(25)
        
        title = QLabel("Appearance & Themes")
        title.setStyleSheet("color: #fff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        
        theme_section = self.create_professional_section("🎨 Theme", [
            self.create_combo_setting("Theme", "theme_select", ["Dark", "Light", "Auto"], "Dark", "Color theme")
        ])
        layout.addWidget(theme_section)
        
        font_section = self.create_professional_section("📝 Typography", [
            self.create_slider_setting("Search Bar Font Size", "font_size", 12, 32, 20, "px", "Search input font size"),
            self.create_slider_setting("Results Font Size", "result_font_size", 10, 24, 14, "px", "Results font size"),
        ])
        layout.addWidget(font_section)
        
        layout.addStretch()
        return page
    
    def create_professional_shortcuts_page(self):
        """Create professional shortcuts page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(25)
        
        title = QLabel("Keyboard Shortcuts")
        title.setStyleSheet("color: #fff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        
        # Global hotkey section
        hotkey_section = self.create_professional_section("⌨️ Global Hotkey", [
            self.create_checkbox_setting("Enable Global Hotkey", "enable_global_hotkey", True, "Enable global hotkey to toggle launcher"),
            self.create_hotkey_setting("Global Hotkey", "global_hotkey", "Ctrl+Space", "Set custom global hotkey combination"),
        ])
        layout.addWidget(hotkey_section)
        

        
        # Built-in shortcuts info
        info_section = self.create_info_section("🔧 Built-in Shortcuts", [
            "• Escape - Close launcher",
            "• Enter - Execute selected item", 
            "• Up/Down Arrow - Navigate results",
            "• F1 - Show help",
            "• Ctrl+L - Clear search",
        ])
        layout.addWidget(info_section)
        
        layout.addStretch()
        return page
    
    def create_professional_performance_page(self):
        """Create professional performance page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(25)
        
        title = QLabel("Performance & Advanced")
        title.setStyleSheet("color: #fff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        
        debug_section = self.create_professional_section("🐛 Development", [
            self.create_checkbox_setting("Debug Mode", "debug_mode", DEBUG, "Enable debug logging"),
        ])
        layout.addWidget(debug_section)
        
        perf_section = self.create_professional_section("⚡ Performance", [
            self.create_checkbox_setting("Enable Icon Cache", "enable_icon_cache", True, "Cache file icons"),
            self.create_slider_setting("Cache Size", "cache_size", 50, 500, 200, "items", "Maximum cached icons"),
        ])
        layout.addWidget(perf_section)
        
        data_section = self.create_action_section("💾 Data Management", [
            ("Clear Usage Data", self.clear_usage_data, "Remove usage statistics"),
            ("Clear Icon Cache", self.clear_icon_cache, "Clear cached icons"),
        ])
        layout.addWidget(data_section)
        
        layout.addStretch()
        return page
    
    def apply_professional_styling(self):
        """Apply professional styling with no borders"""
        self.setStyleSheet("""
            QWidget {
                background: transparent;
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
                border: none;
            }
            QLabel {
                border: none;
                background: transparent;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(255,255,255,0.1);
                width: 8px;
                border-radius: 4px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.3);
                border-radius: 4px;
                min-height: 20px;
                border: none;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.5);
            }
            QVBoxLayout, QHBoxLayout {
                border: none;
            }
            QStackedWidget {
                border: none;
                background: transparent;
            }
        """)
    
    def export_settings(self):
        """Export settings"""
        QMessageBox.information(self, "Export", "Settings export feature coming soon!")
    
    def import_settings(self):
        """Import settings"""
        QMessageBox.information(self, "Import", "Settings import feature coming soon!")
    
    def get_window_button_style(self, color):
        """Get window control button style"""
        return f"""
            QPushButton {{
                background: {color};
                color: white;
                border: none;
                border-radius: 16px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {color}dd;
                transform: scale(1.05);
            }}
            QPushButton:pressed {{
                background: {color}aa;
            }}
        """
    
    def get_action_button_style(self, color):
        """Get action button style"""
        return f"""
            QPushButton {{
                background: {color};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: 600;
                font-size: 13px;
                min-width: 90px;
            }}
            QPushButton:hover {{
                background: {color}dd;
                transform: translateY(-1px);
            }}
            QPushButton:pressed {{
                background: {color}aa;
                transform: translateY(0px);
            }}
        """
        
        # Tab widget for different option categories
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid rgba(255,255,255,0.2);
                background: rgba(30,30,30,0.9);
                border-radius: 10px;
            }
            QTabBar::tab {
                background: rgba(40,40,40,0.8);
                color: #fff;
                padding: 10px 20px;
                margin: 2px;
                border-radius: 5px;
            }
            QTabBar::tab:selected {
                background: rgba(70,70,70,0.9);
            }
            QTabBar::tab:hover {
                background: rgba(60,60,60,0.8);
            }
        """)
        
        # Create tabs
        self.create_general_tab()
        self.create_ai_tab()
        self.create_appearance_tab()
        self.create_hotkeys_tab()
        self.create_advanced_tab()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        # Apply button
        apply_btn = QPushButton("✅ Apply")
        apply_btn.setStyleSheet(self.get_button_style("#4CAF50"))
        apply_btn.clicked.connect(self.apply_settings)
        
        # Cancel button
        cancel_btn = QPushButton("❌ Cancel")
        cancel_btn.setStyleSheet(self.get_button_style("#f44336"))
        cancel_btn.clicked.connect(self.cancel_settings)
        
        # Reset button
        reset_btn = QPushButton("🔄 Reset to Defaults")
        reset_btn.setStyleSheet(self.get_button_style("#FF9800"))
        reset_btn.clicked.connect(self.reset_to_defaults)
        
        button_layout.addWidget(reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(apply_btn)
        
        # Set window style
        self.setStyleSheet("""
            QWidget {
                background: rgba(20,20,20,0.95);
                color: #fff;
                font-family: 'Segoe UI', Arial;
            }
            QLabel {
                color: #fff;
            }
            QLineEdit {
                background: rgba(50,50,50,0.8);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 5px;
                padding: 5px;
                color: #fff;
            }
            QLineEdit:focus {
                border: 1px solid rgba(100,150,255,0.8);
            }
            QComboBox {
                background: rgba(50,50,50,0.8);
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 5px;
                padding: 5px;
                color: #fff;
            }
            QCheckBox {
                color: #fff;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                background: rgba(50,50,50,0.8);
                border: 1px solid rgba(255,255,255,0.3);
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background: rgba(100,150,255,0.8);
                border: 1px solid rgba(100,150,255,0.8);
                border-radius: 3px;
            }
            QSlider::groove:horizontal {
                background: rgba(50,50,50,0.8);
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: rgba(100,150,255,0.8);
                width: 18px;
                height: 18px;
                border-radius: 9px;
                margin-top: -6px;
                margin-bottom: -6px;
            }
        """)
        
        # Add to main layout
        main_layout.addLayout(title_layout)
        main_layout.addWidget(self.tabs)
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)
        self.center_on_screen()
    
    def get_button_style(self, color):
        """Get button style with specified color"""
        return f"""
            QPushButton {{
                background: {color};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: bold;
                min-width: 100px;
            }}
            QPushButton:hover {{
                background: {color}dd;
            }}
            QPushButton:pressed {{
                background: {color}aa;
            }}
        """
    
    def create_general_tab(self):
        """Create general settings tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Search settings
        search_group = QGroupBox("🔍 Search Settings")
        search_layout = QVBoxLayout()
        
        # Search delay
        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("Search Delay (ms):"))
        self.search_delay = QSlider(Qt.Orientation.Horizontal)
        self.search_delay.setRange(50, 1000)
        self.search_delay.setValue(140)
        self.search_delay_label = QLabel("140")
        self.search_delay.valueChanged.connect(lambda v: self.search_delay_label.setText(str(v)))
        delay_layout.addWidget(self.search_delay)
        delay_layout.addWidget(self.search_delay_label)
        search_layout.addLayout(delay_layout)
        
        # Max results
        results_layout = QHBoxLayout()
        results_layout.addWidget(QLabel("Max Results:"))
        self.max_results = QSlider(Qt.Orientation.Horizontal)
        self.max_results.setRange(5, 100)
        self.max_results.setValue(50)
        self.max_results_label = QLabel("50")
        self.max_results.valueChanged.connect(lambda v: self.max_results_label.setText(str(v)))
        results_layout.addWidget(self.max_results)
        results_layout.addWidget(self.max_results_label)
        search_layout.addLayout(results_layout)
        
        search_group.setLayout(search_layout)
        
        # Window settings
        window_group = QGroupBox("🪟 Window Settings")
        window_layout = QVBoxLayout()
        
        # Window opacity
        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(QLabel("Window Opacity:"))
        self.window_opacity = QSlider(Qt.Orientation.Horizontal)
        self.window_opacity.setRange(50, 100)
        self.window_opacity.setValue(95)
        self.opacity_label = QLabel("95%")
        self.window_opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        opacity_layout.addWidget(self.window_opacity)
        opacity_layout.addWidget(self.opacity_label)
        window_layout.addLayout(opacity_layout)
        
        # Auto-hide
        self.auto_hide = QCheckBox("Auto-hide when focus lost")
        self.auto_hide.setChecked(True)
        window_layout.addWidget(self.auto_hide)
        
        # Start with Windows
        self.start_with_windows = QCheckBox("Start with Windows")
        self.start_with_windows.setChecked(True)
        window_layout.addWidget(self.start_with_windows)
        
        window_group.setLayout(window_layout)
        
        layout.addWidget(search_group)
        layout.addWidget(window_group)
        layout.addStretch()
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "General")
    
    def create_ai_tab(self):
        """Create AI settings tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # AI Service Selection
        service_group = QGroupBox("🤖 AI Service")
        service_layout = QVBoxLayout()
        
        service_select_layout = QHBoxLayout()
        service_select_layout.addWidget(QLabel("Current AI Service:"))
        self.ai_service = QComboBox()
        self.ai_service.addItems(["ollama", "openai", "anthropic", "gemini"])
        self.ai_service.setCurrentText(self.ai_assistant.current_service)
        service_select_layout.addWidget(self.ai_service)
        service_layout.addLayout(service_select_layout)
        
        service_group.setLayout(service_layout)
        
        # API Keys
        api_group = QGroupBox("🔑 API Keys")
        api_layout = QVBoxLayout()
        
        # OpenAI
        openai_layout = QHBoxLayout()
        openai_layout.addWidget(QLabel("OpenAI API Key:"))
        self.openai_key = QLineEdit()
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key.setPlaceholderText("sk-...")
        openai_layout.addWidget(self.openai_key)
        api_layout.addLayout(openai_layout)
        
        # Anthropic
        anthropic_layout = QHBoxLayout()
        anthropic_layout.addWidget(QLabel("Anthropic API Key:"))
        self.anthropic_key = QLineEdit()
        self.anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key.setPlaceholderText("sk-ant-...")
        anthropic_layout.addWidget(self.anthropic_key)
        api_layout.addLayout(anthropic_layout)
        
        # Gemini
        gemini_layout = QHBoxLayout()
        gemini_layout.addWidget(QLabel("Gemini API Key:"))
        self.gemini_key = QLineEdit()
        self.gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key.setPlaceholderText("AI...")
        gemini_layout.addWidget(self.gemini_key)
        api_layout.addLayout(gemini_layout)
        
        api_group.setLayout(api_layout)
        
        # Model Settings
        model_group = QGroupBox("🧠 Model Settings")
        model_layout = QVBoxLayout()
        
        # Model selection for each service
        for service in ["openai", "anthropic", "gemini", "ollama"]:
            model_layout_inner = QHBoxLayout()
            model_layout_inner.addWidget(QLabel(f"{service.title()} Model:"))
            model_combo = QComboBox()
            model_combo.setEditable(True)
            
            # Default models
            if service == "openai":
                model_combo.addItems(["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"])
            elif service == "anthropic":
                model_combo.addItems(["claude-3-sonnet-20240229", "claude-3-opus-20240229", "claude-3-haiku-20240307"])
            elif service == "gemini":
                model_combo.addItems(["gemini-pro", "gemini-pro-vision"])
            elif service == "ollama":
                model_combo.addItems(["llama2", "codellama", "mistral", "phi"])
            
            model_combo.setCurrentText(self.ai_assistant.services[service]["model"])
            setattr(self, f"{service}_model", model_combo)
            model_layout_inner.addWidget(model_combo)
            model_layout.addLayout(model_layout_inner)
        
        model_group.setLayout(model_layout)
        
        layout.addWidget(service_group)
        layout.addWidget(api_group)
        layout.addWidget(model_group)
        layout.addStretch()
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "AI Settings")
    
    def create_appearance_tab(self):
        """Create appearance settings tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Theme
        theme_group = QGroupBox("🎨 Theme")
        theme_layout = QVBoxLayout()
        
        theme_select_layout = QHBoxLayout()
        theme_select_layout.addWidget(QLabel("Theme:"))
        self.theme_select = QComboBox()
        self.theme_select.addItems(["Dark", "Light", "Auto"])
        theme_select_layout.addWidget(self.theme_select)
        theme_layout.addLayout(theme_select_layout)
        
        theme_group.setLayout(theme_layout)
        
        # Font
        font_group = QGroupBox("📝 Font")
        font_layout = QVBoxLayout()
        
        # Font size
        font_size_layout = QHBoxLayout()
        font_size_layout.addWidget(QLabel("Search Bar Font Size:"))
        self.font_size = QSlider(Qt.Orientation.Horizontal)
        self.font_size.setRange(12, 32)
        self.font_size.setValue(20)
        self.font_size_label = QLabel("20px")
        self.font_size.valueChanged.connect(lambda v: self.font_size_label.setText(f"{v}px"))
        font_size_layout.addWidget(self.font_size)
        font_size_layout.addWidget(self.font_size_label)
        font_layout.addLayout(font_size_layout)
        
        # Result font size
        result_font_size_layout = QHBoxLayout()
        result_font_size_layout.addWidget(QLabel("Results Font Size:"))
        self.result_font_size = QSlider(Qt.Orientation.Horizontal)
        self.result_font_size.setRange(10, 24)
        self.result_font_size.setValue(14)
        self.result_font_size_label = QLabel("14px")
        self.result_font_size.valueChanged.connect(lambda v: self.result_font_size_label.setText(f"{v}px"))
        result_font_size_layout.addWidget(self.result_font_size)
        result_font_size_layout.addWidget(self.result_font_size_label)
        font_layout.addLayout(result_font_size_layout)
        
        font_group.setLayout(font_layout)
        
        # Animation
        animation_group = QGroupBox("✨ Animations")
        animation_layout = QVBoxLayout()
        
        self.enable_animations = QCheckBox("Enable fade animations")
        self.enable_animations.setChecked(True)
        animation_layout.addWidget(self.enable_animations)
        
        # Animation speed
        anim_speed_layout = QHBoxLayout()
        anim_speed_layout.addWidget(QLabel("Animation Speed:"))
        self.animation_speed = QSlider(Qt.Orientation.Horizontal)
        self.animation_speed.setRange(100, 500)
        self.animation_speed.setValue(200)
        self.anim_speed_label = QLabel("200ms")
        self.animation_speed.valueChanged.connect(lambda v: self.anim_speed_label.setText(f"{v}ms"))
        anim_speed_layout.addWidget(self.animation_speed)
        anim_speed_layout.addWidget(self.anim_speed_label)
        animation_layout.addLayout(anim_speed_layout)
        
        animation_group.setLayout(animation_layout)
        
        layout.addWidget(theme_group)
        layout.addWidget(font_group)
        layout.addWidget(animation_group)
        layout.addStretch()
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Appearance")
    
    def create_hotkeys_tab(self):
        """Create hotkeys settings tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Global Hotkey
        hotkey_group = QGroupBox("⌨️ Global Hotkey")
        hotkey_layout = QVBoxLayout()
        
        # Enable/disable global hotkey
        self.enable_global_hotkey = QCheckBox("Enable Global Hotkey")
        self.enable_global_hotkey.setChecked(True)
        hotkey_layout.addWidget(self.enable_global_hotkey)
        
        # Hotkey combination (future feature)
        hotkey_combo_layout = QHBoxLayout()
        hotkey_combo_layout.addWidget(QLabel("Hotkey Combination:"))
        self.hotkey_combo = QLineEdit("Ctrl+Space")
        self.hotkey_combo.setReadOnly(True)  # For now
        hotkey_combo_layout.addWidget(self.hotkey_combo)
        hotkey_layout.addLayout(hotkey_combo_layout)
        
        hotkey_group.setLayout(hotkey_layout)
        
        # Other shortcuts
        shortcuts_group = QGroupBox("🔧 Application Shortcuts")
        shortcuts_layout = QVBoxLayout()
        
        shortcuts_info = QLabel("""
        Built-in shortcuts:
        • Escape - Close launcher
        • Enter - Execute selected item
        • Up/Down - Navigate results
        • Ctrl+Space - Toggle launcher (global)
        """)
        shortcuts_info.setStyleSheet("color: #ccc; font-style: italic;")
        shortcuts_layout.addWidget(shortcuts_info)
        
        shortcuts_group.setLayout(shortcuts_layout)
        
        layout.addWidget(hotkey_group)
        layout.addWidget(shortcuts_group)
        layout.addStretch()
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Hotkeys")
    
    def create_advanced_tab(self):
        """Create advanced settings tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Debug
        debug_group = QGroupBox("🐛 Debug & Development")
        debug_layout = QVBoxLayout()
        
        self.debug_mode = QCheckBox("Enable Debug Mode")
        self.debug_mode.setChecked(DEBUG)
        debug_layout.addWidget(self.debug_mode)
        
        debug_group.setLayout(debug_layout)
        
        # Performance
        perf_group = QGroupBox("⚡ Performance")
        perf_layout = QVBoxLayout()
        
        # Cache settings
        self.enable_icon_cache = QCheckBox("Enable Icon Cache")
        self.enable_icon_cache.setChecked(True)
        perf_layout.addWidget(self.enable_icon_cache)
        
        # Cache size
        cache_size_layout = QHBoxLayout()
        cache_size_layout.addWidget(QLabel("Icon Cache Size:"))
        self.cache_size = QSlider(Qt.Orientation.Horizontal)
        self.cache_size.setRange(50, 500)
        self.cache_size.setValue(200)
        self.cache_size_label = QLabel("200")
        self.cache_size.valueChanged.connect(lambda v: self.cache_size_label.setText(str(v)))
        cache_size_layout.addWidget(self.cache_size)
        cache_size_layout.addWidget(self.cache_size_label)
        perf_layout.addLayout(cache_size_layout)
        
        perf_group.setLayout(perf_layout)
        
        # Data Management
        data_group = QGroupBox("💾 Data Management")
        data_layout = QVBoxLayout()
        
        # Clear data buttons
        clear_layout = QHBoxLayout()
        
        clear_usage_btn = QPushButton("Clear Usage Data")
        clear_usage_btn.clicked.connect(self.clear_usage_data)
        clear_layout.addWidget(clear_usage_btn)
        
        clear_clipboard_btn = QPushButton("Clear Clipboard History")
        clear_clipboard_btn.clicked.connect(self.clear_clipboard_history)
        clear_layout.addWidget(clear_clipboard_btn)
        
        clear_cache_btn = QPushButton("Clear Icon Cache")
        clear_cache_btn.clicked.connect(self.clear_icon_cache)
        clear_layout.addWidget(clear_cache_btn)
        
        data_layout.addLayout(clear_layout)
        
        data_group.setLayout(data_layout)
        
        layout.addWidget(debug_group)
        layout.addWidget(perf_group)
        layout.addWidget(data_group)
        layout.addStretch()
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Advanced")
    
    def load_current_settings(self):
        """Load current settings into the UI"""
        try:
            # Store original values for cancel functionality
            self.original_values = {
                'ai_service': self.ai_assistant.current_service,
                'theme': self.settings.value("theme", "dark"),
                'search_delay': int(self.settings.value("search_delay", 140)),
                'max_results': int(self.settings.value("max_results", 50)),
                'window_opacity': int(self.settings.value("window_opacity", 95)),
                'auto_hide': self.settings.value("auto_hide", True, type=bool),
            'start_with_windows': self.settings.value("start_with_windows", True, type=bool),
                'font_size': int(self.settings.value("font_size", 20)),
                'result_font_size': int(self.settings.value("result_font_size", 14)),
                'enable_global_hotkey': self.settings.value("enable_global_hotkey", True, type=bool),
                'debug_mode': DEBUG,
                'enable_icon_cache': self.settings.value("enable_icon_cache", True, type=bool),
                'cache_size': int(self.settings.value("cache_size", 200)),
                # Hotkeys
                'global_hotkey': self.settings.value("hotkey_global_hotkey", "Ctrl+Space"),
            }
            
            # Load hotkey values into buttons if they exist
            hotkey_attrs = ['global_hotkey']
            for attr in hotkey_attrs:
                if hasattr(self, attr):
                    saved_value = self.settings.value(f"hotkey_{attr}", getattr(self, attr).text())
                    getattr(self, attr).setText(saved_value)
            
            # Check current startup status and update checkbox
            if hasattr(self, 'start_with_windows'):
                is_in_startup = self.parent_launcher.is_in_startup()
                self.start_with_windows.setChecked(is_in_startup)
            
        except Exception as e:
            debug_print(f"Load settings error: {e}")
    
    def apply_settings(self):
        """Apply and save all settings"""
        try:
            # AI settings
            if hasattr(self, 'ai_service'):
                new_service = self.ai_service.currentText()
                if new_service != self.ai_assistant.current_service:
                    self.ai_assistant.current_service = new_service
                    self.ai_assistant.save_ai_settings()
            
            # API keys
            if hasattr(self, 'openai_key'):
                api_keys = {
                    'openai': self.openai_key.text(),
                    'anthropic': self.anthropic_key.text(),
                    'gemini': self.gemini_key.text()
                }
                
                for service, key in api_keys.items():
                    if key.strip():
                        self.ai_assistant.services[service]["api_key"] = key.strip()
                
                self.ai_assistant.save_ai_settings()
            
            # Application settings
            if hasattr(self, 'search_delay'):
                self.settings.setValue("search_delay", self.search_delay.value())
            if hasattr(self, 'max_results'):
                self.settings.setValue("max_results", self.max_results.value())
            if hasattr(self, 'window_opacity'):
                self.settings.setValue("window_opacity", self.window_opacity.value())
            if hasattr(self, 'auto_hide'):
                self.settings.setValue("auto_hide", self.auto_hide.isChecked())
            if hasattr(self, 'start_with_windows'):
                self.settings.setValue("start_with_windows", self.start_with_windows.isChecked())
                # Apply startup setting
                if self.start_with_windows.isChecked():
                    self.parent_launcher.add_to_startup()
                else:
                    self.parent_launcher.remove_from_startup()
            if hasattr(self, 'font_size'):
                self.settings.setValue("font_size", self.font_size.value())
            if hasattr(self, 'result_font_size'):
                self.settings.setValue("result_font_size", self.result_font_size.value())
            if hasattr(self, 'enable_global_hotkey'):
                self.settings.setValue("enable_global_hotkey", self.enable_global_hotkey.isChecked())
            if hasattr(self, 'debug_mode'):
                self.settings.setValue("debug_mode", self.debug_mode.isChecked())
            if hasattr(self, 'enable_icon_cache'):
                self.settings.setValue("enable_icon_cache", self.enable_icon_cache.isChecked())
            if hasattr(self, 'cache_size'):
                self.settings.setValue("cache_size", self.cache_size.value())
            
            # Save hotkey settings
            hotkey_attrs = ['global_hotkey']
            for attr in hotkey_attrs:
                if hasattr(self, attr):
                    self.settings.setValue(f"hotkey_{attr}", getattr(self, attr).text())
            
            # Apply to parent launcher
            self.apply_to_launcher()
            
            QMessageBox.information(self, "Success", "⚙️ Settings applied successfully!\n\nSome changes may require restart to take full effect.")
            self.close()
            
        except Exception as e:
            debug_print(f"Apply settings error: {e}")
            QMessageBox.warning(self, "Error", f"❌ Failed to apply settings:\n\n{e}")
    
    def cancel_settings(self):
        """Cancel changes and close"""
        self.close()
    
    def reset_to_defaults(self):
        """Reset all settings to defaults"""
        reply = QMessageBox.question(
            self, 
            "Reset Settings", 
            "🔄 Are you sure you want to reset all settings to defaults?\n\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Reset UI controls to defaults (if they exist)
                if hasattr(self, 'search_delay'):
                    self.search_delay.setValue(140)
                if hasattr(self, 'max_results'):
                    self.max_results.setValue(50)
                if hasattr(self, 'window_opacity'):
                    self.window_opacity.setValue(95)
                if hasattr(self, 'auto_hide'):
                    self.auto_hide.setChecked(True)
                if hasattr(self, 'start_with_windows'):
                    self.start_with_windows.setChecked(True)
                if hasattr(self, 'font_size'):
                    self.font_size.setValue(20)
                if hasattr(self, 'result_font_size'):
                    self.result_font_size.setValue(14)
                if hasattr(self, 'enable_global_hotkey'):
                    self.enable_global_hotkey.setChecked(True)
                if hasattr(self, 'debug_mode'):
                    self.debug_mode.setChecked(False)
                if hasattr(self, 'enable_icon_cache'):
                    self.enable_icon_cache.setChecked(True)
                if hasattr(self, 'cache_size'):
                    self.cache_size.setValue(200)
                
                # Clear API keys
                if hasattr(self, 'openai_key'):
                    self.openai_key.clear()
                if hasattr(self, 'anthropic_key'):
                    self.anthropic_key.clear()
                if hasattr(self, 'gemini_key'):
                    self.gemini_key.clear()
                
                # Reset hotkeys to defaults
                if hasattr(self, 'global_hotkey'):
                    self.global_hotkey.setText("Ctrl+Space")
                
                QMessageBox.information(self, "Reset Complete", "✅ All settings have been reset to defaults!")
                
            except Exception as e:
                debug_print(f"Reset error: {e}")
                QMessageBox.warning(self, "Reset Error", f"❌ Failed to reset settings:\n\n{e}")
    
    def apply_to_launcher(self):
        """Apply settings to the main launcher"""
        try:
            # Update search timer
            if hasattr(self.parent_launcher, 'search_timer'):
                # This will affect future timer starts
                pass
            
            # Update window opacity
            opacity = self.window_opacity.value() / 100.0
            self.parent_launcher.setWindowOpacity(opacity)
            
            # Update debug mode
            global DEBUG
            DEBUG = self.debug_mode.isChecked()
            
        except Exception as e:
            debug_print(f"Apply to launcher error: {e}")
    
    def clear_usage_data(self):
        """Clear usage statistics"""
        try:
            # Clear usage data from QSettings
            self.parent_launcher.settings.remove("usage_data/apps")
            self.parent_launcher.settings.remove("usage_data/searches")
            self.parent_launcher.settings.remove("usage_data/last_used")
            
            # Reset in-memory data
            self.parent_launcher.smart_suggestions.usage_data = {"apps": {}, "searches": {}, "last_used": {}}
            
            # Force sync
            self.parent_launcher.settings.sync()
            
            QMessageBox.information(self, "Data Cleared", "Usage data cleared successfully!")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to clear usage data: {e}")
    
    # Clipboard history clearing disabled
    def clear_clipboard_history(self):
        """Clipboard history feature disabled"""
        QMessageBox.information(self, "Feature Disabled", "Clipboard history feature has been disabled.")
    
    def clear_icon_cache(self):
        """Clear icon cache"""
        try:
            global _ICON_CACHE
            _ICON_CACHE.clear()
            QMessageBox.information(self, "Cache Cleared", "Icon cache cleared successfully!")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to clear icon cache: {e}")
    
    def center_on_screen(self):
        """Center window on screen"""
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) // 2, (screen.height() - size.height()) // 2)
    
    def mousePressEvent(self, event):
        """Handle mouse press for window dragging"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        """Handle mouse move for window dragging"""
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, 'drag_position'):
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()


# ---------------- main ----------------
if __name__ == "__main__":
    try:
        debug_print("Application starting...")
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(True)
        
        w = LauncherUI()
        w.show()
        debug_print("Window displayed")
        
        sys.exit(app.exec())
    except Exception as e:
        print(f"Critical error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
