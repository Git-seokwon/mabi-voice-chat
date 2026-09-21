#Requires -Version 5
# 프로그램을 지웁니다. 안내는 이쪽에서 합니다 (bat 본문에 한글을 넣으면 깨집니다).
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT = Split-Path -Parent $PSScriptRoot

# ─── 엉뚱한 폴더를 지우지 않도록 표식을 확인한다
if (-not (Test-Path (Join-Path $ROOT 'src\core.py'))) {
    Write-Host "여기는 마비노기 음성 채팅 폴더가 아닙니다:" -ForegroundColor Red
    Write-Host "  $ROOT"
    Read-Host "`n엔터를 누르면 닫힙니다"
    exit 1
}

function Size-Of($p) {
    if (-not (Test-Path $p)) { return 0 }
    $i = Get-Item $p
    if (-not $i.PSIsContainer) { return $i.Length }
    $s = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object Length -Sum).Sum
    if ($s) { return $s } else { return 0 }
}
function MB($n) { '{0,8:N0} MB' -f ($n / 1MB) }

function Remove-Safely($p, $label) {
    if (-not (Test-Path $p)) { return }
    try {
        Remove-Item $p -Recurse -Force -ErrorAction Stop
        Write-Host "  지움  $label"
    } catch {
        Write-Host "  실패  $label  ($($_.Exception.Message))" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  마비노기 음성 채팅 지우기" -ForegroundColor Cyan
Write-Host "  ========================================"
Write-Host "  $ROOT"
Write-Host ""

# ─── 돌고 있으면 먼저 내린다 (파일이 잠겨 있으면 못 지웁니다)
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*mabi_voice*' }
if ($live) {
    Write-Host "  실행 중인 것을 먼저 내립니다..." -ForegroundColor Yellow
    $live | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# ─── 내 것이 어디 있는지 (core.data_dir 과 같은 규칙)
$DATA = $env:MABI_DATA
if (-not $DATA) {
    if ((Test-Path (Join-Path $ROOT 'models')) -or (Test-Path (Join-Path $ROOT 'settings.json'))) {
        $DATA = $ROOT
    } else {
        $DATA = Join-Path $env:LOCALAPPDATA 'mabi-voice-chat'
    }
}
$MODELS = $env:MABI_MODELS
if (-not $MODELS) { $MODELS = Join-Path $DATA 'models' }

$RT   = Join-Path $ROOT 'runtime'
$NV   = Join-Path $RT 'python\Lib\site-packages\nvidia'
$SET  = Join-Path $DATA 'settings.json'
$LOG  = Join-Path $DATA 'sent.log'

$szRT = Size-Of $RT; $szNV = Size-Of $NV; $szMD = Size-Of $MODELS
$szPG = (Size-Of $ROOT) - $szRT - $szMD

Write-Host "  찾은 것"
Write-Host ("    내장 파이썬·꾸러미   " + (MB ($szRT - $szNV)))
Write-Host ("      그중 CUDA 라이브러리 " + (MB $szNV))
Write-Host ("    음성 인식 모델       " + (MB $szMD) + "   $MODELS")
Write-Host ("    프로그램 파일        " + (MB $szPG))
if (Test-Path $SET) { Write-Host "    내 설정              settings.json" }
if (Test-Path $LOG) { Write-Host "    보낸 말 기록          sent.log" }

# ─── 허깅페이스 캐시에 남은 모델 (다른 프로그램도 쓸 수 있는 공용 자리)
$HUB = Join-Path $env:USERPROFILE '.cache\huggingface\hub'
$cache = @()
if (Test-Path $HUB) {
    $cache = Get-ChildItem $HUB -Directory -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -like '*whisper*' }
}
$szCache = 0
foreach ($c in $cache) { $szCache += (Size-Of $c.FullName) }
if ($cache) {
    Write-Host ""
    Write-Host ("  따로 물어볼 것: 공용 캐시에 남은 모델 " + (MB $szCache)) -ForegroundColor Yellow
    Write-Host "    $HUB"
}

Write-Host ""
Write-Host "  무엇을 지울까요?"
Write-Host "    [1] 전부   - 프로그램 · 내장 파이썬 · CUDA · 모델 · 설정"
Write-Host "    [2] 받은 것만 - 내장 파이썬 · CUDA · 모델 (프로그램과 설정은 남김)"
Write-Host "    [3] 그만두기"
Write-Host ""
$pick = Read-Host "  번호"

if ($pick -ne '1' -and $pick -ne '2') {
    Write-Host "`n  그만두었습니다. 아무것도 지우지 않았습니다." -ForegroundColor Green
    Read-Host "`n  엔터를 누르면 닫힙니다"
    exit 0
}

$total = $szRT + $szMD
if ($pick -eq '1') { $total += $szPG }
Write-Host ""
Write-Host ("  " + (MB $total).Trim() + " 를 지웁니다. 되돌릴 수 없습니다.") -ForegroundColor Yellow
$yes = Read-Host "  정말 지우려면 y 를 누르세요"
if ($yes -ne 'y' -and $yes -ne 'Y') {
    Write-Host "`n  그만두었습니다." -ForegroundColor Green
    Read-Host "`n  엔터를 누르면 닫힙니다"
    exit 0
}

Write-Host ""
Remove-Safely $RT     "내장 파이썬과 CUDA 라이브러리"
Remove-Safely $MODELS "음성 인식 모델"

if ($pick -eq '1') {
    Remove-Safely $SET "설정"
    Remove-Safely $LOG "보낸 말 기록"
    if ($DATA -ne $ROOT) { Remove-Safely $DATA "내 것 폴더" }
}

# ─── 공용 캐시는 따로 묻는다
if ($cache) {
    Write-Host ""
    Write-Host ("  공용 캐시에 모델이 " + (MB $szCache).Trim() + " 남아 있습니다.")
    Write-Host "  이 자리는 다른 프로그램도 쓸 수 있습니다. 확실할 때만 지우세요." -ForegroundColor Yellow
    $c = Read-Host "  같이 지울까요? (y/n)"
    if ($c -eq 'y' -or $c -eq 'Y') {
        foreach ($d in $cache) { Remove-Safely $d.FullName $d.Name }
    } else {
        Write-Host "  남겨 두었습니다."
    }
}

Write-Host ""
if ($pick -eq '1') {
    Write-Host "  프로그램 폴더는 이 창이 닫힌 뒤에 지워집니다." -ForegroundColor Cyan
    Write-Host "  다 지워졌습니다. 그동안 고마웠습니다." -ForegroundColor Green
    Read-Host "`n  엔터를 누르면 닫히고 폴더가 사라집니다"
    $target = $ROOT.TrimEnd('\')
    Start-Process cmd.exe -WindowStyle Hidden -ArgumentList @(
        '/c', "timeout /t 3 /nobreak >nul & rd /s /q `"$target`"")
} else {
    Write-Host "  받은 것만 지웠습니다." -ForegroundColor Green
    Write-Host "  다시 쓰시려면 실행.bat 을 누르면 필요한 것을 새로 받습니다."
    Read-Host "`n  엔터를 누르면 닫힙니다"
}
