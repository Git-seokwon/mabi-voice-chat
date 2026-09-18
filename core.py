# -*- coding: utf-8 -*-
"""음성 -> 인게임 채팅 엔진. UI 는 모른다.

콘솔판(voice_chat.py)과 UI판(mabi_voice.pyw)이 이 파일을 함께 쓴다.
Engine 은 일어난 일을 콜백으로만 알리고, 화면에 직접 찍지 않는다.
"""
import base64
import ctypes
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd


def find_cli():
    """게임 CLI 위치. PATH 에 등록되어 있으면 그걸 쓰고, 없으면 흔한 자리를 본다.

    토글을 켜면 게임이 CLI 를 깔면서 PATH 에 등록해 준다. 사람마다 설치
    드라이브가 다르므로 경로를 박아 두지 않는다. MABI_CLI 로 직접 지정할 수도 있다.
    """
    env = os.environ.get("MABI_CLI")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("MabinogiMobile_CLI")
    if found:
        return found
    for drive in ("C:", "D:", "E:", "F:"):
        p = drive + r"\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe"
        if os.path.isfile(p):
            return p
    return "MabinogiMobile_CLI.exe"        # 마지막 수단. 없으면 실행 때 알려준다


CLI = find_cli()
SAMPLE_RATE = 16000
FRAME = 480                   # 30ms
CHAT_LIMIT = 50               # write_chat 한 줄 최대 글자수

START_FRAMES = 3              # 이만큼 연속 크면 말 시작
MIN_SEC = 0.4                 # 이보다 짧으면 기침·잡음으로 보고 버림
MAX_SEC = 15.0                # 이보다 길면 잘라서 넘김
PRE_ROLL = 6                  # 말 시작 직전 프레임도 함께 (첫 음절 보존)

# --- 전역 단축키 ---
# GetAsyncKeyState 로 키 상태를 직접 읽는다. 창 포커스와 무관하므로
# 다른 게임이 앞에 있어도 먹힌다.
VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C

# 이름 -> 가상키. 한 모디파이어에 좌우 두 키가 있으면 둘 다 인정한다.
MODIFIERS = {
    "win": (VK_LWIN, VK_RWIN),
    "ctrl": (VK_CONTROL,),
    "control": (VK_CONTROL,),
    "shift": (VK_SHIFT,),
    "alt": (VK_MENU,),
}
KEYS = {"f%d" % i: 0x6F + i for i in range(1, 13)}          # F1..F12
KEYS.update({chr(c).lower(): c for c in range(0x41, 0x5B)})  # A..Z
KEYS.update({str(d): 0x30 + d for d in range(10)})           # 0..9
KEYS["space"] = 0x20
KEYS["insert"] = 0x2D
KEYS["scrolllock"] = 0x91
KEYS["pause"] = 0x13


def parse_hotkey(spec):
    """'win+f9' -> ([(0x5B,0x5C)], 0x78). 못 읽으면 win+f9 로 돌아간다."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return [MODIFIERS["win"]], KEYS["f9"]
    key = KEYS.get(parts[-1])
    mods = [MODIFIERS[p] for p in parts[:-1] if p in MODIFIERS]
    if key is None:
        return [MODIFIERS["win"]], KEYS["f9"]
    return mods, key


# RegisterHotKey 용 모디파이어 비트. 이쪽은 운영체제가 조합을 먼저 가로채므로
# 게임이 키를 삼켜도, 게임이 관리자 권한이어도 우리에게 전달된다.
MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002,
             "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def hotkey_flags(spec):
    """'win+f9' -> (0x0008, 0x78). 못 읽으면 (None, None)."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None, None
    vk = KEYS.get(parts[-1])
    if vk is None:
        return None, None
    flags = 0
    for p in parts[:-1]:
        flags |= MOD_FLAGS.get(p, 0)
    return flags, vk


