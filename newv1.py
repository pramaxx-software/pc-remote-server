import qrcode
import asyncio
import base64
import io
import json
import os
import re
import socket
import sys
import threading
import time
import subprocess
import urllib.request
import urllib.error
import uuid
import customtkinter as ctk
import websockets
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Controller as MouseController, Button
import pystray
from PIL import Image, ImageDraw, ImageGrab
import ctypes

# Set tema tampilan Modern
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

APP_VERSION = "2.0"


def get_config_path():
    # Ambil direktori AppData\Roaming milik user Windows saat ini
    appdata_dir = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "PramaxxRemoteKeyboardServer")

    # Buat foldernya secara otomatis kalau belum ada
    if not os.path.exists(appdata_dir):
        os.makedirs(appdata_dir, exist_ok=True)

    return os.path.join(appdata_dir, "config.json")


# --- KONFIGURASI API & FILE ---
CONFIG_FILE = get_config_path()
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

# Endpoint API Web Panel yang relevan saja (Validasi Token & Sync Settings)
WEB_PANEL_BASE_URL = "https://pc-remote.pramaxx.biz.id/api"
WEB_PANEL_API_URL = f"{WEB_PANEL_BASE_URL}/validate_token"
WEB_PANEL_SYNC_URL = f"{WEB_PANEL_BASE_URL}/sync_settings"

DEFAULT_CONFIG = {
    "device_id": "",   # Digenerate otomatis saat pertama run
    "port": 8765,
    "mode": "Lokal (LAN)",
    "web_token": "",   # Token hash yang diinput user (device -> cloudflare)
    "cf_token": "",    # Token asli Cloudflare hasil dari backend
    "security_pin": "",
    "auto_minimize": False,
    # Fitur remote
    "allow_mouse": True,
    "allow_stream": True,
    "stream_fps": 15,
    "stream_quality": 50,
}


def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded = json.load(f)
                config.update(loaded)
        except Exception:
            pass

    if not config.get("device_id"):
        config["device_id"] = str(uuid.uuid4())
        save_config(config)

    return config


def save_config(config_data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)


def api_post(url, payload, headers=None, timeout=10):
    """Helper kecil untuk POST JSON ke web panel dan mengembalikan dict hasil parse."""
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "User-Agent": "PramaxxRemoteKeyboardClient/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


# --- 1. SETUP KEYBOARD & MOUSE CONTROLLER ---
keyboard = KeyboardController()
mouse = MouseController()

SPECIAL_KEYS = {
    "enter": Key.enter, "space": Key.space, "backspace": Key.backspace,
    "tab": Key.tab, "esc": Key.esc, "up": Key.up, "down": Key.down,
    "left": Key.left, "right": Key.right, "ctrl": Key.ctrl, "alt": Key.alt,
    "shift": Key.shift, "cmd": Key.cmd, "win": Key.cmd,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12
}

MOUSE_BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}


def get_key_obj(key_str):
    return SPECIAL_KEYS.get(key_str.lower(), key_str)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_cloudflared_path():
    if hasattr(sys, '_MEIPASS'):
        bundled_path = os.path.join(sys._MEIPASS, "cloudflared.exe")
        if os.path.exists(bundled_path):
            return bundled_path
    local_path = os.path.join(os.path.abspath("."), "cloudflared.exe")
    if os.path.exists(local_path):
        return local_path
    return None


