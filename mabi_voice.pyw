# -*- coding: utf-8 -*-
"""마비노기 모바일 음성 채팅 — 창 + 트레이 상주 프로그램.

pythonw 로 띄우면 콘솔 없이 배경에서 돈다. 실행.bat 을 쓰면 된다.
창을 닫아도 꺼지지 않고 트레이로 내려간다. 끄려면 트레이 메뉴의 종료.
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core

BG = "#1e1f26"
FG = "#e6e6ea"
SUB = "#9aa0aa"
ACCENT = "#7fd1a0"
WARN = "#e8a33d"
BAD = "#e06c6c"
PANEL = "#272935"


class App:
    def __init__(self):
        self.s = core.load_settings()
        self.events = queue.Queue()
        self.level = 0.0
        self.tray = None
        self.engine = None
        self.overlay = None
        self._hiding = False        # withdraw 가 스스로 Unmap 을 부르는 걸 걸러낸다

        self.root = tk.Tk()
        self.root.title("마비노기 음성 채팅")
        self.root.configure(bg=BG)
        self.root.geometry("560x520")
        self.root.minsize(480, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        # 최소화도 닫기와 같게 다룬다. 작업표시줄에 남기지 않고 오버레이로 보낸다.
        self.root.bind("<Unmap>", self._on_unmap)

        self._build()
        self.root.after(50, self._drain)
        self.root.after(60, self._tick_level)

        threading.Thread(target=self._boot, daemon=True).start()
        self._start_tray()

    # ------------------------------------------------------------ 화면
    def _build(self):
        pad = dict(padx=14)

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", pady=(14, 6), **pad)

        self.dot = tk.Label(head, text="●", bg=BG, fg=SUB, font=("Segoe UI", 15))
        self.dot.pack(side="left")
        self.state_lbl = tk.Label(head, text="준비 중", bg=BG, fg=FG,
                                  font=("Malgun Gothic", 13, "bold"))
        self.state_lbl.pack(side="left", padx=(6, 0))

        self.toggle_btn = tk.Button(head, text="듣기 끄기 (F9)", command=self.toggle,
                                    bg=PANEL, fg=FG, relief="flat", bd=0,
                                    activebackground="#333644", activeforeground=FG,
                                    font=("Malgun Gothic", 9), padx=12, pady=5,
                                    cursor="hand2")
        self.toggle_btn.pack(side="right")

        self.info_lbl = tk.Label(self.root, text="", bg=BG, fg=SUB, anchor="w",
                                 justify="left", font=("Malgun Gothic", 9))
        self.info_lbl.pack(fill="x", pady=(0, 8), **pad)

        # 음량 막대
        meter = tk.Frame(self.root, bg=BG)
        meter.pack(fill="x", pady=(0, 10), **pad)
        tk.Label(meter, text="음량", bg=BG, fg=SUB,
                 font=("Malgun Gothic", 9)).pack(side="left")
        self.meter = tk.Canvas(meter, height=10, bg=PANEL, highlightthickness=0)
        self.meter.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.bar = self.meter.create_rectangle(0, 0, 0, 10, fill=ACCENT, width=0)
        self.thr = self.meter.create_line(0, 0, 0, 10, fill=WARN, width=2)

        # 기록
        box = tk.Frame(self.root, bg=PANEL)
        box.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(box, bg=PANEL, fg=FG, relief="flat", bd=0, wrap="word",
                           font=("Malgun Gothic", 9), padx=10, pady=8,
                           state="disabled", height=10)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("sent", foreground=ACCENT)
        self.log.tag_configure("dropped", foreground=SUB)
        self.log.tag_configure("error", foreground=BAD)
        self.log.tag_configure("info", foreground=SUB)
        self.log.tag_configure("listen", foreground=WARN)
        self.log.tag_configure("meta", foreground=SUB, font=("Malgun Gothic", 8))

        # 설정
        cfg = tk.Frame(self.root, bg=BG)
        cfg.pack(fill="x", pady=(10, 4), **pad)

        tk.Label(cfg, text="마이크", bg=BG, fg=SUB,
                 font=("Malgun Gothic", 9)).grid(row=0, column=0, sticky="w")
        self.devs = [(None, "윈도우 기본 (%s)" % core.device_name(None))]
        self.devs += [(i, "%d · %s" % (i, d["name"].strip()[:40]))
                      for i, d in core.input_devices()]
        self.dev_var = tk.StringVar()
        self.dev_cb = ttk.Combobox(cfg, state="readonly", textvariable=self.dev_var,
                                   values=[n for _, n in self.devs], width=40)
        cur = self.s.get("device")
        self.dev_cb.current(next((k for k, (i, _) in enumerate(self.devs)
                                 if i == cur), 0))
        self.dev_cb.grid(row=0, column=1, columnspan=3, sticky="we", padx=(8, 0), pady=2)
        self.dev_cb.bind("<<ComboboxSelected>>", self.change_device)

        self.dry_var = tk.BooleanVar(value=bool(self.s.get("dry")))
        tk.Checkbutton(cfg, text="연습 모드 (보내지 않고 인식만)", variable=self.dry_var,
                       command=self.change_dry, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, bd=0,
                       highlightthickness=0, font=("Malgun Gothic", 9)
                       ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 2))

        self.nm_lbl = tk.Label(cfg, text="", bg=BG, fg=SUB, font=("Malgun Gothic", 9))
        self.nm_lbl.grid(row=2, column=0, columnspan=2, sticky="w")
        self.nm = tk.Scale(cfg, from_=1.5, to=8.0, resolution=0.1, orient="horizontal",
                           showvalue=0, command=self.change_noise, bg=BG, fg=FG,
                           troughcolor=PANEL, highlightthickness=0, bd=0,
                           activebackground=ACCENT, length=180)
        self.nm.set(float(self.s.get("noise_mult", 3.0)))
        self.nm.grid(row=2, column=2, columnspan=2, sticky="we", padx=(8, 0))

        self.hs_lbl = tk.Label(cfg, text="", bg=BG, fg=SUB, font=("Malgun Gothic", 9))
        self.hs_lbl.grid(row=3, column=0, columnspan=2, sticky="w")
        self.hs = tk.Scale(cfg, from_=0.3, to=2.5, resolution=0.1, orient="horizontal",
                           showvalue=0, command=self.change_hang, bg=BG, fg=FG,
                           troughcolor=PANEL, highlightthickness=0, bd=0,
                           activebackground=ACCENT, length=180)
        self.hs.set(float(self.s.get("hang_sec", 0.8)))
        self.hs.grid(row=3, column=2, columnspan=4, sticky="we", padx=(8, 0))

        tk.Label(cfg, text="단축키", bg=BG, fg=SUB,
                 font=("Malgun Gothic", 9)).grid(row=4, column=0, sticky="w",
                                                 pady=(6, 0))
        self.hk_var = tk.StringVar(value=self.s.get("hotkey", "win+f9"))
        hk = tk.Entry(cfg, textvariable=self.hk_var, width=12, bg=PANEL, fg=FG,
                      relief="flat", insertbackground=FG,
                      font=("Malgun Gothic", 9))
        hk.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        hk.bind("<Return>", self.change_hotkey)
        hk.bind("<FocusOut>", self.change_hotkey)

        self.ov_var = tk.BooleanVar(value=bool(self.s.get("overlay", True)))
        tk.Checkbutton(cfg, text="내렸을 때 작은 표시창 보이기", variable=self.ov_var,
                       command=self.change_overlay, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, bd=0,
                       highlightthickness=0, font=("Malgun Gothic", 9)
                       ).grid(row=4, column=2, columnspan=3, sticky="w",
                              padx=(8, 0), pady=(6, 0))
        cfg.columnconfigure(3, weight=1)

        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", pady=(2, 12), **pad)
        tk.Label(foot, text="창을 닫거나 최소화하면 작은 표시창으로 내려갑니다",
                 bg=BG, fg=SUB, font=("Malgun Gothic", 8)).pack(side="left")
        tk.Button(foot, text="종료", command=self.quit, bg=PANEL, fg=SUB,
                  relief="flat", bd=0, activebackground="#333644",
                  font=("Malgun Gothic", 9), padx=10, pady=3,
                  cursor="hand2").pack(side="right")

        self._labels()

    def _labels(self):
        self.nm_lbl.config(text="민감도  소음의 %.1f배" % self.nm.get())
        self.hs_lbl.config(text="말끝 기다림  %.1f초" % self.hs.get())

    # ------------------------------------------------------------ 엔진
    def _boot(self):
        # 엔진은 on_event(kind, text, meta) 로 세 인자를 준다.
        # queue.put 을 그대로 넘기면 put(item, block, timeout) 으로 먹히므로 감싼다.
        self.engine = core.Engine(self.s,
                                  on_event=lambda k, t, m: self.events.put((k, t, m)),
                                  on_level=self._set_level)
        try:
            self.engine.start()
        except Exception as e:
            self.events.put(("error", "시작 실패: %s" % e, {}))

    def _set_level(self, rms):
        self.level = max(self.level * 0.7, rms)     # 살짝 붙잡아 눈에 보이게

    # ------------------------------------------------------------ 이벤트
    def _drain(self):
        # 이 안에서 예외가 새면 화면만 죽고 엔진은 계속 채팅을 보낸다.
        # 그런 반쪽 상태가 제일 위험하므로, 무슨 일이 있어도 되돌아온다.
        try:
            while True:
                try:
                    item = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    kind, text, meta = item
                except (TypeError, ValueError):
                    kind, text, meta = "error", "알 수 없는 이벤트: %r" % (item,), {}
                self._append(kind, text, meta)
                if kind == "listen":
                    self._paint_state(meta.get("on", True))
                if kind == "info" and text.startswith("마이크:"):
                    self.info_lbl.config(text=text)
                if kind == "info" and "준비 완료" in text:
                    self._paint_state(self.engine.listening if self.engine else True)
        except Exception as e:
            try:
                self._append("error", "화면 갱신 오류: %s" % e, {})
            except Exception:
                pass
        finally:
            self.root.after(50, self._drain)

    def _append(self, kind, text, meta):
        stamp = __import__("time").strftime("%H:%M:%S")
        self.log.configure(state="normal")
        if kind == "sent":
            head = "연습" if meta.get("dry") else "전송"
            self.log.insert("end", "%s  [%s] " % (stamp, head), "meta")
            self.log.insert("end", text + "\n", "sent")
            bits = []
            if meta.get("sec"):
                bits.append("말 %.1f초" % meta["sec"])
            if meta.get("took"):
                bits.append("인식 %.1f초" % meta["took"])
            if meta.get("lines", 1) > 1:
                bits.append("%d줄로 나눔" % meta["lines"])
            if bits:
                self.log.insert("end", "            %s\n" % " · ".join(bits), "meta")
        elif kind == "dropped":
            self.log.insert("end", "%s  (버림) %s\n" % (stamp, text[:60]), "dropped")
        else:
            self.log.insert("end", "%s  %s\n" % (stamp, text), kind)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _paint_state(self, on):
        on = bool(on)
        key = core.hotkey_label(self.s.get("hotkey", "win+f9"))
        self.dot.config(fg=ACCENT if on else SUB)
        self.state_lbl.config(text="듣고 있습니다" if on else "듣기 꺼짐")
        self.toggle_btn.config(text="듣기 %s (%s)" % ("끄기" if on else "켜기", key))
        self._overlay_paint(on)
        if self.tray:
            self.tray.icon = make_icon(on)
            self.tray.title = "마비노기 음성 채팅 — %s" % ("듣는 중" if on else "꺼짐")

    def _tick_level(self):
        # rms 는 아주 작은 값이라 눈에 보이게 늘린다
        frac = min(1.0, self.level * 12)
        if self.root.state() != "withdrawn":
            w = max(1, self.meter.winfo_width())
            self.meter.coords(self.bar, 0, 0, int(w * frac), 10)
            if self.engine:
                t = max(self.engine.noise * float(self.s["noise_mult"]), 0.012)
                x = int(w * min(1.0, t * 12))
                self.meter.coords(self.thr, x, 0, x, 10)
        elif self.overlay is not None and self.overlay.winfo_viewable():
            self.o_meter.coords(self.o_bar, 0, 0, int(44 * frac), 6)
        self.level *= 0.82
        self.root.after(60, self._tick_level)

    # ------------------------------------------------------------ 조작
    def toggle(self):
        if self.engine:
            self.engine.set_listening(not self.engine.listening)

    def change_device(self, _=None):
        idx = self.dev_cb.current()
        self.s["device"] = self.devs[idx][0]
        core.save_settings(self.s)
        if self.engine:
            try:
                self.engine.open_stream()
            except Exception as e:
                self.events.put(("error", "마이크를 열 수 없습니다: %s" % e, {}))

    def change_dry(self):
        self.s["dry"] = bool(self.dry_var.get())
        core.save_settings(self.s)
        msg = ("켜짐 — 채팅으로 보내지 않습니다" if self.s["dry"]
               else "꺼짐 — 이제 실제로 보냅니다")
        self.events.put(("listen", "연습 모드 " + msg, {"on": self.engine.listening
                                                     if self.engine else True}))

    def change_noise(self, _=None):
        self.s["noise_mult"] = float(self.nm.get())
        self._labels()
        core.save_settings(self.s)

    def change_hang(self, _=None):
        self.s["hang_sec"] = float(self.hs.get())
        self._labels()
        core.save_settings(self.s)

    def change_hotkey(self, _=None):
        spec = self.hk_var.get().strip().lower() or "win+f9"
        if not core.valid_hotkey(spec):
            self.events.put(("error", "단축키를 읽지 못해 win+f9 로 둡니다: %s" % spec, {}))
            spec = "win+f9"
            self.hk_var.set(spec)
        if spec == self.s.get("hotkey"):
            return
        self.s["hotkey"] = spec
        core.save_settings(self.s)
        self.events.put(("info", "단축키를 %s 로 바꿨습니다. 다음 실행부터 적용됩니다."
                         % core.hotkey_label(spec), {}))
        self._paint_state(self.engine.listening if self.engine else True)

    def change_overlay(self):
        self.s["overlay"] = bool(self.ov_var.get())
        core.save_settings(self.s)
        if not self.s["overlay"]:
            self._overlay_show(False)

    # ------------------------------------------------------------ 오버레이
    def _build_overlay(self):
        """디스코드 오버레이처럼, 항상 위에 떠 있는 작은 표시창."""
        o = tk.Toplevel(self.root)
        o.overrideredirect(True)                 # 제목줄 없는 납작한 창
        o.attributes("-topmost", True)
        o.attributes("-alpha", 0.88)
        o.configure(bg="#14151a")
        o.withdraw()

        wrap = tk.Frame(o, bg="#14151a", padx=9, pady=5,
                        highlightbackground="#3a3d4a", highlightthickness=1)
        wrap.pack()

        self.o_dot = tk.Label(wrap, text="●", bg="#14151a", fg=ACCENT,
                              font=("Segoe UI", 11))
        self.o_dot.pack(side="left")
        self.o_txt = tk.Label(wrap, text="듣는 중", bg="#14151a", fg=FG,
                              font=("Malgun Gothic", 9, "bold"))
        self.o_txt.pack(side="left", padx=(5, 7))
        self.o_meter = tk.Canvas(wrap, width=44, height=6, bg="#272935",
                                 highlightthickness=0)
        self.o_meter.pack(side="left")
        self.o_bar = self.o_meter.create_rectangle(0, 0, 0, 6, fill=ACCENT, width=0)

        # 끌어서 옮기기
        def press(e):
            o._dx, o._dy = e.x_root - o.winfo_x(), e.y_root - o.winfo_y()
            o._moved = False

        def drag(e):
            o._moved = True
            o.geometry("+%d+%d" % (e.x_root - o._dx, e.y_root - o._dy))

        def release(e):
            if getattr(o, "_moved", False):
                self.s["overlay_pos"] = [o.winfo_x(), o.winfo_y()]
                core.save_settings(self.s)
            else:
                self.show()                       # 끌지 않고 딸깍 -> 창 열기

        for w in (o, wrap, self.o_dot, self.o_txt, self.o_meter):
            w.bind("<Button-1>", press)
            w.bind("<B1-Motion>", drag)
            w.bind("<ButtonRelease-1>", release)
            w.bind("<Button-3>", lambda e: self.toggle())   # 오른쪽 클릭 -> 켜고 끄기

        self.overlay = o
        o.update_idletasks()
        pos = self.s.get("overlay_pos")
        if pos:
            o.geometry("+%d+%d" % (pos[0], pos[1]))
        else:                                     # 처음엔 오른쪽 아래
            sw, sh = o.winfo_screenwidth(), o.winfo_screenheight()
            o.geometry("+%d+%d" % (sw - o.winfo_width() - 24, sh - 110))

    def _overlay_show(self, on=True):
        if not self.s.get("overlay", True):
            return
        if self.overlay is None:
            self._build_overlay()
        if on:
            self.overlay.deiconify()
            self.overlay.attributes("-topmost", True)
        else:
            self.overlay.withdraw()

    def _overlay_paint(self, listening):
        if self.overlay is None:
            return
        col = ACCENT if listening else SUB
        self.o_dot.config(fg=col)
        self.o_txt.config(text="듣는 중" if listening else "꺼짐",
                          fg=FG if listening else SUB)
        self.o_meter.itemconfig(self.o_bar, fill=col)

    # ------------------------------------------------------------ 트레이
    def _start_tray(self):
        try:
            import pystray
        except Exception:
            return

        def show(icon=None, item=None):
            self.root.after(0, self.show)

        def toggle(icon=None, item=None):
            self.root.after(0, self.toggle)

        def quit_(icon=None, item=None):
            self.root.after(0, self.quit)

        menu = pystray.Menu(
            pystray.MenuItem("창 열기", show, default=True),
            pystray.MenuItem(lambda i: "듣기 끄기" if (self.engine and self.engine.listening)
                             else "듣기 켜기", toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", quit_),
        )
        self.tray = pystray.Icon("mabi_voice", make_icon(True),
                                 "마비노기 음성 채팅", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _on_unmap(self, event):
        if event.widget is not self.root or self._hiding:
            return
        if self.root.state() == "iconic":          # 최소화 버튼을 누른 경우
            self.hide()

    def hide(self):
        self._hiding = True
        self.root.withdraw()
        self._hiding = False
        self._overlay_show(True)
        self._overlay_paint(self.engine.listening if self.engine else True)

    def show(self):
        self._overlay_show(False)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        if self.engine:
            self.engine.shutdown()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


def make_icon(on):
    """트레이 아이콘: 듣는 중이면 초록, 꺼지면 회색인 마이크 모양."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (127, 209, 160, 255) if on else (140, 146, 156, 255)
    d.ellipse((2, 2, size - 2, size - 2), fill=(30, 31, 38, 255), outline=col, width=3)
    d.rounded_rectangle((26, 16, 38, 38), radius=6, fill=col)      # 마이크 몸통
    d.arc((20, 28, 44, 46), start=0, end=180, fill=col, width=3)   # 받침
    d.line((32, 44, 32, 50), fill=col, width=3)
    return img


if __name__ == "__main__":
    if not core.claim_single_instance():
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showwarning(
            "마비노기 음성 채팅",
            "이미 실행 중입니다.\n\n"
            "두 개가 동시에 들으면 채팅이 두 번씩 나갑니다.\n"
            "트레이 아이콘을 확인해 주세요.")
        r.destroy()
        sys.exit(0)
    App().run()
