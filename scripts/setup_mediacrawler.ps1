param(
    [string]$Target = "external\MediaCrawler",
    [switch]$AcceptNonCommercialLicense
)

$ErrorActionPreference = "Stop"
$PinnedCommit = "0625e01a6bc717a3fc9c96d3dac7fb8957043838"
$Repository = "https://github.com/NanmiCoder/MediaCrawler.git"

if (-not $AcceptNonCommercialLicense) {
    throw "请先阅读 MediaCrawler LICENSE；仅在接受非商业学习研究许可后使用 -AcceptNonCommercialLicense。"
}

$Workspace = (Resolve-Path -LiteralPath ".").Path
$TargetPath = [System.IO.Path]::GetFullPath((Join-Path $Workspace $Target))
if (-not $TargetPath.StartsWith($Workspace, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "目标目录必须位于当前项目内。"
}
if (Test-Path -LiteralPath $TargetPath) {
    throw "目标目录已存在，未执行覆盖：$TargetPath"
}

git clone --filter=blob:none $Repository $TargetPath
git -C $TargetPath checkout --detach $PinnedCommit

Write-Host "MediaCrawler 已固定到提交 $PinnedCommit"
Write-Host "下一步：进入 $TargetPath 后运行 uv sync，并按官方文档配置 Chrome CDP。"
Write-Host "确认合规后，在 .env 中设置 COLLECTOR_LICENSE_ACCEPTED=true。"
