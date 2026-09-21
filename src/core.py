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
import sys
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

VERSION = "1.4.1"
REPO = "Git-seokwon/mabi-voice-chat"


# ---------------------------------------------------------------- CUDA 준비
# cuBLAS 가 없으면 윈도우가 모달 창을 띄우고 멈춘다. 그래서 창을 막고,
# 검색 경로를 넓히고, 실제로 불러와 본 뒤에야 CUDA 를 쓴다.
_cuda_note = None
_cuda_ok = None


def _suppress_dll_error_box():
    SEM_FAILCRITICALERRORS = 0x0001
    SEM_NOGPFAULTERRORBOX = 0x0002
    try:
        ctypes.windll.kernel32.SetErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
    except Exception:
        pass


def _cuda_dll_dirs():
    """cuBLAS 가 있을 만한 자리들. 앞쪽이 우선."""
    import glob
    dirs = []
    # 1) 이 프로그램 런타임에 pip 로 깔린 nvidia 패키지
    for sp in sys.path:
        if sp.endswith("site-packages"):
            dirs += sorted(glob.glob(os.path.join(sp, "nvidia", "*", "bin")))
    # 2) 시스템에 깔린 CUDA 툴킷
    env = os.environ.get("CUDA_PATH")
    if env:
        dirs.append(os.path.join(env, "bin"))
    dirs += sorted(glob.glob(
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin"),
        reverse=True)
    return [d for d in dirs if os.path.isdir(d)]


def cuda_ready():
    """(GPU 를 쓸 수 있나, 사람이 읽을 설명). 한 번만 살펴보고 기억한다."""
    global _cuda_ok, _cuda_note
    if _cuda_ok is not None:
        return _cuda_ok, _cuda_note

    _suppress_dll_error_box()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            _cuda_ok, _cuda_note = False, "GPU 를 찾지 못했습니다 (CPU 로 씁니다)"
            return _cuda_ok, _cuda_note
    except Exception as e:
        _cuda_ok, _cuda_note = False, "GPU 확인 실패: %s" % str(e)[:60]
        return _cuda_ok, _cuda_note

    for d in _cuda_dll_dirs():
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
    try:
        ctypes.WinDLL("cublas64_12.dll")
        _cuda_ok, _cuda_note = True, "GPU 사용 가능"
    except OSError:
        _cuda_ok = False
        _cuda_note = ("GPU 가 있지만 cuBLAS 가 없어 CPU 로 씁니다. "
                      "GPU로_바꾸기.bat (setup_gpu.bat) 을 한 번 실행하면 빨라집니다.")
    return _cuda_ok, _cuda_note


EXE = "MabinogiMobile_CLI.exe"


def _reg_path_dirs():
    """레지스트리의 PATH. 프로세스가 물려받은 PATH 가 낡았을 때를 위한 것."""
    import winreg
    out = []
    for root, sub in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
            for p in str(val).split(";"):
                p = os.path.expandvars(p.strip().strip('"'))
                if p:
                    out.append(p)
        except Exception:
            pass
    return out


def _reg_install_dirs():
    """제거 항목에 적힌 '마비노기 모바일' 설치 위치. PATH 와 무관하다."""
    import winreg
    out = []
    bases = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, base in bases:
        try:
            with winreg.OpenKey(root, base) as bk:
                for i in range(winreg.QueryInfoKey(bk)[0]):
                    try:
                        name = winreg.EnumKey(bk, i)
                        with winreg.OpenKey(bk, name) as k:
                            disp = str(winreg.QueryValueEx(k, "DisplayName")[0])
                            if "마비노기" not in disp and "mabinogi" not in disp.lower():
                                continue
                            loc = str(winreg.QueryValueEx(k, "InstallLocation")[0])
                            if loc.strip():
                                out.append(loc.strip().strip('"'))
                    except Exception:
                        continue
        except Exception:
            pass
    return out


def _drive_dirs():
    """드라이브마다 흔한 설치 자리. 넥슨 런처는 설치 폴더를 고를 수 있다."""
    import string
    subs = (r"Nexon\MabinogiMobile",
            r"Games\Nexon\MabinogiMobile",
            r"Game\Nexon\MabinogiMobile",
            r"Program Files\Nexon\MabinogiMobile",
            r"Program Files (x86)\Nexon\MabinogiMobile",
            r"MabinogiMobile")
    out = []
    for letter in string.ascii_uppercase:
        root = "%s:\\" % letter
        if not os.path.isdir(root):
            continue
        out += [os.path.join(root, s) for s in subs]
    return out


def find_cli(settings=None):
    """게임 CLI 를 찾는다. 못 찾으면 None. PATH 한 갈래만 믿지 않는다."""
    # 1) 사람이 직접 지정한 것이 가장 우선
    for p in ((settings or {}).get("cli_path"), os.environ.get("MABI_CLI")):
        if p and os.path.isfile(p):
            return p
    # 2) 지금 프로세스의 PATH
    got = shutil.which("MabinogiMobile_CLI")
    if got and os.path.isfile(got):
        return got
    # 3) 레지스트리의 PATH, 4) 설치 위치, 5) 드라이브 훑기
    for d in _reg_path_dirs() + _reg_install_dirs() + _drive_dirs():
        try:
            p = os.path.join(d, EXE)
            if os.path.isfile(p):
                return p
        except Exception:
            continue
    return None


# 훑을 때 들어가지 않는 폴더. 게임이 있을 리 없고 크기만 큰 곳들.
_SKIP_DIRS = {
    "windows", "programdata", "appdata", "$recycle.bin",
    "system volume information", "recovery", "perflogs",
    "node_modules", "__pycache__", "venv", ".venv", "site-packages",
    "onedrive", "temp", "tmp", "cache", "winsxs", "assembly",
}
# 이 낱말이 들어간 폴더는 먼저 본다
_HINTS = ("nexon", "mabinogi", "game", "games", "program files")


def deep_find_cli(time_budget=25.0, on_progress=None):
    """디스크를 훑어 CLI 를 찾는다. 얕은 곳부터, 깊이 5칸까지, 시간 안에서만."""
    import string
    from collections import deque as dq

    deadline = time.time() + float(time_budget)
    todo = dq((r, 0) for r in ("%s:\\" % c for c in string.ascii_uppercase)
              if os.path.isdir(r))
    seen = 0
    while todo and time.time() < deadline:
        d, depth = todo.popleft()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        seen += 1
        if on_progress and seen % 40 == 0:
            on_progress(d)
        for e in entries:
            try:
                if e.is_file(follow_symlinks=False) and e.name.lower() == EXE.lower():
                    return e.path
            except OSError:
                continue
        if depth >= 5:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            n = e.name.lower()
            if n in _SKIP_DIRS or n.startswith((".", "$")):
                continue
            if any(h in n for h in _HINTS):
                todo.appendleft((e.path, depth + 1))     # 그럴듯한 곳 먼저
            else:
                todo.append((e.path, depth + 1))
    return None


_cli = None


def cli_path(settings=None, rescan=False):
    """찾은 CLI 경로. 한 번 찾으면 기억한다."""
    global _cli
    if rescan or _cli is None or not os.path.isfile(_cli or ""):
        _cli = find_cli(settings)
    return _cli


def set_cli_path(path, settings=None):
    """창에서 사람이 골라 준 경로를 쓴다."""
    global _cli
    if not path or not os.path.isfile(path):
        return False
    _cli = path
    if settings is not None:
        settings["cli_path"] = path
        save_settings(settings)
    return True


SAMPLE_RATE = 16000
FRAME = 480                   # 30ms
CHAT_LIMIT = 50               # write_chat 한 줄 최대 글자수

START_FRAMES = 3              # 이만큼 연속 크면 말 시작
MIN_SEC = 0.4                 # 이보다 짧으면 기침·잡음으로 보고 버림
MAX_SEC = 15.0                # 이보다 길면 잘라서 넘김
PRE_ROLL = 6                  # 말 시작 직전 프레임도 함께 (첫 음절 보존)

# --- 전역 단축키 ---
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


# RegisterHotKey 용 모디파이어 비트
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


# 위스퍼에 미리 귀띔하는 낱말. 이런 말이 나올 거라고 알려 주면 더 잘 알아듣는다.
GAME_WORDS = (
    "마비노기 던전 레이드 파티 길드 퀘스트 보스 사냥 채집 제작 강화 인챈트 "
    "던바튼 티르코네일 이멘마하 반호르 콜헨 켈라 카브 페라 마스 알비 "
    "상아탑 여신 모리안 나오 티르 밀레시안 에린 룬 엠블럼 장신구 "
    "장궁병 검술사 마법사 힐러 격투가 음유시인 전사"
)


def build_prompt(extra=""):
    """위스퍼에 넘길 귀띔 문장. 사용자가 적은 낱말을 뒤에 붙인다."""
    extra = " ".join(str(extra).replace(",", " ").split())
    return (GAME_WORDS + " " + extra).strip()


def parse_replace(spec):
    """'던젼>던전, 파뤼>파티' -> {'던젼': '던전', '파뤼': '파티'}"""
    out = {}
    for part in str(spec).split(","):
        if ">" not in part:
            continue
        a, b = part.split(">", 1)
        a, b = a.strip(), b.strip()
        if a:
            out[a] = b
    return out


# 위스퍼가 무음·잡음에 붙이는 흔한 헛문장들. 이런 건 보내지 않는다.
HALLUCINATIONS = (
    "감사합니다", "시청해주셔서", "구독과 좋아요", "구독", "자막",
    "MBC 뉴스", "KBS", "한글자막", "본 영상은", "다음 영상에서",
)

# core.py 는 src\ 안에 있다. 프로그램 뿌리는 그 부모다.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    r"""모델·설정·기록을 두는 곳. MABI_DATA > 프로그램 폴더 > %LOCALAPPDATA%."""
    env = os.environ.get("MABI_DATA")
    if env:
        return env
    for mark in ("models", "settings.json"):
        if os.path.exists(os.path.join(APP_DIR, mark)):
            return APP_DIR
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "mabi-voice-chat")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return APP_DIR
    return d


