# -*- coding: utf-8 -*-
"""2단계: 말하면 바로 인게임 채팅으로 나간다.

    python voice_chat.py
    python voice_chat.py --dev 28          # 마이크 지정
    python voice_chat.py --dry             # 전송하지 않고 인식만 (연습용)

목소리를 자동으로 감지한다. 말소리가 들리면 녹음을 시작하고, 0.8초쯤
조용해지면 끝난 것으로 보고 인식해서 채팅으로 보낸다. 확인 단계는 없다.

F9 로 듣기를 켜고 끌 수 있다. 자리를 비울 때는 꼭 끄는 게 좋다.
Ctrl+C 로 종료.
"""
import base64
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CLI = r"C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe"
SAMPLE_RATE = 16000
FRAME = 480                   # 30ms
CHAT_LIMIT = 50               # write_chat 한 줄 최대 글자수
MODEL = os.environ.get("MABI_STT_MODEL", "large-v3")

# --- 목소리 감지 기준 ---
START_FRAMES = 3              # 이만큼 연속 크면 말 시작
HANG_SEC = 0.8                # 이만큼 조용하면 말 끝
MIN_SEC = 0.4                 # 이보다 짧으면 기침·잡음으로 보고 버림
MAX_SEC = 15.0                # 이보다 길면 잘라서 넘김
PRE_ROLL = 6                  # 말 시작 직전 프레임도 같이 넘김 (첫 음절 보존)
NOISE_MULT = 3.0              # 주변 소음의 몇 배를 말소리로 볼지

VK_TOGGLE = 0x78              # F9

# 위스퍼가 무음·잡음에 붙이는 흔한 헛문장들. 이런 건 보내지 않는다.
HALLUCINATIONS = (
    "감사합니다", "시청해주셔서", "구독과 좋아요", "구독", "자막", "자막 제공",
    "MBC 뉴스", "KBS", "한글자막", "본 영상은", "다음 영상에서",
)


# ---------------------------------------------------------------- 채팅 보내기

def cli(cmd, body=None):
    args = [CLI, cmd]
    if body is not None:
        args.append("base64:" + base64.b64encode(body.encode("utf-8")).decode("ascii"))
    p = subprocess.run(args, capture_output=True)
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        return p.returncode, json.loads(out)
    except Exception:
        return p.returncode, {"_raw": out}


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


def send_chat(text):
    """여러 줄이면 차례로. rate_limited 는 알려준 시간만큼 쉬고 재시도."""
    for ln in split_for_chat(text):
        for _ in range(4):
            rc, r = cli("write_chat", ln)
            if isinstance(r, dict) and r.get("error") == "rate_limited":
                time.sleep(float(r.get("retryAfterSeconds") or 3) + 0.3)
                continue
            if isinstance(r, dict) and r.get("error"):
                print("   전송 거절: %s" % r.get("message", r["error"]))
            break
        time.sleep(1.2)


# ---------------------------------------------------------------- 결과 손질

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
    # 같은 글자만 반복되는 인식 실패
    if len(set(text.replace(" ", ""))) <= 1:
        return None
    return text


# ---------------------------------------------------------------- 본체

