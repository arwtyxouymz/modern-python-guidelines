$ErrorActionPreference = "Stop"

$tool = Join-Path $PSScriptRoot "modern_python.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $tool @args
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $tool @args
    exit $LASTEXITCODE
}

Write-Error "modern-python-guidelines: Python 3.10 or newer is required"
exit 2
