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
import subprocess
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

CLI = r"C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe"
SAMPLE_RATE = 16000
FRAME = 480                   # 30ms
CHAT_LIMIT = 50               # write_chat 한 줄 최대 글자수

START_FRAMES = 3              # 이만큼 연속 크면 말 시작
MIN_SEC = 0.4                 # 이보다 짧으면 기침·잡음으로 보고 버림
MAX_SEC = 15.0                # 이보다 길면 잘라서 넘김
PRE_ROLL = 6                  # 말 시작 직전 프레임도 함께 (첫 음절 보존)

VK_TOGGLE = 0x78              # F9

# 위스퍼가 무음·잡음에 붙이는 흔한 헛문장들. 이런 건 보내지 않는다.
HALLUCINATIONS = (
    "감사합니다", "시청해주셔서", "구독과 좋아요", "구독", "자막",
    "MBC 뉴스", "KBS", "한글자막", "본 영상은", "다음 영상에서",
)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "settings.json")
DEFAULTS = {
    "device": None,           # None 이면 윈도우 기본 마이크
    "model": "large-v3",
    "dry": False,             # True 면 인식만 하고 보내지 않는다
    "noise_mult": 3.0,        # 주변 소음의 몇 배를 말소리로 볼지
    "hang_sec": 0.8,          # 이만큼 조용하면 말이 끝난 것으로 본다
    "log_to_file": True,
}


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
    def load_model(self):
        from faster_whisper import WhisperModel
        name = self.s["model"]
        for device, compute in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                self.model = WhisperModel(name, device=device, compute_type=compute)
                self.on_event("info", "모델 %s / %s(%s) 준비 완료" % (name, device, compute), {})
                return device
            except Exception as e:
                self.on_event("info", "%s 사용 불가: %s"
                              % (device, str(e).splitlines()[0][:90]), {})
        self.on_event("error", "모델을 올리지 못했습니다.", {})
        return None

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

    # --- F9 감시. 게임 창이 앞에 있어도 먹히게 키 상태를 직접 읽는다
    def _watch_key(self):
        gaks = ctypes.windll.user32.GetAsyncKeyState
        down = False
        while not self.stop_flag.is_set():
            now = bool(gaks(VK_TOGGLE) & 0x8000)
            if now and not down:
                self.set_listening(not self.listening)
            down = now
            time.sleep(0.04)

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
        if self.load_model() is None:
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