def settings_path():
    return os.path.join(data_dir(), "settings.json")
DEFAULTS = {
    "device": None,           # None 이면 윈도우 기본 마이크
    "model": None,            # None 이면 첫 실행에 이 PC 에 맞는 것을 권한다
    "dry": False,             # True 면 인식만 하고 보내지 않는다
    "noise_mult": 3.0,        # 주변 소음의 몇 배를 말소리로 볼지
    "gain": 1.0,              # 입력 증폭. 마이크가 작으면 올린다
    "min_level": 0.012,       # 문턱의 최소값. 작은 마이크는 이걸 낮춰야 한다
    "hang_sec": 0.8,          # 이만큼 조용하면 말이 끝난 것으로 본다
    "log_to_file": True,
    "hotkey": "win+f9",       # 듣기 켜고 끄기. 창 포커스와 무관하게 먹힌다
    "overlay": True,          # 창을 내리면 작은 표시창을 띄운다
    "overlay_pos": None,      # [x, y]. 끌어서 옮긴 자리를 기억한다
    "cli_path": None,         # 게임 CLI 를 못 찾을 때 직접 지정한 경로
    "mode": "auto",           # auto = 말하면 감지, ptt = 단축키로 눌러서 말하기
    "cpu_threads": 0,         # 0 이면 알아서. GPU 없이 쓸 때 속도를 좌우한다
    "vocab": "",              # 자주 쓰는 낱말. 위스퍼에 미리 귀띔한다
    "replace": "",            # 고쳐 쓰기. "던젼>던전, 파뤼>파티" 꼴
    "check_update": True,     # 시작할 때 새 판이 있는지 본다
}