class VoiceChat:
    def __init__(self, dev=None, dry=False):
        self.dev = dev
        self.dry = dry
        self.listening = True
        self.jobs = queue.Queue()
        self.noise = 0.005          # 주변 소음 기준선. 계속 갱신된다.
        self.last_sent = ("", 0.0)

        self.buf = []               # 지금 모으는 말
        self.pre = deque(maxlen=PRE_ROLL)
        self.loud = 0
        self.quiet = 0
        self.in_speech = False

    # --- 마이크 콜백: 프레임마다 크기를 보고 말의 시작과 끝을 가린다
    def on_audio(self, indata, frames, time_info, status):
        frame = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-9)

        if not self.in_speech:
            # 조용할 때의 소리를 소음 기준선으로 천천히 따라간다
            self.noise = 0.995 * self.noise + 0.005 * rms
            self.pre.append(frame)

        threshold = max(self.noise * NOISE_MULT, 0.012)

        if rms > threshold:
            self.loud += 1
            self.quiet = 0
        else:
            self.quiet += 1
            self.loud = 0

        if not self.in_speech:
            if self.loud >= START_FRAMES and self.listening:
                self.in_speech = True
                self.buf = list(self.pre)      # 첫 음절이 잘리지 않게
                self.pre.clear()
        else:
            self.buf.append(frame)
            hang = int(HANG_SEC * SAMPLE_RATE / FRAME)
            too_long = len(self.buf) * FRAME / SAMPLE_RATE >= MAX_SEC
            if self.quiet >= hang or too_long:
                self.in_speech = False
                audio = np.concatenate(self.buf)
                self.buf = []
                if len(audio) / SAMPLE_RATE >= MIN_SEC:
                    self.jobs.put(audio)

    # --- F9 감시: 창을 옮기지 않아도 먹히게 GetAsyncKeyState 를 본다
    def watch_key(self):
        gaks = ctypes.windll.user32.GetAsyncKeyState
        down = False
        while True:
            now = bool(gaks(VK_TOGGLE) & 0x8000)
            if now and not down:
                self.listening = not self.listening
                print("\n[F9] 듣기 %s" % ("켜짐" if self.listening else "꺼짐"), flush=True)
            down = now
            time.sleep(0.04)

    # --- 인식하고 보내는 일꾼
    def work(self, model):
        while True:
            audio = self.jobs.get()
            sec = len(audio) / SAMPLE_RATE
            t0 = time.time()
            segments, _ = model.transcribe(
                audio, language="ko", beam_size=1, vad_filter=True,
                condition_on_previous_text=False)
            raw = " ".join(s.text.strip() for s in segments).strip()
            took = time.time() - t0

            text = clean(raw)
            if not text:
                print("  (%.1f초 버림: %s)" % (sec, raw[:30] or "빈 결과"), flush=True)
                continue

            # 같은 말이 연달아 두 번 잡히는 경우를 막는다
            prev, when = self.last_sent
            if text == prev and time.time() - when < 5:
                print("  (%.1f초 중복 건너뜀)" % sec, flush=True)
                continue
            self.last_sent = (text, time.time())

            nlines = len(split_for_chat(text))
            tag = "연습" if self.dry else "전송"
            print("  [%s] %s   (%.1f초 말, 인식 %.1f초, %d자%s)"
                  % (tag, text, sec, took, len(text),
                     ", %d줄" % nlines if nlines > 1 else ""), flush=True)
            if not self.dry:
                send_chat(text)

    def run(self):
        from faster_whisper import WhisperModel
        for device, compute in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                model = WhisperModel(MODEL, device=device, compute_type=compute)
                print("모델 %s / %s(%s) 준비 완료" % (MODEL, device, compute))
                break
            except Exception as e:
                print("%s 불가: %s" % (device, str(e).splitlines()[0][:100]))
        else:
            raise SystemExit("모델을 올리지 못했습니다.")

        if not self.dry:
            rc, r = cli("status")
            if rc != 0 or (isinstance(r, dict) and r.get("pipe") != "connected"):
                print("게임에 연결되지 않았습니다: %s" % json.dumps(r, ensure_ascii=False))
                print("게임 클라이언트와 'MM AI 에이전트 활성화' 설정을 확인해 주세요.")
                return
            print("게임 연결 확인")

        threading.Thread(target=self.watch_key, daemon=True).start()
        threading.Thread(target=self.work, args=(model,), daemon=True).start()

        info = sd.query_devices(self.dev if self.dev is not None else sd.default.device[0])
        print("\n마이크: %s" % info["name"].strip())
        print("말하면 %s. F9 로 듣기 켜고 끄기, Ctrl+C 로 종료."
              % ("화면에만 찍힙니다" if self.dry else "바로 채팅으로 나갑니다"))
        print("-" * 60, flush=True)

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=FRAME, device=self.dev, callback=self.on_audio):
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n종료합니다.")


def main():
    args = sys.argv[1:]
    dev = os.environ.get("MABI_MIC")
    if "--dev" in args:
        dev = args[args.index("--dev") + 1]
    VoiceChat(dev=int(dev) if dev else None, dry="--dry" in args).run()


if __name__ == "__main__":
    main()
