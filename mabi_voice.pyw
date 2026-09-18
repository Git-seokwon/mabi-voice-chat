# -*- coding: utf-8 -*-
"""마비노기 모바일 음성 채팅 — 창 + 트레이 상주 프로그램.

pythonw 로 띄우면 콘솔 없이 배경에서 돈다. 실행.bat 을 쓰면 된다.
창을 닫거나 최소화하면 작은 표시창으로 내려간다. 끄려면 트레이 메뉴의 종료.
"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
import models

# ---------------------------------------------------------------- 모양
BG      = "#14151b"     # 창 바닥
CARD    = "#1c1e27"     # 카드
LINE    = "#2b2e3c"     # 경계선
FG      = "#e9eaf0"     # 본문
DIM     = "#a2a8b8"     # 보조
MUTE    = "#6f7585"     # 더 옅게
MINT    = "#6ee7a8"     # 켜짐
AMBER   = "#e9b44c"     # 주의 · 문턱
RED     = "#ef6b6b"     # 오류
HOVER   = "#282b38"

F_TITLE = ("Malgun Gothic", 14, "bold")
F_BODY  = ("Malgun Gothic", 9)
F_SMALL = ("Malgun Gothic", 8)
F_CAP   = ("Consolas", 10, "bold")      # 키캡
F_SEC   = ("Malgun Gothic", 8, "bold")  # 구역 제목


def card(parent, **kw):
    f = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                 highlightthickness=1, bd=0, **kw)
    return f


def section(parent, text):
    return tk.Label(parent, text=text, bg=CARD, fg=MUTE, font=F_SEC, anchor="w")


class Btn(tk.Label):
    """평평한 버튼. tk.Button 은 윈도우에서 테두리가 지워지지 않아 Label 로 만든다."""

    def __init__(self, parent, text, command, primary=False, bg=CARD, **kw):
        self.primary = primary
        self.base = bg
        self.command = command
        self._enabled = True
        fg = BG if primary else FG
        fill = MINT if primary else HOVER
        super().__init__(parent, text=text, bg=fill, fg=fg, font=F_BODY,
                         padx=12, pady=6, cursor="hand2", **kw)
        self._fill = fill
        self._fg = fg
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _):
        if self._enabled:
            self.config(bg="#8df0bf" if self.primary else "#33374a")

    def _on_leave(self, _):
        if self._enabled:
            self.config(bg=self._fill)

    def _on_click(self, _):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.config(fg=self._fg if on else MUTE,
                    bg=self._fill if on else CARD,
                    cursor="hand2" if on else "arrow")


class Meter(tk.Canvas):
    """음량 막대. 지금 음량과 '말소리로 인정하는 문턱' 을 함께 보여준다."""

    def __init__(self, parent, height=8, bg=CARD, **kw):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, **kw)
        self.h = height
        self.track = self.create_rectangle(0, 0, 0, height, fill="#23262f", width=0)
        self.fill = self.create_rectangle(0, 0, 0, height, fill=MINT, width=0)
        self.tick = self.create_rectangle(0, 0, 0, height, fill=AMBER, width=0)
        self.bind("<Configure>", lambda e: self.coords(self.track, 0, 0, e.width, self.h))

    def paint(self, frac, thr_frac=None, color=MINT):
        w = max(1, self.winfo_width())
        self.coords(self.fill, 0, 0, int(w * max(0.0, min(1.0, frac))), self.h)
        self.itemconfig(self.fill, fill=color)
        if thr_frac is None:
            self.coords(self.tick, 0, 0, 0, 0)
        else:
            x = int(w * max(0.0, min(1.0, thr_frac)))
            self.coords(self.tick, x, 0, x + 2, self.h)


class App:
    def __init__(self):
        self.s = core.load_settings()
        self.events = queue.Queue()
        self.level = 0.0
        self.tray = None
        self.engine = None
        self.overlay = None
        self._hiding = False        # withdraw 가 스스로 Unmap 을 부르는 걸 걸러낸다
        self.capturing = False      # 단축키를 받아 적는 중
        self._cap_until = 0

        self.root = tk.Tk()
        self.root.title("마비노기 음성 채팅 %s" % core.VERSION)
        self.root.configure(bg=BG)
        self.root.geometry("580x660")
        self.root.minsize(520, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Unmap>", self._on_unmap)

        self._theme()
        self._build()
        self.root.after(50, self._drain)
        self.root.after(60, self._tick_level)

        threading.Thread(target=self._boot, daemon=True).start()
        self._start_tray()

    # ------------------------------------------------------------ 테마
    def _theme(self):
        st = ttk.Style()
        st.theme_use("clam")        # clam 이어야 색을 바꿀 수 있다
        st.configure("D.TCombobox", fieldbackground=HOVER, background=HOVER,
                     foreground=FG, arrowcolor=DIM, bordercolor=LINE,
                     lightcolor=HOVER, darkcolor=HOVER, selectbackground=HOVER,
                     selectforeground=FG, padding=4)
        st.map("D.TCombobox", fieldbackground=[("readonly", HOVER)],
               foreground=[("readonly", FG)])
        st.configure("D.Horizontal.TScale", background=CARD, troughcolor="#23262f",
                     bordercolor=CARD, lightcolor=MINT, darkcolor=MINT)
        st.configure("D.Vertical.TScrollbar", background=HOVER, troughcolor=CARD,
                     bordercolor=CARD, arrowcolor=DIM, lightcolor=HOVER,
                     darkcolor=HOVER)

    # ------------------------------------------------------------ 화면
    def _build(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=14)

        # ---- 상태 카드
        top = card(outer)
        top.pack(fill="x")
        pad = tk.Frame(top, bg=CARD)
        pad.pack(fill="x", padx=14, pady=12)

        line1 = tk.Frame(pad, bg=CARD)
        line1.pack(fill="x")
        self.dot = tk.Label(line1, text="●", bg=CARD, fg=MUTE, font=("Segoe UI", 16))
        self.dot.pack(side="left")
        names = tk.Frame(line1, bg=CARD)
        names.pack(side="left", padx=(8, 0))
        self.state_lbl = tk.Label(names, text="준비 중", bg=CARD, fg=FG,
                                  font=F_TITLE, anchor="w")
        self.state_lbl.pack(anchor="w")
        self.info_lbl = tk.Label(names, text="모델을 올리고 있습니다", bg=CARD,
                                 fg=MUTE, font=F_SMALL, anchor="w")
        self.info_lbl.pack(anchor="w")

        right = tk.Frame(line1, bg=CARD)
        right.pack(side="right")
        self.toggle_btn = Btn(right, "듣기 끄기", self.toggle, primary=True)
        self.toggle_btn.pack(side="right")
        self.hk_pill = tk.Label(right, text="Win+F9", bg="#23262f", fg=DIM,
                                font=F_CAP, padx=8, pady=4)
        self.hk_pill.pack(side="right", padx=(0, 8))

        m = tk.Frame(pad, bg=CARD)
        m.pack(fill="x", pady=(12, 0))
        self.meter = Meter(m)
        self.meter.pack(fill="x")
        self.meter_lbl = tk.Label(pad, text="초록은 지금 음량, 주황은 말소리로 보는 문턱",
                                  bg=CARD, fg=MUTE, font=F_SMALL, anchor="w")
        self.meter_lbl.pack(fill="x", pady=(4, 0))

        # ---- 탭
        tabs = tk.Frame(outer, bg=BG)
        tabs.pack(fill="x", pady=(12, 8))
        self.tab_btns = {}
        for key, text in (("log", "기록"), ("cfg", "설정")):
            b = tk.Label(tabs, text=text, bg=BG, fg=MUTE, font=F_BODY,
                         padx=14, pady=5, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            self.tab_btns[key] = b

        self.body = tk.Frame(outer, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.panes = {"log": self._build_log(), "cfg": self._build_cfg()}
        self.show_tab("log")

        # ---- 바닥
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(10, 0))
        tk.Label(foot, text="창을 닫거나 최소화하면 작은 표시창으로 내려갑니다",
                 bg=BG, fg=MUTE, font=F_SMALL).pack(side="left")
        Btn(foot, "종료", self.quit, bg=BG).pack(side="right")

        self._labels()
        self.pick_model()

    def _build_log(self):
        c = card(self.body)
        box = tk.Frame(c, bg=CARD)
        box.pack(fill="both", expand=True, padx=2, pady=2)
        self.log = tk.Text(box, bg=CARD, fg=FG, relief="flat", bd=0, wrap="word",
                           font=F_BODY, padx=12, pady=10, state="disabled",
                           insertbackground=FG, selectbackground=HOVER)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=self.log.yview, style="D.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("sent", foreground=MINT)
        self.log.tag_configure("dropped", foreground=MUTE)
        self.log.tag_configure("error", foreground=RED)
        self.log.tag_configure("info", foreground=DIM)
        self.log.tag_configure("listen", foreground=AMBER)
        self.log.tag_configure("meta", foreground=MUTE, font=F_SMALL)
        return c

    def _build_cfg(self):
        c = card(self.body)
        g = tk.Frame(c, bg=CARD)
        g.pack(fill="both", expand=True, padx=14, pady=12)
        g.columnconfigure(1, weight=1)
        r = 0

        # 마이크
        section(g, "마이크").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self.devs = [(None, "윈도우 기본 · %s" % core.device_name(None))]
        self.devs += [(i, "%d · %s" % (i, d["name"].strip()[:38]))
                      for i, d in core.input_devices()]
        self.dev_var = tk.StringVar()
        self.dev_cb = ttk.Combobox(g, state="readonly", textvariable=self.dev_var,
                                   values=[n for _, n in self.devs],
                                   style="D.TCombobox")
        cur = self.s.get("device")
        self.dev_cb.current(next((k for k, (i, _) in enumerate(self.devs)
                                  if i == cur), 0))
        self.dev_cb.grid(row=r, column=0, columnspan=2, sticky="we", pady=(4, 12))
        self.dev_cb.bind("<<ComboboxSelected>>", self.change_device)
        r += 1

        # 모델
        section(g, "모델").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self.model_rows = models.catalog_rows()
        self.model_var = tk.StringVar()
        self.model_cb = ttk.Combobox(g, state="readonly", textvariable=self.model_var,
                                     values=[x["text"] for x in self.model_rows],
                                     style="D.TCombobox")
        cur_model = self.s.get("model") or models.DEFAULT
        self.model_cb.current(next((k for k, x in enumerate(self.model_rows)
                                    if x["name"] == cur_model), 0))
        self.model_cb.grid(row=r, column=0, sticky="we", pady=(4, 0))
        self.model_cb.bind("<<ComboboxSelected>>", self.pick_model)
        self.model_btn = Btn(g, "적용", self.apply_model)
        self.model_btn.grid(row=r, column=1, sticky="e", padx=(10, 0), pady=(4, 0))
        r += 1
        self.model_note = tk.Label(g, text="", bg=CARD, fg=MUTE, font=F_SMALL,
                                   anchor="w", justify="left")
        self.model_note.grid(row=r, column=0, columnspan=2, sticky="we", pady=(4, 0))
        r += 1
        self.dl = Meter(g, height=6)
        self.dl_lbl = tk.Label(g, text="", bg=CARD, fg=DIM, font=F_SMALL, anchor="w")
        self.dl_row = r
        r += 2                                  # 내려받는 동안만 쓰는 두 줄
        tk.Frame(g, bg=LINE, height=1).grid(row=r, column=0, columnspan=2,
                                            sticky="we", pady=12)
        r += 1

        # 단축키
        section(g, "듣기 켜고 끄기 단축키").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        hk = tk.Frame(g, bg=CARD)
        hk.grid(row=r, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.hk_cap = tk.Label(hk, text="Win+F9", bg="#23262f", fg=FG,
                               font=F_CAP, padx=14, pady=7)
        self.hk_cap.pack(side="left")
        self.hk_btn = Btn(hk, "키 바꾸기", self.start_capture)
        self.hk_btn.pack(side="left", padx=(8, 0))
        r += 1
        self.hk_hint = tk.Label(g, text="버튼을 누른 뒤 원하는 조합을 누르세요",
                                bg=CARD, fg=MUTE, font=F_SMALL, anchor="w")
        self.hk_hint.grid(row=r, column=0, columnspan=2, sticky="we", pady=(5, 12))
        r += 1
        tk.Frame(g, bg=LINE, height=1).grid(row=r, column=0, columnspan=2,
                                            sticky="we", pady=(0, 12))
        r += 1

        # 감지
        section(g, "목소리 감지").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self.nm_lbl = tk.Label(g, text="", bg=CARD, fg=DIM, font=F_BODY, anchor="w")
        self.nm_lbl.grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.nm = ttk.Scale(g, from_=1.5, to=8.0, orient="horizontal",
                            command=self.change_noise, style="D.Horizontal.TScale")
        self.nm.set(float(self.s.get("noise_mult", 3.0)))
        self.nm.grid(row=r, column=1, sticky="we", padx=(12, 0), pady=(6, 0))
        r += 1
        self.hs_lbl = tk.Label(g, text="", bg=CARD, fg=DIM, font=F_BODY, anchor="w")
        self.hs_lbl.grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.hs = ttk.Scale(g, from_=0.3, to=2.5, orient="horizontal",
                            command=self.change_hang, style="D.Horizontal.TScale")
        self.hs.set(float(self.s.get("hang_sec", 0.8)))
        self.hs.grid(row=r, column=1, sticky="we", padx=(12, 0), pady=(6, 0))
        r += 1
        tk.Frame(g, bg=LINE, height=1).grid(row=r, column=0, columnspan=2,
                                            sticky="we", pady=12)
        r += 1

        # 그밖에
        self.dry_var = tk.BooleanVar(value=bool(self.s.get("dry")))
        self._check(g, "연습 모드 — 인식만 하고 채팅으로 보내지 않습니다",
                    self.dry_var, self.change_dry).grid(row=r, column=0,
                                                        columnspan=2, sticky="w")
        r += 1
        self.ov_var = tk.BooleanVar(value=bool(self.s.get("overlay", True)))
        self._check(g, "내렸을 때 작은 표시창 보이기", self.ov_var,
                    self.change_overlay).grid(row=r, column=0, columnspan=2,
                                              sticky="w", pady=(4, 0))
        return c

    def _check(self, parent, text, var, cmd):
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd,
                              bg=CARD, fg=DIM, selectcolor="#23262f",
                              activebackground=CARD, activeforeground=FG,
                              bd=0, highlightthickness=0, font=F_BODY,
                              anchor="w", cursor="hand2")

    def show_tab(self, key):
        for k, b in self.tab_btns.items():
            on = k == key
            b.config(fg=FG if on else MUTE, bg=CARD if on else BG)
        for k, p in self.panes.items():
            p.pack_forget()
        self.panes[key].pack(fill="both", expand=True)

    def _labels(self):
        # ttk.Scale 은 set() 하는 순간 command 를 부른다. 창을 만드는 도중이라
        # 라벨이 아직 없을 수 있으니 조용히 넘긴다.
        if not hasattr(self, "hs_lbl"):
            return
        self.nm_lbl.config(text="민감도  소음의 %.1f배" % float(self.nm.get()))
        self.hs_lbl.config(text="말끝 기다림  %.1f초" % float(self.hs.get()))

    # ------------------------------------------------------------ 단축키 받아 적기
    def start_capture(self):
        if self.capturing:
            return
        self.capturing = True
        self._cap_until = time.time() + 8
        self.hk_btn.config(text="취소")
        self.hk_btn.command = self.cancel_capture
        self.hk_cap.config(text="키를 누르세요", fg=AMBER)
        self.hk_hint.config(text="Win · Ctrl · Shift · Alt 과 함께 눌러도 됩니다. "
                                 "Esc 로 취소, 8초 뒤 자동 취소", fg=AMBER)
        self.root.after(30, self._capture_tick)

    def cancel_capture(self):
        self.capturing = False
        self.hk_btn.config(text="키 바꾸기")
        self.hk_btn.command = self.start_capture
        self.hk_cap.config(fg=FG)
        self.hk_hint.config(text="버튼을 누른 뒤 원하는 조합을 누르세요", fg=MUTE)
        self._paint_hotkey()

    def _capture_tick(self):
        """키 상태를 직접 읽어 조합을 알아낸다. Win 키까지 잡으려면 이 방법뿐이다."""
        if not self.capturing:
            return
        gaks = ctypes.windll.user32.GetAsyncKeyState
        if gaks(0x1B) & 0x8000 or time.time() > self._cap_until:      # Esc
            self.cancel_capture()
            return

        mods = [n for n in ("ctrl", "shift", "alt", "win")
                if any(gaks(v) & 0x8000 for v in core.MODIFIERS[n])]
        key = next((n for n, vk in sorted(core.KEYS.items())
                    if gaks(vk) & 0x8000), None)

        if key is None:
            shown = "+".join(core.hotkey_label(m) for m in mods) or "키를 누르세요"
            self.hk_cap.config(text=shown + (" + …" if mods else ""))
            self.root.after(30, self._capture_tick)
            return

        spec = "+".join(mods + [key])
        self.capturing = False
        self.hk_btn.config(text="키 바꾸기")
        self.hk_btn.command = self.start_capture
        self.hk_cap.config(fg=FG)
        if self.engine:
            self.engine.set_hotkey(spec)
        else:
            self.s["hotkey"] = spec
            core.save_settings(self.s)
        self.hk_hint.config(text="바뀌었습니다. 바로 쓸 수 있습니다.", fg=MINT)
        self._paint_hotkey()
        self._paint_state(self.engine.listening if self.engine else True)

    def _paint_hotkey(self):
        label = core.hotkey_label(self.s.get("hotkey", "win+f9"))
        self.hk_cap.config(text=label)
        self.hk_pill.config(text=label)

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
                    self.info_lbl.config(text=text)
                    self._paint_state(self.engine.listening if self.engine else True)
        except Exception as e:
            try:
                self._append("error", "화면 갱신 오류: %s" % e, {})
            except Exception:
                pass
        finally:
            self.root.after(50, self._drain)

    def _append(self, kind, text, meta):
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        if kind == "sent":
            head = "연습" if meta.get("dry") else "전송"
            self.log.insert("end", "%s  %s  " % (stamp, head), "meta")
            self.log.insert("end", text + "\n", "sent")
            bits = []
            if meta.get("sec"):
                bits.append("말 %.1f초" % meta["sec"])
            if meta.get("took"):
                bits.append("인식 %.1f초" % meta["took"])
            if meta.get("lines", 1) > 1:
                bits.append("%d줄로 나눔" % meta["lines"])
            if bits:
                self.log.insert("end", "                 %s\n" % " · ".join(bits), "meta")
        elif kind == "dropped":
            self.log.insert("end", "%s  버림  %s\n" % (stamp, text[:60]), "dropped")
        else:
            self.log.insert("end", "%s  %s\n" % (stamp, text), kind)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _paint_state(self, on):
        on = bool(on)
        self.dot.config(fg=MINT if on else MUTE)
        self.state_lbl.config(text="듣고 있습니다" if on else "듣기 꺼짐",
                              fg=FG if on else DIM)
        self.toggle_btn.config(text="듣기 끄기" if on else "듣기 켜기")
        self._paint_hotkey()
        self._overlay_paint(on)
        if self.tray:
            self.tray.icon = make_icon(on)
            self.tray.title = "마비노기 음성 채팅 — %s" % ("듣는 중" if on else "꺼짐")

    def _tick_level(self):
        frac = min(1.0, self.level * 12)          # rms 는 작아서 눈에 보이게 늘린다
        thr = None
        if self.engine:
            t = max(self.engine.noise * float(self.s["noise_mult"]), 0.012)
            thr = min(1.0, t * 12)
        listening = self.engine.listening if self.engine else True
        if self.root.state() != "withdrawn":
            self.meter.paint(frac, thr, MINT if listening else MUTE)
        elif self.overlay is not None and self.overlay.winfo_viewable():
            self.o_meter.paint(frac, None, MINT if listening else MUTE)
        self.level *= 0.82
        self.root.after(60, self._tick_level)

    # ------------------------------------------------------------ 조작
    def toggle(self):
        if self.engine:
            self.engine.set_listening(not self.engine.listening)

    def change_device(self, _=None):
        self.s["device"] = self.devs[self.dev_cb.current()][0]
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
        self.events.put(("listen", "연습 모드 " + msg,
                         {"on": self.engine.listening if self.engine else True}))

    def change_noise(self, _=None):
        self.s["noise_mult"] = float(self.nm.get())
        self._labels()
        core.save_settings(self.s)

    def change_hang(self, _=None):
        self.s["hang_sec"] = float(self.hs.get())
        self._labels()
        core.save_settings(self.s)

    def change_overlay(self):
        self.s["overlay"] = bool(self.ov_var.get())
        core.save_settings(self.s)
        if not self.s["overlay"]:
            self._overlay_show(False)

    # ------------------------------------------------------------ 모델
    def _sel_model(self):
        return self.model_rows[self.model_cb.current()]

    def _refresh_models(self):
        keep = self._sel_model()["name"]
        self.model_rows = models.catalog_rows()
        self.model_cb.configure(values=[x["text"] for x in self.model_rows])
        self.model_cb.current(next((k for k, x in enumerate(self.model_rows)
                                    if x["name"] == keep), 0))
        self.pick_model()

    def pick_model(self, _=None):
        x = self._sel_model()
        self.model_btn.config(text="적용" if x["ready"] else "내려받기")
        self.model_note.config(text=x["note"])

    def _dl_show(self, on):
        if on:
            self.dl.grid(row=self.dl_row, column=0, columnspan=2,
                         sticky="we", pady=(10, 0))
            self.dl_lbl.grid(row=self.dl_row + 1, column=0, columnspan=2,
                             sticky="we", pady=(4, 0))
        else:
            self.dl.grid_remove()
            self.dl_lbl.grid_remove()

    def apply_model(self):
        x = self._sel_model()
        name = x["name"]
        self.model_btn.set_enabled(False)
        if x["ready"]:
            self.model_btn.config(text="바꾸는 중")
            threading.Thread(target=self._swap_model, args=(name,),
                             daemon=True).start()
            return
        self.model_btn.config(text="받는 중")
        self._dl_show(True)
        self.dl.paint(0)
        self.dl_lbl.config(text="%s 준비 중..." % name)
        self.show_tab("cfg")
        threading.Thread(target=self._download_model, args=(name,),
                         daemon=True).start()

    def _download_model(self, name):
        def prog(done, total):
            # 콜백은 다른 스레드에서 온다. 화면은 메인 스레드에서만 만진다.
            self.root.after(0, self._dl_paint, done, total)
        try:
            models.download(name, on_progress=prog,
                            on_log=lambda m: self.events.put(("info", m, {})))
        except Exception as e:
            self.events.put(("error", "내려받기 실패: %s" % str(e)[:120], {}))
            self.root.after(0, self._dl_done, name, False)
            return
        self.root.after(0, self._dl_done, name, True)

    def _dl_paint(self, done, total):
        frac = (done / total) if total else 0
        self.dl.paint(frac, None, MINT)
        self.dl_lbl.config(text="%.0f / %.0f MB   %.0f%%" % (done, total, frac * 100))

    def _dl_done(self, name, ok):
        self._dl_show(False)
        self.model_btn.set_enabled(True)
        self._refresh_models()
        if ok:
            threading.Thread(target=self._swap_model, args=(name,),
                             daemon=True).start()

    def _swap_model(self, name):
        ok = False
        try:
            if self.engine:
                ok = self.engine.reload_model(name)
        except Exception as e:
            self.events.put(("error", "모델을 바꾸지 못했습니다: %s" % str(e)[:120], {}))
        if ok:
            self.s["model"] = name
            core.save_settings(self.s)
        self.root.after(0, lambda: self.model_btn.set_enabled(True))
        self.root.after(0, self.pick_model)

    # ------------------------------------------------------------ 오버레이
    def _build_overlay(self):
        """디스코드 오버레이처럼, 항상 위에 떠 있는 작은 표시창."""
        o = tk.Toplevel(self.root)
        o.overrideredirect(True)                 # 제목줄 없는 납작한 창
        o.attributes("-topmost", True)
        o.attributes("-alpha", 0.9)
        o.configure(bg=LINE)
        o.withdraw()

        wrap = tk.Frame(o, bg="#111217", padx=10, pady=6)
        wrap.pack(padx=1, pady=1)

        self.o_dot = tk.Label(wrap, text="●", bg="#111217", fg=MINT,
                              font=("Segoe UI", 11))
        self.o_dot.pack(side="left")
        self.o_txt = tk.Label(wrap, text="듣는 중", bg="#111217", fg=FG,
                              font=("Malgun Gothic", 9, "bold"))
        self.o_txt.pack(side="left", padx=(6, 8))
        self.o_meter = Meter(wrap, height=6, bg="#111217", width=46)
        self.o_meter.pack(side="left")

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
        self.o_dot.config(fg=MINT if listening else MUTE)
        self.o_txt.config(text="듣는 중" if listening else "꺼짐",
                          fg=FG if listening else MUTE)

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

    # ------------------------------------------------------------ 창 수명
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
    col = (110, 231, 168, 255) if on else (130, 136, 150, 255)
    d.ellipse((2, 2, size - 2, size - 2), fill=(20, 21, 27, 255), outline=col, width=3)
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
