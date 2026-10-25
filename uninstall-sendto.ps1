# 从「发送到」菜单里移除「7z 哈希对比」。只删那个 .lnk，不碰别的东西。
param(
    [string]$Name = "7z 哈希对比"
)

$sendTo = [Environment]::GetFolderPath("SendTo")
$lnkPath = Join-Path $sendTo "$Name.lnk"

if (Test-Path -LiteralPath $lnkPath) {
    Remove-Item -LiteralPath $lnkPath
    Write-Host "已删除：$lnkPath" -ForegroundColor Green
} else {
    Write-Host "没有找到 $lnkPath —— 本来就没装过。" -ForegroundColor Yellow
}