def valid_hotkey(spec):
    """'win+f9' 처럼 읽을 수 있는 조합인지. 모디파이어만 있으면 안 된다."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts or parts[-1] not in KEYS:
        return False
    return all(p in MODIFIERS for p in parts[:-1])


def hotkey_label(spec):
    if not valid_hotkey(spec):
        return "Win+F9"
    names = {"win": "Win", "ctrl": "Ctrl", "control": "Ctrl",
             "shift": "Shift", "alt": "Alt"}
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    return "+".join(names.get(p, p.upper()) for p in parts) or "Win+F9"


# 위스퍼가 무음·잡음에 붙이는 흔한 헛문장들. 이런 건 보내지 않는다.
HALLUCINATIONS = (
    "감사합니다", "시청해주셔서", "구독과 좋아요", "구독", "자막",
    "MBC 뉴스", "KBS", "한글자막", "본 영상은", "다음 영상에서",
)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "settings.json")
DEFAULTS = {
    "device": None,           # None 이면 윈도우 기본 마이크
    "model": None,            # None 이면 첫 실행에 이 PC 에 맞는 것을 권한다
    "dry": False,             # True 면 인식만 하고 보내지 않는다
    "noise_mult": 3.0,        # 주변 소음의 몇 배를 말소리로 볼지
    "hang_sec": 0.8,          # 이만큼 조용하면 말이 끝난 것으로 본다
    "log_to_file": True,
    "hotkey": "win+f9",       # 듣기 켜고 끄기. 창 포커스와 무관하게 먹힌다
    "overlay": True,          # 창을 내리면 작은 표시창을 띄운다
    "overlay_pos": None,      # [x, y]. 끌어서 옮긴 자리를 기억한다
}


_mutex = None


def claim_single_instance(name="mabi_voice_chat"):
    """이미 돌고 있으면 False. 두 개가 동시에 들으면 채팅이 두 번씩 나간다.

    이름 있는 뮤텍스는 프로세스가 죽으면 윈도우가 알아서 풀어 주므로,
    강제 종료되어도 잠금이 남지 않는다.
    """
    global _mutex
    ERROR_ALREADY_EXISTS = 183
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, name)     # 핸들은 붙잡아 둔다
    return k32.GetLastError() != ERROR_ALREADY_EXISTS


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 게임 CLI

def cli(cmd, body=None):
    args = [CLI, cmd]
    if body is not None:
        args.append("base64:" + base64.b64encode(body.encode("utf-8")).decode("ascii"))
    try:
        p = subprocess.run(args, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        return 127, {"error": "cli_missing", "message": "MabinogiMobile_CLI.exe 를 찾을 수 없습니다."}
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        return p.returncode, json.loads(out)
    except Exception:
        return p.returncode, {"_raw": out}


def game_status():
    """(연결됐나, 사람이 읽을 설명)"""
    rc, r = cli("status")
    if isinstance(r, dict) and r.get("pipe") == "connected":
        return True, "게임 연결됨"
    if isinstance(r, dict) and r.get("error") == "cli_missing":
        return False, r["message"]
    reason = (r or {}).get("reason") if isinstance(r, dict) else None
    if reason == "option_off":
        return False, "게임에서 'MM AI 에이전트 활성화' 를 켜 주세요"
    if reason == "game_off":
        return False, "게임 클라이언트가 실행되지 않았습니다"
    return False, "게임에 연결되지 않았습니다"


def split_for_chat(text, limit=CHAT_LIMIT):
    """50자 한도에 맞춰 띄어쓰기 기준으로 쪼갠다."""
    lines, cur = [], ""
    for w in text.split():
        cand = w if not cur else cur + " " + w
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            while len(w) > limit:
                lines.append(w[:limit])
                w = w[limit:]
            cur = w
    if cur:
        lines.append(cur)
    return lines


def clean(text):
    """보낼 만한 말인지 판단해서 다듬는다. 버릴 것은 None."""
    text = " ".join(text.split()).strip()
    if len(text) < 2:
        return None
    # '/' '#' 로 시작하면 게임이 명령으로 보고 거절한다. 떼어낸다.
    while text and text[0] in "/#":
        text = text[1:].lstrip()
    if len(text) < 2:
        return None
    for bad in HALLUCINATIONS:
        if bad in text:
            return None
    if len(set(text.replace(" ", ""))) <= 1:    # 같은 글자 반복 = 인식 실패
        return None
    return text


def input_devices():
    return [(i, d) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
            and "매퍼" not in d["name"] and "캡처 드라이버" not in d["name"]]


def device_name(dev):
    try:
        if dev is None:
            dev = sd.default.device[0]
        return sd.query_devices(dev)["name"].strip()
    except Exception:
        return "?"


# ---------------------------------------------------------------- 엔진

class Engine:
    """마이크를 듣고 있다가, 한 마디가 끝나면 인식해서 채팅으로 보낸다.

    on_event(kind, text, meta) 로 바깥에 알린다.
      kind: "info" | "sent" | "dropped" | "error" | "listen"
    on_level(rms) 은 프레임마다 불린다. UI 음량 막대용.
    """

    def __init__(self, settings, on_event=None, on_level=None):
        self.s = settings
        self.on_event = on_event or (lambda *a: None)
        self.on_level = on_level or (lambda v: None)

        self.listening = True
        self.model = None
        self.stream = None
        self.jobs = queue.Queue()
        self.stop_flag = threading.Event()
        self.hotkey_restart = threading.Event()   # 단축키를 바꾸면 다시 등록한다

        self.noise = 0.005
        self.last_sent = ("", 0.0)
        self._buf = []
        self._pre = deque(maxlen=PRE_ROLL)
        self._loud = 0
        self._quiet = 0
        self._in_speech = False

    # --- 기록
    def _log_file(self, text):
        if not self.s.get("log_to_file"):
            return
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:
            pass

    # --- 모델
    def _model_target(self, name):
        """(엔진에 넘길 값, 설명). 없으면 (None, 이유)."""
        import models
        if models.is_installed(name):
            return models.model_dir(name), "models 폴더"
        # 앱 폴더에 없더라도 허깅페이스 캐시에 이미 있으면 그걸 쓴다.
        # 3GB 를 다시 받게 하지 않기 위한 배려다.
        try:
            from faster_whisper import WhisperModel
            WhisperModel(name, device="cpu", compute_type="int8",
                         local_files_only=True)
            return name, "허깅페이스 캐시"
        except Exception:
            return None, "아직 받지 않음"

    def load_model(self, name=None):
        from faster_whisper import WhisperModel
        name = name or self.s["model"]
        target, where = self._model_target(name)
        if target is None:
            self.on_event("error",
                          "모델 %s 을 아직 받지 않았습니다. 창에서 내려받아 주세요." % name, {})
            return None
        for device, compute in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                self.model = WhisperModel(target, device=device, compute_type=compute)
                self.s["model"] = name
                self.on_event("info", "모델 %s / %s(%s) 준비 완료 · %s"
                              % (name, device, compute, where), {})
                return device
            except Exception as e:
                self.on_event("info", "%s 사용 불가: %s"
                              % (device, str(e).splitlines()[0][:90]), {})
        self.on_event("error", "모델을 올리지 못했습니다.", {})
        return None

    def reload_model(self, name):
        """돌아가는 중에 모델을 갈아 끼운다. 듣기는 잠깐 멈춘다."""
        was = self.listening
        self.set_listening(False)
        old, self.model = self.model, None
        del old
        ok = self.load_model(name) is not None
        self.set_listening(was and ok)
        return ok

    # --- 마이크 콜백
    def _on_audio(self, indata, frames, time_info, status):
        frame = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-9)
        self.on_level(rms)

        if not self._in_speech:
            # 조용할 때의 소리를 소음 기준선으로 천천히 따라간다
            self.noise = 0.995 * self.noise + 0.005 * rms
            self._pre.append(frame)

        threshold = max(self.noise * float(self.s["noise_mult"]), 0.012)
        if rms > threshold:
            self._loud += 1
            self._quiet = 0
        else:
            self._quiet += 1
            self._loud = 0

        if not self._in_speech:
            if self._loud >= START_FRAMES and self.listening:
                self._in_speech = True
                self._buf = list(self._pre)      # 첫 음절이 잘리지 않게
                self._pre.clear()
        else:
            self._buf.append(frame)
            hang = int(float(self.s["hang_sec"]) * SAMPLE_RATE / FRAME)
            too_long = len(self._buf) * FRAME / SAMPLE_RATE >= MAX_SEC
            if self._quiet >= hang or too_long:
                self._in_speech = False
                audio = np.concatenate(self._buf)
                self._buf = []
                if len(audio) / SAMPLE_RATE >= MIN_SEC:
                    self.jobs.put(audio)

    # --- 인식하고 보내는 일꾼
    def _worker(self):
        while not self.stop_flag.is_set():
            try:
                audio = self.jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.model is None:          # 모델을 아직 안 받았으면 흘려보낸다
                continue
            sec = len(audio) / SAMPLE_RATE
            t0 = time.time()
            try:
                segments, _ = self.model.transcribe(
                    audio, language="ko", beam_size=1, vad_filter=True,
                    condition_on_previous_text=False)
                raw = " ".join(s.text.strip() for s in segments).strip()
            except Exception as e:
                self.on_event("error", "인식 실패: %s" % str(e)[:80], {})
                continue
            took = time.time() - t0

            text = clean(raw)
            meta = {"sec": sec, "took": took, "raw": raw}
            if not text:
                self.on_event("dropped", raw or "빈 결과", meta)
                continue

            prev, when = self.last_sent
            if text == prev and time.time() - when < 5:
                self.on_event("dropped", "중복: %s" % text, meta)
                continue
            self.last_sent = (text, time.time())

            lines = split_for_chat(text)
            meta["lines"] = len(lines)
            if self.s.get("dry"):
                self.on_event("sent", text, dict(meta, dry=True))
                continue

            self.on_event("sent", text, meta)
            self._log_file(text)
            for ln in lines:
                for _ in range(4):
                    rc, r = cli("write_chat", ln)
                    if isinstance(r, dict) and r.get("error") == "rate_limited":
                        time.sleep(float(r.get("retryAfterSeconds") or 3) + 0.3)
                        continue
                    if isinstance(r, dict) and r.get("error"):
                        self.on_event("error", "전송 거절: %s"
                                      % r.get("message", r["error"]), {})
                    break
                time.sleep(1.2)

    # --- 단축키 감시
    def _watch_key(self):
        """먼저 RegisterHotKey 로 등록해 본다. 실패하면 키 상태 읽기로 대신한다.

        GetAsyncKeyState 로 읽는 방식은 게임이 관리자 권한으로 돌고 우리가
        아니면 막힌다. RegisterHotKey 는 운영체제가 조합을 가로채서 우리
        메시지 큐에 넣어 주므로 그 벽을 넘는다. Win 조합을 써도 시작 메뉴가
        열리지 않는다 - 운영체제가 조합을 먹어 버리기 때문이다.
        """
        # 단축키를 바꾸면 다시 등록해야 하므로 통째로 되돌아오는 고리로 둔다.
        while not self.stop_flag.is_set():
            self.hotkey_restart.clear()
            spec = self.s.get("hotkey", "win+f9")
            if not self._register_hotkey(spec):
                self.on_event(
                    "info",
                    "단축키 %s 등록 실패 (다른 프로그램이 이미 쓰는 조합일 수 있음). "
                    "키 상태 읽기로 대신하지만, 게임이 관리자 권한이면 안 먹힙니다."
                    % hotkey_label(spec), {})
                self._poll_hotkey(spec)

    def set_hotkey(self, spec):
        """단축키를 즉시 갈아 끼운다. 감시 고리가 다시 등록한다."""
        self.s["hotkey"] = spec
        save_settings(self.s)
        self.hotkey_restart.set()

    def _register_hotkey(self, spec):
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        flags, vk = hotkey_flags(spec)
        if vk is None:
            return False
        # RegisterHotKey 는 등록한 스레드의 큐로 WM_HOTKEY 를 보낸다.
        # 그래서 등록과 메시지 받기를 같은 스레드에서 해야 한다.
        if not u32.RegisterHotKey(None, 1, flags | MOD_NOREPEAT, vk):
            return False
        self.on_event("info", "단축키 %s 등록됨" % hotkey_label(spec), {})

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                        ("pt_y", wintypes.LONG)]

        msg = MSG()
        try:
            while not self.stop_flag.is_set() and not self.hotkey_restart.is_set():
                # PeekMessage 로 받아야 중단 요청을 확인할 틈이 생긴다
                if u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_HOTKEY:
                        self.set_listening(not self.listening)
                else:
                    time.sleep(0.03)
        finally:
            u32.UnregisterHotKey(None, 1)
        return True

    def _poll_hotkey(self, spec):
        """예비 수단. 키 상태를 직접 읽는다."""
        u32 = ctypes.windll.user32
        gaks = u32.GetAsyncKeyState
        mods, key = parse_hotkey(spec)
        uses_win = MODIFIERS["win"] in mods
        down = False

        def held(vks):
            return any(gaks(v) & 0x8000 for v in vks)

        while not self.stop_flag.is_set() and not self.hotkey_restart.is_set():
            now = bool(gaks(key) & 0x8000) and all(held(g) for g in mods)
            if now and not down:
                self.set_listening(not self.listening)
                if uses_win:
                    # Win 을 떼는 순간 시작 메뉴가 열리는 걸 막는다.
                    u32.keybd_event(VK_CONTROL, 0, 0, 0)
                    u32.keybd_event(VK_CONTROL, 0, 2, 0)
            down = now
            time.sleep(0.03)

    def set_listening(self, on):
        self.listening = bool(on)
        if not self.listening:                   # 듣기를 끄면 모으던 말은 버린다
            self._in_speech = False
            self._buf = []
        self.on_event("listen", "듣기 켜짐" if self.listening else "듣기 꺼짐",
                      {"on": self.listening})

    # --- 수명
    def open_stream(self):
        self.close_stream()
        dev = self.s.get("device")
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                     dtype="float32", blocksize=FRAME,
                                     device=dev, callback=self._on_audio)
        self.stream.start()
        self.noise = 0.005
        self.on_event("info", "마이크: %s" % device_name(dev), {})

    def close_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def start(self):
        import models
        if not self.s.get("model"):
            # 첫 실행. 이 PC 형편에 맞는 것을 골라 권한다.
            pick, why = models.recommend()
            self.s["model"] = pick
            save_settings(self.s)
            self.on_event("info", "모델을 %s 로 골랐습니다 — %s" % (pick, why), {})
        if self.load_model() is None:
            # 모델이 없으면 마이크는 열어 두고 기다린다. 창에서 받으면 바로 쓴다.
            self.on_event("info", "모델을 받은 뒤에 인식이 시작됩니다.", {})
            threading.Thread(target=self._worker, daemon=True).start()
            threading.Thread(target=self._watch_key, daemon=True).start()
            self.open_stream()
            return False
        if not self.s.get("dry"):
            ok, msg = game_status()
            self.on_event("info" if ok else "error", msg, {})
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._watch_key, daemon=True).start()
        self.open_stream()
        return True

    def shutdown(self):
        self.stop_flag.set()
        self.close_stream()
