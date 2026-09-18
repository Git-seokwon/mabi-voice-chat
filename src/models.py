# -*- coding: utf-8 -*-
r"""모델 고르기·내려받기·재활용.

local_dir 로 받는다. 허깅페이스 기본 캐시는 윈도우에서 복사본을 하나 더
만들어 디스크를 두 배로 쓴다.
"""
import os
import shutil
import threading

# models.py 는 src\ 안에 있다. 프로그램 뿌리는 그 부모다.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def models_dir():
    r"""모델을 두는 곳. MABI_MODELS 로 정할 수 있다."""
    env = os.environ.get("MABI_MODELS")
    if env:
        return env
    try:
        import core
        return os.path.join(core.data_dir(), "models")
    except Exception:
        return os.path.join(APP_DIR, "models")

# turbo 만 저장소가 다르다. size_mb 는 어림값이고 받을 때 서버에 다시 묻는다.
CATALOG = [
    {"name": "tiny",           "repo": "Systran/faster-whisper-tiny",
     "size_mb": 75,   "label": "tiny",           "korean": "나쁨",
     "note": "아주 빠르지만 한국어를 자주 틀린다"},
    {"name": "base",           "repo": "Systran/faster-whisper-base",
     "size_mb": 145,  "label": "base",           "korean": "나쁨",
     "note": "가볍다. 짧은 말 정도"},
    {"name": "small",          "repo": "Systran/faster-whisper-small",
     "size_mb": 484,  "label": "small",          "korean": "보통",
     "note": "GPU 없이 쓸 만한 마지노선"},
    {"name": "medium",         "repo": "Systran/faster-whisper-medium",
     "size_mb": 1530, "label": "medium",         "korean": "좋음",
     "note": "무난하다"},
    {"name": "large-v3-turbo", "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
     "size_mb": 1620, "label": "large-v3-turbo", "korean": "매우 좋음",
     "note": "large-v3 에서 디코더를 줄인 것. 절반 크기에 훨씬 빠르다"},
    {"name": "large-v3",       "repo": "Systran/faster-whisper-large-v3",
     "size_mb": 3087, "label": "large-v3",       "korean": "가장 좋음",
     "note": "가장 정확하지만 무겁다. GPU 가 있어야 대화에 쓸 만하다"},
]
BY_NAME = {m["name"]: m for m in CATALOG}
DEFAULT = "large-v3-turbo"


def model_dir(name):
    return os.path.join(models_dir(), name)


def is_installed(name):
    d = model_dir(name)
    return os.path.isfile(os.path.join(d, "model.bin"))


def installed_size_mb(name):
    d = model_dir(name)
    if not os.path.isdir(d):
        return 0
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


def in_hf_cache(name):
    """허깅페이스 기본 캐시에 이미 있는지. 있으면 다시 받게 하지 않는다."""
    try:
        from huggingface_hub import try_to_load_from_cache
        got = try_to_load_from_cache(BY_NAME[name]["repo"], "model.bin")
        return isinstance(got, str) and os.path.isfile(got)
    except Exception:
        return False


def available(name):
    """쓸 수 있는 상태인지. models 폴더든 허깅페이스 캐시든 상관없다."""
    return is_installed(name) or in_hf_cache(name)


def resolve(name):
    """엔진에 넘길 값. 받아 둔 게 있으면 그 폴더, 없으면 이름 그대로."""
    return model_dir(name) if is_installed(name) else name


def delete(name):
    d = model_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


WANTED = (".bin", ".json", ".txt", ".md")


