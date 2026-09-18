# -*- coding: utf-8 -*-
"""1단계: 마이크 -> STT 인식만 확인. 채팅은 보내지 않는다.

    python stt_test.py            # 6초 녹음 후 인식
    python stt_test.py 10         # 10초 녹음
    MABI_STT_MODEL=medium python stt_test.py

인식 결과와 함께 "게임 채팅으로 보낸다면 몇 줄로 쪼개지는가"까지 보여준다.
채팅 한 줄은 50자가 한도라서, 말이 길면 여러 줄이 된다.
"""
import os
import sys
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000          # whisper가 기대하는 샘플레이트
CHAT_LIMIT = 50              # write_chat 한 줄 최대 글자수
MODEL = os.environ.get("MABI_STT_MODEL", "large-v3")

# 게임이 VRAM을 4GB 넘게 쓰고 있어서 float16(3.1GB)은 다툰다.
# int8_float16은 1.6GB로 절반이고 한국어 정확도는 거의 같다.
BACKENDS = (("cuda", "int8_float16"), ("cuda", "float16"), ("cpu", "int8"))


def pick_backend():
    """CUDA가 되면 CUDA, 안 되면 CPU로 내려간다."""
    from faster_whisper import WhisperModel

    for device, compute in BACKENDS:
        try:
            t0 = time.time()
            m = WhisperModel(MODEL, device=device, compute_type=compute)
            print("모델 %s / %s(%s) 준비 완료 (%.1f초)"
                  % (MODEL, device, compute, time.time() - t0))
            return m, device
        except Exception as e:
            print("%s 사용 불가 -> %s" % (device, str(e).splitlines()[0][:120]))
    raise SystemExit("모델을 올리지 못했습니다.")


def record(seconds):
    print("\n>>> 지금 말하세요 (%d초) ..." % seconds, flush=True)
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32")
    sd.wait()
    print(">>> 녹음 끝", flush=True)
    return audio.reshape(-1)


def split_for_chat(text, limit=CHAT_LIMIT):
    """말이 길면 채팅 한 줄 한도에 맞춰 띄어쓰기 기준으로 쪼갠다."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            # 한 단어가 한도를 넘으면 글자 단위로 자른다
            while len(w) > limit:
                lines.append(w[:limit])
                w = w[limit:]
            cur = w
    if cur:
        lines.append(cur)
    return lines


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    model, device = pick_backend()

    audio = record(seconds)
    peak = float(np.abs(audio).max())
    print("입력 음량 최대치 %.3f%s" % (peak, "  <- 너무 작습니다" if peak < 0.02 else ""))

    t0 = time.time()
    segments, info = model.transcribe(audio, language="ko", beam_size=5,
                                      vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    took = time.time() - t0

    print("\n인식 %.2f초 (%s, 음성길이 %.1f초)" % (took, device, info.duration))
    if not text:
        print("인식된 말이 없습니다.")
        return

    print("결과: %s" % text)
    print("길이: %d자" % len(text))

    if text[0] in "/#":
        print("주의: 첫 글자가 '%s' 입니다. 게임이 명령으로 보고 거절하므로 떼어내야 합니다." % text[0])

    lines = split_for_chat(text)
    print("\n채팅으로 보낸다면 %d줄:" % len(lines))
    for i, ln in enumerate(lines, 1):
        print("  %d. (%2d자) %s" % (i, len(ln), ln))


if __name__ == "__main__":
    main()
