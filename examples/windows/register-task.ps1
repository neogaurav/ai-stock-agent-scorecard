# One-time setup: run ingest.ps1 every 15 minutes, hidden.
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -File C:\Code\ai-stock-agent-scorecard\examples\windows\ingest.ps1"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "AgentScorecardIngest" -Action $action -Trigger $trigger -Description "Seal new agent reports"
