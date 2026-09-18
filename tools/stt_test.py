# -*- coding: utf-8 -*-
"""1단계: 마이크 -> STT 인식만 확인. 채팅은 보내지 않는다.

    python stt_test.py --list        # 입력 장치 목록
    python stt_test.py --scan        # 어느 장치에 소리가 들어오는지 훑기
    python stt_test.py               # 6초 녹음 후 인식
    python stt_test.py 10            # 10초 녹음
    python stt_test.py 6 --dev 28    # 장치 번호 지정

    MABI_MIC=28 python stt_test.py   # 장치를 환경변수로 고정
    MABI_STT_MODEL=medium python stt_test.py

인식 결과와 함께 "게임 채팅으로 보낸다면 몇 줄로 쪼개지는가"까지 보여준다.
채팅 한 줄은 50자가 한도라서, 말이 길면 여러 줄이 된다.
"""
import os
import sys
import time

import numpy as np
import sounddevice as sd

# 윈도우 콘솔은 CP949라서 한글이 깨진다. 출력을 UTF-8로 못 박는다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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


def input_devices():
    """입력 채널이 있는 장치만, (번호, 정보) 목록으로."""
    return [(i, d) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


def list_devices():
    default = sd.default.device[0]
    print("입력 장치 목록:")
    for i, d in input_devices():
        api = sd.query_hostapis(d["hostapi"])["name"]
        print("  %2d  %-9s ch=%d sr=%.0f  %s%s"
              % (i, api, d["max_input_channels"], d["default_samplerate"],
                 d["name"].strip(), "   <= 기본" if i == default else ""))


def measure(dev, seconds=2.0):
    """한 장치에서 잠깐 받아 최대 음량을 잰다. 못 열면 None."""
    try:
        info = sd.query_devices(dev)
        sr = int(info["default_samplerate"])
        a = sd.rec(int(seconds * sr), samplerate=sr, channels=1,
                   dtype="float32", device=dev)
        sd.wait()
        return float(np.abs(a).max())
    except Exception as e:
        return None


def scan():
    """말하는 동안 장치를 하나씩 훑어서 살아 있는 마이크를 찾아낸다."""
    cands = [(i, d) for i, d in input_devices()
             if "매퍼" not in d["name"] and "캡처 드라이버" not in d["name"]]
    print("이제부터 계속 말씀하세요. 장치를 하나씩 2초씩 들어봅니다.\n")
    results = []
    for i, d in cands:
        peak = measure(i)
        name = d["name"].strip()[:46]
        if peak is None:
            print("  %2d  열 수 없음        %s" % (i, name))
            continue
        bar = "#" * min(40, int(peak * 60))
        print("  %2d  peak %.3f  %-40s %s" % (i, peak, bar, name))
        results.append((peak, i, name))

    if not results:
        print("\n열 수 있는 입력 장치가 없습니다.")
        return
    results.sort(reverse=True)
    best, idx, name = results[0]
    print()
    if best < 0.02:
        print("어느 장치에서도 소리가 잡히지 않았습니다 (최대 %.3f)." % best)
        print("윈도우 설정 > 시스템 > 소리 > 입력 에서 마이크 음소거와")
        print("입력 볼륨, 그리고 앱의 마이크 접근 권한을 확인해 주세요.")
    else:
        print("가장 잘 들어온 장치: %d (%s), peak %.3f" % (idx, name, best))
        print("이걸로 쓰시려면:  python stt_test.py 6 --dev %d" % idx)


def record(seconds, dev=None):
    if dev is None:
        dev = sd.default.device[0]
    info = sd.query_devices(dev)
    print("\n장치 %s (%s)" % (dev, info["name"].strip()))
    print(">>> 지금 말하세요 (%g초) ..." % seconds, flush=True)
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32", device=dev)
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
    args = sys.argv[1:]

    if "--list" in args:
        list_devices()
        return
    if "--scan" in args:
        scan()
        return

    dev = os.environ.get("MABI_MIC")
    if "--dev" in args:
        dev = args[args.index("--dev") + 1]
    dev = int(dev) if dev is not None else None

    seconds = 6.0
    for a in args:
        if a.replace(".", "", 1).isdigit():
            seconds = float(a)
            break

    model, device = pick_backend()

    audio = record(seconds, dev)
    peak = float(np.abs(audio).max())
    print("입력 음량 최대치 %.3f%s" % (peak, "  <- 너무 작습니다" if peak < 0.02 else ""))
    if peak == 0.0:
        print("소리가 전혀 들어오지 않았습니다. 'python stt_test.py --scan' 으로")
        print("어느 장치에 소리가 들어오는지 먼저 찾아 주세요.")
        return

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
