# -*- coding: utf-8 -*-
"""창을 그리는 부품들. 둥근 카드·버튼·막대와 세로로 넘기는 칸.

tkinter 에는 둥근 모서리가 없다. 캔버스에 둥근 사각형을 직접 그리고 그 위에
내용을 올리는 식으로 만든다.
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- 색과 글꼴
BG     = "#0f1115"      # 창 바닥
CARD   = "#171a21"      # 카드
SUNK   = "#1e222b"      # 카드 안의 입력칸·버튼
LINE   = "#272c38"      # 경계선
FG     = "#eceef4"      # 본문
DIM    = "#9aa3b2"      # 보조
MUTE   = "#6b7382"      # 더 옅게
MINT   = "#5fd6a4"      # 켜짐·주버튼
MINT_H = "#7ee6ba"      # 주버튼에 마우스 올렸을 때
AMBER  = "#eab464"      # 주의·문턱
RED    = "#f0736b"      # 오류
HOVER  = "#2a3040"      # 보통 버튼에 마우스 올렸을 때

F_TITLE = ("Malgun Gothic", 15, "bold")
F_SEC   = ("Malgun Gothic", 9, "bold")
F_BODY  = ("Malgun Gothic", 9)
F_SMALL = ("Malgun Gothic", 8)
F_CAP   = ("Consolas", 10, "bold")


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    """둥근 사각형. 모서리를 점으로 깎아 부드럽게 잇는다."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def apply_theme(root):
    """ttk 부품(콤보박스·슬라이더·스크롤바)의 색을 맞춘다."""
    st = ttk.Style(root)
    st.theme_use("clam")        # clam 이어야 색이 먹는다
    st.configure("D.TCombobox", fieldbackground=SUNK, background=SUNK,
                 foreground=FG, arrowcolor=DIM, bordercolor=SUNK,
                 lightcolor=SUNK, darkcolor=SUNK, selectbackground=SUNK,
                 selectforeground=FG, padding=6, relief="flat")
    st.map("D.TCombobox", fieldbackground=[("readonly", SUNK)],
           foreground=[("readonly", FG)], bordercolor=[("focus", LINE)])
    st.configure("D.Horizontal.TScale", background=CARD, troughcolor=SUNK,
                 bordercolor=CARD, lightcolor=MINT, darkcolor=MINT)
    st.configure("D.Vertical.TScrollbar", background=LINE, troughcolor=BG,
                 bordercolor=BG, arrowcolor=MUTE, lightcolor=LINE,
                 darkcolor=LINE, relief="flat", arrowsize=12)
    st.map("D.Vertical.TScrollbar", background=[("active", MUTE)])


class Card(tk.Frame):
    """둥근 카드. 내용은 .body 안에 넣는다."""

    def __init__(self, parent, radius=14, pad=16, fill=CARD, outer=BG, **kw):
        super().__init__(parent, bg=outer, **kw)
        self.radius, self.pad, self.fill = radius, pad, fill
        self.canvas = tk.Canvas(self, bg=outer, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=fill)
        self._win = self.canvas.create_window(pad, pad, anchor="nw",
                                              window=self.body)
        self._shape = None
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_body(self, e):
        self.canvas.configure(height=e.height + self.pad * 2)
        self._redraw()

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width - self.pad * 2)
        self._redraw()

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        if self._shape:
            self.canvas.delete(self._shape)
        self._shape = rounded(self.canvas, 0, 0, w - 1, h - 1, self.radius,
                              fill=self.fill, outline=LINE, width=1)
        self.canvas.tag_lower(self._shape)


