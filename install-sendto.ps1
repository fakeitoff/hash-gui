# 在「发送到」菜单里加一个「7z 哈希对比」项。
# 只写当前用户的 SendTo 文件夹（一个 .lnk 文件），不碰注册表，不需要管理员。
# 卸载：运行 uninstall-sendto.bat，或直接把那个 .lnk 删掉。
param(
    [string]$Name = "7z 哈希对比"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainPy = Join-Path $project "main.py"

if (-not (Test-Path -LiteralPath $mainPy)) {
    Write-Host "找不到 $mainPy" -ForegroundColor Red
    exit 1
}

# 优先 pythonw.exe（无控制台窗口），退而用 python.exe
$pythonw = $null
foreach ($hint in @("pythonw", "python")) {
    $cmd = Get-Command $hint -ErrorAction SilentlyContinue
    if ($cmd) { $pythonw = $cmd.Source; break }
}
if (-not $pythonw) {
    foreach ($cand in @("$env:LOCALAPPDATA\Programs\Python", "D:\ProgramFiles\python", "C:\Python311")) {
        $exe = Join-Path $cand "pythonw.exe"
        if (Test-Path -LiteralPath $exe) { $pythonw = $exe; break }
    }
}
if (-not $pythonw) {
    Write-Host "未找到 Python（需要自带 tkinter 的官方安装包）。" -ForegroundColor Red
    exit 1
}

$sendTo = [Environment]::GetFolderPath("SendTo")
if (-not (Test-Path -LiteralPath $sendTo)) {
    New-Item -ItemType Directory -Path $sendTo -Force | Out-Null
}
$lnkPath = Join-Path $sendTo "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
# 「发送到」会把选中的每个文件/文件夹作为独立参数追加在后面
$lnk.Arguments = "`"$mainPy`" --run"
$lnk.WorkingDirectory = $project
$lnk.Description = "用系统 7-Zip 逐文件计算哈希并对比是否相同"
# 有 7-Zip 就用它的图标，找不到就用系统默认
$icon = $null
foreach ($dir in @("D:\ProgramFiles\7-Zip", "C:\Program Files\7-Zip", "C:\Program Files (x86)\7-Zip")) {
    if (Test-Path -LiteralPath (Join-Path $dir "7zFM.exe")) { $icon = (Join-Path $dir "7zFM.exe") + ",0"; break }
}
if ($icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host "已添加到「发送到」菜单：" -ForegroundColor Green
Write-Host "  $lnkPath"
Write-Host "  目标：$pythonw `"$mainPy`" --run"
Write-Host ""
Write-Host "用法：在资源管理器里选中任意多个文件/文件夹 → 右键 → 发送到 → $Name"
Write-Host "      工具会一次收到全部路径（文件夹按「含子文件夹」设置展开）并自动开始计算。"
