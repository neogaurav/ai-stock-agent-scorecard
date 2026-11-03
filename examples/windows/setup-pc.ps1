# One-time PC setup for ai-stock-agent-scorecard (Windows, PowerShell).
#   - installs the package from GitHub into your current Python
#   - clones the private data repo and the public site repo into $CodeDir
#   - initialises the ledger and writes the two job scripts
#   - registers two scheduled tasks: ingest every 15 min, nightly at 8:30 PM
# Re-running is safe.
param(
  [string]$CodeDir   = "C:\Code",
  [string]$Reports   = "C:\Code\claude-trading-agents\results",
  [string]$GitHubUser = "neogaurav",
  [string]$NightlyAt = "8:30PM"
)
$ErrorActionPreference = "Stop"
$Data = Join-Path $CodeDir "scorecard-data"
$Site = Join-Path $CodeDir "ai-stock-agent-scorecard-site"

Write-Host "1/5 Installing ai-stock-agent-scorecard ..."
$py = (Get-Command python).Source
& $py -m pip install --upgrade "ai-stock-agent-scorecard[yfinance] @ git+https://github.com/$GitHubUser/ai-stock-agent-scorecard"

Write-Host "2/5 Cloning repos ..."
if (-not (Test-Path $Data)) { git clone "https://github.com/$GitHubUser/scorecard-data.git" $Data }
if (-not (Test-Path $Site)) { git clone "https://github.com/$GitHubUser/ai-stock-agent-scorecard-site.git" $Site }

Write-Host "3/5 Initialising ledger ..."
& $py -m ai_stock_agent_scorecard.cli --root $Data init --title "AI Stock Agent Scorecard" --reveal-delay-days 5
# private/ holds the salts. Keep it IN this private repo as a backup; losing salts means calls can never be revealed.
Set-Content -Path (Join-Path $Data ".gitignore") -Value "site_build/`n__pycache__/"
New-Item -ItemType Directory -Force -Path (Join-Path $Data "scripts") | Out-Null

Write-Host "4/5 Writing job scripts ..."
$ingest = @"
`$ErrorActionPreference = "Stop"
& "$py" -m ai_stock_agent_scorecard.cli --root "$Data" ingest "$Reports"
git -C "$Data" add -A
git -C "$Data" diff --cached --quiet; if (`$LASTEXITCODE -ne 0) { git -C "$Data" commit -q -m "seal `$(Get-Date -Format s)"; git -C "$Data" push -q }
"@
$nightly = @"
`$ErrorActionPreference = "Stop"
Start-Transcript -Path "$Data\scripts\nightly.log" -Append | Out-Null
& "$py" -m ai_stock_agent_scorecard.cli --root "$Data" ingest "$Reports"
& "$py" -m ai_stock_agent_scorecard.cli --root "$Data" nightly --out "$Data\site_build"
& "$py" -m ai_stock_agent_scorecard.cli --root "$Data" verify
git -C "$Data" add -A
git -C "$Data" diff --cached --quiet; if (`$LASTEXITCODE -ne 0) { git -C "$Data" commit -q -m "nightly `$(Get-Date -Format yyyy-MM-dd)"; git -C "$Data" push -q }
robocopy "$Data\site_build" "$Site" /MIR /XD .git /NFL /NDL /NJH /NJS | Out-Null
git -C "$Site" add -A
git -C "$Site" diff --cached --quiet; if (`$LASTEXITCODE -ne 0) { git -C "$Site" commit -q -m "dashboard `$(Get-Date -Format yyyy-MM-dd)"; git -C "$Site" push -q -u origin HEAD:main }
Stop-Transcript | Out-Null
"@
Set-Content -Path (Join-Path $Data "scripts\ingest.ps1") -Value $ingest
Set-Content -Path (Join-Path $Data "scripts\nightly.ps1") -Value $nightly

Write-Host "5/5 Registering scheduled tasks ..."
$ps = "powershell.exe"
$a1 = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Data\scripts\ingest.ps1`""
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "AgentScorecard-Ingest" -Action $a1 -Trigger $t1 -Description "Seal new agent calls every 15 min" -Force | Out-Null
$a2 = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Data\scripts\nightly.ps1`""
$t2 = New-ScheduledTaskTrigger -Daily -At $NightlyAt
$s2 = New-ScheduledTaskSettingsSet -StartWhenAvailable   # runs on wake if the PC was asleep at 8:30
Register-ScheduledTask -TaskName "AgentScorecard-Nightly" -Action $a2 -Trigger $t2 -Settings $s2 -Description "Grade, reveal, publish dashboard" -Force | Out-Null

Write-Host "Running the nightly job once now to publish the first (empty) dashboard ..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Data\scripts\nightly.ps1"
Write-Host "Done. Dashboard repo: https://github.com/$GitHubUser/ai-stock-agent-scorecard-site"