class Button(tk.Canvas):
    """둥근 버튼. kind 는 primary(초록) · ghost(보통) · quiet(옅음)."""

    def __init__(self, parent, text, command=None, kind="ghost", bg=CARD,
                 padx=14, pady=8, radius=10, **kw):
        self.kind, self.command, self.base = kind, command, bg
        self._text, self._enabled = text, True
        self._font = tkfont.Font(font=F_BODY)
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, **kw)
        self.padx, self.pady, self.radius = padx, pady, radius
        self._hover = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self._resize()

    # --- 색
    def _colors(self):
        if not self._enabled:
            return SUNK, MUTE
        if self.kind == "primary":
            return (MINT_H if self._hover else MINT), "#10231b"
        if self.kind == "quiet":
            return (HOVER if self._hover else self.base), (FG if self._hover else DIM)
        return (HOVER if self._hover else SUNK), FG

    def _resize(self):
        w = self._font.measure(self._text) + self.padx * 2
        h = self._font.metrics("linespace") + self.pady * 2
        self.configure(width=w, height=h)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill, fg = self._colors()
        rounded(self, 0, 0, w - 1, h - 1, self.radius, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=F_BODY)

    # --- 바깥에서 쓰는 것
    def set_text(self, t):
        self._text = t
        self._resize()

    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self.set_text(kw.pop("text"))
            if not cnf and not kw:
                return None
        return super().configure(cnf, **kw) if (cnf or kw) else None

    config = configure

    def cget(self, key):
        return self._text if key == "text" else super().cget(key)

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def _enter(self, _):
        self._hover = True
        self._draw()

    def _leave(self, _):
        self._hover = False
        self._draw()

    def _click(self, _):
        if self._enabled and self.command:
            self.command()


class Bar(tk.Canvas):
    """둥근 막대. 지금 값과 문턱을 함께 보여준다."""

    def __init__(self, parent, height=10, bg=CARD, **kw):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0,
                         bd=0, **kw)
        self.h = height
        self.bind("<Configure>", lambda e: self.paint(*self._last))
        self._last = (0.0, None, MINT)

    def paint(self, frac, thr=None, color=MINT):
        self._last = (frac, thr, color)
        self.delete("all")
        w = max(2, self.winfo_width())
        r = self.h / 2
        rounded(self, 0, 0, w - 1, self.h, r, fill=SUNK, outline="")
        fw = int(w * max(0.0, min(1.0, frac)))
        if fw > 2:
            rounded(self, 0, 0, fw, self.h, r, fill=color, outline="")
        if thr is not None:
            x = int(w * max(0.0, min(1.0, thr)))
            self.create_rectangle(x, 0, x + 2, self.h, fill=AMBER, width=0)


class ScrollArea(tk.Frame):
    """세로로 넘겨 보는 칸. 내용은 .body 안에 넣는다.

    창이 작아 내용이 다 안 보이던 문제 때문에 둔다. 넘길 것이 없으면
    스크롤막대를 감춘다.
    """

    def __init__(self, parent, bg=BG, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                style="D.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._wheel_on)
        self.canvas.bind("<Leave>", self._wheel_off)
        self._shown = False

    def _on_body(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _on_scroll(self, lo, hi):
        need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
        if need and not self._shown:
            self.vs.pack(side="right", fill="y", padx=(4, 0))
            self._shown = True
        elif not need and self._shown:
            self.vs.pack_forget()
            self._shown = False
        self.vs.set(lo, hi)

    def _wheel_on(self, _):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _wheel_off(self, _):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, e):
        if self._shown:
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


def label(parent, text, fg=DIM, font=F_BODY, bg=CARD, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, anchor="w", **kw)


def section(parent, text, bg=CARD):
    return tk.Label(parent, text=text, bg=bg, fg=MUTE, font=F_SEC, anchor="w")


def entry(parent, var, bg=SUNK):
    return tk.Entry(parent, textvariable=var, bg=bg, fg=FG, relief="flat",
                    insertbackground=FG, font=F_BODY, highlightthickness=0)


def check(parent, text, var, cmd, bg=CARD):
    return tk.Checkbutton(parent, text=text, variable=var, command=cmd,
                          bg=bg, fg=DIM, selectcolor=SUNK, activebackground=bg,
                          activeforeground=FG, bd=0, highlightthickness=0,
                          font=F_BODY, anchor="w", cursor="hand2")