# --- 2. KELAS UTAMA APLIKASI ---
class ModernRemoteServerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config_data = load_config()
        self.device_id = self.config_data.get("device_id")
        self.port = int(self.config_data.get("port", 8765))
        self.local_ip = get_local_ip()

        # Status kontrol & monitoring
        self.is_running = True
        self.active_clients = 0
        self.streaming_clients = 0
        self.server_loop = None
        self.server_thread = None
        self.tray_icon = None
        self.cf_process = None
        self.cf_thread = None

        self.title(f"Pramaxx Remote Server v{APP_VERSION}")
        self.geometry("460x520")
        self.resizable(False, False)

        self.setup_ui()
        self.setup_tray()

        # Jalankan server WebSocket & Cloudflare
        self.start_server_thread()
        self.check_and_init_cloudflare()

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self.on_minimize)

    # ---------------------------------------------------------------
    # UI SETUP
    # ---------------------------------------------------------------
    def setup_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            header, text="Pramaxx Remote Server",
            font=ctk.CTkFont(size=19, weight="bold")
        ).pack(side="left")

        ctk.CTkLabel(
            header, text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=11), text_color="#6b7280"
        ).pack(side="right")

        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=(5, 15))

        self.tab_status = self.tabview.add("Status")
        self.tab_settings = self.tabview.add("Pengaturan")

        self.setup_status_tab()
        self.setup_settings_tab()

    def setup_status_tab(self):
        info_frame = ctk.CTkFrame(self.tab_status, fg_color="#2b2b2b", corner_radius=8)
        info_frame.pack(fill="x", padx=10, pady=(10, 5))

        self.mode_label = ctk.CTkLabel(
            info_frame, text=f"Mode: {self.config_data.get('mode', 'Lokal (LAN)')}",
            font=ctk.CTkFont(size=11, slant="italic")
        )
        self.mode_label.pack(pady=(8, 0))

        self.ip_label = ctk.CTkLabel(
            info_frame, text=f"{self.local_ip}:{self.port}",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#3b82f6"
        )
        self.ip_label.pack(pady=(0, 8))

        self.status_label = ctk.CTkLabel(
            self.tab_status, text="● LAN: Menunggu koneksi...",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#f59e0b"
        )
        self.status_label.pack(pady=(12, 2))

        self.cf_status_label = ctk.CTkLabel(
            self.tab_status, text="● Pramaxx Tunnel: Nonaktif (Mode Lokal)",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#6b7280"
        )
        self.cf_status_label.pack(pady=(2, 8))

        # Ringkasan fitur remote aktif
        feature_frame = ctk.CTkFrame(self.tab_status, fg_color="#232323", corner_radius=8)
        feature_frame.pack(fill="x", padx=10, pady=(6, 8))

        ctk.CTkLabel(
            feature_frame, text="Fitur Remote",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self.feature_keyboard_label = ctk.CTkLabel(
            feature_frame, text="⌨  Keyboard: Aktif", font=ctk.CTkFont(size=11), text_color="#10b981"
        )
        self.feature_keyboard_label.pack(anchor="w", padx=10, pady=1)

        self.feature_mouse_label = ctk.CTkLabel(
            feature_frame, text="🖱  Mouse: -", font=ctk.CTkFont(size=11)
        )
        self.feature_mouse_label.pack(anchor="w", padx=10, pady=1)

        self.feature_stream_label = ctk.CTkLabel(
            feature_frame, text="🖥  Screen Stream: -", font=ctk.CTkFont(size=11)
        )
        self.feature_stream_label.pack(anchor="w", padx=10, pady=(1, 8))

        self.refresh_feature_labels()

        ctk.CTkButton(
            self.tab_status, text="Tampilkan QR Code", command=self.show_qr_code,
            fg_color="#10b981", hover_color="#059669"
        ).pack(pady=(6, 5), padx=10, fill="x")

    def refresh_feature_labels(self):
        allow_mouse = self.config_data.get("allow_mouse", True)
        allow_stream = self.config_data.get("allow_stream", True)

        self.feature_mouse_label.configure(
            text=f"🖱  Mouse: {'Aktif' if allow_mouse else 'Dinonaktifkan'}",
            text_color="#10b981" if allow_mouse else "#6b7280"
        )
        self.feature_stream_label.configure(
            text=f"🖥  Screen Stream: {'Aktif' if allow_stream else 'Dinonaktifkan'}"
                 f" ({self.streaming_clients} sesi)" if allow_stream else "🖥  Screen Stream: Dinonaktifkan",
            text_color="#10b981" if allow_stream else "#6b7280"
        )

    # --- FITUR QR CODE ---
    def show_qr_code(self):
        mode = self.config_data.get("mode", "Lokal (LAN)")

        if mode == "Online (Cloudflare Tunnel)":
            ip_domain = self.config_data.get("tunnel_url", self.local_ip)
            ip_domain = ip_domain.replace("https://", "").replace("http://", "")
        else:
            ip_domain = self.local_ip

        port = self.port
        pin = self.config_data.get("security_pin", "")

        qr_payload = json.dumps({
            "ip": ip_domain,
            "port": str(port),
            "pin": pin,
            "mouse": self.config_data.get("allow_mouse", True),
            "stream": self.config_data.get("allow_stream", True),
        })

        try:
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(qr_payload)
            qr.make(fit=True)
            qr_wrapper = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_wrapper.get_image()

            qr_window = ctk.CTkToplevel(self)
            qr_window.title("Scan QR Code")
            qr_window.geometry("300x360")
            qr_window.attributes("-topmost", True)
            qr_window.resizable(False, False)

            ctk.CTkLabel(qr_window, text="Scan via Aplikasi Android", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))

            ctk_qr = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(220, 220))
            lbl_qr = ctk.CTkLabel(qr_window, image=ctk_qr, text="")
            lbl_qr.pack(pady=10)

            ctk.CTkLabel(qr_window, text=f"PIN: {pin if pin else 'Tidak Ada'}", font=ctk.CTkFont(size=16, weight="bold", family="Consolas"), text_color="#10b981").pack(pady=5)

        except Exception as e:
            print(f"Gagal memuat QR Code: {e}")

    # ---------------------------------------------------------------
    # TAB PENGATURAN
    # ---------------------------------------------------------------
    def setup_settings_tab(self):
        ctk.CTkLabel(self.tab_settings, text="Mode Koneksi:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(8, 0))
        self.mode_option = ctk.CTkOptionMenu(
            self.tab_settings, values=["Lokal (LAN)", "Online (Cloudflare Tunnel)"],
            command=self.on_mode_change
        )
        self.mode_option.set(self.config_data.get("mode", "Lokal (LAN)"))
        self.mode_option.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.tab_settings, text="Port Server Lokal:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0, 0))
        self.port_entry = ctk.CTkEntry(self.tab_settings, placeholder_text="8765")
        self.port_entry.insert(0, str(self.config_data.get("port", 8765)))
        self.port_entry.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.tab_settings, text="Token Koneksi Online:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0, 0))
        token_frame = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        token_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.token_entry = ctk.CTkEntry(token_frame, show="*")
        self.token_entry.insert(0, self.config_data.get("web_token", ""))
        self.token_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.submit_token_btn = ctk.CTkButton(
            token_frame, text="Submit Token", width=95,
            command=self.on_submit_token, fg_color="#3b82f6", hover_color="#2563eb"
        )
        self.submit_token_btn.pack(side="right")

        ctk.CTkLabel(self.tab_settings, text="PIN Keamanan (Akses Android App):", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0, 0))
        pin_frame = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        pin_frame.pack(fill="x", padx=10, pady=(0, 10))

        current_pin = self.config_data.get("security_pin", "Belum ada PIN")
        self.pin_label = ctk.CTkLabel(
            pin_frame, text=current_pin,
            font=ctk.CTkFont(size=14, weight="bold", family="Consolas"),
            fg_color="#2a2a2a", corner_radius=6, height=28
        )
        self.pin_label.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.copy_btn = ctk.CTkButton(
            pin_frame, text="📋 Copy", width=70,
            command=self.on_copy_pin, fg_color="#4b5563", hover_color="#374151"
        )
        self.copy_btn.pack(side="right")

        # --- Toggle fitur remote ---
        ctk.CTkLabel(self.tab_settings, text="Fitur Remote:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(4, 2))

        self.mouse_switch = ctk.CTkSwitch(
            self.tab_settings, text="Izinkan Remote Mouse",
            command=self.on_toggle_mouse
        )
        if self.config_data.get("allow_mouse", True):
            self.mouse_switch.select()
        self.mouse_switch.pack(anchor="w", padx=10, pady=(0, 4))

        self.stream_switch = ctk.CTkSwitch(
            self.tab_settings, text="Izinkan Screen Streaming",
            command=self.on_toggle_stream
        )
        if self.config_data.get("allow_stream", True):
            self.stream_switch.select()
        self.stream_switch.pack(anchor="w", padx=10, pady=(0, 10))

        self.save_btn = ctk.CTkButton(
            self.tab_settings, text="Simpan & Terapkan Semua", command=self.save_settings,
            fg_color="#10b981", hover_color="#059669"
        )
        self.save_btn.pack(fill="x", padx=10, side="bottom", pady=5)

    def on_toggle_mouse(self):
        self.config_data["allow_mouse"] = bool(self.mouse_switch.get())
        save_config(self.config_data)
        self.refresh_feature_labels()

    def on_toggle_stream(self):
        self.config_data["allow_stream"] = bool(self.stream_switch.get())
        save_config(self.config_data)
        self.refresh_feature_labels()

    # --- AKSI VALIDASI TOKEN & SIMPAN ---
    def on_submit_token(self):
        web_token = self.token_entry.get().strip()
        if not web_token:
            self.update_cf_status("● Pramaxx Tunnel: Token kosong! Masukkan token web panel.", "#ef4444")
            return

        self.submit_token_btn.configure(text="Validating...", fg_color="#f59e0b", state="disabled")
        self.update_cf_status("● Pramaxx Tunnel: Memvalidasi token ke server...", "#f59e0b")
        threading.Thread(target=self._validate_token_worker, args=(web_token,), daemon=True).start()

    def _validate_token_worker(self, web_token):
        try:
            res_data = api_post(WEB_PANEL_API_URL, {
                "token": web_token,
                "device_id": self.device_id,
                "port": self.port
            })

            if res_data.get("success") or res_data.get("valid"):
                real_cf_token = res_data.get("cf_token")
                if not real_cf_token:
                    raise ValueError("Token CF asli tidak ada di response server.")

                new_pin = res_data.get('pin')
                if not new_pin:
                    raise ValueError("Security PIN Tidak Valid!")

                tunnel_url = res_data.get("cf_tunnel", "")
                self.config_data["tunnel_url"] = tunnel_url

                self.config_data["web_token"] = web_token
                self.config_data["cf_token"] = real_cf_token
                self.config_data["security_pin"] = new_pin
                save_config(self.config_data)

                self.after(0, lambda: self.pin_label.configure(text=new_pin))
                self.after(0, lambda: self.submit_token_btn.configure(text="✔ Valid!", fg_color="#10b981", state="normal"))
                self.after(2500, lambda: self.submit_token_btn.configure(text="Submit Token", fg_color="#3b82f6"))

                self.update_cf_status("● Pramaxx Tunnel: Token valid! Mengaktifkan tunnel...", "#10b981")

                if self.mode_option.get() == "Online (Cloudflare Tunnel)":
                    self.check_and_init_cloudflare()
            else:
                err_msg = res_data.get("message", "Token web panel tidak valid!")
                self.update_cf_status(f"● Pramaxx Tunnel: {err_msg}", "#ef4444")
                self.after(0, lambda: self.submit_token_btn.configure(text="✕ Ditolak", fg_color="#ef4444", state="normal"))
                self.after(2500, lambda: self.submit_token_btn.configure(text="Submit Token", fg_color="#3b82f6"))

        except Exception as e:
            print(f"Validation Error: {e}")
            self.update_cf_status("● Pramaxx Tunnel: Gagal terhubung ke server validasi!", "#ef4444")
            self.after(0, lambda: self.submit_token_btn.configure(text="✕ Offline/Error", fg_color="#ef4444", state="normal"))
            self.after(2500, lambda: self.submit_token_btn.configure(text="Submit Token", fg_color="#3b82f6"))

    def on_copy_pin(self):
        pin_text = self.pin_label.cget("text")
        if pin_text and pin_text != "Belum ada PIN":
            self.clipboard_clear()
            self.clipboard_append(pin_text)
            self.update()
            self.copy_btn.configure(text="✔ Copied!", fg_color="#10b981")
            self.after(2000, lambda: self.copy_btn.configure(text="📋 Copy", fg_color="#4b5563"))

    def update_status(self, text, color):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def update_cf_status(self, text, color):
        self.after(0, lambda: self.cf_status_label.configure(text=text, text_color=color))

    def save_settings(self):
        try:
            new_port = int(self.port_entry.get())
        except ValueError:
            new_port = 8765

        new_config = dict(self.config_data)
        new_config.update({
            "device_id": self.device_id,
            "mode": self.mode_option.get(),
            "port": new_port,
            "web_token": self.token_entry.get().strip(),
            "cf_token": self.config_data.get("cf_token", ""),
            "security_pin": self.config_data.get("security_pin", ""),
            "auto_minimize": False,
            "allow_mouse": bool(self.mouse_switch.get()),
            "allow_stream": bool(self.stream_switch.get()),
        })

        save_config(new_config)
        self.config_data = new_config
        self.port = new_port
        self.ip_label.configure(text=f"{self.local_ip}:{self.port}")
        self.refresh_feature_labels()
        self.check_and_init_cloudflare()

        self.save_btn.configure(text="Syncing to Web Panel...", fg_color="#f59e0b", state="disabled")
        threading.Thread(target=self._sync_settings_worker, args=(new_config,), daemon=True).start()

    def _sync_settings_worker(self, config_data):
        try:
            res_data = api_post(WEB_PANEL_SYNC_URL, {
                "device_id": config_data["device_id"],
                "web_token": config_data["web_token"],
                "port": config_data["port"],
                "mode": config_data["mode"],
                "local_ip": self.local_ip
            }, timeout=7)

            if res_data.get("success"):
                self.after(0, lambda: self.save_btn.configure(text="✔ Tersimpan & Sinkron!", fg_color="#10b981", state="normal"))
            else:
                self.after(0, lambda: self.save_btn.configure(text="⚠ Tersimpan (Gagal Sync Web)", fg_color="#eab308", state="normal"))

        except (urllib.error.URLError, socket.timeout):
            self.after(0, lambda: self.save_btn.configure(text="✔ Tersimpan Lokal (Offline)", fg_color="#3b82f6", state="normal"))
        except Exception:
            self.after(0, lambda: self.save_btn.configure(text="⚠ Tersimpan (Error Sync)", fg_color="#eab308", state="normal"))
        finally:
            self.after(3000, lambda: self.save_btn.configure(text="Simpan & Terapkan Semua", fg_color="#10b981", state="normal"))

    # --- CLOUDFLARED TUNNEL MANAGEMENT ---
    def check_and_init_cloudflare(self):
        mode = self.config_data.get("mode")
        if mode == "Online (Cloudflare Tunnel)":
            cf_path = get_cloudflared_path()
            if not cf_path:
                self.prompt_download_cloudflared()
            else:
                self.start_cloudflare_tunnel(cf_path)
        else:
            self.stop_cloudflare_tunnel()
            self.update_cf_status("● Pramaxx Tunnel: Nonaktif (Mode Lokal)", "#6b7280")

    def prompt_download_cloudflared(self):
        self.update_cf_status("● Pramaxx Tunnel: Belum terinstall!", "#ef4444")
        dialog = ctk.CTkToplevel(self)
        dialog.title("Install Cloudflared")
        dialog.geometry("320x180")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        ctk.CTkLabel(dialog, text="Komponen Cloudflare belum ada.\nDownload otomatis sekarang (~18MB)?", justify="center").pack(pady=20)

        def do_download():
            dialog.destroy()
            self.update_cf_status("● Pramaxx Tunnel: Mendownload...", "#f59e0b")
            threading.Thread(target=self._download_cf_worker, daemon=True).start()

        ctk.CTkButton(dialog, text="Download & Install", command=do_download, fg_color="#3b82f6").pack(pady=5)
        ctk.CTkButton(dialog, text="Batal", command=dialog.destroy, fg_color="#6b7280").pack(pady=5)

    def _download_cf_worker(self):
        try:
            dest_path = os.path.join(os.path.abspath("."), "cloudflared.exe")
            urllib.request.urlretrieve(CLOUDFLARED_URL, dest_path)
            self.update_cf_status("● Pramaxx Tunnel: Terinstall! Menghubungkan...", "#10b981")
            self.start_cloudflare_tunnel(dest_path)
        except Exception as e:
            self.update_cf_status("● Pramaxx Tunnel: Gagal download!", "#ef4444")
            print(f"Download error: {e}")

    def start_cloudflare_tunnel(self, cf_path):
        self.stop_cloudflare_tunnel()
        token = self.config_data.get("cf_token", "").strip()
        if not token:
            self.update_cf_status("● Pramaxx Tunnel: Token asli kosong! Submit token di Pengaturan.", "#ef4444")
            return

        self.update_cf_status("● Pramaxx Tunnel: Menghubungkan tunnel...", "#f59e0b")
        self.cf_thread = threading.Thread(target=self._run_cf_process, args=(cf_path, token), daemon=True)
        self.cf_thread.start()

    def _run_cf_process(self, cf_path, token):
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.cf_process = subprocess.Popen(
                [cf_path, "tunnel", "run", "--token", token],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags
            )
            for line in self.cf_process.stdout:
                if "Registered tunnel connection" in line or "Connection established" in line:
                    self.update_cf_status("● Pramaxx Tunnel: Online (Terhubung!)", "#10b981")
                elif "error" in line.lower() or "failed" in line.lower():
                    self.update_cf_status("● Pramaxx Tunnel: Error / Token Asli Expired", "#ef4444")
        except Exception:
            self.update_cf_status("● Pramaxx Tunnel: Terputus / Error", "#ef4444")

    def stop_cloudflare_tunnel(self):
        if self.cf_process:
            try:
                self.cf_process.terminate()
                self.cf_process = None
            except Exception:
                pass

    def on_mode_change(self, choice):
        self.mode_label.configure(text=f"Mode: {choice}")

    # --- SYSTEM TRAY MANAGEMENT ---
    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), color=(18, 18, 18))
        dc = ImageDraw.Draw(image)
        dc.ellipse((8, 8, 56, 56), fill=(59, 130, 246))
        return image

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Buka Tampilan", self.show_from_tray, default=True),
            pystray.MenuItem("Keluar (Exit)", self.exit_app)
        )
        self.tray_icon = pystray.Icon("RemoteKeyboard", self.create_tray_image(), "Pramaxx Remote Keyboard Server", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self):
        self.withdraw()

    def show_from_tray(self, icon=None, item=None):
        self.after(0, self.deiconify)

    def on_minimize(self, event):
        if self.state() == 'iconic':
            self.hide_to_tray()

    # --- WEBSOCKET SERVER LOGIC (KEYBOARD + MOUSE + STREAM) ---
    async def websocket_handler(self, websocket):
        client_ip = websocket.remote_address[0]
        print(f"[{client_ip}] Terhubung!")

        self.active_clients += 1
        self.update_status(f"● LAN: Terhubung ({client_ip}) - {self.active_clients} Client", "#10b981")

        session = {"streaming": False}

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue

                server_pin = self.config_data.get("security_pin", "")
                if server_pin and data.get("pin") != server_pin:
                    print("PIN Keamanan Salah! Perintah ditolak.")
                    continue

                cmd_type = data.get("type")

                if cmd_type in ("press", "shortcut"):
                    self.process_keyboard_command(data)

                # --- PERBAIKAN: "mouse_position_percent" DITAMBAHKAN KE LIST DI BAWAH INI ---
                elif cmd_type in ("mouse_move", "mouse_click", "mouse_scroll", "mouse_position", "mouse_position_percent"):
                    self.process_mouse_command(data)

                elif cmd_type == "stream_start":
                    if not self.config_data.get("allow_stream", True):
                        await websocket.send(json.dumps({"type": "stream_error", "message": "Screen streaming dinonaktifkan di server."}))
                    elif not session["streaming"]:
                        session["streaming"] = True
                        self.streaming_clients += 1
                        self.after(0, self.refresh_feature_labels)
                        asyncio.ensure_future(self.stream_screen(websocket, session))

                elif cmd_type == "stream_stop":
                    if session["streaming"]:
                        session["streaming"] = False
                        self.streaming_clients = max(0, self.streaming_clients - 1)
                        self.after(0, self.refresh_feature_labels)

        except websockets.exceptions.ConnectionClosed:
            print(f"[{client_ip}] Terputus!")
        finally:
            if session["streaming"]:
                self.streaming_clients = max(0, self.streaming_clients - 1)
            session["streaming"] = False
            self.after(0, self.refresh_feature_labels)

            self.active_clients = max(0, self.active_clients - 1)
            if self.active_clients > 0:
                self.update_status(f"● LAN: Terhubung - {self.active_clients} Client Aktif", "#10b981")
            else:
                self.update_status("● LAN: Menunggu koneksi...", "#f59e0b")

    def process_keyboard_command(self, data):
        try:
            cmd_type = data.get("type")
            if cmd_type == "press":
                key_val = get_key_obj(data.get("key", ""))
                keyboard.press(key_val)
                keyboard.release(key_val)
            elif cmd_type == "shortcut":
                keys = [get_key_obj(k) for k in data.get("keys", [])]
                for k in keys:
                    keyboard.press(k)
                for k in reversed(keys):
                    keyboard.release(k)
        except Exception as e:
            print(f"Error memproses perintah keyboard: {e}")

    def process_mouse_command(self, data):
        if not self.config_data.get("allow_mouse", True):
            return
        try:
            cmd_type = data.get("type")

            # --- KONTROL ABSOLUT (SINKRON DENGAN JARI/KURSOR CLIENT) ---
            if cmd_type == "mouse_position_percent":
                percent_x = float(data.get("percentX", 0))
                percent_y = float(data.get("percentY", 0))
                
                # Ambil resolusi asli layar PC pake ctypes (Windows Only)
                user32 = ctypes.windll.user32
                screen_width = user32.GetSystemMetrics(0)
                screen_height = user32.GetSystemMetrics(1)
                
                # Konversi persentase ke posisi pixel di PC
                target_x = int(screen_width * percent_x)
                target_y = int(screen_height * percent_y)
                
                # Pindahkan kursor PC (Pynput)
                mouse.position = (target_x, target_y)
                print(f"Posisi Absolut: X={target_x}, Y={target_y}")

            # --- KONTROL RELATIF (UNTUK TRACKPAD BIASA) ---
            elif cmd_type == "mouse_move":
                dx = float(data.get("dx", 0))
                dy = float(data.get("dy", 0))
                mouse.move(dx, dy)
                print(f"Posisi Relatif (Move): DX={dx}, DY={dy}")

            # --- KONTROL KLIK MOUSE ---
            elif cmd_type == "mouse_click":
                btn_name = data.get("button", "left")
                button_obj = MOUSE_BUTTONS.get(btn_name, Button.left)
                double = bool(data.get("double", False))
                if data.get("down_only"):
                    mouse.press(button_obj)
                elif data.get("up_only"):
                    mouse.release(button_obj)
                else:
                    mouse.click(button_obj, 2 if double else 1)

            # --- KONTROL SCROLL ---
            elif cmd_type == "mouse_scroll":
                dx = int(data.get("dx", 0))
                dy = int(data.get("dy", 0))
                mouse.scroll(dx, dy)

        except Exception as e:
            print(f"Error memproses perintah mouse: {e}")

    async def stream_screen(self, websocket, session):
        is_cf = self.config_data.get("mode") == "Online (Cloudflare Tunnel)"
        
        default_fps = 10 if is_cf else 15
        default_qual = 30 if is_cf else 50
        
        fps = max(1, int(self.config_data.get("stream_fps", default_fps)))
        quality = max(10, min(95, int(self.config_data.get("stream_quality", default_qual))))
        delay = 1.0 / fps

        try:
            while session["streaming"] and self.config_data.get("allow_stream", True):
                start = time.time()
                try:
                    # 1. Ambil posisi kursor asli dari pynput
                    mx, my = mouse.position

                    # 2. Tangkap layar (tanpa include_cursor karena sering gagal di Windows)
                    img = ImageGrab.grab()
                    
                    if img is None:
                        await websocket.send(json.dumps({
                            "type": "stream_error", 
                            "message": "Gagal menangkap layar. Pastikan PC tidak terkunci/Sleep!"
                        }))
                        break

                    orig_w, orig_h = img.size

                    # 3. Resize gambar ke thumbnail (mengikuti ukuran stream)
                    target_size = (640, 360) if is_cf else (960, 540)
                    img.thumbnail(target_size)
                    thumb_w, thumb_h = img.size

                    # 4. Sesuaikan posisi kursor agar pas dengan ukuran thumbnail yang sudah di-resize
                    scale_x = thumb_w / orig_w
                    scale_y = thumb_h / orig_h
                    cursor_x = int(mx * scale_x)
                    cursor_y = int(my * scale_y)

                    # 5. Gambar bentuk pointer kursor secara manual di atas gambar
                    draw = ImageDraw.Draw(img)
                    pointer_points = [
                        (cursor_x, cursor_y),
                        (cursor_x, cursor_y + 12),
                        (cursor_x + 4, cursor_y + 9),
                        (cursor_x + 9, cursor_y + 14),
                        (cursor_x + 11, cursor_y + 11),
                        (cursor_x + 6, cursor_y + 6),
                        (cursor_x + 10, cursor_y + 6)
                    ]
                    # Gambar kursor warna putih dengan garis pinggir hitam agar terlihat jelas di background apa pun
                    draw.polygon(pointer_points, fill="white", outline="black")

                    # 6. Konversi ke JPEG dan kirim ke client via WebSocket
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, format="JPEG", quality=quality)
                    b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")

                    await websocket.send(json.dumps({
                        "type": "frame",
                        "width": img.width,
                        "height": img.height,
                        "data": b64_data
                    }))
                    
                except websockets.exceptions.ConnectionClosed:
                    print("Stream terhenti: Client terputus.")
                    break
                except Exception as e:
                    print(f"Stream error: {e}")
                    try:
                        await websocket.send(json.dumps({
                            "type": "stream_error", 
                            "message": f"Server Error: {str(e)}"
                        }))
                    except: 
                        pass
                    break

                elapsed = time.time() - start
                await asyncio.sleep(max(0.0, delay - elapsed))
                
        except asyncio.CancelledError:
            pass
        finally:
            session["streaming"] = False
            

    # --- THREADING & ASYNCIO MANAGEMENT ---
    async def _run_server_async(self):
        try:
            async with websockets.serve(self.websocket_handler, "0.0.0.0", self.port, max_size=None):
                print(f"Server berjalan di ws://0.0.0.0:{self.port}")
                await asyncio.Future()
        except OSError as e:
            if e.errno == 10048:
                print(f"[ERROR] Port {self.port} sedang digunakan oleh aplikasi lain!")
                self.update_status(f"● Error: Port {self.port} sibuk/terpakai!", "#ef4444")
            else:
                print(f"[ERROR] Server error: {e}")

    def start_async_server(self):
        self.server_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.server_loop)
        try:
            self.server_loop.run_until_complete(self._run_server_async())
        except Exception as e:
            print(f"Server berhenti: {e}")

    def start_server_thread(self):
        self.server_thread = threading.Thread(target=self.start_async_server, daemon=True)
        self.server_thread.start()

    def exit_app(self, icon=None, item=None):
        self.is_running = False
        self.stop_cloudflare_tunnel()
        if self.tray_icon:
            self.tray_icon.stop()
        if self.server_loop:
            self.server_loop.call_soon_threadsafe(self.server_loop.stop)
        self.after(0, self.destroy)


# --- JALANKAN APLIKASI ---
if __name__ == "__main__":
    app = ModernRemoteServerApp()
    app.mainloop()