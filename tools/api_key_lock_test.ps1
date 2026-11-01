<#
  Offline tests for step 1 of "API keys locked to the computer" in Workbench
  (modules\api_key_store.py; Plugin Core, docs/design/api-key-protection.md).

      powershell -NoProfile -File tools\api_key_lock_test.ps1 -CoreDll <path> [-SidekickRepo <path>]
                 [-RealDataRoot <path>] [-Python <python.exe>] [-SkipPytest] [-Keep]

  Across the languages: Workbench (Python) locks keys into a scratch locked
  file and Plugin Core reads them, then the other way round - with Sidekick
  (AutoHotkey) as well when -SidekickRepo names a Sidekick checkout with step 1.
  The same file byte for byte, the same sentences word for word, two writers at
  once. Then the whole pytest suite (tests\), which redirects itself.

  -CoreDll is a build of Plugin Core (dotnet build build\Supervertaler.Core.Build.csproj).
  Without it the cross-language checks are skipped, and said to be.

  Everything runs against a scratch folder: APPDATA (for the Python and
  AutoHotkey children), the data root and config.json, checked before anything
  runs. The user's own files - the locked key file and config.json in the real
  %APPDATA%\Supervertaler, the plain key file and Workbench's settings.json in
  -RealDataRoot (default: %USERPROFILE%\Supervertaler), the Sidekick bridge
  file - are compared by name, size and timestamps before and after every
  phase, never opened. Any change fails the run (if a Supervertaler product
  saved its settings meanwhile, run again).

  Made-up keys only; nothing is sent to a provider; never prints a key.
  Exit code 0 when everything passed.
