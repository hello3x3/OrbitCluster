# Native Windows OrbitCluster CLI bootstrap. No Python/Git/WSL prerequisite.
param(
 [Parameter(Mandatory=$true)][string]$Server,
 [string]$InstallDir, [string]$BinDir, [string]$ConfigDir,
 [string]$CaBundle, [switch]$RequireHttps
)
$ErrorActionPreference='Stop'
$Version='0.2.0'
$uri=$null
if (-not [Uri]::TryCreate($Server,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -notin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $Server -match '[\s\\]') { throw '请提供完整 HTTP / HTTPS 服务地址，不能包含账号、查询参数或片段。' }
if ($RequireHttps -and $uri.Scheme -ne 'https') { throw '当前安装要求 HTTPS。' }
$Server=$Server.TrimEnd('/')
$arch=[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($arch -eq 'x64') {$arch='amd64'}
if ($arch -notin @('amd64','arm64')) {throw '暂未提供当前 Windows 处理器的安装包。'}
$work=Join-Path ([IO.Path]::GetTempPath()) ('orbit-install-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
 Write-Host '[1/5] 检测系统：Windows' $arch
 $artifact="orbit-$Version-windows-$arch.zip"
 $fetch=@('--fail','--show-error','--progress-bar','--connect-timeout','15','--max-time','300','--max-redirs','0')
 if ($CaBundle -and $uri.Scheme -eq 'https') {$fetch+=@('--cacert',$CaBundle)}
 Write-Host '[2/5] 从门户下载' $artifact
 & curl.exe @fetch "$Server/terminal/downloads/$artifact" -o "$work\client.zip"
 if ($LASTEXITCODE -ne 0) {throw '客户端未发布或下载失败，原安装未修改。'}
 & curl.exe @fetch "$Server/terminal/downloads/$artifact.sha256" -o "$work\digest"
 if ($LASTEXITCODE -ne 0) {throw '校验文件下载失败。'}
 Write-Host '[3/5] 校验下载文件'
 $expected=((Get-Content "$work\digest" -Raw).Trim() -split '\s+')[0]
 if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash "$work\client.zip" -Algorithm SHA256).Hash -ne $expected) {throw 'SHA256 校验失败，原安装保持不变。'}
 Add-Type -AssemblyName System.IO.Compression.FileSystem
 $zip=[IO.Compression.ZipFile]::OpenRead("$work\client.zip")
 try {if ($zip.Entries.Count -ne 1 -or $zip.Entries[0].FullName -ne 'orbit.exe') {throw '安装包内容不正确。'}} finally {$zip.Dispose()}
 Write-Host '[4/5] 解压并检查客户端'
 Expand-Archive -LiteralPath "$work\client.zip" -DestinationPath "$work\client"
 $exe="$work\client\orbit.exe"
 if ((& $exe --version) -ne "orbit $Version" -or $LASTEXITCODE -ne 0) {throw '客户端版本检查失败。'}
 $arguments=@('setup','--server',$Server)
 if ($InstallDir) {$arguments+=@('--install-dir',$InstallDir)}
 if ($BinDir) {$arguments+=@('--bin-dir',$BinDir)}
 if ($ConfigDir) {$arguments+=@('--config-dir',$ConfigDir)}
 if ($CaBundle) {$arguments+=@('--ca-bundle',$CaBundle)}
 if ($RequireHttps) {$arguments+='--require-https'}
 Write-Host '[5/5] 安装到当前用户目录并保存服务地址'
 & $exe @arguments
 if ($LASTEXITCODE -ne 0) {throw '安装失败。'}
 Write-Host '安装完成。默认客户端位于 %LOCALAPPDATA%\OrbitCluster\bin\orbit.exe。'
 Write-Host '将该 bin 目录加入用户 PATH，或使用完整路径运行 orbit.exe。安装器不修改系统 PATH。'
 Write-Host '运行 orbit login --username 用户名；卸载 orbit uninstall，清除配置用 --purge。'
} finally {Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue}
