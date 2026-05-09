"""
desktop_pet.py - A tkinter-based desktop pet player for Codex-style sprite atlases.

Features:
- Transparent, borderless window with sprite animation
- Drag to move, right-click context menu, double-click state cycling
- Chat-aware mode: polls state file to sync with WorkBuddy agent status
- Pixel-art speech bubble and completion OK button
- Sound toggle with persistent config
- State persists until explicitly changed (no auto-revert)

Usage:
    python desktop_pet.py --atlas <atlas.png> [--manifest <pet.json>] [--scale 2.0]
"""

import os
import sys
import json
import argparse
import random
import time
import ctypes
import winsound
import tkinter as tk

# Use a specific color for window transparency that won't clash with pet pixels
TRANSPARENT_COLOR = "#F0F0F2"

from PIL import Image, ImageTk

from pet_constants import (
    FRAME_WIDTH, FRAME_HEIGHT, COLUMNS, DEFAULT_FPS,
    WANDER_INTERVAL, WANDER_STEP, STATE_POLL_INTERVAL,
    STATE_NAMES, STATE_ALIASES, STATE_LABELS,
    STATE_FILE, PET_CONFIG_FILE, BUBBLE_BG, BUBBLE_TEXT,
    CTX_BAR_HEIGHT, CTX_BAR_PAD_X, CTX_BAR_PAD_Y,
    CTX_BAR_COLORS, CTX_BAR_BG, CTX_BAR_BORDER,
    MODEL_ENV, THINKING_ENABLED_ENV, THINKING_BUDGET_ENV,
)
from pixel_bubble import PixelBubble


# ── helpers ──

def _load_config() -> dict:
    """Load pet config from file, returning defaults if missing."""
    defaults = {"sound_enabled": True}
    try:
        if os.path.exists(PET_CONFIG_FILE):
            with open(PET_CONFIG_FILE, "r", encoding="utf-8") as f:
                defaults.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def _save_config(config: dict):
    """Persist pet config to disk (merges with existing)."""
    try:
        existing = {}
        if os.path.exists(PET_CONFIG_FILE):
            with open(PET_CONFIG_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.update(config)
        os.makedirs(os.path.dirname(PET_CONFIG_FILE), exist_ok=True)
        with open(PET_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except (OSError, json.JSONDecodeError):
        pass


# Capture foreground window at startup (before pet window steals focus)
_SAVED_HWND = None
try:
    _SAVED_HWND = ctypes.windll.user32.GetForegroundWindow()
except Exception:
    pass


def _focus_app_window():
    """Bring the Claude Code terminal window to foreground (Windows only)."""
    try:
        if _SAVED_HWND:
            # Preserve maximized/minimized state
            if ctypes.windll.user32.IsIconic(_SAVED_HWND):
                ctypes.windll.user32.ShowWindow(_SAVED_HWND, 9)  # SW_RESTORE
            elif ctypes.windll.user32.IsZoomed(_SAVED_HWND):
                ctypes.windll.user32.ShowWindow(_SAVED_HWND, 3)  # SW_SHOWMAXIMIZED
            # else: window is normal, don't resize — just focus
            ctypes.windll.user32.SetForegroundWindow(_SAVED_HWND)
            return
    except Exception:
        pass

    # Fallback: search for a window containing "Claude" in the title
    try:
        titles = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_callback(hwnd_enum, _):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd_enum)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd_enum, buf, length + 1)
            if "Claude" in buf.value or "claude" in buf.value:
                titles.append(hwnd_enum)
            return True

        ctypes.windll.user32.EnumWindows(enum_callback, 0)
        if titles:
            hwnd = titles[0]
            if ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 9)
            elif ctypes.windll.user32.IsZoomed(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 3)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


# ── main class ──

