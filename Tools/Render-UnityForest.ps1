[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [Alias('Unity')]
    [string]$UnityExe,

    [Alias('FFmpeg')]
    [string]$FFmpegExe = 'ffmpeg.exe',

    [string]$OutputDirectory,

    [ValidateRange(64, 3840)]
    [int]$Width = 1280,

    [ValidateRange(64, 2160)]
    [int]$Height = 720,

    [ValidateRange(2, 60)]
    [int]$Fps = 24,

    [switch]$Smoke,
    [switch]$FramesOnly
)

$ErrorActionPreference = 'Stop'
if ($Width % 2 -ne 0 -or $Height % 2 -ne 0) {
    throw 'Use even dimensions for H.264 4:2:0 encoding.'
}

$project = (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..')).FullName
if (-not (Test-Path -LiteralPath (Join-Path $project 'ProjectSettings\ProjectVersion.txt') -PathType Leaf)) {
    throw "The Unity project was not found at $project."
}
$editor = (Get-Command -Name $UnityExe -CommandType Application).Source

if ($OutputDirectory) {
    $output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory).TrimEnd('\', '/')
} else {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $suffix = [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $output = Join-Path $project "Render\unity-$stamp-$suffix"
}
if (Test-Path -LiteralPath $output) {
    throw "The output directory already exists. Choose a new directory instead of $output."
}
[void][IO.Directory]::CreateDirectory($output)

$log = Join-Path $output 'unity-editor.log'
$renderReport = Join-Path $output 'render-report.json'
$frames = Join-Path $output 'frames'
$unityArguments = @(
    '-batchmode', '-quit', '-force-d3d11',
    '-projectPath', ('"{0}"' -f $project),
    '-executeMethod', 'Dinosaug.ForestRender.RenderBatch',
    '-logFile', ('"{0}"' -f $log),
    '-forest-output', ('"{0}"' -f $output),
    '-forest-width', $Width.ToString([Globalization.CultureInfo]::InvariantCulture),
    '-forest-height', $Height.ToString([Globalization.CultureInfo]::InvariantCulture),
    '-forest-fps', $Fps.ToString([Globalization.CultureInfo]::InvariantCulture)
)
if ($Smoke) { $unityArguments += '-forest-smoke' }

Write-Host "Starting Unity capture. Editor log is $log"
$unity = Start-Process -FilePath $editor -ArgumentList $unityArguments -WorkingDirectory $project -WindowStyle Hidden -Wait -PassThru
if ($unity.ExitCode -ne 0) {
    throw "Unity exited with code $($unity.ExitCode). See $log."
}
if (-not (Test-Path -LiteralPath $renderReport -PathType Leaf)) {
    throw "Unity exited without a completed capture report. See $log."
}

$report = Get-Content -LiteralPath $renderReport -Raw | ConvertFrom-Json
$expectedFrames = if ($Smoke) { 15 } else { 5 * [int][Math]::Round(2.5 * $Fps) }
if ($report.frameCount -ne $expectedFrames -or $report.frameRate -ne $Fps -or $report.width -ne $Width -or
    $report.height -ne $Height -or $report.smoke -ne $Smoke.IsPresent -or @($report.shots).Count -ne 5) {
    throw "The Unity report did not match the requested capture. See $renderReport."
}
for ($index = 0; $index -lt $expectedFrames; $index++) {
    $frame = Join-Path $frames ('frame_{0:D5}.png' -f $index)
    if (-not (Test-Path -LiteralPath $frame -PathType Leaf) -or (Get-Item -LiteralPath $frame).Length -eq 0) {
        throw "Unity reported a missing or empty frame at $frame."
    }
}

$result = [ordered]@{
    UnityVersion = $report.unityVersion
    FrameCount = $expectedFrames
    Frames = $frames
    RenderReport = $renderReport
    EditorLog = $log
}
if ($Smoke -or $FramesOnly) {
    [pscustomobject]$result
    return
}

$ffmpeg = Get-Command -Name $FFmpegExe -CommandType Application
$ffprobe = Join-Path (Split-Path -Parent $ffmpeg.Source) 'ffprobe.exe'
if (-not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) {
    $probeCommand = Get-Command -Name 'ffprobe.exe' -CommandType Application -ErrorAction SilentlyContinue
    $ffprobe = $probeCommand.Source
}
$video = Join-Path $output 'stegosaur-forest-unity.mp4'
$framePattern = Join-Path $frames 'frame_%05d.png'
$encodeArguments = @(
    '-hide_banner', '-loglevel', 'error', '-xerror', '-n',
    '-framerate', $Fps, '-start_number', 0, '-i', $framePattern,
    '-frames:v', $expectedFrames, '-c:v', 'libx264', '-preset', 'medium', '-crf', 18,
    '-vf', 'scale=out_color_matrix=bt709,format=yuv420p',
    '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1',
    '-movflags', '+faststart', '-an', $video
)
& $ffmpeg.Source @encodeArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $video -PathType Leaf)) {
    throw "FFmpeg could not encode the capture. The source frames remain in $frames."
}

& $ffmpeg.Source -hide_banner -loglevel error -xerror -i $video -map '0:v:0' -f null -
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg could not decode the encoded video at $video."
}

$verification = [ordered]@{
    decode = 'PASS'
    streamCheck = 'NOT_RUN'
    expectedFrameCount = $expectedFrames
    sha256 = (Get-FileHash -LiteralPath $video -Algorithm SHA256).Hash.ToLowerInvariant()
}
if ($ffprobe) {
    $probeArguments = @(
        '-v', 'error', '-count_frames', '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_frames,duration',
        '-of', 'json', $video
    )
    $streamJson = & $ffprobe @probeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "FFprobe could not read the encoded stream at $video."
    }
    $stream = (($streamJson -join [Environment]::NewLine) | ConvertFrom-Json).streams | Select-Object -First 1
    if ($stream.codec_name -ne 'h264' -or $stream.pix_fmt -ne 'yuv420p' -or $stream.width -ne $Width -or
        $stream.height -ne $Height -or $stream.avg_frame_rate -ne "$Fps/1" -or [int]$stream.nb_read_frames -ne $expectedFrames) {
        throw "The encoded stream at $video did not match the requested video."
    }
    $verification.streamCheck = 'PASS'
    $verification.stream = $stream
}
$videoCheck = Join-Path $output 'video-verification.json'
[IO.File]::WriteAllText($videoCheck, ($verification | ConvertTo-Json -Depth 5) + [Environment]::NewLine)
$result.Video = $video
$result.VideoVerification = $videoCheck
[pscustomobject]$result
