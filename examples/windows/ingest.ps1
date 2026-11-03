# Seal new PM-agent reports within minutes of being written, then push so GitHub
# records the time. Run from Task Scheduler every 15 minutes.
$ErrorActionPreference = "Stop"
$Ledger  = "C:\Code\ledger-data"                     # clone of your PRIVATE ledger-data repo
$Reports = "C:\Code\claude-trading-agents\results"   # where the PM agent writes reports
thesis-ledger --root $Ledger ingest $Reports --git
git -C $Ledger push --quiet
