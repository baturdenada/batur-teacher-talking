# Publishes this folder to GitHub as a public repo and triggers the .exe build.
# Run with publish.bat. First run opens your browser once to log in to GitHub.
$ErrorActionPreference = "Continue"   # Windows PowerShell 5.1: "Stop" breaks on gh stderr
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$name  = "batur-teacher-talking"
# local file  ->  path on GitHub   (workflow last = one build per publish)
$map = [ordered]@{
    "app.py" = "app.py"; "icon.png" = "icon.png"
    "requirements.txt" = "requirements.txt"; "requirements-local.txt" = "requirements-local.txt"
    "install.ps1" = "install.ps1"; "install.sh" = "install.sh"; "README.md" = "README.md"; ".gitignore" = ".gitignore"
    "publish.ps1" = "publish.ps1"; "publish.bat" = "publish.bat"
    "build-workflow.yml" = ".github/workflows/build.yml"
}
$files = @($map.Keys)
Set-Location $PSScriptRoot

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { Write-Host "GitHub CLI not found. Install it from https://cli.github.com" -ForegroundColor Red; exit 1 }

# 1) log in (browser) with permission to upload the build workflow
& $gh auth status -h github.com *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Opening GitHub login in your browser..." -ForegroundColor Cyan
    & $gh auth login -h github.com -p https -w -s workflow
}
$st = (& $gh auth status -h github.com 2>&1 | Out-String)
if ($st -notmatch "workflow") {
    Write-Host "Granting 'workflow' permission (browser)..." -ForegroundColor Cyan
    & $gh auth refresh -h github.com -s workflow
}
$owner = (& $gh api user --jq .login)
if ($LASTEXITCODE -ne 0 -or -not $owner) { Write-Host "GitHub login failed." -ForegroundColor Red; exit 1 }
$owner = $owner.Trim()
$full  = "$owner/$name"

# 2) safety: never publish an API key
foreach ($f in $files) {
    if ($f -match "\.(ico|png)$") { continue }
    $t = Get-Content -Raw -Path $f
    if ($t -match "AQ\.[A-Za-z0-9_\-]{30,}" -or $t -match "AIza[0-9A-Za-z_\-]{30,}") {
        Write-Host "STOP: $f looks like it contains an API key. Nothing was uploaded." -ForegroundColor Red; exit 1
    }
}

# 3) create the repo if needed
& $gh repo view $full *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating public repo $full ..." -ForegroundColor Cyan
    & $gh repo create $name --public --description "Live English captions for lessons (Gemini / Whisper)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not create the repo." -ForegroundColor Red; exit 1 }
}

function BlobSha([byte[]]$b) {
    $h = [Text.Encoding]::ASCII.GetBytes("blob $($b.Length)`0")
    $all = New-Object byte[] ($h.Length + $b.Length)
    [Array]::Copy($h, $all, $h.Length); [Array]::Copy($b, 0, $all, $h.Length, $b.Length)
    (([Security.Cryptography.SHA1]::Create().ComputeHash($all)) | ForEach-Object { $_.ToString("x2") }) -join ""
}

# 4) upload changed files (GitHub contents API — no git needed)
$changed = 0
$tmp = [IO.Path]::GetTempFileName()
foreach ($k in $files) {
    $f = $map[$k]
    $bytes = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $k))
    if ($f -in @("install.ps1", "install.sh", "README.md")) {
        $txt = [Text.Encoding]::UTF8.GetString($bytes).Replace("__REPO__", $full)
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($txt)
    }
    $remote = (& $gh api "repos/$full/contents/$f" --jq .sha 2>$null)
    if ($LASTEXITCODE -ne 0) { $remote = "" }
    if ($remote -and $remote.Trim() -eq (BlobSha $bytes)) { Write-Host "  unchanged  $f"; continue }
    $body = @{ message = "Update $f"; content = [Convert]::ToBase64String($bytes) }
    if ($remote) { $body.sha = $remote.Trim() }
    [IO.File]::WriteAllText($tmp, ($body | ConvertTo-Json -Compress))
    & $gh api -X PUT "repos/$full/contents/$f" --input $tmp | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "Upload failed: $f" -ForegroundColor Red; exit 1 }
    Write-Host "  uploaded   $f" -ForegroundColor Green
    $changed++
}
Remove-Item $tmp -ErrorAction SilentlyContinue

Write-Host ""
if ($changed -eq 0) {
    Write-Host "Nothing changed. To rebuild anyway:  gh workflow run build.yml -R $full"
} else {
    Write-Host "Uploaded $changed file(s). GitHub is now building the .exe (about 3-5 min):" -ForegroundColor Cyan
    Write-Host "  https://github.com/$full/actions"
}
Write-Host ""
Write-Host "Student install - Windows (PowerShell):" -ForegroundColor Yellow
Write-Host "  irm https://raw.githubusercontent.com/$full/main/install.ps1 | iex"
Write-Host "Student install - Mac (Terminal):" -ForegroundColor Yellow
Write-Host "  curl -fsSL https://raw.githubusercontent.com/$full/main/install.sh | bash"
Write-Host "Direct download:  https://github.com/$full/releases/latest"
Start-Process "https://github.com/$full/actions"
