$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[Lumina] 安装运行依赖 ..."
python -m pip install -r requirements.txt
Write-Host "[Lumina] 安装打包工具 ..."
python -m pip install pyinstaller

Write-Host "[Lumina] 开始打包 ..."
python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "Lumina" `
    --icon "assets\lumina.ico" `
    --add-data "assets;assets" `
    main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败"
}

Write-Host ""
Write-Host "[Lumina] 打包完成：dist\Lumina\Lumina.exe" -ForegroundColor Green
Write-Host "首次运行会在 exe 同级创建 data\ 目录并写入示例数据。"
