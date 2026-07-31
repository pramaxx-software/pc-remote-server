import asyncio
import base64
import io
import json
import os
import queue
import threading
import time

import customtkinter as ctk
import websockets
from PIL import Image

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

APP_VERSION = "1.0"


def get_config_path():
    appdata_dir = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "PramaxxRemoteClient")
    if not os.path.exists(appdata_dir):
        os.makedirs(appdata_dir, exist_ok=True)
    return os.path.join(appdata_dir, "client_config.json")


CONFIG_FILE = get_config_path()

DEFAULT_CONFIG = {
    "host": "",
    "port": 8765,
    "pin": "",
    "mouse_sensitivity": 1.5,
}


def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config.update(json.load(f))
        except Exception:
            pass
    return config


def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)
    except Exception:
        pass


# Map tkinter keysym -> nama key yang dikenali server
KEYSYM_MAP = {
    "Return": "enter", "KP_Enter": "enter",
    "BackSpace": "backspace",
    "Escape": "esc",
    "Tab": "tab",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "Super_L": "win", "Super_R": "win",
    "space": "space",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
    "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
    "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
}

SHORTCUT_PRESETS = [
    ("Copy", ["ctrl", "c"]),
    ("Paste", ["ctrl", "v"]),
    ("Cut", ["ctrl", "x"]),
    ("Undo", ["ctrl", "z"]),
    ("Select All", ["ctrl", "a"]),
    ("Save", ["ctrl", "s"]),
    ("Alt+Tab", ["alt", "tab"]),
    ("Win+D", ["win", "d"]),
]


class PramaxxRemoteClientApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config_data = load_config()

        self.loop = None
        self.loop_thread = None
        self.ws = None
        self.connected = False
        self.connecting = False
        self.streaming = False

        self.frame_queue = queue.Queue(maxsize=2)
        self.last_frame_time = 0.0
        self.fps_display = 0.0

        # State drag trackpad
        self._drag_last_x = None
        self._drag_last_y = None
        self._drag_moved = 0

        self.title(f"Pramaxx Remote Client (Test) v{APP_VERSION}")
        self.geometry("520x700")
        self.resizable(False, False)

        self.setup_ui()
        self.start_loop_thread()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self._poll_frame_queue)

    # ---------------------------------------------------------------
    # UI SETUP
    # ---------------------------------------------------------------
    def setup_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            header, text="Pramaxx Remote Client",
            font=ctk.CTkFont(size=19, weight="bold")
        ).pack(side="left")

        ctk.CTkLabel(
            header, text=f"v{APP_VERSION} (Test)",
            font=ctk.CTkFont(size=11), text_color="#6b7280"
        ).pack(side="right")

        self.setup_connection_panel()

        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=(8, 15))

        self.tab_keyboard = self.tabview.add("Keyboard")
        self.tab_trackpad = self.tabview.add("Trackpad")
        self.tab_screen = self.tabview.add("Screen")

        self.setup_keyboard_tab()
        self.setup_trackpad_tab()
        self.setup_screen_tab()

    def setup_connection_panel(self):
        panel = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=10)
        panel.pack(fill="x", padx=15, pady=(5, 5))

        row1 = ctk.CTkFrame(panel, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(row1, text="Host/IP:", font=ctk.CTkFont(size=11), width=55, anchor="w").pack(side="left")
        self.host_entry = ctk.CTkEntry(row1, placeholder_text="192.168.1.10 atau domain tunnel")
        self.host_entry.insert(0, self.config_data.get("host", ""))
        self.host_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))

        ctk.CTkLabel(row1, text="Port:", font=ctk.CTkFont(size=11), width=35, anchor="w").pack(side="left")
        self.port_entry = ctk.CTkEntry(row1, width=70)
        self.port_entry.insert(0, str(self.config_data.get("port", 8765)))
        self.port_entry.pack(side="left", padx=(4, 0))

        row2 = ctk.CTkFrame(panel, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(row2, text="PIN:", font=ctk.CTkFont(size=11), width=55, anchor="w").pack(side="left")
        self.pin_entry = ctk.CTkEntry(row2, show="*", width=110)
        self.pin_entry.insert(0, self.config_data.get("pin", ""))
        self.pin_entry.pack(side="left", padx=(4, 8))

        self.connect_btn = ctk.CTkButton(
            row2, text="Connect", command=self.on_connect_toggle,
            fg_color="#3b82f6", hover_color="#2563eb", width=100
        )
        self.connect_btn.pack(side="left", padx=(0, 8))

        self.conn_status_label = ctk.CTkLabel(
            row2, text="● Belum terhubung", font=ctk.CTkFont(size=11, weight="bold"), text_color="#6b7280"
        )
        self.conn_status_label.pack(side="left", padx=(4, 0))

    # --- Keyboard tab ---
    def setup_keyboard_tab(self):
        ctk.CTkLabel(
            self.tab_keyboard, text="Ketik di sini (langsung terkirim ke server):",
            font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=10, pady=(10, 2))

        self.type_entry = ctk.CTkEntry(self.tab_keyboard, placeholder_text="Klik lalu ketik...")
        self.type_entry.pack(fill="x", padx=10, pady=(0, 10))
        self.type_entry.bind("<KeyPress>", self.on_key_press_event)

        ctk.CTkLabel(
            self.tab_keyboard, text="Tombol Khusus:", font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=10, pady=(0, 4))

        special_frame = ctk.CTkFrame(self.tab_keyboard, fg_color="transparent")
        special_frame.pack(fill="x", padx=10, pady=(0, 10))

        special_keys = [
            "enter", "backspace", "esc", "tab",
            "up", "down", "left", "right",
        ]
        for i, key in enumerate(special_keys):
            btn = ctk.CTkButton(
                special_frame, text=key.upper(), width=95,
                command=lambda k=key: self.send_key_press(k)
            )
            btn.grid(row=i // 4, column=i % 4, padx=3, pady=3)

        ctk.CTkLabel(
            self.tab_keyboard, text="Shortcut Cepat:", font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=10, pady=(6, 4))

        shortcut_frame = ctk.CTkFrame(self.tab_keyboard, fg_color="transparent")
        shortcut_frame.pack(fill="x", padx=10, pady=(0, 10))

        for i, (label, keys) in enumerate(SHORTCUT_PRESETS):
            btn = ctk.CTkButton(
                shortcut_frame, text=label, width=95, fg_color="#374151", hover_color="#4b5563",
                command=lambda k=keys: self.send_shortcut(k)
            )
            btn.grid(row=i // 4, column=i % 4, padx=3, pady=3)

        ctk.CTkLabel(
            self.tab_keyboard, text="Shortcut Custom (pisahkan koma, mis. ctrl,shift,esc):",
            font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=10, pady=(6, 2))

        custom_frame = ctk.CTkFrame(self.tab_keyboard, fg_color="transparent")
        custom_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.custom_shortcut_entry = ctk.CTkEntry(custom_frame, placeholder_text="ctrl,alt,t")
        self.custom_shortcut_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ctk.CTkButton(
            custom_frame, text="Kirim", width=70, command=self.on_send_custom_shortcut
        ).pack(side="right")

    def on_key_press_event(self, event):
        keysym = event.keysym
        if not self.connected:
            return "break"

        if keysym in KEYSYM_MAP:
            self.send_key_press(KEYSYM_MAP[keysym])
        elif len(keysym) == 1:
            self.send_key_press(keysym)
        elif event.char and len(event.char) == 1 and event.char.isprintable():
            self.send_key_press(event.char)

        # Cegah entry lokal ikut menampilkan teks (biar ga dobel & tidak nyangkut)
        return "break"

    def send_key_press(self, key):
        self.send_json({"type": "press", "key": key})

    def send_shortcut(self, keys):
        self.send_json({"type": "shortcut", "keys": keys})

    def on_send_custom_shortcut(self):
        raw = self.custom_shortcut_entry.get().strip()
        if not raw:
            return
        keys = [k.strip().lower() for k in raw.split(",") if k.strip()]
        if keys:
            self.send_shortcut(keys)

    # --- Trackpad tab ---
    def setup_trackpad_tab(self):
        ctk.CTkLabel(
            self.tab_trackpad, text="Geser jari/mouse di area bawah untuk menggerakkan kursor.\nTap singkat = klik kiri.",
            font=ctk.CTkFont(size=11), text_color="#9ca3af", justify="left"
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self.trackpad_area = ctk.CTkFrame(self.tab_trackpad, fg_color="#232323", corner_radius=10, height=260)
        self.trackpad_area.pack(fill="x", padx=10, pady=(0, 10))
        self.trackpad_area.pack_propagate(False)

        pad_label = ctk.CTkLabel(self.trackpad_area, text="🖱  Area Trackpad", text_color="#4b5563")
        pad_label.place(relx=0.5, rely=0.5, anchor="center")

        for widget in (self.trackpad_area, pad_label):
            widget.bind("<ButtonPress-1>", self.on_trackpad_press)
            widget.bind("<B1-Motion>", self.on_trackpad_drag)
            widget.bind("<ButtonRelease-1>", self.on_trackpad_release)
            widget.bind("<MouseWheel>", self.on_trackpad_scroll_windows)
            widget.bind("<Button-4>", self.on_trackpad_scroll_linux_up)
            widget.bind("<Button-5>", self.on_trackpad_scroll_linux_down)

        click_frame = ctk.CTkFrame(self.tab_trackpad, fg_color="transparent")
        click_frame.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkButton(
            click_frame, text="Klik Kiri", command=lambda: self.send_mouse_click("left"),
            fg_color="#3b82f6", hover_color="#2563eb"
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        ctk.CTkButton(
            click_frame, text="Klik Kanan", command=lambda: self.send_mouse_click("right"),
            fg_color="#374151", hover_color="#4b5563"
        ).pack(side="left", expand=True, fill="x", padx=4)

        ctk.CTkButton(
            click_frame, text="Double Klik", command=lambda: self.send_mouse_click("left", double=True),
            fg_color="#374151", hover_color="#4b5563"
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

        sens_frame = ctk.CTkFrame(self.tab_trackpad, fg_color="transparent")
        sens_frame.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(sens_frame, text="Sensitivitas:", font=ctk.CTkFont(size=11)).pack(side="left")
        self.sens_slider = ctk.CTkSlider(
            sens_frame, from_=0.5, to=3.0, number_of_steps=25, command=self.on_sensitivity_change
        )
        self.sens_slider.set(self.config_data.get("mouse_sensitivity", 1.5))
        self.sens_slider.pack(side="left", fill="x", expand=True, padx=8)

    def on_sensitivity_change(self, value):
        self.config_data["mouse_sensitivity"] = round(float(value), 2)
        save_config(self.config_data)

    def on_trackpad_press(self, event):
        self._drag_last_x = event.x
        self._drag_last_y = event.y
        self._drag_moved = 0

    def on_trackpad_drag(self, event):
        if self._drag_last_x is None:
            self._drag_last_x, self._drag_last_y = event.x, event.y
            return
        dx = event.x - self._drag_last_x
        dy = event.y - self._drag_last_y
        self._drag_last_x, self._drag_last_y = event.x, event.y
        self._drag_moved += abs(dx) + abs(dy)

        sens = self.config_data.get("mouse_sensitivity", 1.5)
        if dx or dy:
            self.send_json({"type": "mouse_move", "dx": dx * sens, "dy": dy * sens})

    def on_trackpad_release(self, event):
        # Gerakan sangat kecil dianggap tap -> klik kiri
        if self._drag_moved < 4:
            self.send_mouse_click("left")
        self._drag_last_x = None
        self._drag_last_y = None
        self._drag_moved = 0

    def on_trackpad_scroll_windows(self, event):
        direction = 1 if event.delta > 0 else -1
        self.send_json({"type": "mouse_scroll", "dx": 0, "dy": direction})

    def on_trackpad_scroll_linux_up(self, event):
        self.send_json({"type": "mouse_scroll", "dx": 0, "dy": 1})

    def on_trackpad_scroll_linux_down(self, event):
        self.send_json({"type": "mouse_scroll", "dx": 0, "dy": -1})

    def send_mouse_click(self, button, double=False):
        self.send_json({"type": "mouse_click", "button": button, "double": double})

    # --- Screen tab ---
    def setup_screen_tab(self):
        top_row = ctk.CTkFrame(self.tab_screen, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(10, 6))

        self.stream_btn = ctk.CTkButton(
            top_row, text="Mulai Streaming", command=self.on_toggle_stream,
            fg_color="#10b981", hover_color="#059669"
        )
        self.stream_btn.pack(side="left")

        self.stream_status_label = ctk.CTkLabel(
            top_row, text="● Stream: Nonaktif", font=ctk.CTkFont(size=11, weight="bold"), text_color="#6b7280"
        )
        self.stream_status_label.pack(side="right")

        self.screen_frame = ctk.CTkFrame(self.tab_screen, fg_color="#141414", corner_radius=10, height=460)
        self.screen_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.screen_frame.pack_propagate(False)

        self.screen_image_label = ctk.CTkLabel(self.screen_frame, text="Belum ada gambar", text_color="#4b5563")
        self.screen_image_label.place(relx=0.5, rely=0.5, anchor="center")

    def on_toggle_stream(self):
        if not self.connected:
            self.stream_status_label.configure(text="● Sambungkan dulu ke server", text_color="#ef4444")
            return

        if not self.streaming:
            self.streaming = True
            self.send_json({"type": "stream_start"})
            self.stream_btn.configure(text="Stop Streaming", fg_color="#ef4444", hover_color="#dc2626")
            self.stream_status_label.configure(text="● Stream: Menyambung...", text_color="#f59e0b")
        else:
            self.streaming = False
            self.send_json({"type": "stream_stop"})
            self.stream_btn.configure(text="Mulai Streaming", fg_color="#10b981", hover_color="#059669")
            self.stream_status_label.configure(text="● Stream: Nonaktif", text_color="#6b7280")

    # ---------------------------------------------------------------
    # NETWORKING (WEBSOCKET CLIENT)
    # ---------------------------------------------------------------
    def start_loop_thread(self):
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def on_connect_toggle(self):
        if self.connected or self.connecting:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        host = self.host_entry.get().strip()
        port_str = self.port_entry.get().strip()
        pin = self.pin_entry.get().strip()

        if not host:
            self.update_conn_status("● Host wajib diisi", "#ef4444")
            return
        try:
            port = int(port_str)
        except ValueError:
            self.update_conn_status("● Port tidak valid", "#ef4444")
            return

        self.config_data.update({"host": host, "port": port, "pin": pin})
        save_config(self.config_data)

        self.connecting = True
        self.connect_btn.configure(text="Menghubungkan...", state="disabled")
        self.update_conn_status("● Menghubungkan...", "#f59e0b")

        asyncio.run_coroutine_threadsafe(self._connect_async(host, port), self.loop)

    async def _connect_async(self, host, port):
        uri = f"ws://{host}:{port}"
        try:
            self.ws = await websockets.connect(uri, max_size=None, ping_interval=20, ping_timeout=20)
            self.connected = True
            self.connecting = False
            self.after(0, lambda: self.connect_btn.configure(text="Disconnect", state="normal", fg_color="#ef4444", hover_color="#dc2626"))
            self.update_conn_status("● Terhubung", "#10b981")
            asyncio.ensure_future(self._receive_loop(), loop=self.loop)
        except Exception as e:
            self.connected = False
            self.connecting = False
            self.after(0, lambda: self.connect_btn.configure(text="Connect", state="normal", fg_color="#3b82f6", hover_color="#2563eb"))
            self.update_conn_status(f"● Gagal konek: {e}", "#ef4444")

    async def _receive_loop(self):
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = data.get("type")
                if msg_type == "frame":
                    self._enqueue_frame(data.get("data", ""))
                elif msg_type == "stream_error":
                    self.after(0, lambda m=data.get("message", ""): self.stream_status_label.configure(
                        text=f"● {m}", text_color="#ef4444"
                    ))
                    self.streaming = False
                    self.after(0, lambda: self.stream_btn.configure(text="Mulai Streaming", fg_color="#10b981", hover_color="#059669"))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected = False
            self.streaming = False
            self.after(0, lambda: self.connect_btn.configure(text="Connect", state="normal", fg_color="#3b82f6", hover_color="#2563eb"))
            self.after(0, lambda: self.stream_btn.configure(text="Mulai Streaming", fg_color="#10b981", hover_color="#059669"))
            self.update_conn_status("● Terputus dari server", "#6b7280")

    def disconnect(self):
        if self.ws and self.loop:
            asyncio.run_coroutine_threadsafe(self._disconnect_async(), self.loop)
        self.connected = False
        self.connecting = False

    async def _disconnect_async(self):
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass

    def send_json(self, payload):
        if not self.connected or not self.ws or not self.loop:
            return
        payload["pin"] = self.config_data.get("pin", "")
        try:
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)
        except Exception as e:
            print(f"Gagal mengirim data: {e}")

    def update_conn_status(self, text, color):
        self.after(0, lambda: self.conn_status_label.configure(text=text, text_color=color))

    # ---------------------------------------------------------------
    # SCREEN STREAM RENDERING
    # ---------------------------------------------------------------
    def _enqueue_frame(self, b64_data):
        if not b64_data:
            return
        try:
            raw = base64.b64decode(b64_data)
        except Exception:
            return

        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.frame_queue.put_nowait(raw)
        except queue.Full:
            pass

    def _poll_frame_queue(self):
        try:
            raw = self.frame_queue.get_nowait()
        except queue.Empty:
            raw = None

        if raw is not None:
            try:
                img = Image.open(io.BytesIO(raw))
                target_w = self.screen_frame.winfo_width() or 480
                target_h = self.screen_frame.winfo_height() or 460
                img.thumbnail((max(target_w - 10, 100), max(target_h - 10, 100)))

                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self.screen_image_label.configure(image=ctk_img, text="")
                self.screen_image_label.image = ctk_img

                now = time.time()
                if self.last_frame_time:
                    inst_fps = 1.0 / max(now - self.last_frame_time, 0.001)
                    self.fps_display = (self.fps_display * 0.8) + (inst_fps * 0.2)
                self.last_frame_time = now

                if self.streaming:
                    self.stream_status_label.configure(
                        text=f"● Stream: Aktif (~{self.fps_display:.0f} fps)", text_color="#10b981"
                    )
            except Exception as e:
                print(f"Gagal render frame: {e}")

        self.after(33, self._poll_frame_queue)

    # ---------------------------------------------------------------
    # CLOSE
    # ---------------------------------------------------------------
    def on_close(self):
        try:
            if self.streaming:
                self.send_json({"type": "stream_stop"})
            self.disconnect()
        except Exception:
            pass

        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

        self.destroy()


if __name__ == "__main__":
    app = PramaxxRemoteClientApp()
    app.mainloop()