#>
param(
    [string]$CoreDll = '',
    [string]$SidekickRepo = '',
    [string]$RealDataRoot = (Join-Path $env:USERPROFILE 'Supervertaler'),
    [string]$Python = 'python',
    [string]$Ahk64 = 'C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe',
    [switch]$SkipPytest,
    [switch]$Keep
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:passed = 0
$script:failed = 0

function Say([string]$line) {
    Write-Host $line
    if ($line.StartsWith('PASS')) { $script:passed++ }
    elseif ($line.StartsWith('FAIL') -or $line.StartsWith('ABORT')) { $script:failed++ }
}
function Check([bool]$ok, [string]$name) { Say ($(if ($ok) { 'PASS  ' } else { 'FAIL  ' }) + $name) }

# --- The user's own files: name, size and times only, never opened ----------
$realAppData = [Environment]::GetFolderPath('ApplicationData')     # not the variable this script changes
$watch = @(
    @((Join-Path $realAppData 'Supervertaler'), 'api-keys*'),
    @((Join-Path $realAppData 'Supervertaler'), 'config.json'),
    @((Join-Path $RealDataRoot 'settings'), 'api-keys*'),
    @((Join-Path $RealDataRoot 'workbench\settings'), 'settings.json'),
    @((Join-Path $RealDataRoot 'workbench\runtime'), 'sidekick-bridge.json')
)
function Get-RealFiles {
    $lines = foreach ($w in $watch) {
        if (Test-Path -LiteralPath $w[0]) {
            Get-ChildItem -LiteralPath $w[0] -Filter $w[1] -Force | Sort-Object Name | ForEach-Object {
                '{0}|{1}|{2}|{3}' -f $_.FullName, $_.Length, $_.CreationTimeUtc.Ticks, $_.LastWriteTimeUtc.Ticks }
        } else { "$($w[0])|no folder" }
    }
    ($lines -join "`n")
}
$realBefore = Get-RealFiles
Write-Host "INFO  watching the user's own files in: $realAppData\Supervertaler; $RealDataRoot"
function Assert-RealUntouched([string]$after) {
    $now = Get-RealFiles
    if ($now -ne $realBefore) {
        Say "ABORT  the user's own files changed during $after (names, sizes and times only):"
        Write-Host "      before: $realBefore"
        Write-Host "      after:  $now"
        exit 1
    }
}

# --- The scratch folder: APPDATA, data root, config.json ---------------------
$root = Join-Path ([IO.Path]::GetTempPath()) ('wb-keylock-' + [guid]::NewGuid().ToString('N').Substring(0, 12))
$appData = Join-Path $root 'appdata'
$dataRoot = Join-Path $root 'data'
$run = Join-Path $root 'run'
foreach ($d in @((Join-Path $appData 'Supervertaler'), (Join-Path $dataRoot 'settings'), $run, (Join-Path $root 'home'))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
[IO.File]::WriteAllText((Join-Path $appData 'Supervertaler\config.json'), (@{ user_data_path = $dataRoot } | ConvertTo-Json), $utf8)
$plainPath = Join-Path $dataRoot 'settings\api-keys.json'
$lockedPath = Join-Path $appData 'Supervertaler\api-keys.locked.json'
if ($plainPath.StartsWith($realAppData) -or $plainPath.StartsWith($RealDataRoot) -or
    $lockedPath -eq (Join-Path $realAppData 'Supervertaler\api-keys.locked.json')) {
    Say 'ABORT  the scratch paths are not scratch'; exit 2
}
Write-Host "INFO  scratch folder: $root"

# The same made-up keys as tools\api_key_lock_xlang.py.
$K1 = 'sk-ant-test-K1-PROJ-001'
$UNI = 'sk-test-Acm' + [char]0xE9 + '-' + [char]0xDC + 'ber-PROJ-001'
$GT = 'gt-test-Acme-PROJ-001'
$GEM = 'AIza-test-Acme-PROJ-001'
$MI = 'mistral-test-Acme-PROJ-001'
$SK = 'deepseek-test-Sidekick-PROJ-001'
$DS2 = 'deepseek-test-Core-PROJ-001'
$KEY_WB = 'sk-test-Workbench-PROJ-001'
$KEY_ACME = 'sk-test-AcmeServer-PROJ-001'
$OR = 'sk-or-test-PROJ-001'
$allKeys = @($K1, $UNI, $GT, $GEM, $MI, $SK, $DS2, $KEY_WB, $KEY_ACME, $OR)
$profileName = "Acme & <PROJ-001>'s server"
$place = 'Settings ' + [char]0x2192 + ' AI Settings'
$script:output = New-Object System.Text.StringBuilder

# One phase of the Python side. It sets APPDATA itself, before importing
# Workbench's modules, so this script's own environment is left alone.
function Invoke-Python([string]$phase, [string]$arg = '') {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Python (Join-Path $repo 'tools\api_key_lock_xlang.py') $appData $phase $arg 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    $sawFail = $false
    foreach ($item in $out) {
        $line = "$item"
        [void]$script:output.AppendLine($line)
        if ($line -match '^(PASS|FAIL|ABORT|INFO)') { Say $line; if ($line -match '^(FAIL|ABORT)') { $sawFail = $true } }
        elseif ($line.Trim()) { Write-Host "      $line" }
    }
    if ($code -ne 0 -and -not $sawFail) { Say "FAIL  [python] phase $phase ended with exit code $code" }
    if ($code -eq 2) { exit 2 }
    Assert-RealUntouched "python phase $phase"
}

# One phase of the Sidekick side: a small script including Sidekick's lib
# files, run with APPDATA pointed at the scratch folder for that child only.
# Warnings off: Sidekick's own harness checks those; an error still fails here.
$sidekickScript = $null
if ($SidekickRepo -and (Test-Path -LiteralPath $Ahk64) -and (Test-Path -LiteralPath (Join-Path $SidekickRepo 'lib\sharedkeys.ahk'))) {
    $sidekickScript = Join-Path $run 'sidekick.ahk'
    $libs = 'jxon', 'sharedkeys', 'data', 'menu_builder', 'ai', 'quicktrans', 'providers'
    $lines = @('#Requires AutoHotkey v2.0', '#NoTrayIcon', '#SingleInstance Off', '#Warn All, Off')
    $lines += $libs | ForEach-Object { '#Include "' + (Join-Path $SidekickRepo "lib\$_.ahk") + '"' }
    $lines += @(
        'T_Out := A_Args[2], T_Root := A_Args[3]',
        'T_Say(line) => FileAppend(line "`n", T_Out, "UTF-8")',
        'if !(InStr(SK_File(), T_Root "\") = 1 && InStr(SK_LockedFile(), T_Root "\") = 1) {',
        '    T_Say("ABORT  [sidekick] key files not in the scratch folder"), ExitApp(2)',
        '}',
        'if (A_Args[1] = "read") {',
        '    uni := "sk-test-Acm" Chr(0xE9) "-" Chr(0xDC) "ber-PROJ-001"',
        ('    ok := SK_Key("anthropic") == "' + $K1 + '" && SK_Key("openai") == uni && SK_Key("google") == "' + $GT + '"'),
        '    T_Say((ok ? "PASS" : "FAIL") "  [sidekick] Sidekick reads the keys Workbench locked (one not plain ASCII, one saved under Workbench''s MT name)")',
        '} else if (A_Args[1] = "lock") {',
        ('    T_Say((SK_KeySet("deepseek", "' + $SK + '") ? "PASS" : "FAIL") "  [sidekick] Sidekick locks a key")'),
        '}',
        'ExitApp(0)')
    [IO.File]::WriteAllText($sidekickScript, ($lines -join "`r`n"), $utf8)
} else {
    Write-Host 'SKIP  no -SidekickRepo (or no AutoHotkey v2): Sidekick is not part of the cross-language checks'
}
function Invoke-Sidekick([string]$phase) {
    $results = Join-Path $run "sidekick-$phase.txt"
    $saved = $env:APPDATA
    $env:APPDATA = $appData             # for the child only; restored at once
    try {
        $p = Start-Process -FilePath $Ahk64 -PassThru -NoNewWindow -Wait -WorkingDirectory $run `
            -ArgumentList '/ErrorStdOut', "`"$sidekickScript`"", $phase, "`"$results`"", "`"$root`"" `
            -RedirectStandardOutput (Join-Path $run "sidekick-$phase-stdout.txt") `
            -RedirectStandardError (Join-Path $run "sidekick-$phase-stderr.txt")
    } finally {
        $env:APPDATA = $saved
    }
    $lines = $(if (Test-Path -LiteralPath $results) { [IO.File]::ReadAllLines($results, $utf8) } else { @() })
    if (-not $lines) { Say "FAIL  [sidekick] phase $phase wrote nothing (exit code $($p.ExitCode))" }
    foreach ($l in $lines) { Say $l }
    foreach ($stream in 'stdout', 'stderr') {
        $said = [IO.File]::ReadAllText((Join-Path $run "sidekick-$phase-$stream.txt"))
        if ($said.Trim()) { Say "FAIL  [sidekick] AutoHotkey said: $($said.Trim())" }
    }
    Assert-RealUntouched "sidekick phase $phase"
}

# --- Plugin Core, redirected the same way before anything touches it --------
Add-Type -AssemblyName System.Security
$store = $null
if ($CoreDll) {
    $asm = [Reflection.Assembly]::LoadFrom((Resolve-Path -LiteralPath $CoreDll).Path)
    $store = $asm.GetType('Supervertaler.Core.ApiKeyStore', $true)
    $paths = $asm.GetType('Supervertaler.Core.SupervertalerPaths', $true)
    $paths::Set($dataRoot)
    $store.GetMethod('RedirectLockedFile', [Reflection.BindingFlags]'NonPublic, Static').Invoke($null, [object[]]@([string]$lockedPath)) | Out-Null
    if ($store::FilePath -ne $plainPath -or $store::LockedFilePath -ne $lockedPath) {
        Say "ABORT  Plugin Core's key files are not redirected: $($store::FilePath) / $($store::LockedFilePath)"; exit 2
    }
    $coreLog = Join-Path $root 'core.log'
    $store::Log = [Action[string]] { param($m) [IO.File]::AppendAllText($coreLog, $m + "`r`n", $utf8) }
    Write-Host "INFO  Plugin Core: $CoreDll, redirected to the scratch folder"
} else {
    Write-Host 'SKIP  no -CoreDll: the checks against Plugin Core are not run'
}

function Lock-WithEntropy([string]$key) {
    [Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect(
        $utf8.GetBytes($key), $utf8.GetBytes('PROJ-001'), 'CurrentUser'))
}
function Clear-KeyFiles {
    foreach ($f in @($plainPath, $lockedPath)) { Remove-Item -LiteralPath $f -ErrorAction SilentlyContinue }
}

if ($store) {
    Write-Host ''
    Write-Host '=== Each reads what the others locked ==='
    Invoke-Python 'lock'
    Remove-Item -LiteralPath $plainPath
    Check ($store::Get('claude') -ceq $K1) 'core: reads the Anthropic key Workbench locked'
    Check ($store::Get('openai') -ceq $UNI) 'core: and the one that is not plain ASCII'
    Check ($store::Get('google') -ceq $GT) "core: and Workbench's Google Translate key, under the shared id"
    Check ($store::Get($store::PrivateId('workbench', 'custom_openai', $profileName)) -ceq $KEY_WB) "core: and Workbench's profile key"
    if ($sidekickScript) { Invoke-Sidekick 'read' }

    $acmeId = $store::PrivateId('trados', 'custom_openai', $profileName)
    Check ($store::Set('gemini', $GEM) -and $store::Set('mistral', $MI) -and $store::Set($acmeId, $KEY_ACME)) 'core: locks two keys and a Trados profile key'
    if ($sidekickScript) { Invoke-Sidekick 'lock' }
    Remove-Item -LiteralPath $plainPath
    Invoke-Python 'read' $(if ($sidekickScript) { 'sidekick' } else { '' })
    Remove-Item -LiteralPath $plainPath
    Check (($store::Get($acmeId) -ceq $KEY_ACME) -and ($store::Get('gemini') -ceq $GEM)) "core: its entries survived Workbench's rewrite"
    Check ($store::Get('openrouter') -ceq $OR) 'core: and reads the key Workbench saved last'
    if ($sidekickScript) { Check ($store::Get('deepseek') -ceq $SK) "core: and Sidekick's" }

    Write-Host ''
    Write-Host '=== The same file, byte for byte ==='
    Check ($store::Set('deepseek', $DS2)) 'core: rewrites the locked file'
    $coreBytes = [IO.File]::ReadAllBytes($lockedPath)
    $pyRender = Join-Path $run 'python-render.json'
    Invoke-Python 'render' $pyRender
    $same = (Test-Path -LiteralPath $pyRender) -and ([Convert]::ToBase64String([IO.File]::ReadAllBytes($pyRender)) -ceq [Convert]::ToBase64String($coreBytes))
    Check $same "format: Workbench writes the locked file byte for byte as core does ($($coreBytes.Length) bytes, with & < > ' in an id)"

    Write-Host ''
    Write-Host '=== The same sentences, word for word ==='
    $ids = @('claude', 'openai', 'gemini', 'mistral', 'deepseek', 'openrouter', 'deepl', 'google', 'microsoft', 'custom_openai', $acmeId)
    foreach ($state in @('refused', 'damaged', 'absent')) {
        Clear-KeyFiles
        if ($state -eq 'refused') {
            $entries = [ordered]@{}
            foreach ($id in $ids) { $entries[$id] = Lock-WithEntropy "sk-test-$id-PROJ-001" }
            [IO.File]::WriteAllText($lockedPath, ($entries | ConvertTo-Json), $utf8)
        } elseif ($state -eq 'damaged') {
            [IO.File]::WriteAllText($lockedPath, '{ "claude": "AQAA', $utf8)
        }
        $mine = Join-Path $run "python-messages-$state.txt"
        Invoke-Python 'messages' $mine
        $py = @{}
        foreach ($l in [IO.File]::ReadAllLines($mine, $utf8)) { $f = $l.Split("`t"); $py["$($f[0])|$($f[1])"] = $f[2] }
        $same = $py.Count -eq (2 * $ids.Count)
        $differs = @()
        foreach ($id in $ids) {
            $m1 = [string]$store::UnreadableKeyNote($id)
            $m2 = [string]$store::MissingKeyMessage($id, $place)
            if ($py["$id|M1"] -cne $m1 -or $py["$id|M2"] -cne $m2) { $same = $false; $differs += $id }
        }
        Check $same "messages ($state): Workbench's M1 and M2 are core's, word for word, for $($ids.Count) ids$(if ($differs) { ' - differ: ' + ($differs -join ', ') })"
    }

    # M6: each sets its own copy aside; the sentence around the path is the same.
    Clear-KeyFiles
    [IO.File]::WriteAllText($plainPath, "{`r`n  `"claude`": `"$K1`"`r`n}`r`n", $utf8)
    [IO.File]::WriteAllText($lockedPath, '{ "claude": "AQAA', $utf8)
    $coreNotice = [string]$store::SyncLocked().Notice
    [IO.File]::WriteAllText($lockedPath, '{ "claude": "AQAA', $utf8)
    $noticeFile = Join-Path $run 'python-notice.txt'
    Invoke-Python 'notice' $noticeFile
    $pyNotice = [IO.File]::ReadAllText($noticeFile, $utf8)
    $pattern = '^(.*set aside as ).*(api-keys\.locked\.damaged-[0-9-]+\.json)(\. .*)$'
    Check (($coreNotice -match $pattern) -and (($coreNotice -replace $pattern, '$1$3') -ceq ($pyNotice -replace $pattern, '$1$3'))) 'messages: M6 (damaged file set aside) is core''s sentence'

    Write-Host ''
    Write-Host '=== Two writers at once ==='
    Clear-KeyFiles
    $startFile = Join-Path $run 'race-start.txt'
    $start = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + 2500) / 1000.0
    [IO.File]::WriteAllText($startFile, $start.ToString([Globalization.CultureInfo]::InvariantCulture))
    $race = Start-Process -FilePath $Python -PassThru -NoNewWindow `
        -ArgumentList "`"$(Join-Path $repo 'tools\api_key_lock_xlang.py')`"", "`"$appData`"", 'race', "`"$startFile`"" `
        -RedirectStandardOutput (Join-Path $run 'race-stdout.txt') -RedirectStandardError (Join-Path $run 'race-stderr.txt')
    $null = $race.Handle     # without it, ExitCode stays empty after the process ends
    while (([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0) -lt $start) { Start-Sleep -Milliseconds 1 }
    $coreOk = $true
    for ($i = 0; $i -lt 40; $i++) { if (-not $store::Set("core-$i", "value-core-$i-PROJ-001")) { $coreOk = $false } }
    $race.WaitForExit()
    Check ($coreOk -and $race.ExitCode -eq 0) 'race: core and Workbench each save 40 keys at the same moment'
    $plain = $store::Load()
    $inPlain = 0; foreach ($w in 'core', 'python') { for ($i = 0; $i -lt 40; $i++) { if ($plain["$w-$i"] -ceq "value-$w-$i-PROJ-001") { $inPlain++ } } }
    Remove-Item -LiteralPath $plainPath
    $inLocked = 0; foreach ($w in 'core', 'python') { for ($i = 0; $i -lt 40; $i++) { if ($store::Get("$w-$i") -ceq "value-$w-$i-PROJ-001") { $inLocked++ } } }
    Check (($inPlain -eq 80) -and ($inLocked -eq 80)) "race: all 80 kept, in the plain file ($inPlain) and the locked file ($inLocked)"
    Assert-RealUntouched 'the cross-language checks'
}

# --- No key anywhere but the plain files --------------------------------------
$leaks = @()
# (The plain files hold keys by design; sidekick.ahk is this test's own script.)
Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.Name -notlike 'api-keys.json*' -and $_.Name -ne 'sidekick.ahk' } | ForEach-Object {
    $raw = [IO.File]::ReadAllBytes($_.FullName)
    $text8 = $utf8.GetString($raw)
    $text16 = [Text.Encoding]::Unicode.GetString($raw)
    foreach ($k in $allKeys) {
        if ($text8.Contains($k) -or $text16.Contains($k)) { $leaks += "$($_.Name)" }
    }
}
foreach ($k in $allKeys) { if ($script:output.ToString().Contains($k)) { $leaks += 'the Python output' } }
$fileCount = (Get-ChildItem -LiteralPath $root -Recurse -File).Count
Check ($leaks.Count -eq 0) "no key text (UTF-8 or UTF-16) in any locked file, set-aside copy, log or output: $($allKeys.Count) keys x $fileCount files$(if ($leaks) { ' - found in: ' + (($leaks | Sort-Object -Unique) -join ', ') })"

# --- The pytest suite ---------------------------------------------------------
if (-not $SkipPytest) {
    Write-Host ''
    Write-Host '=== pytest (tests\) ==='
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $savedQt = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = 'offscreen'
    Push-Location $repo       # the tests import modules\ from the repository folder
    try {
        $out = & $Python -m pytest tests -q -p no:cacheprovider 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $saved
        $env:QT_QPA_PLATFORM = $savedQt
    }
    $summary = ($out | ForEach-Object { "$_" } | Where-Object { $_ -match '\d+ (passed|failed)' } | Select-Object -Last 1)
    if ($code -ne 0) { $out | ForEach-Object { Write-Host "      $_" } }
    Check ($code -eq 0) "pytest: $summary"
    Assert-RealUntouched 'pytest'
}

Assert-RealUntouched 'the whole run'
Write-Host ''
Write-Host "PASSED $($script:passed), FAILED $($script:failed). The user's own files: unchanged (names, sizes and times)."
if (-not $Keep -and $script:failed -eq 0) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
else { Write-Host "INFO  scratch folder kept: $root" }
exit $(if ($script:failed -eq 0) { 0 } else { 1 })