_mutex = None


def claim_single_instance(name="mabi_voice_chat"):
    """이미 돌고 있으면 False. 두 개가 들으면 채팅이 두 번씩 나간다."""
    global _mutex
    ERROR_ALREADY_EXISTS = 183
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, name)     # 핸들은 붙잡아 둔다
    return k32.GetLastError() != ERROR_ALREADY_EXISTS


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 게임 CLI

def cli(cmd, body=None, settings=None):
    exe = cli_path(settings)
    if not exe:
        return 127, {"error": "cli_missing",
                     "message": "게임 조작 프로그램(%s)을 찾지 못했습니다." % EXE}
    args = [exe, cmd]
    if body is not None:
        args.append("base64:" + base64.b64encode(body.encode("utf-8")).decode("ascii"))
    try:
        p = subprocess.run(args, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        return 127, {"error": "cli_missing",
                     "message": "%s 를 실행할 수 없습니다." % exe}
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        return p.returncode, json.loads(out)
    except Exception:
        return p.returncode, {"_raw": out}


def game_status(settings=None):
    """(연결됐나, 사람이 읽을 설명)"""
    rc, r = cli("status", settings=settings)
    if isinstance(r, dict) and r.get("pipe") == "connected":
        return True, "게임 연결됨"
    if isinstance(r, dict) and r.get("error") == "cli_missing":
        return False, (r["message"] +
                       " 게임에서 'MM AI 에이전트 활성화' 를 켜면 깔립니다. "
                       "이미 켜 두셨다면 설정 탭에서 직접 지정해 주세요.")
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


def clean(text, replace=None):
    """보낼 만한 말인지 판단해서 다듬는다. 버릴 것은 None."""
    text = " ".join(text.split()).strip()
    for a, b in (replace or {}).items():
        text = text.replace(a, b)
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


def latest_version():
    """GitHub 에 올라온 최신 판 번호. 못 보면 None."""
    import json as _json
    import urllib.request
    try:
        url = "https://api.github.com/repos/%s/releases/latest" % REPO
        req = urllib.request.Request(url, headers={"User-Agent": "mabi-voice-chat"})
        with urllib.request.urlopen(req, timeout=5) as r:
            tag = _json.loads(r.read().decode("utf-8")).get("tag_name") or ""
        return tag.lstrip("vV") or None
    except Exception:
        return None


def newer(a, b):
    """판 번호 a 가 b 보다 새것인가."""
    def parts(v):
        out = []
        for x in str(v).split("."):
            try:
                out.append(int(x))
            except ValueError:
                out.append(0)
        return out
    return parts(a) > parts(b)


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
        self.raw_max = 0.0          # 증폭하기 전의 최대 음량
        self.recording = False      # 눌러서 말하기로 녹음 중인가
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
        path = os.path.join(data_dir(), "sent.log")
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
        # 허깅페이스 캐시에 이미 있으면 그걸 쓴다. 다시 받게 하지 않는다.
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
        ok, note = cuda_ready()
        if not ok:
            self.on_event("info", note, {})
        plans = ([("cuda", "int8_float16")] if ok else []) + [("cpu", "int8")]
        # GPU 가 없으면 쓰레드 수가 속도를 좌우한다. 게임도 돌고 있으니
        # 전부 쓰지는 않고 절반쯤만 쓴다.
        want = int(self.s.get("cpu_threads") or 0)
        if want <= 0:
            want = max(4, min(8, (os.cpu_count() or 4) // 2))
        for device, compute in plans:
            try:
                self.model = WhisperModel(target, device=device, compute_type=compute,
                                          cpu_threads=want)
                self.s["model"] = name
                self.on_event("info", "모델 %s / %s(%s) 준비 완료 · %s%s"
                              % (name, device, compute, where,
                                 " · 쓰레드 %d" % want if device == "cpu" else ""), {})
                if device == "cpu" and name in ("medium", "large-v3",
                                                "large-v3-turbo"):
                    self.on_event("error",
                                  "CPU 로 %s 를 돌리면 말보다 인식이 한참 늦습니다. "
                                  "설정에서 small 로 바꾸시는 편이 낫습니다." % name, {})
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
        # 자동 맞추기가 보는 값 (증폭 전)
        self.raw_max = max(self.raw_max, float(np.abs(frame).max()))
        gain = float(self.s.get("gain", 1.0))
        if gain != 1.0:
            frame = np.clip(frame * gain, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-9)
        self.on_level(rms)

        if self.s.get("mode") == "ptt":
            # 눌러서 말하기: 단축키를 누른 동안에만 모은다. 소리 크기는 안 본다.
            if self.recording:
                self._buf.append(frame)
                if len(self._buf) * FRAME / SAMPLE_RATE >= MAX_SEC:
                    self.stop_record()
            else:
                self._pre.append(frame)
            return

        if not self._in_speech:
            # 조용할 때의 소리를 소음 기준선으로 천천히 따라간다
            self.noise = 0.995 * self.noise + 0.005 * rms
            self._pre.append(frame)

        threshold = max(self.noise * float(self.s["noise_mult"]),
                        float(self.s.get("min_level", 0.012)))
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
                    condition_on_previous_text=False,
                    initial_prompt=build_prompt(self.s.get("vocab", "")))
                raw = " ".join(s.text.strip() for s in segments).strip()
            except Exception as e:
                self.on_event("error", "인식 실패: %s" % str(e)[:80], {})
                continue
            took = time.time() - t0

            text = clean(raw, parse_replace(self.s.get("replace", "")))
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
                    rc, r = cli("write_chat", ln, settings=self.s)
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
        """RegisterHotKey 로 등록한다. 실패하면 키 상태 읽기로 대신한다."""
        # 단축키를 바꾸면 다시 등록해야 하므로 고리로 둔다
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
        # 등록과 메시지 받기는 같은 스레드여야 한다
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
                        self.hotkey_pressed()
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
                self.hotkey_pressed()
                if uses_win:
                    # Win 을 떼는 순간 시작 메뉴가 열리는 걸 막는다.
                    u32.keybd_event(VK_CONTROL, 0, 0, 0)
                    u32.keybd_event(VK_CONTROL, 0, 2, 0)
            down = now
            time.sleep(0.03)

    def start_record(self):
        if self.recording or not self.listening:
            return
        self._buf = list(self._pre)          # 첫 음절이 잘리지 않게
        self._pre.clear()
        self.recording = True
        self.on_event("listen", "녹음 중", {"on": True, "rec": True})

    def stop_record(self):
        if not self.recording:
            return
        self.recording = False
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, "float32")
        self._buf = []
        if len(audio) / SAMPLE_RATE >= MIN_SEC:
            self.jobs.put(audio)
            self.on_event("listen", "녹음 끝", {"on": True, "rec": False})
        else:
            self.on_event("listen", "너무 짧습니다", {"on": True, "rec": False})

    def toggle_record(self):
        self.stop_record() if self.recording else self.start_record()

    def hotkey_pressed(self):
        """단축키가 눌렸을 때. 방식에 따라 하는 일이 다르다."""
        if self.s.get("mode") == "ptt":
            self.toggle_record()
        else:
            self.set_listening(not self.listening)

    def threshold(self):
        """지금 말소리로 인정하는 문턱."""
        return max(self.noise * float(self.s["noise_mult"]),
                   float(self.s.get("min_level", 0.012)))

    def suggest_gain(self, target=0.35):
        """방금 들어온 소리를 보고 알맞은 증폭을 셈한다. (증폭, 원래최대)"""
        peak = self.raw_max
        if peak < 1e-4:
            return None, peak
        g = max(1.0, min(20.0, target / peak))
        return round(g, 1), peak

    def set_listening(self, on):
        self.listening = bool(on)
        if not self.listening:                   # 듣기를 끄면 모으던 말은 버린다
            self._in_speech = False
            self.recording = False
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
            ok, msg = game_status(self.s)
            self.on_event("info" if ok else "error", msg, {})
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._watch_key, daemon=True).start()
        if self.s.get("check_update", True):
            threading.Thread(target=self._check_update, daemon=True).start()
        self.open_stream()
        return True

    def _check_update(self):
        got = latest_version()
        if got and newer(got, VERSION):
            self.on_event("info",
                          "새 판 %s 이 나왔습니다. github.com/%s/releases/latest"
                          % (got, REPO), {})

    def shutdown(self):
        self.stop_flag.set()
        self.close_stream()
