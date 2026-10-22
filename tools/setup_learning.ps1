$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$runtimePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) { throw 'Run setup.bat first.' }
Write-Host 'Optional local learning: downloads about 20 GB; leave at least 55 GB free for conversion.'
& $runtimePython -m venv .venv-training
if ($LASTEXITCODE -ne 0) { throw 'Training environment creation failed.' }
$trainingPython = Join-Path $projectRoot '.venv-training\Scripts\python.exe'
& $trainingPython -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
& $trainingPython -m pip install -r requirements-training.txt
if ($LASTEXITCODE -ne 0) { throw 'Training dependencies failed.' }
& $trainingPython -c 'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
if ($LASTEXITCODE -ne 0) { throw 'CUDA check failed; runtime settings were not changed.' }
& $runtimePython tools\download_learning_base.py
if ($LASTEXITCODE -ne 0) { throw 'Training base download failed.' }
& $runtimePython tools\download_llama_converter.py
if ($LASTEXITCODE -ne 0) { throw 'Converter download failed.' }
& $trainingPython -m unittest discover -s tests_training
if ($LASTEXITCODE -ne 0) { throw 'Training preparation check failed.' }
& $runtimePython tools\enable_learning.py
if ($LASTEXITCODE -ne 0) { throw 'Could not enable learning; existing settings preserved.' }
Write-Host 'Local learning enabled. Restart the bot. It only trains after an empty-channel idle period.'