def remote_size_mb(name):
    """서버에 물어 실제 받을 크기를 받아온다. 안 되면 어림값."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(BY_NAME[name]["repo"], files_metadata=True)
        total = sum(f.size or 0 for f in info.siblings
                    if f.rfilename.lower().endswith(WANTED))
        if total:
            return total / 1e6
    except Exception:
        pass
    return BY_NAME[name]["size_mb"]


def _tqdm_class(report):
    """진행 표시기를 가로채 진행량을 알린다.

    허깅페이스가 바이트 표시기를 두 개(받기·재조립) 만들고 둘 다 전체까지
    차오르므로, 더하지 않고 가장 많이 간 것을 쓴다.
    """
    from tqdm.auto import tqdm as base

    live = []
    lock = threading.Lock()

    # disable=True 인 tqdm 은 unit/n/total 속성이 없다. 직접 센다.
    class Reporting(base):
        def __init__(self, *a, **kw):
            self._unit = kw.get("unit", "it")
            self._total = kw.get("total") or 0
            self._done = kw.get("initial", 0) or 0
            kw["disable"] = True                 # 콘솔에는 찍지 않는다
            super().__init__(*a, **kw)
            if self._unit == "B":
                with lock:
                    live.append(self)

        def update(self, n=1):
            self._done += (n or 0)
            self._report()
            return super().update(n)

        def close(self):
            self._report()
            return super().close()

        def _report(self):
            with lock:
                done = max([t._done for t in live] or [0])
                total = max([t._total for t in live] or [0])
            try:
                report(done, total)
            except Exception:
                pass

    return Reporting


def download(name, on_progress=None, on_log=None):
    r"""모델을 models\<이름>\ 에 받는다. on_progress(받은MB, 전체MB)."""
    if name not in BY_NAME:
        raise ValueError("모르는 모델: %s" % name)
    os.makedirs(models_dir(), exist_ok=True)
    dest = model_dir(name)
    log = on_log or (lambda m: None)

    from huggingface_hub import snapshot_download

    # 표시기에 total 이 안 들어오므로 전체 크기는 서버에 따로 묻는다.
    total_mb = remote_size_mb(name)

    def report(done_bytes, _unused_total):
        if on_progress:
            # 서버가 말한 크기를 넘길 일은 없어야 하지만, 넘으면 깎는다
            on_progress(min(done_bytes / 1e6, total_mb), total_mb)

    log("%s 내려받기 시작 (%.0f MB)" % (name, total_mb))
    snapshot_download(
        repo_id=BY_NAME[name]["repo"],
        local_dir=dest,
        # 큰 파일은 여러 형식이 올라와 있는 저장소도 있어 필요한 것만 받는다
        allow_patterns=["*" + e for e in WANTED],
        tqdm_class=_tqdm_class(report),
        max_workers=4,
    )
    if not is_installed(name):
        raise RuntimeError("받았지만 model.bin 이 없습니다: %s" % dest)
    log("%s 준비 완료 (%.0f MB, %s)" % (name, installed_size_mb(name), dest))
    return dest


def cuda_available():
    """드라이버만 있는 게 아니라 cuBLAS 까지 실제로 불러와지는지 본다."""
    try:
        import core
        return core.cuda_ready()[0]
    except Exception:
        return False


def free_vram_mb():
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def recommend():
    """이 PC 에 맞는 모델 이름과 그 이유."""
    if not cuda_available():
        return "small", "GPU 가속을 쓸 수 없어 작은 모델을 권합니다"

    free = free_vram_mb()
    if free >= 3000:
        return "large-v3", "VRAM 여유 %dMB. 가장 정확한 모델을 쓸 수 있습니다" % free
    if free >= 1500:
        return "large-v3-turbo", "VRAM 여유 %dMB. 정확도와 무게가 알맞습니다" % free
    return "small", "VRAM 여유 %dMB 로 빡빡합니다" % free


def catalog_rows():
    """UI 에 뿌릴 줄 목록."""
    rows = []
    for m in CATALOG:
        got = is_installed(m["name"])
        cached = False if got else in_hf_cache(m["name"])
        size = installed_size_mb(m["name"]) if got else m["size_mb"]
        mark = " · 받아둠" if got else (" · 캐시에 있음" if cached else "")
        rows.append({
            "name": m["name"],
            "text": "%s · %s · %.1fGB%s" % (
                m["label"], m["korean"], size / 1000, mark),
            "installed": got,
            "cached": cached,
            "ready": got or cached,
            "note": m["note"],
        })
    return rows
