# -*- coding: utf-8 -*-
r"""배포용 zip 을 만든다.

    python build_dist.py

runtime\ 과 models\ 는 넣지 않는다. 받는 사람이 실행.bat 을 누르면 자기
PC 에 맞는 것이 자동으로 들어간다. 그래서 배포본은 아주 작다.
"""
import os
import sys
import zipfile

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import core

NAME = "mabi-voice-chat"

# 배포본에 들어갈 것. 여기 없는 것은 안 들어간다.
FILES = [
    "README.md",
    "requirements.txt",
    "core.py",
    "models.py",
    "mabi_voice.pyw",
    "setup.bat",
    "setup_gpu.bat",
    "실행.bat",
    "설치.bat",
    "콘솔로_실행.bat",
    "관리자로_실행.bat",
    "GPU로_바꾸기.bat",
    "tools/voice_chat.py",
    "tools/stt_test.py",
]

# 실수로 딸려 들어가면 안 되는 것들. 이름이 스치기만 해도 멈춘다.
FORBIDDEN = ("settings.json", "sent.log", "runtime", "models/", "__pycache__",
             ".git", "dist")


def main():
    out_dir = os.path.join(APP, "dist")
    os.makedirs(out_dir, exist_ok=True)
    stem = "%s-%s" % (NAME, core.VERSION)
    zip_path = os.path.join(out_dir, stem + ".zip")

    files = list(FILES)
    # LICENSE 는 정해지면 넣는다. 없어도 묶이게 둔다.
    if os.path.isfile(os.path.join(APP, "LICENSE")):
        files.insert(1, "LICENSE")
    else:
        print("LICENSE 가 없어 넣지 않았습니다. 없으면 기본값은 '모든 권리 보유' 입니다.")
        print()

    missing = [f for f in files if not os.path.isfile(os.path.join(APP, f))]
    if missing:
        print("빠진 파일이 있습니다:")
        for f in missing:
            print("   -", f)
        return 1

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            inner = "%s/%s" % (stem, f)          # 압축을 풀면 폴더 하나로 나온다
            for bad in FORBIDDEN:
                if bad in f:
                    print("들어가면 안 되는 파일입니다:", f)
                    return 1
            z.write(os.path.join(APP, f), inner)

    size = os.path.getsize(zip_path)
    print("만들었습니다: %s" % zip_path)
    print("   %d개 파일, %.1f KB" % (len(files), size / 1024))
    print()
    print("받는 사람은 압축을 풀고 실행.bat 을 누르면 됩니다.")
    print("첫 실행에 파이썬(45MB)과 패키지(약 400MB), 그리고 고른 모델을 받습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
