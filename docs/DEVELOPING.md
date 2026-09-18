# 만드는 사람용

쓰는 법은 [../README.md](../README.md) 에 있다. 이 문서는 고치고 배포하는 쪽이다.

## 구성

```
실행.bat                    사용자가 누르는 단 하나의 파일
README.md  LICENSE  requirements.txt

src/
  core.py                  목소리 감지 · 인식 · 전송. UI 를 모른다
  models.py                모델 목록 · 내려받기 · 위치 관리
  mabi_voice.pyw           창과 트레이. core 를 콜백으로만 부린다
scripts/
  setup.bat                파이썬과 꾸러미를 runtime\ 에 들인다
  setup_gpu.bat            GPU 가속 라이브러리(550MB)
  build_dist.py            배포용 zip 만들기
  설치.bat / 콘솔로_실행.bat / 관리자로_실행.bat / GPU로_바꾸기.bat
tools/
  voice_chat.py            같은 엔진의 콘솔판
  stt_test.py              마이크와 인식만 시험 (--list, --scan)
docs/
  DEVELOPING.md            이 문서
  images/                  README 에 넣는 스크린샷
```

뿌리에는 **누르는 것(`실행.bat`)과 읽는 것(`README.md`, `LICENSE`)** 만 둔다.
나머지는 갈래별로 넣었다. `src\` 의 모듈은 자기 부모를 프로그램 뿌리로 본다
(`APP_DIR`). 모델과 설정의 자리를 정할 때 그 값을 쓴다.

`core.Engine` 은 화면에 직접 찍지 않는다. `on_event(kind, text, meta)` 와
`on_level(rms)` 로만 바깥에 알린다. `kind` 는 `info` `sent` `dropped`
`error` `listen` 다섯 가지다. 그래서 창판과 콘솔판이 같은 엔진을 쓴다.

## 개발 중 실행

```
python src\mabi_voice.pyw        # 꾸러미가 깔린 파이썬으로
scripts\콘솔로_실행.bat          # 배포용 런타임으로, 오류를 보면서
```

`python tools\stt_test.py --scan` 은 마이크가 여러 개일 때 어느 장치에 소리가
들어오는지 하나씩 들어 본다. 마이크가 의심될 때 먼저 돌린다.

## 배포본 만들기

```
python scriptsuild_dist.py
```

`dist\mabi-voice-chat-<판번호>.zip` 이 나온다. 40KB 안쪽이다. 무거운 것
(`runtime\`, `models\`)은 넣지 않고 받는 사람 첫 실행에 채운다.

- 들어가는 것은 `build_dist.py` 의 `FILES` 목록이 전부다.
- `settings.json`, `sent.log`, `runtime`, `models`, `__pycache__` 는 이름이
  스치기만 해도 멈춘다. 개인 설정이 딸려 나가는 것을 코드로 막는다.
- 압축 **안쪽** 폴더 이름에는 판번호가 없다(`mabi-voice-chat`). 그래야 옛
  폴더에 그대로 덮어쓸 수 있다. 판번호는 압축 파일 이름에만 붙는다.
- 판번호는 `src\core.py` 의 `VERSION` 한 곳에서 고친다.

## 왜 이렇게 만들었나

겉보기에 이상한 선택들은 대부분 실제로 부딪힌 문제 때문이다.

### 파이썬을 따로 들고 온다

공식 내장판(embeddable) 파이썬에는 **tkinter 가 없어서** 창을 띄울 수 없다.
그래서 tkinter 가 들어 있는 python-build-standalone 빌드(45MB)를 받아
`runtime\` 에 둔다. 압축 풀기는 윈도우 10 이상에 기본으로 있는 `tar` 로 한다.

설치 끝에 `tkinter`, `sounddevice`, `faster_whisper`, `pystray`, `PIL` 임포트를
확인하고 나서야 `.deps-ok` 표시를 남긴다. 반쯤 깔린 상태로 넘어가지 않게 한다.

### 배치 파일 본문은 ASCII 만 쓴다

cmd 는 `.bat` 을 OEM 코드페이지(한국어 윈도우면 CP949)로 읽는다. UTF-8 로 쓴
한글 주석이 뭉개지면서 그 바이트 안에 `&` 가 생겨, 주석 뒷부분이 명령으로
실행된 일이 있다. 파일 **이름**의 한글은 문제없다.

그래서 실제 내용이 있는 스크립트는 ASCII 이름으로 두고(`scripts\setup.bat`,
`scripts\setup_gpu.bat`), 한글 이름 파일은 그것을 부르는 한 줄 껍데기로 만들었다.

### GPU 는 cuBLAS 가 있어야 쓴다

CTranslate2 가 GPU 를 쓰려면 `cublas64_12.dll` 이 필요하다. CUDA 툴킷을 깐
PC 에는 이미 있지만 대부분은 없다. 없는 상태로 GPU 를 시도하면 윈도우가
"DLL 을 찾을 수 없습니다" 모달 창을 띄우고 **거기서 프로그램이 멈춘다.**

`core.cuda_ready()` 가 이 순서로 처리한다.

1. `SetErrorMode` 로 그 모달 창을 막는다
2. DLL 이 있을 만한 자리를 `os.add_dll_directory` 로 넣는다 — 우리 런타임의
   `nvidia\*\bin`, `CUDA_PATH`, 설치된 CUDA 툴킷
3. `cublas64_12.dll` 을 실제로 불러와 본다. 성공해야만 CUDA 를 시도한다

`models.recommend()` 도 이 판단을 쓴다. 드라이버만 보고 GPU 가 있다고 여겨
큰 모델을 권하면 CPU 로 떨어져 쓸 수 없게 된다.

연산 형식은 `int8_float16` 이다. `float16` 보다 메모리를 절반만 쓰고 정확도
차이는 작다. 게임이 VRAM 을 4GB 넘게 쓰는 경우가 흔해서 이쪽을 골랐다.

### 단축키는 RegisterHotKey 로 잡는다

`GetAsyncKeyState` 로 키 상태를 읽는 방식은 **게임이 관리자 권한으로 돌고
우리가 아니면 막힌다.** 게임이 앞에 있을 때만 단축키가 안 듣는 증상이 이것이다.

`RegisterHotKey` 는 운영체제가 조합을 먼저 가로채 우리 스레드 큐에
`WM_HOTKEY` 를 넣는다. 포커스와 권한의 벽을 넘고, Win 조합에서 시작 메뉴가
열리는 문제도 없다. 등록한 스레드에서 메시지를 받아야 하므로 등록과 수신이
같은 고리 안에 있고, `PeekMessage` 로 받아 중단 요청을 확인할 틈을 남긴다.

등록이 실패하면(다른 프로그램이 같은 조합을 이미 쓰는 경우)에만 키 상태 읽기로
내려간다. 그래도 안 되면 `scripts\관리자로_실행.bat` 이 마지막 수단이다.

### 게임 CLI 는 PATH 만 믿지 않는다

PATH 는 프로그램이 시작할 때 물려받는다. 게임이 토글을 켜며 PATH 에 등록해도,
이미 떠 있던 탐색기에서 실행한 프로그램은 옛 PATH 를 쥐고 있어 찾지 못한다.
원래는 재부팅해야 풀린다. 아예 등록이 안 되는 PC 도 있었다.

`core.find_cli()` 가 순서대로 시도한다.

1. 사람이 직접 지정한 경로 (`settings.cli_path`)
2. `MABI_CLI` 환경변수
3. 지금 프로세스의 PATH (`shutil.which`)
4. **레지스트리에 적힌** User / Machine PATH — 물려받은 PATH 가 낡아도 통한다
5. **레지스트리의 게임 설치 위치** — 제거 항목의 `InstallLocation`
6. 드라이브마다 흔한 설치 자리

여기서도 못 찾으면 창의 **디스크에서 찾기** 가 `core.deep_find_cli()` 를
부른다. 너비 우선으로 얕은 곳부터, 깊이 5칸까지, 윈도우 폴더와 캐시류는
건너뛰고, `nexon` `mabinogi` `game` 이 든 폴더를 먼저 본다. 시간 예산을
넘기면 멈춘다.

### 모델은 `local_dir` 로 받는다

허깅페이스 기본 캐시는 원본(`blobs`)과 사용본(`snapshots`)을 따로 둔다. 보통은
심볼릭 링크로 한 벌만 쓰지만 **윈도우는 개발자 모드가 아니면 링크를 못 만들어
그대로 복사한다.** large-v3 가 3.1GB 가 아니라 6.2GB 를 먹고 있었다.
`local_dir` 로 받으면 한 벌만 남는다.

진행률은 두 군데를 조심해야 한다.

- 허깅페이스가 진행 표시기에 `total` 을 바로 넣어 주지 않는다. 그래서 전체
  크기는 서버에 미리 묻고(`remote_size_mb`), 표시기에서는 받은 양만 가져온다.
- 바이트를 세는 표시기가 **두 개** 만들어진다. 받는 단계와 조각을 파일로 다시
  맞추는 단계다. 둘 다 전체 크기까지 차오르므로 더하면 200% 가 된다. 그래서
  더하지 않고 가장 많이 간 것 하나를 쓴다.
- `disable=True` 인 tqdm 은 `__init__` 을 일찍 끝내 `unit` `n` `total` 속성이
  아예 없다. tqdm 내부를 보지 말고 직접 세야 한다.

### 내 것은 프로그램 폴더 밖에 둔다

판을 올릴 때 모델을 다시 받지 않게 하려고 `core.data_dir()` 을 뒀다.
`MABI_DATA` → 프로그램 폴더에 이미 `models\` 나 `settings.json` 이 있으면 거기
(예전부터 쓰던 사람 보호) → `%LOCALAPPDATA%\mabi-voice-chat` 순이다.
모델만 다른 곳에 두려면 `MABI_MODELS` 를 쓴다. README 에는 이 표를 넣지 않았다.
받아서 쓰는 사람에게는 "덮어쓰면 된다" 한 줄로 충분하다.

### 두 개가 동시에 돌지 못하게 막는다

두 개가 같은 목소리를 들으면 채팅이 두 번씩 나간다. 이름 있는 뮤텍스로 두 번째
실행을 거절한다. 프로세스가 죽으면 윈도우가 잠금을 풀어 주므로 강제 종료에도
남지 않는다.

여기에 더해, 창 갱신 고리(`_drain`)를 `try/finally` 로 감싸 무슨 예외가 나도
다음 갱신을 다시 예약한다. **화면만 죽고 엔진이 살아 있는 반쪽 상태**가 가장
위험하다. 창은 조용한데 배경에서 계속 채팅을 보내게 된다.

## 손볼 때 조심할 것

- `core` 는 UI 를 몰라야 한다. 화면에 찍고 싶으면 `on_event` 를 쓴다.
- 콜백은 다른 스레드에서 온다. tkinter 위젯은 메인 스레드에서만 만진다
  (`root.after(0, ...)`).
- `ttk.Scale` 은 `set()` 하는 순간 `command` 를 부른다. 창을 만드는 도중에
  불려서 아직 없는 위젯을 건드릴 수 있다.
- 새 `.bat` 을 만들면 본문이 ASCII 인지 확인한다.
- 게임에 보내는 한글은 base64 로 감싸야 한다. 콘솔 코드페이지가 UTF-8 이 아닌
  경우 명령줄에서 뭉개진다. `core.cli()` 가 알아서 한다.