class DesktopPet:
    """A desktop pet window with sprite animation and chat-aware mode."""

    def __init__(self, atlas_path: str, manifest_path: str = None, scale: float = 2.0,
                 state_file: str = None, chat_aware: bool = True, debug_border: bool = False):
        self.scale = scale
        self.frame_w = int(FRAME_WIDTH * scale)
        self.frame_h = int(FRAME_HEIGHT * scale)
        self.debug_border = debug_border
        self.current_state = "idle"
        self.current_frame = 0
        self.dragging = False
        self.drag_offset = (0, 0)
        self._drag_prev_x = 0  # Track drag direction
        self._pre_drag_state = "idle"  # State before drag started
        self.wander_job = None
        self.chat_aware = chat_aware
        self.state_file = state_file or STATE_FILE
        self.last_state_mtime = 0
        self.last_chat_state: str | None = None

        # Config
        config = _load_config()
        self.sound_enabled = config.get("sound_enabled", True)
        self.agree_enabled = config.get("agree_enabled", True)

        # ── Load atlas ──
        self.atlas = Image.open(atlas_path).convert("RGBA")

        # ── Load manifest ──
        self.states: dict[str, dict] = {}
        if manifest_path and os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for s in manifest.get("states", []):
                self.states[s["name"]] = {
                    "row": s["row"], "frames": s["frames"],
                    "fps": s.get("fps", DEFAULT_FPS),
                    "durations": s.get("durations", []),
                }
        else:
            for i, name in enumerate(STATE_NAMES):
                self.states[name] = {"row": i, "frames": 8, "fps": DEFAULT_FPS}

        # ── Root window ──
        self.root = tk.Tk()
        self.root.title("Desktop Pet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        except Exception:
            pass

        self.main_frame = tk.Frame(self.root, bg=TRANSPARENT_COLOR)
        if self.debug_border:
            self.main_frame.config(highlightbackground="#FF00FF", highlightthickness=2)
        self.main_frame.pack()

        self.canvas = tk.Canvas(
            self.main_frame, width=self.frame_w, height=self.frame_h,
            bg=TRANSPARENT_COLOR, highlightthickness=0,
        )
        self.canvas.pack()

        # Image item on canvas (must exist before _animate)
        self.photo_item = self.canvas.create_image(0, 0, anchor="nw")

        # ── Context bar canvas ──
        self._ctx_bar_w = self.frame_w - CTX_BAR_PAD_X * 2
        self._ctx_canvas = tk.Canvas(
            self.main_frame,
            width=self.frame_w, height=CTX_BAR_HEIGHT + CTX_BAR_PAD_Y * 2,
            bg=TRANSPARENT_COLOR, highlightthickness=0,
        )
        self._ctx_canvas.pack()
        self._ctx_pct = 0
        self._ctx_used_tokens = 0
        self._ctx_total_tokens = 0

        # ── OK button (shown only during waving state) ──
        self.ok_btn_frame = tk.Frame(self.main_frame, bg=TRANSPARENT_COLOR)
        self.ok_btn = tk.Button(
            self.ok_btn_frame, text="OK",
            font=("Courier New", 10, "bold"),
            bg=BUBBLE_BG, fg=BUBBLE_TEXT,
            activebackground="#e8e8d8", activeforeground=BUBBLE_TEXT,
            relief="solid", borderwidth=2, padx=16, pady=2,
            cursor="hand2", command=self._on_ok_click,
        )
        self.ok_btn.pack(pady=(2, 4))
        self.ok_btn_frame.pack_forget()  # hidden initially

        # ── Pixel bubble ──
        self.bubble = PixelBubble(self.root)

        # ── Events ──
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<ButtonPress-3>", self._show_menu)

        # ── Context menu ──
        self.menu = tk.Menu(self.root, tearoff=0)

        # State switcher submenu
        self._state_menu = tk.Menu(self.menu, tearoff=0)
        for state_name in self.states:
            label = STATE_LABELS.get(state_name, state_name)
            self._state_menu.add_command(
                label=label,
                command=lambda s=state_name: self.set_state(s),
            )
        self.menu.add_cascade(label="切换状态", menu=self._state_menu)
        self.menu.add_separator()
        self.wander_var = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label="随机漫游", variable=self.wander_var,
                                   command=self._toggle_wander)
        self.menu.add_separator()
        self.agree_var = tk.BooleanVar(value=self.agree_enabled)
        self.menu.add_checkbutton(label="同意提示", variable=self.agree_var,
                                   command=self._toggle_agree)
        self.menu.add_separator()
        self.chat_aware_var = tk.BooleanVar(value=self.chat_aware)
        self.menu.add_checkbutton(label="聊天感知模式", variable=self.chat_aware_var,
                                   command=self._toggle_chat_aware)
        self.sound_var = tk.BooleanVar(value=self.sound_enabled)
        self.menu.add_checkbutton(label="提示音", variable=self.sound_var,
                                   command=self._toggle_sound)
        self.menu.add_separator()
        self.menu.add_command(label="模型信息", command=self._show_model_info)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

        # ── Pet switcher submenu ──
        self._pet_dir = os.path.dirname(os.path.dirname(os.path.abspath(atlas_path)))  # assets/
        self._current_pet_name = os.path.basename(os.path.dirname(atlas_path))
        self._build_pet_switcher()

        # ── Position at bottom-right ──
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f"+{screen_w - self.frame_w - 50}+{screen_h - self.frame_h - 50}")

        # ── Pre-cache frames ──
        self.photo_frames: dict[str, list] = {}
        self._precache_frames()

        # Context bar tracking
        self._config_mtime = 0

        # ── Initialise ──
        if self.chat_aware:
            self._init_state_file()

        self._animate()

        if self.chat_aware:
            self._poll_chat_state()
        self._start_wander()  # always on; polling auto-pauses during active states
        self._update_context()  # initial draw

    # ── State file ────────────────────────────────────────────

    def _init_state_file(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        if not os.path.exists(self.state_file):
            self._write_state_file("idle", "")

    def _write_state_file(self, state: str, message: str = ""):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"state": state, "message": message,
                           "timestamp": time.time()}, f, indent=2)
        except OSError:
            pass

    def _poll_chat_state(self):
        """Poll the state file for chat-driven state changes.
        Also checks pet_config.json mtime to drive context bar updates."""
        self._check_config_update()

        try:
            if os.path.exists(self.state_file):
                mtime = os.path.getmtime(self.state_file)
                if mtime > self.last_state_mtime:
                    self.last_state_mtime = mtime
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    new_state = data.get("state", "idle")
                    message = data.get("message", "")
                    anim_state = STATE_ALIASES.get(new_state, new_state)

                    if new_state != self.last_chat_state:
                        prev_state = self.last_chat_state
                        self.last_chat_state = new_state
                        if anim_state != "idle":
                            self._stop_wander()
                        self.set_state(anim_state)

                        # Show bubble (use default text if none provided)
                        if message:
                            self._show_bubble(message)
                        elif new_state == "agree":
                            self._show_bubble("需要确认操作")
                        else:
                            self.bubble.hide()

                        self._play_state_sound(new_state)

                        # Show appropriate button
                        if new_state == "agree" and self.agree_enabled:
                            self._show_agree_button()
                        elif new_state == "waving":
                            self._show_ok_button()
                        else:
                            self._hide_buttons()

                        # When conversation completes, refresh context bar after 1s
                        if new_state in ("idle", "waving") and prev_state not in ("idle", "waving", None):
                            self.root.after(1000, self._update_context)
        except (OSError, json.JSONDecodeError):
            pass

        self.root.after(STATE_POLL_INTERVAL, self._poll_chat_state)

    # ── Animation ─────────────────────────────────────────────

    def _precache_frames(self):
        """Pre-extract all frames as PhotoImages, preserving original alpha."""
        for state_name, info in self.states.items():
            row = info["row"]
            self.photo_frames[state_name] = []
            for col in range(info["frames"]):
                x1, y1 = col * FRAME_WIDTH, row * FRAME_HEIGHT
                frame = self.atlas.crop((x1, y1, x1 + FRAME_WIDTH, y1 + FRAME_HEIGHT))
                frame = frame.resize((self.frame_w, self.frame_h), Image.LANCZOS)
                self.photo_frames[state_name].append(ImageTk.PhotoImage(frame))

    def _animate(self):
        state = self.states.get(self.current_state)
        if state is None:
            return
        frames = self.photo_frames.get(self.current_state, [])
        if not frames:
            return

        self.current_frame %= len(frames)
        self.canvas.itemconfig(self.photo_item, image=frames[self.current_frame])

        # Determine delay: per-frame durations > flat fps
        durations = state.get("durations", [])
        if durations and self.current_frame < len(durations):
            delay = durations[self.current_frame]
        else:
            delay = int(1000 / state.get("fps", DEFAULT_FPS))

        self.current_frame += 1
        self.root.after(delay, self._animate)

    def set_state(self, state_name: str):
        if state_name in self.states:
            self.current_state = state_name
            self.current_frame = 0

    # ── Bubble & OK button ────────────────────────────────────

    def _show_bubble(self, text: str):
        if not text or not text.strip():
            return
        # Draw first so width/height are known, then position
        self.bubble._draw(text)
        pet_x = self.root.winfo_x()
        pet_y = self.root.winfo_y()
        content_top = self._get_content_top()
        offset_x = (self.frame_w - self.bubble.width) // 2 if self.bubble.width < self.frame_w else -20
        bx = pet_x + offset_x
        by = pet_y + content_top - self.bubble.height + 2
        self.bubble._win.geometry(f"+{bx}+{by}")
        self.bubble._win.deiconify()
        self.bubble._win.lift()
        self.bubble._visible = True

    def _get_content_top(self) -> int:
        """Find topmost non-transparent pixel row of the current frame (scaled)."""
        info = self.states.get(self.current_state)
        if not info:
            return 0
        row = info["row"]
        for py in range(FRAME_HEIGHT):
            for px in range(0, FRAME_WIDTH, 4):
                pixel = self.atlas.getpixel((px, row * FRAME_HEIGHT + py))
                if len(pixel) == 4 and pixel[3] > 0:
                    if not (pixel[0] > 240 and pixel[1] > 240 and pixel[2] > 240):
                        return int(py * self.scale)
        return 0

    def _get_content_bottom(self) -> int:
        """Find bottommost non-transparent pixel row (scaled)."""
        info = self.states.get(self.current_state)
        if not info:
            return self.frame_h
        row = info["row"]
        for py in range(FRAME_HEIGHT - 1, -1, -1):
            for px in range(0, FRAME_WIDTH, 4):
                pixel = self.atlas.getpixel((px, row * FRAME_HEIGHT + py))
                if len(pixel) == 4 and pixel[3] > 0:
                    if not (pixel[0] > 240 and pixel[1] > 240 and pixel[2] > 240):
                        return int((py + 1) * self.scale)
        return self.frame_h

    def _show_agree_button(self):
        """Show '同意' button that focuses WorkBuddy for manual approval."""
        self.ok_btn.config(text="同意", command=self._on_agree_click)
        self.ok_btn_frame.pack(fill="x", padx=4)

    def _show_ok_button(self):
        """Show 'OK' button for task completion."""
        self.ok_btn.config(text="OK", command=self._on_ok_click)
        self.ok_btn_frame.pack(fill="x", padx=4)

    def _hide_buttons(self):
        self.ok_btn_frame.pack_forget()

    def _on_ok_click(self):
        _focus_app_window()
        self.set_state("idle")
        self.bubble.hide()
        self._hide_buttons()
        self._write_state_file("idle", "")

    def _on_agree_click(self):
        """Agree button: focus WorkBuddy so user can manually approve."""
        _focus_app_window()
        self.set_state("idle")
        self.bubble.hide()
        self._hide_buttons()
        self._write_state_file("idle", "")

    # ── Sound ─────────────────────────────────────────────────

    def _play_state_sound(self, state: str):
        if not self.sound_enabled:
            return
        try:
            if state in ("waving", "agree"):
                winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    def _toggle_agree(self):
        self.agree_enabled = self.agree_var.get()
        _save_config({"agree_enabled": self.agree_enabled})

    def _toggle_sound(self):
        self.sound_enabled = self.sound_var.get()
        _save_config({"sound_enabled": self.sound_enabled})

    def _toggle_chat_aware(self):
        self.chat_aware = self.chat_aware_var.get()
        if self.chat_aware:
            self._init_state_file()
            self._poll_chat_state()
        # Wander stays on; polling auto-pauses it during non-idle states

    def _toggle_wander(self):
        if self.wander_var.get():
            self._start_wander()
        else:
            self._stop_wander()

    # ── Drag & input ──────────────────────────────────────────

    def _on_press(self, event):
        self.dragging = True
        widget = event.widget
        x_root = widget.winfo_rootx() + event.x
        y_root = widget.winfo_rooty() + event.y
        self.drag_offset = (x_root - self.root.winfo_x(),
                           y_root - self.root.winfo_y())
        self._drag_prev_x = x_root
        self._pre_drag_state = self.current_state

    def _on_drag(self, event):
        if not self.dragging:
            return
        widget = event.widget
        x_root = widget.winfo_rootx() + event.x
        y_root = widget.winfo_rooty() + event.y
        dx = x_root - self._drag_prev_x

        # Set animation based on drag direction
        if dx > 3:
            self.set_state("running-right")
        elif dx < -3:
            self.set_state("running-left")

        self._drag_prev_x = x_root

        x = x_root - self.drag_offset[0]
        y = y_root - self.drag_offset[1]
        self.root.geometry(f"+{x}+{y}")
        if self.bubble.visible:
            offset_x = (self.frame_w - self.bubble.width) // 2 if self.bubble.width < self.frame_w else -20
            ct = self._get_content_top()
            self.bubble.move(x + offset_x, y + ct - self.bubble.height + 2)

    def _on_release(self, event):
        self.dragging = False
        self.set_state(self._pre_drag_state)

    def _on_double_click(self, event):
        names = list(self.states.keys())
        idx = names.index(self.current_state) if self.current_state in names else 0
        self.set_state(names[(idx + 1) % len(names)])

    def _show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    # ── Wander ────────────────────────────────────────────────

    def _start_wander(self):
        if self.wander_job:
            return
        self.wander_var.set(True)

        def wander():
            if self.dragging:
                self.wander_job = self.root.after(WANDER_INTERVAL, wander)
                return
            action = random.choice(["idle", "move_right", "move_left", "jump", "wave"])
            if action == "idle":
                self.set_state("idle")
            elif action == "move_right":
                self._move_pet(WANDER_STEP, 0)
                self.set_state("running-right")
            elif action == "move_left":
                self._move_pet(-WANDER_STEP, 0)
                self.set_state("running-left")
            elif action == "jump":
                self.set_state("jumping")
            elif action == "wave":
                self.set_state("waving")
            if action != "idle":
                self.root.after(2000, lambda: self.set_state("idle") if not self.dragging else None)
            self.wander_job = self.root.after(WANDER_INTERVAL, wander)

        self.wander_job = self.root.after(WANDER_INTERVAL, wander)

    def _stop_wander(self):
        self.wander_var.set(False)
        if self.wander_job:
            self.root.after_cancel(self.wander_job)
            self.wander_job = None

    def _move_pet(self, dx: int, dy: int):
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(x, sw - self.frame_w))
        y = max(0, min(y, sh - self.frame_h - 30))
        self.root.geometry(f"+{x}+{y}")

    # ── Pet switcher ─────────────────────────────────────────

    def _build_pet_switcher(self):
        """Scan assets/ for available pets and build the switcher submenu."""
        self._pet_menu = tk.Menu(self.menu, tearoff=0)
        pets = self._scan_pets()

        if not pets:
            self._pet_menu.add_command(label="(无其他宠物)", state="disabled")
        else:
            for name, atlas_path, manifest_path in pets:
                check = "✓ " if name == self._current_pet_name else "  "
                self._pet_menu.add_command(
                    label=f"{check}{name}",
                    command=lambda n=name, a=atlas_path, m=manifest_path: self._switch_pet(n, a, m),
                )

        # Insert switcher after state submenu and before退出
        self.menu.insert_cascade(1, label="切换宠物", menu=self._pet_menu)

    def _scan_pets(self) -> list[tuple[str, str, str]]:
        """Scan assets directory for available pets. Returns [(name, atlas, manifest), ...]."""
        pets = []
        try:
            for entry in sorted(os.listdir(self._pet_dir)):
                pet_dir = os.path.join(self._pet_dir, entry)
                if not os.path.isdir(pet_dir):
                    continue
                atlas = os.path.join(pet_dir, f"{entry}_atlas.png")
                manifest = os.path.join(pet_dir, "pet.json")
                if os.path.exists(atlas) and os.path.exists(manifest):
                    pets.append((entry, atlas, manifest))
        except OSError:
            pass
        return pets

    def _switch_pet(self, name: str, atlas: str, manifest: str):
        """Switch to a different pet by restarting the process."""
        if name == self._current_pet_name:
            return
        # Save preference
        _save_config({"active_pet": name})
        # Spawn new instance with new pet, then quit
        try:
            import subprocess
            subprocess.Popen(
                [sys.executable, __file__, "--atlas", atlas,
                 "--manifest", manifest, "--scale", str(self.scale)],
            )
        except Exception:
            pass
        self._quit()

    # ── Context bar ──────────────────────────────────────────

    def _draw_context_bar(self):
        """Draw the context usage bar."""
        self._ctx_canvas.delete("all")
        pct = max(0, min(100, self._ctx_pct))
        bw = self._ctx_bar_w
        bh = CTX_BAR_HEIGHT
        px = CTX_BAR_PAD_X
        py = CTX_BAR_PAD_Y

        # Background
        self._ctx_canvas.create_rectangle(
            px, py, px + bw, py + bh,
            fill=CTX_BAR_BG, outline=CTX_BAR_BORDER, width=1,
        )

        if pct <= 0:
            if self._ctx_tokens_text():
                self._ctx_canvas.create_text(
                    px + bw / 2, py + bh / 2, text=self._ctx_tokens_text(),
                    font=("Courier New", 7), fill="#888888", anchor="center",
                )
            return

        # Color selection
        if pct < 60:
            color = CTX_BAR_COLORS["low"]
        elif pct < 85:
            color = CTX_BAR_COLORS["medium"]
        else:
            color = CTX_BAR_COLORS["high"]

        fill_w = int(bw * pct / 100)
        self._ctx_canvas.create_rectangle(
            px, py, px + fill_w, py + bh,
            fill=color, outline="", width=0,
        )

        # Percentage label
        label = self._ctx_tokens_text() or f"{pct}%"
        self._ctx_canvas.create_text(
            px + bw / 2, py + bh / 2, text=label,
            font=("Courier New", 7, "bold"), fill="#ffffff", anchor="center",
        )

    def _ctx_tokens_text(self) -> str:
        """Return a tokens-used/total string if available."""
        if self._ctx_used_tokens > 0 and self._ctx_total_tokens > 0:
            uk = self._ctx_used_tokens / 1000
            tk = self._ctx_total_tokens / 1000
            return f"{uk:.0f}K/{tk:.0f}K"
        return ""

    def _check_config_update(self):
        """Check pet_config.json mtime; redraw context bar if changed."""
        try:
            if os.path.exists(PET_CONFIG_FILE):
                mtime = os.path.getmtime(PET_CONFIG_FILE)
                if mtime > self._config_mtime:
                    self._config_mtime = mtime
                    self._update_context()
        except OSError:
            pass

    def _update_context(self):
        """Read context from config file and redraw the bar (one-shot, event-driven)."""
        try:
            config = _load_config()
            used = config.get("context_used", 0)
            total = config.get("context_total", 0)
            self._ctx_used_tokens = int(used)
            self._ctx_total_tokens = int(total)
            if total > 0:
                self._ctx_pct = int(used / total * 100)
            else:
                self._ctx_pct = 0
        except Exception:
            pass
        self._draw_context_bar()

    # ── Model info dialog ─────────────────────────────────────

    def _show_model_info(self):
        """Show a popup dialog with current model and context information."""
        import os as _os
        import re as _re

        model = _os.environ.get(MODEL_ENV, "未知")

        # Detect thinking support from model name
        _thinking_models = (
            "claude-opus", "claude-sonnet", "claude-haiku",
            "deepseek-v4", "deepseek-r1", "deepseek-reasoner",
        )
        _model_lower = model.lower()
        _supports_thinking = any(tm in _model_lower for tm in _thinking_models)

        thinking_mode = "已启用" if _supports_thinking else "未启用"

        # Thinking intensity from budget env var or config
        thinking_budget = _os.environ.get(THINKING_BUDGET_ENV, "")
        thinking_intensity = "标准"
        if thinking_budget:
            try:
                tb = int(thinking_budget)
                if tb <= 1024:
                    thinking_intensity = "低"
                elif tb <= 4096:
                    thinking_intensity = "中"
                elif tb <= 16384:
                    thinking_intensity = "高"
                else:
                    thinking_intensity = "最大"
            except ValueError:
                pass
        else:
            # Infer from model: larger models get higher thinking by default
            if "opus" in _model_lower or "pro" in _model_lower:
                thinking_intensity = "高"

        # Context window from model name
        context_total = self._ctx_total_tokens
        if context_total <= 0:
            m = _re.search(r'\[(\d+)([km])\]', model)
            if m:
                val = int(m.group(1))
                unit = m.group(2).lower()
                context_total = val * 1000 if unit == 'k' else val * 1000000

        context_used = self._ctx_used_tokens
        ctx_pct = self._ctx_pct

        # Build info text
        lines = [
            f"模型: {model}",
            f"思考模式: {thinking_mode}",
            f"思考强度: {thinking_intensity}",
        ]
        if context_total > 0:
            if context_used > 0:
                lines.append(f"上下文: {context_used:,} / {context_total:,} tokens ({ctx_pct}%)")
            else:
                lines.append(f"上下文窗口: {context_total:,} tokens")
        else:
            lines.append("上下文: 未知")

        info_text = "\n".join(lines)

        # Create a simple dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("模型信息")
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#2a2a2a")

        # Position near the pet
        px = self.root.winfo_x()
        py = self.root.winfo_y()
        dialog.geometry(f"+{px}+{py - 20}")

        frame = tk.Frame(dialog, bg="#2a2a2a", padx=12, pady=10)
        frame.pack()

        tk.Label(
            frame, text=info_text,
            font=("Courier New", 9),
            bg="#2a2a2a", fg="#e0e0e0",
            justify="left",
        ).pack()

        # Close button
        btn_frame = tk.Frame(frame, bg="#2a2a2a")
        btn_frame.pack(pady=(8, 0))
        tk.Button(
            btn_frame, text="关闭",
            font=("Courier New", 9, "bold"),
            bg="#555555", fg="#ffffff",
            activebackground="#777777", activeforeground="#ffffff",
            relief="flat", padx=16, pady=2,
            command=dialog.destroy,
        ).pack()

        # Click outside to close
        dialog.bind("<Button-1>", lambda e: dialog.destroy())
        dialog.focus_set()

    # ── Lifecycle ─────────────────────────────────────────────

    def _quit(self):
        self._stop_wander()
        self.bubble.hide()
        self.bubble.destroy()
        try:
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
        except OSError:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── entry point ──

def main():
    parser = argparse.ArgumentParser(description="WorkBuddy Desktop Pet Player")
    parser.add_argument("--atlas", required=True, help="Path to sprite atlas PNG")
    parser.add_argument("--manifest", default=None, help="Path to pet.json manifest")
    parser.add_argument("--scale", type=float, default=2.0, help="Display scale factor")
    parser.add_argument("--state-file", default=None, help="Path to chat state JSON file")
    parser.add_argument("--no-chat-aware", action="store_true",
                        help="Disable chat-aware mode")
    parser.add_argument("--debug-border", action="store_true",
                        help="Show debug border around pet window")
    args = parser.parse_args()

    if not os.path.exists(args.atlas):
        print(f"ERROR: Atlas not found: {args.atlas}", file=sys.stderr)
        sys.exit(1)

    pet = DesktopPet(
        args.atlas, args.manifest, args.scale,
        state_file=args.state_file,
        chat_aware=not args.no_chat_aware,
        debug_border=args.debug_border,
    )
    pet.run()


if __name__ == "__main__":
    main()
