# -*- coding: utf-8 -*-
"""콘솔판. UI 없이 터미널에서 돌린다. 엔진은 core.py 와 같은 것을 쓴다.

    python voice_chat.py
    python voice_chat.py --dev 28     # 마이크 지정
    python voice_chat.py --dry        # 보내지 않고 인식만

창과 트레이가 있는 쪽은 src/mabi_voice.pyw 다. 실행.bat 으로 띄운다.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import core

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def show(kind, text, meta):
    if kind == "sent":
        head = "연습" if meta.get("dry") else "전송"
        bits = []
        if meta.get("sec"):
            bits.append("말 %.1f초" % meta["sec"])
        if meta.get("took"):
            bits.append("인식 %.1f초" % meta["took"])
        if meta.get("lines", 1) > 1:
            bits.append("%d줄" % meta["lines"])
        print("  [%s] %s   (%s)" % (head, text, ", ".join(bits)), flush=True)
    elif kind == "dropped":
        print("  (버림) %s" % text[:60], flush=True)
    elif kind == "listen":
        print("\n[F9] %s" % text, flush=True)
    else:
        print("%s" % text, flush=True)


def main():
    args = sys.argv[1:]
    # 모르는 인자를 줬을 때 그냥 듣기 시작하면 위험하다. 설명만 찍고 끝낸다.
    if {"-h", "--help", "/?"} & set(args):
        print(__doc__)
        return
    unknown = [a for a in args if a.startswith("-") and a not in ("--dev", "--dry")]
    if unknown:
        print("모르는 옵션: %s" % " ".join(unknown))
        print(__doc__)
        return

    s = core.load_settings()
    if "--dev" in args:
        s["device"] = int(args[args.index("--dev") + 1])
    if "--dry" in args:
        s["dry"] = True
    if "--dev" in args or "--dry" in args:
        pass                      # 이번 실행에만 적용. 설정 파일은 건드리지 않는다

    if not core.claim_single_instance():
        print("이미 실행 중입니다. 두 개가 동시에 들으면 채팅이 두 번씩 나갑니다.")
        return

    engine = core.Engine(s, on_event=show)
    if not engine.start():
        return
    print("말하면 %s. F9 로 듣기 켜고 끄기, Ctrl+C 로 종료."
          % ("화면에만 찍힙니다" if s.get("dry") else "바로 채팅으로 나갑니다"))
    print("-" * 60, flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        engine.shutdown()
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
