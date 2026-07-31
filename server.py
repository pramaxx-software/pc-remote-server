import qrcode
import asyncio
import json
import os
import random
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
from pynput.keyboard import Controller, Key
import pystray
from PIL import Image, ImageDraw

# Set tema tampilan Modern
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

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

# ⚠️ GANTI DENGAN URL ENDPOINT API WEB PANEL LO NANTI ⚠️
WEB_PANEL_API_URL = "https://pc-remote.pramaxx.biz.id/api/validate_token"
WEB_PANEL_SYNC_URL = "https://pc-remote.pramaxx.biz.id/api/sync_settings"
WEB_PANEL_HEARTBEAT_URL = "https://pc-remote.pramaxx.biz.id/api/heartbeat"
# WEB_PANEL_API_URL = "http://localhost:8000/api/validate_token"
# WEB_PANEL_SYNC_URL = "http://localhost:8000/api/sync_settings"
# WEB_PANEL_HEARTBEAT_URL = "http://localhost:8000/api/heartbeat"

DEFAULT_CONFIG = {
    "device_id": "",   # Digenerate otomatis saat pertama run
    "port": 8765,
    "mode": "Lokal (LAN)",
    "web_token": "",   # Token hash yang diinput user
    "cf_token": "",    # Token asli Cloudflare hasil dari backend
    "security_pin": "",
    "auto_minimize": False
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

# --- 1. SETUP KEYBOARD CONTROLLER & SPECIAL KEYS ---
keyboard = Controller()

SPECIAL_KEYS = {
    "enter": Key.enter, "space": Key.space, "backspace": Key.backspace,
    "tab": Key.tab, "esc": Key.esc, "up": Key.up, "down": Key.down,
    "left": Key.left, "right": Key.right, "ctrl": Key.ctrl, "alt": Key.alt,
    "shift": Key.shift, "cmd": Key.cmd, "win": Key.cmd,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12
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
        self.server_loop = None
        self.server_thread = None
        self.tray_icon = None
        self.cf_process = None
        self.cf_thread = None
        
        self.title("Pramaxx Remote Keyboard Server")
        self.geometry("440x510")
        self.resizable(False, False)
        
        self.setup_ui()
        self.setup_tray()
        
        # Jalankan server WebSocket & Cloudflare
        self.start_server_thread()
        self.check_and_init_cloudflare()
        
        # Jalankan fitur Heartbeat di background
        self.start_heartbeat_thread()
        
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self.on_minimize)

    def setup_ui(self):
        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=(5, 15))
        
        self.tab_status = self.tabview.add("Status")
        self.tab_settings = self.tabview.add("Pengaturan")
        
        self.setup_status_tab()
        self.setup_settings_tab()

    def setup_status_tab(self):
        ctk.CTkLabel(
            self.tab_status, text="Pramaxx Remote Server", 
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(10, 5))
        
        info_frame = ctk.CTkFrame(self.tab_status, fg_color="#2b2b2b", corner_radius=8)
        info_frame.pack(fill="x", padx=10, pady=5)
        
        self.mode_label = ctk.CTkLabel(
            info_frame, text=f"Mode: {self.config_data.get('mode', 'Lokal (LAN)')}", 
            font=ctk.CTkFont(size=11, slant="italic")
        )
        self.mode_label.pack(pady=(5,0))
        
        self.ip_label = ctk.CTkLabel(
            info_frame, text=f"{self.local_ip}:{self.port}", 
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#3b82f6"
        )
        self.ip_label.pack(pady=(0, 5))

        self.status_label = ctk.CTkLabel(
            self.tab_status, text="● LAN: Menunggu koneksi...", 
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#f59e0b"
        )
        self.status_label.pack(pady=(10, 2))

        self.cf_status_label = ctk.CTkLabel(
            self.tab_status, text="● Pramaxx Tunnel: Nonaktif (Mode Lokal)", 
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#6b7280"
        )

        self.cf_status_label.pack(pady=(2, 10))

        # --- TAMBAHAN: Tombol Tampilkan QR Code ---
        ctk.CTkButton(
            self.tab_status, text="Tampilkan QR Code", command=self.show_qr_code,
            fg_color="#10b981", hover_color="#059669"
        ).pack(pady=(5, 5))

        # ctk.CTkButton(
        #     self.tab_status, text="Sembunyikan ke Tray", command=self.hide_to_tray,
        #     fg_color="#374151", hover_color="#4b5563"
        # ).pack(side="bottom", pady=10)
        
    # --- FITUR QR CODE ---
    def show_qr_code(self):
        # Tentukan IP atau Domain berdasarkan mode saat ini
        mode = self.config_data.get("mode", "Lokal (LAN)")
        
        if mode == "Online (Cloudflare Tunnel)":
            # Pake domain dari Cloudflare (kalo kosong, otomatis balik ke LAN)
            ip_domain = self.config_data.get("tunnel_url", self.local_ip) 
            # Bersihkan https:// dari tunnel url jika ada
            ip_domain = ip_domain.replace("https://", "").replace("http://", "")
        else:
            ip_domain = self.local_ip

        port = self.port
        pin = self.config_data.get("security_pin", "")

        # Format JSON sesuai yang diminta sama Android App lo
        qr_payload = json.dumps({
            "ip": ip_domain,
            "port": str(port),
            "pin": pin
        })

        try:
            # Generate Gambar QR Code
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(qr_payload)
            qr.make(fit=True)
            qr_wrapper = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_wrapper.get_image()

            # Bikin Jendela Modal / Popup Baru
            qr_window = ctk.CTkToplevel(self)
            qr_window.title("Scan QR Code")
            qr_window.geometry("300x360")
            qr_window.attributes("-topmost", True)
            qr_window.resizable(False, False)

            ctk.CTkLabel(qr_window, text="Scan via Aplikasi Android", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))

            # Render QR Code ke Layar
            ctk_qr = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(220, 220))
            lbl_qr = ctk.CTkLabel(qr_window, image=ctk_qr, text="")
            lbl_qr.pack(pady=10)

            ctk.CTkLabel(qr_window, text=f"PIN: {pin if pin else 'Tidak Ada'}", font=ctk.CTkFont(size=16, weight="bold", family="Consolas"), text_color="#10b981").pack(pady=5)
            
        except Exception as e:
            print(f"Gagal memuat QR Code: {e}")

    def setup_settings_tab(self):
        

        ctk.CTkLabel(self.tab_settings, text="Mode Koneksi:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0,0))
        self.mode_option = ctk.CTkOptionMenu(
            self.tab_settings, values=["Lokal (LAN)", "Online (Cloudflare Tunnel)"],
            command=self.on_mode_change
        )
        self.mode_option.set(self.config_data.get("mode", "Lokal (LAN)"))
        self.mode_option.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.tab_settings, text="Port Server Lokal:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0,0))
        self.port_entry = ctk.CTkEntry(self.tab_settings, placeholder_text="8765")
        self.port_entry.insert(0, str(self.config_data.get("port", 8765)))
        self.port_entry.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.tab_settings, text="Token Koneksi Online:", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0,0))
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

        ctk.CTkLabel(self.tab_settings, text="PIN Keamanan (Akses Android App):", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(0,0))
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

        self.save_btn = ctk.CTkButton(
            self.tab_settings, text="Simpan & Terapkan Semua", command=self.save_settings,
            fg_color="#10b981", hover_color="#059669"
        )
        self.save_btn.pack(fill="x", padx=10, side="bottom", pady=5)

    # --- 3. FITUR HEARTBEAT / MONITORING ---
    def start_heartbeat_thread(self):
        """Memulai thread background untuk kirim ping ke web panel setiap 3 menit."""
        threading.Thread(target=self._heartbeat_worker, daemon=True).start()

    def _heartbeat_worker(self):
        # Tunggu 3 detik awal agar server websocket siap dulu
        time.sleep(3)
        while self.is_running:
            try:
                payload = json.dumps({
                    "device_id": self.device_id,
                    "web_token": self.config_data.get("web_token", ""),
                    "mode": self.config_data.get("mode", "Lokal (LAN)"),
                    "port": self.port,
                    "local_ip": self.local_ip,
                    "active_clients": self.active_clients,
                    "status": "online"
                }).encode("utf-8")
                
                req = urllib.request.Request(
                    WEB_PANEL_HEARTBEAT_URL, 
                    data=payload, 
                    headers={"Content-Type": "application/json", "User-Agent": "PramaxxRemoteKeyboardClient/1.0"},
                    method="POST"
                )
                
                # Kirim senyap dengan timeout 5 detik
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass # Heartbeat sukses
            except Exception:
                # Gagal ping (misal offline), abaikan tanpa error agar tidak mengganggu program
                pass
                
            # Tidur selama 180 detik (3 menit), dicicil per detik agar bisa cepat exit saat aplikasi ditutup
            for _ in range(180):
                if not self.is_running:
                    break
                time.sleep(1)

    # --- 4. AKSI VALIDASI TOKEN & SIMPAN ---
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
            payload = json.dumps({
                "token": web_token,
                "device_id": self.device_id,
                "port": self.port
            }).encode("utf-8")
            
            req = urllib.request.Request(
                WEB_PANEL_API_URL, 
                data=payload, 
                headers={"Content-Type": "application/json", "User-Agent": "PramaxxRemoteKeyboardClient/1.0"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = response.read().decode("utf-8")
                res_data = json.loads(res_body)
                
            if res_data.get("success") or res_data.get("valid"):
                real_cf_token = res_data.get("cf_token")
                if not real_cf_token:
                    raise ValueError("Token CF asli tidak ada di response server.")
                
                new_pin = res_data.get('pin');
                if not new_pin:
                    raise ValueError("Security PIN Tidak Valid!")
                
                tunnel_url = res_data.get("cf_tunnel", "")
                self.config_data["tunnel_url"] = tunnel_url

                # new_pin = f"{random.randint(0, 999999):06d}"
                
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
                print(f"Validation Error: {payload}")
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
            
        new_config = {
            "device_id": self.device_id,
            "mode": self.mode_option.get(),
            "port": new_port,
            "web_token": self.token_entry.get().strip(),
            "cf_token": self.config_data.get("cf_token", ""),
            "security_pin": self.config_data.get("security_pin", ""),
            "auto_minimize": False
        }
        
        save_config(new_config)
        self.config_data = new_config
        self.port = new_port
        self.ip_label.configure(text=f"{self.local_ip}:{self.port}")
        self.check_and_init_cloudflare()
        
        self.save_btn.configure(text="Syncing to Web Panel...", fg_color="#f59e0b", state="disabled")
        threading.Thread(target=self._sync_settings_worker, args=(new_config,), daemon=True).start()

    def _sync_settings_worker(self, config_data):
        try:
            payload = json.dumps({
                "device_id": config_data["device_id"],
                "web_token": config_data["web_token"],
                "port": config_data["port"],
                "mode": config_data["mode"],
                "local_ip": self.local_ip
            }).encode("utf-8")
            
            req = urllib.request.Request(
                WEB_PANEL_SYNC_URL, 
                data=payload, 
                headers={"Content-Type": "application/json", "User-Agent": "PramaxxRemoteKeyboardClient/1.0"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=7) as response:
                res_body = response.read().decode("utf-8")
                res_data = json.loads(res_body)
                
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

    # --- 5. CLOUDFLARED TUNNEL MANAGEMENT ---
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

    # --- 6. SYSTEM TRAY MANAGEMENT ---
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

    # --- 7. LOGIKA WEBSOCKET SERVER (DENGAN CLIENT COUNTER) ---
    async def websocket_handler(self, websocket):
        client_ip = websocket.remote_address[0]
        print(f"[{client_ip}] Terhubung!")
        
        # Tambah counter client aktif saat ada yang konek
        self.active_clients += 1
        self.update_status(f"● LAN: Terhubung ({client_ip}) - {self.active_clients} Client", "#10b981")

        try:
            async for message in websocket:
                self.process_command(message)
        except websockets.exceptions.ConnectionClosed:
            print(f"[{client_ip}] Terputus!")
        finally:
            # Kurangi counter saat disconnect
            self.active_clients = max(0, self.active_clients - 1)
            if self.active_clients > 0:
                self.update_status(f"● LAN: Terhubung - {self.active_clients} Client Aktif", "#10b981")
            else:
                self.update_status("● LAN: Menunggu koneksi...", "#f59e0b")

    def process_command(self, message):
        try:
            data = json.loads(message)
            server_pin = self.config_data.get("security_pin", "")
            if server_pin and data.get("pin") != server_pin:
                print("PIN Keamanan Salah! Perintah ditolak.")
                return

            cmd_type = data.get("type")
            if cmd_type == "press":
                key_val = get_key_obj(data.get("key"))
                keyboard.press(key_val)
                keyboard.release(key_val)
            elif cmd_type == "shortcut":
                keys = [get_key_obj(k) for k in data.get("keys", [])]
                for k in keys: keyboard.press(k)
                for k in reversed(keys): keyboard.release(k)
        except Exception as e:
            print(f"Error memproses data: {e}")

    # --- 8. THREADING & ASYNCIO MANAGEMENT ---
    async def _run_server_async(self):
        try:
            # Menggunakan websockets.serve dengan reuse_address untuk mencegah error 10048 sementara
            async with websockets.serve(self.websocket_handler, "0.0.0.0", self.port):
                print(f"Server berjalan di ws://0.0.0.0:{self.port}")
                await asyncio.Future()
        except OSError as e:
            if e.errno == 10048:
                print(f"[ERROR] Port {self.port} sedang digunakan oleh aplikasi lain! Tutup aplikasi tersebut atau ganti port di pengaturan.")
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
        # Hentikan semua looping background
        self.is_running = False
        self.stop_cloudflare_tunnel()
        if self.tray_icon: self.tray_icon.stop()
        if self.server_loop: self.server_loop.call_soon_threadsafe(self.server_loop.stop)
        self.after(0, self.destroy)

# --- 9. JALANKAN APLIKASI ---
if __name__ == "__main__":
    app = ModernRemoteServerApp()
    app.mainloop()