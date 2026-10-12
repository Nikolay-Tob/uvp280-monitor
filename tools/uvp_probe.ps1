<#
.SYNOPSIS
  uvp_probe.ps1 (v2) - диагностика УВП-280 по Modbus TCP. ТОЛЬКО ЧТЕНИЕ.

.DESCRIPTION
  Ничего не настраивает в приборе. Единственные записи - регистры ЗАПРОСОВ:
  7600/7601 (карта параметров) и 7900-7914 (пробные запросы архива).
  С ключом -TestLogin дополнительно проверяется вход (HR200/201/203), после чего сеанс сбрасывается.
  Работает в обычном Windows PowerShell 5.1, ничего устанавливать не нужно.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uvp_probe.ps1 -Ip 192.168.0.10
  powershell -ExecutionPolicy Bypass -File .\uvp_probe.ps1 -Ip 192.168.0.10 -NoArchive
  powershell -ExecutionPolicy Bypass -File .\uvp_probe.ps1 -Ip 192.168.0.10 -TestLogin     (вход проверяется В КОНЦЕ полного опроса)
  powershell -ExecutionPolicy Bypass -File .\uvp_probe.ps1 -Ip 192.168.0.10 -OnlyLogin     (только проверка входа, сразу)

  Результат: файл uvp_probe_<ip>_<дата>.txt в текущей папке - его и пришлите.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Ip,
    [int]$Port = 502,
    [int]$Unit = 1,
    [int]$TimeoutMs = 5000,
    [int]$DelayMs = 10,
    [switch]$NoArchive,
    [switch]$NoOil,
    [switch]$TestLogin,
    [switch]$OnlyLogin,
    [string]$Out
)

$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Text.Encoding]::RegisterProvider([Text.CodePagesEncodingProvider]::Instance) } catch {}
$script:Enc = [Text.Encoding]::GetEncoding(1251)
$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Off = 0
$script:Tid = 0
$script:Unit = $Unit
$script:Stream = $null
$script:Client = $null
$script:Reconnects = 0
$script:P32 = @(0, 1, 2, 3)
$script:P64 = @(0, 1, 2, 3, 4, 5, 6, 7)

function Say([string]$t = '') { $script:Lines.Add($t); Write-Host $t }
function Sect([string]$t) {
    $script:Lines.Add(''); $script:Lines.Add('=' * 78); $script:Lines.Add($t); $script:Lines.Add('=' * 78)
    Write-Host ''; Write-Host ('== ' + $t)
}
function Fmt($v) { ([double]$v).ToString('G7', $inv) }
function Hex($r) { (($r | ForEach-Object { '{0:X4}' -f $_ }) -join ' ') }

# ---------- Modbus TCP ----------
function Connect-Dev {
    if ($null -ne $script:Client) { try { $script:Client.Close() } catch {} }
    $c = New-Object Net.Sockets.TcpClient
    $ar = $c.BeginConnect($Ip, $Port, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) { try { $c.Close() } catch {}; throw 'таймаут подключения' }
    $c.EndConnect($ar)
    $c.ReceiveTimeout = $TimeoutMs; $c.SendTimeout = $TimeoutMs
    $script:Client = $c
    $script:Stream = $c.GetStream()
    $script:Stream.ReadTimeout = $TimeoutMs; $script:Stream.WriteTimeout = $TimeoutMs
}
function Read-Exact([int]$n) {
    $buf = New-Object 'byte[]' $n
    $got = 0
    while ($got -lt $n) {
        $k = $script:Stream.Read($buf, $got, $n - $got)
        if ($k -le 0) { throw 'соединение закрыто прибором' }
        $got += $k
    }
    return , $buf
}
function Send-Pdu([byte[]]$pdu) {
    for ($attempt = 0; $attempt -le 2; $attempt++) {
        try {
            if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
            $script:Tid = ($script:Tid + 1) -band 0xFFFF
            $len = $pdu.Length + 1
            $hdr = [byte[]]@(($script:Tid -shr 8), ($script:Tid -band 255), 0, 0, ($len -shr 8), ($len -band 255), $script:Unit)
            $buf = [byte[]]($hdr + $pdu)
            $script:Stream.Write($buf, 0, $buf.Length)
            $h = Read-Exact 7
            $ln = $h[4] * 256 + $h[5]
            $body = Read-Exact ($ln - 1)
            if (($body[0] -band 0x80) -ne 0) { throw ('MB' + $body[1]) }
            return , $body
        }
        catch {
            if ($_.Exception.Message -match '^MB\d+$') { throw }
            if ($attempt -ge 2) { throw }
            Say ('  (сбой связи: {0}; переподключаюсь, попытка {1})' -f $_.Exception.Message, ($attempt + 2))
            Start-Sleep -Milliseconds 700
            Connect-Dev
            $script:Reconnects++
        }
    }
}
function Read-Regs([int]$fc, [int]$addr, [int]$n) {
    $a = $addr + $script:Off
    if ($a -lt 0) { throw 'MB2' }
    $b = Send-Pdu ([byte[]]@($fc, ($a -shr 8), ($a -band 255), ($n -shr 8), ($n -band 255)))
    $cnt = [int]($b[1] / 2)
    $r = New-Object 'int[]' $cnt
    for ($i = 0; $i -lt $cnt; $i++) { $r[$i] = $b[2 + 2 * $i] * 256 + $b[3 + 2 * $i] }
    return , $r
}
function Write-Regs([int]$addr, [int[]]$vals) {
    $a = $addr + $script:Off
    $n = $vals.Length
    $data = New-Object 'byte[]' ($n * 2)
    for ($i = 0; $i -lt $n; $i++) { $data[2 * $i] = [byte]($vals[$i] -shr 8); $data[2 * $i + 1] = [byte]($vals[$i] -band 255) }
    $pdu = [byte[]]([byte[]]@(16, ($a -shr 8), ($a -band 255), ($n -shr 8), ($n -band 255), ($n * 2)) + $data)
    [void](Send-Pdu $pdu)
}
function Try-Regs([string]$label, [int]$fc, [int]$addr, [int]$n) {
    try { return , (Read-Regs $fc $addr $n) }
    catch {
        if ($_.Exception.Message -match '^MB(\d+)$') { Say ('  {0,-44} ошибка Modbus {1}' -f $label, $Matches[1]); return $null }
        throw
    }
}
function RdHR([int]$a, [int]$n) { Read-Regs 3 $a $n }
function RdIR([int]$a, [int]$n) { Read-Regs 4 $a $n }

# ---------- декодеры с учётом порядка байт (определяется по контрольным константам) ----------
function Arrange($r, [int]$i, [int]$n, $perm) {
    $raw = New-Object 'byte[]' $n
    for ($k = 0; $k -lt ($n -shr 1); $k++) { $raw[2 * $k] = [byte]($r[$i + $k] -shr 8); $raw[2 * $k + 1] = [byte]($r[$i + $k] -band 255) }
    $o = New-Object 'byte[]' $n
    for ($k = 0; $k -lt $n; $k++) { $o[$k] = $raw[$perm[$k]] }
    return , $o
}
function Get-U32($r, [int]$i) { $b = Arrange $r $i 4 $script:P32; ([int64]$b[0] * 16777216) + ([int64]$b[1] * 65536) + ([int64]$b[2] * 256) + $b[3] }
function Get-I32($r, [int]$i) { $v = Get-U32 $r $i; if ($v -gt 2147483647) { $v - 4294967296 } else { $v } }
function Get-F32($r, [int]$i) { $b = Arrange $r $i 4 $script:P32; [double][BitConverter]::ToSingle([byte[]]@($b[3], $b[2], $b[1], $b[0]), 0) }
function Get-F64($r, [int]$i) { $b = Arrange $r $i 8 $script:P64; [BitConverter]::ToDouble([byte[]]@($b[7], $b[6], $b[5], $b[4], $b[3], $b[2], $b[1], $b[0]), 0) }
function Get-S16([int]$v) { if ($v -ge 32768) { $v - 65536 } else { $v } }
function Get-Bytes($r) {
    $l = New-Object System.Collections.Generic.List[byte]
    foreach ($x in $r) { $l.Add([byte]($x -shr 8)); $l.Add([byte]($x -band 255)) }
    return , $l.ToArray()
}
function Get-CStr($r) {
    $b = Get-Bytes $r
    $n = [Array]::IndexOf($b, [byte]0)
    if ($n -lt 0) { $n = $b.Length }
    return $script:Enc.GetString($b, 0, $n)
}
function Enc-U32([int64]$v) {   # значение -> 2 регистра в текущем порядке байт
    $b = @([int](($v -shr 24) -band 255), [int](($v -shr 16) -band 255), [int](($v -shr 8) -band 255), [int]($v -band 255))
    $raw = New-Object 'int[]' 4
    for ($k = 0; $k -lt 4; $k++) { $raw[$script:P32[$k]] = $b[$k] }
    return [int[]]@(($raw[0] * 256 + $raw[1]), ($raw[2] * 256 + $raw[3]))
}
function Find-Perm32($r) {
    foreach ($a in 0..3) { foreach ($b in 0..3) { foreach ($c in 0..3) { foreach ($d in 0..3) {
        if (($a -eq $b) -or ($a -eq $c) -or ($a -eq $d) -or ($b -eq $c) -or ($b -eq $d) -or ($c -eq $d)) { continue }
        $p = @($a, $b, $c, $d)
        $o = Arrange $r 0 4 $p
        $v = ([int64]$o[0] * 16777216) + ([int64]$o[1] * 65536) + ([int64]$o[2] * 256) + $o[3]
        if ($v -eq 1234567890) { return , $p }
    } } } }
    return $null
}
function Find-Perm64($r) {
    foreach ($w0 in 0..3) { foreach ($w1 in 0..3) { foreach ($w2 in 0..3) { foreach ($w3 in 0..3) {
        $ws = @($w0, $w1, $w2, $w3)
        if (($w0 -eq $w1) -or ($w0 -eq $w2) -or ($w0 -eq $w3) -or ($w1 -eq $w2) -or ($w1 -eq $w3) -or ($w2 -eq $w3)) { continue }
        foreach ($swap in 0..1) {
            $p = New-Object 'int[]' 8
            for ($w = 0; $w -lt 4; $w++) {
                if ($swap -eq 0) { $p[2 * $w] = 2 * $ws[$w]; $p[2 * $w + 1] = 2 * $ws[$w] + 1 } else { $p[2 * $w] = 2 * $ws[$w] + 1; $p[2 * $w + 1] = 2 * $ws[$w] }
            }
            $o = Arrange $r 0 8 $p
            $d = [BitConverter]::ToDouble([byte[]]@($o[7], $o[6], $o[5], $o[4], $o[3], $o[2], $o[1], $o[0]), 0)
            if ([math]::Abs($d - 123.4567890123456) -lt 1e-9) { return , $p }
        }
    } } } }
    return $null
}

# ---------- таблица регистров нефтяного узла (из карты регистров) ----------
$OilTable = @(
    @('IR', 3500, 'double', 'СИКН Масса брутто т'),
    @('IR', 3504, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3506, 'double', 'Масса нетто т'),
    @('IR', 3510, 'float', 'Массовый расход нетто т/ч'),
    @('IR', 3512, 'double', 'Объем брутто м3'),
    @('IR', 3516, 'float', 'Объемный расход брутто м3/ч'),
    @('IR', 3550, 'float', 'Давление МПа'),
    @('IR', 3552, 'float', 'Температура С'),
    @('IR', 3554, 'float', 'Плотность в р.у. кг/ м3'),
    @('IR', 3556, 'float', 'Плотность в ст.у. при 15 оС кг/ м3'),
    @('IR', 3558, 'float', 'Плотность в ст.у. при 20 оС кг/ м3'),
    @('IR', 3560, 'float', 'Влагосодержание, объёмная доля %'),
    @('IR', 3562, 'float', 'Влагосодержание, массовая доля %'),
    @('IR', 3564, 'float', 'Вязкость сСт'),
    @('IR', 3566, 'float', 'Объемный расход брутто м3/ч'),
    @('IR', 3568, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3570, 'int16', 'Признак используемого влагомера:'),
    @('IR', 3600, 'float', 'Линия 1 Давление МПа'),
    @('IR', 3602, 'float', 'Температура С'),
    @('IR', 3604, 'float', 'Плотность в р.у. кг/ м3'),
    @('IR', 3606, 'double', 'Масса брутто т'),
    @('IR', 3610, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3612, 'double', 'Масса нетто т'),
    @('IR', 3616, 'float', 'Массовый расход нетто т/ч'),
    @('IR', 3618, 'double', 'Объем брутто м3'),
    @('IR', 3622, 'float', 'Объемный расход брутто м3/ч'),
    @('IR', 3700, 'float', 'Линия Давление МПа'),
    @('IR', 3702, 'float', 'Температура С'),
    @('IR', 3704, 'float', 'Плотность в р.у. кг/ м3'),
    @('IR', 3706, 'double', 'Масса брутто т'),
    @('IR', 3710, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3712, 'double', 'Масса нетто т'),
    @('IR', 3716, 'float', 'Массовый расход нетто т/ч'),
    @('IR', 3718, 'double', 'Объем брутто м3'),
    @('IR', 3722, 'float', 'Объемный расход брутто м3/ч'),
    @('IR', 3800, 'float', 'Линия Давление МПа'),
    @('IR', 3802, 'float', 'Температура С'),
    @('IR', 3804, 'float', 'Плотность в р.у. кг/ м3'),
    @('IR', 3806, 'double', 'Масса брутто т'),
    @('IR', 3810, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3812, 'double', 'Масса нетто т'),
    @('IR', 3816, 'float', 'Массовый расход нетто т/ч'),
    @('IR', 3818, 'double', 'Объем брутто м3'),
    @('IR', 3822, 'float', 'Объемный расход брутто м3/ч'),
    @('IR', 3900, 'float', 'Линия Давление МПа'),
    @('IR', 3902, 'float', 'Температура С'),
    @('IR', 3904, 'float', 'Плотность в р.у. кг/ м3'),
    @('IR', 3906, 'double', 'Масса брутто т'),
    @('IR', 3910, 'float', 'Массовый расход брутто т/ч'),
    @('IR', 3912, 'double', 'Масса нетто т'),
    @('IR', 3916, 'float', 'Массовый расход нетто т/ч'),
    @('IR', 3918, 'double', 'Объем брутто м3'),
    @('IR', 3922, 'float', 'Объемный расход брутто м3/ч'),
    @('BASE', 0, 'uint32', 'Уникальное значение, определяющее набор знач'),
    @('BASE', 2, 'int16', 'Время начала периода, день'),
    @('BASE', 3, 'int16', 'Время начала периода, месяц'),
    @('BASE', 4, 'int16', 'Время начала периода, год'),
    @('BASE', 5, 'int16', 'Время начала периода, часы'),
    @('BASE', 6, 'int16', 'Время начала периода, минуты'),
    @('BASE', 7, 'int16', 'Время начала периода, секунды'),
    @('BASE', 8, 'int16', 'Время окончания периода, день'),
    @('BASE', 9, 'int16', 'Время окончания периода, месяц'),
    @('BASE', 10, 'int16', 'Время окончания периода, год'),
    @('BASE', 11, 'int16', 'Время окончания периода, часы'),
    @('BASE', 12, 'int16', 'Время окончания периода, минуты'),
    @('BASE', 13, 'int16', 'Время окончания периода, секунды'),
    @('BASE', 14, 'float', 'Давление (*) МПа'),
    @('BASE', 16, 'float', 'Температура (*) о'),
    @('BASE', 18, 'float', 'Плотность в р.у. (*) кг/ м3'),
    @('BASE', 20, 'float', 'Плотность в ст.у. при 15 оС (*) кг/ м3'),
    @('BASE', 22, 'float', 'Плотность в ст.у. при 20 оС (*) кг/ м3'),
    @('BASE', 24, 'float', 'Влагосодержание, объемная доля (*) %'),
    @('BASE', 26, 'float', 'Влагосодержание, массовая доля (*) %'),
    @('BASE', 28, 'float', 'Вязкость (*) сСт'),
    @('BASE', 30, 'float', 'Расход брутто(масса) т/ч'),
    @('BASE', 32, 'double', 'Масса брутто т'),
    @('BASE', 36, 'double', 'Масса брутто на начало периода т'),
    @('BASE', 40, 'double', 'Масса брутто на конец периода т'),
    @('BASE', 44, 'double', 'Масса нетто т'),
    @('BASE', 48, 'double', 'Масса нетто на начало периода т'),
    @('BASE', 52, 'double', 'Масса нетто на конец периода т'),
    @('BASE', 56, 'double', 'Масса балласта т'),
    @('BASE', 60, 'double', 'Объем брутто м3'),
    @('BASE', 64, 'double', 'Объем брутто при 15 оС м3'),
    @('BASE', 68, 'double', 'Объем брутто при 20 оС м3'),
    @('BASE', 72, 'double', 'Объем брутто на начало периода м3'),
    @('BASE', 76, 'double', 'Объем брутто на конец периода м3'),
    @('BASE', 80, 'float', 'Давление (*) МПа'),
    @('BASE', 82, 'float', 'Температура (*) о'),
    @('BASE', 84, 'float', 'Плотность в р.у. (*) кг/ м3'),
    @('BASE', 86, 'float', 'Плотность в ст.у. при 15 оС(*) кг/ м3'),
    @('BASE', 88, 'float', 'Плотность в ст.у. при 20 оС(*) кг/ м3'),
    @('BASE', 100, 'float', 'Давление (**) МПа'),
    @('BASE', 102, 'float', 'Температура (**) о'),
    @('BASE', 104, 'float', 'Плотность в р.у. (**) кг/ м3'),
    @('BASE', 106, 'float', 'Расход брутто(масса) т/ч'),
    @('BASE', 108, 'double', 'Масса брутто Т'),
    @('BASE', 112, 'double', 'Масса брутто на начало периода Т'),
    @('BASE', 116, 'double', 'Масса брутто на конец периода Т'),
    @('BASE', 120, 'double', 'Масса нетто Т'),
    @('BASE', 124, 'double', 'Масса нетто на начало периода Т'),
    @('BASE', 128, 'double', 'Масса нетто на конец периода Т'),
    @('BASE', 132, 'double', 'Масса балласта Т'),
    @('BASE', 136, 'double', 'Объем брутто м3'),
    @('BASE', 140, 'double', 'Объем брутто при 15 оС м3'),
    @('BASE', 144, 'double', 'Объем брутто при 20 оС м3'),
    @('BASE', 148, 'double', 'Объем брутто на начало периода м3'),
    @('BASE', 152, 'double', 'Объем брутто на конец периода м3'),
    @('BASE', 200, 'float', 'Давление (**) МПа'),
    @('BASE', 202, 'float', 'Температура (**) о'),
    @('BASE', 204, 'float', 'Плотность в р.у. (**) кг/ м3'),
    @('BASE', 206, 'float', 'Расход брутто(масса) т/ч'),
    @('BASE', 208, 'double', 'Масса брутто т'),
    @('BASE', 212, 'double', 'Масса брутто на начало периода т'),
    @('BASE', 216, 'double', 'Масса брутто на конец периода т'),
    @('BASE', 220, 'double', 'Масса нетто т'),
    @('BASE', 224, 'double', 'Масса нетто на начало периода т'),
    @('BASE', 228, 'double', 'Масса нетто на конец периода т'),
    @('BASE', 232, 'double', 'Масса балласта т'),
    @('BASE', 236, 'double', 'Объем брутто м3'),
    @('BASE', 240, 'double', 'Объем брутто при 15 оС м3'),
    @('BASE', 244, 'double', 'Объем брутто при 20 оС м3'),
    @('BASE', 248, 'double', 'Объем брутто на начало периода м3'),
    @('BASE', 252, 'double', 'Объем брутто на конец периода м3'),
    @('BASE', 300, 'float', 'Давление (**) МПа'),
    @('BASE', 302, 'float', 'Температура (**) о'),
    @('BASE', 304, 'float', 'Плотность в р.у. (**) кг/ м3'),
    @('BASE', 306, 'float', 'Расход брутто(масса) т/ч'),
    @('BASE', 308, 'double', 'Масса брутто т'),
    @('BASE', 312, 'double', 'Масса брутто на начало периода т'),
    @('BASE', 316, 'double', 'Масса брутто на конец периода т'),
    @('BASE', 320, 'double', 'Масса нетто т'),
    @('BASE', 324, 'double', 'Масса нетто на начало периода т'),
    @('BASE', 328, 'double', 'Масса нетто на конец периода т'),
    @('BASE', 332, 'double', 'Масса балласта т'),
    @('BASE', 336, 'double', 'Объем брутто м3'),
    @('BASE', 340, 'double', 'Объем брутто при 15 оС м3'),
    @('BASE', 344, 'double', 'Объем брутто при 20 оС м3'),
    @('BASE', 348, 'double', 'Объем брутто на начало периода м3'),
    @('BASE', 352, 'double', 'Объем брутто на конец периода м3'),
    @('BASE', 400, 'float', 'Давление (**) МПа'),
    @('BASE', 402, 'float', 'Температура (**) о'),
    @('BASE', 404, 'float', 'Плотность в р.у. (**) кг/ м3'),
    @('BASE', 406, 'float', 'Расход брутто(масса) т/ч'),
    @('BASE', 408, 'double', 'Масса брутто т'),
    @('BASE', 412, 'double', 'Масса брутто на начало периода т'),
    @('BASE', 416, 'double', 'Масса брутто на конец периода т'),
    @('BASE', 420, 'double', 'Масса нетто т'),
    @('BASE', 424, 'double', 'Масса нетто на начало периода т'),
    @('BASE', 428, 'double', 'Масса нетто на конец периода т'),
    @('BASE', 432, 'double', 'Масса балласта т'),
    @('BASE', 436, 'double', 'Объем брутто м3'),
    @('BASE', 440, 'double', 'Объем брутто при 15 оС м3'),
    @('BASE', 444, 'double', 'Объем брутто при 20 оС м3'),
    @('BASE', 448, 'double', 'Объем брутто на начало периода м3'),
    @('BASE', 452, 'double', 'Объем брутто на конец периода м3'),
    @('HR', 17100, 'uint16', 'Маска отключения линий СИКН.'),
    @('HR', 17200, 'float', 'Коэффициент К0'),
    @('HR', 17202, 'float', 'Коэффициент К1'),
    @('HR', 17204, 'float', 'Коэффициент К2'),
    @('HR', 17206, 'float', 'Коэффициент К18'),
    @('HR', 17208, 'float', 'Коэффициент К19'),
    @('HR', 17210, 'float', 'Коэффициент К20А'),
    @('HR', 17212, 'float', 'Коэффициент К20В'),
    @('HR', 17214, 'float', 'Коэффициент К21А'),
    @('HR', 17216, 'float', 'Коэффициент К21В'),
    @('HR', 17218, 'float', 'Текущее значение плотности.'),
    @('HR', 17250, 'float', 'M-фактор'),
    @('HR', 17252, 'float', 'К-фактор для датчиков без ГХ'),
    @('HR', 17254, 'float', 'К-фактор при F1'),
    @('HR', 17256, 'float', 'Частота F1'),
    @('HR', 17258, 'float', 'К-фактор при F2'),
    @('HR', 17260, 'float', 'Частота F2'),
    @('HR', 17262, 'float', 'К-фактор при F3'),
    @('HR', 17264, 'float', 'Частота F3'),
    @('HR', 17266, 'float', 'К-фактор при F4'),
    @('HR', 17268, 'float', 'Частота F4'),
    @('HR', 17270, 'float', 'К-фактор при F5'),
    @('HR', 17272, 'float', 'Частота F5'),
    @('HR', 17274, 'float', 'К-фактор при F6'),
    @('HR', 17276, 'float', 'Частота F6'),
    @('HR', 17300, 'float', 'M-фактор'),
    @('HR', 17302, 'float', 'К-фактор для датчиков без ГХ'),
    @('HR', 17304, 'float', 'К-фактор при F1'),
    @('HR', 17306, 'float', 'Частота F1'),
    @('HR', 17308, 'float', 'К-фактор при F2'),
    @('HR', 17310, 'float', 'Частота F2'),
    @('HR', 17312, 'float', 'К-фактор при F3'),
    @('HR', 17314, 'float', 'Частота F3'),
    @('HR', 17316, 'float', 'К-фактор при F4'),
    @('HR', 17318, 'float', 'Частота F4'),
    @('HR', 17320, 'float', 'К-фактор при F5'),
    @('HR', 17322, 'float', 'Частота F5'),
    @('HR', 17324, 'float', 'К-фактор при F6'),
    @('HR', 17326, 'float', 'Частота F6'),
    @('HR', 17400, 'float', 'M-фактор'),
    @('HR', 17402, 'float', 'К-фактор для датчиков без ГХ'),
    @('HR', 17404, 'float', 'К-фактор при F1'),
    @('HR', 17406, 'float', 'Частота F1'),
    @('HR', 17408, 'float', 'К-фактор при F2'),
    @('HR', 17410, 'float', 'Частота F2'),
    @('HR', 17412, 'float', 'К-фактор при F3'),
    @('HR', 17414, 'float', 'Частота F3'),
    @('HR', 17416, 'float', 'К-фактор при F4'),
    @('HR', 17418, 'float', 'Частота F4'),
    @('HR', 17420, 'float', 'К-фактор при F5'),
    @('HR', 17422, 'float', 'Частота F5'),
    @('HR', 17424, 'float', 'К-фактор при F6'),
    @('HR', 17426, 'float', 'Частота F6'),
    @('HR', 17500, 'float', 'M-фактор'),
    @('HR', 17502, 'float', 'К-фактор для датчиков без ГХ'),
    @('HR', 17504, 'float', 'К-фактор при F1'),
    @('HR', 17506, 'float', 'Частота F1'),
    @('HR', 17508, 'float', 'К-фактор при F2'),
    @('HR', 17510, 'float', 'Частота F2'),
    @('HR', 17512, 'float', 'К-фактор при F3'),
    @('HR', 17514, 'float', 'Частота F3'),
    @('HR', 17516, 'float', 'К-фактор при F4'),
    @('HR', 17518, 'float', 'Частота F4'),
    @('HR', 17520, 'float', 'К-фактор при F5'),
    @('HR', 17522, 'float', 'Частота F5'),
    @('HR', 17524, 'float', 'К-фактор при F6'),
    @('HR', 17526, 'float', 'Частота F6'),
    @('HR', 17600, 'float', 'M-фактор'),
    @('HR', 17602, 'float', 'К-фактор для датчиков без ГХ'),
    @('HR', 17604, 'float', 'К-фактор при F1'),
    @('HR', 17606, 'float', 'Частота F1'),
    @('HR', 17608, 'float', 'К-фактор при F2'),
    @('HR', 17610, 'float', 'Частота F2'),
    @('HR', 17612, 'float', 'К-фактор при F3'),
    @('HR', 17614, 'float', 'Частота F3'),
    @('HR', 17616, 'float', 'К-фактор при F4'),
    @('HR', 17618, 'float', 'Частота F4'),
    @('HR', 17620, 'float', 'К-фактор при F5'),
    @('HR', 17622, 'float', 'Частота F5'),
    @('HR', 17624, 'float', 'К-фактор при F6'),
    @('HR', 17626, 'float', 'Частота F6'),
    @('HR', 18000, 'uint16', 'Номер трубопровода для назначения в качестве'),
    @('HR', 18001, 'uint16', 'Номер трубопровода для назначения в качестве'),
    @('HR', 18002, 'uint32', 'Расходомеры с импульсным выходом количества:'),
    @('HR', 18004, 'uint32', 'Количество успешно завершенных КМХ.'),
    @('HR', 18006, 'uint32', 'Команда запуска/прерывания КМХ.'),
    @('HR', 18010, 'float', 'Эталонная линия Давление(*) МПа'),
    @('HR', 18012, 'float', 'Контролируемая линия Давление(**) МПа'),
    @('HR', 18014, 'float', 'Эталонная линия Температура(*) оС'),
    @('HR', 18016, 'float', 'Контролируемая линия Температура(**) оС'),
    @('HR', 18018, 'float', 'Эталонная линия Плотность нефтяной жидкости '),
    @('HR', 18020, 'float', 'Контролируемая линия Плотность нефтяной жидк'),
    @('HR', 18022, 'float', 'Эталонная линия Массовый (объемный) расход б'),
    @('HR', 18024, 'float', 'Контролируемая линия Массовый (объемный) рас'),
    @('HR', 18026, 'float', 'Эталонная линия Усреднённая частота с датчик'),
    @('HR', 18028, 'float', 'Контролируемая линия Усреднённая частота с д'),
    @('HR', 18030, 'float', 'Эталонная линия Объем брутто м3'),
    @('HR', 18032, 'float', 'Контролируемая линия Объем брутто м3'),
    @('HR', 18034, 'float', 'Эталонная линия Масса брутто т'),
    @('HR', 18036, 'float', 'Контролируемая линия Масса брутто т'),
    @('HR', 18038, 'float', 'Эталонная линия Число импульсов'),
    @('HR', 18040, 'float', 'Контролируемая линия Число импульсов'),
    @('HR', 18042, 'float', 'Отклонение массы(объема) от эталонной линии '),
    @('HR', 18044, 'float', 'М-фактор для контролируемой линии'),
    @('HR', 18046, 'float', 'Выполнено в текущем КМХ %'),
    @('HR', 18500, 'Int16', 'Конфигурация алгоритма работы. Регистр тольк'),
    @('HR', 18501, 'Int16', 'Длительность импульса в миллисекундах'),
    @('HR', 18502, 'Int32', 'Количество проб. Уменьшающийся счётчик. При '),
    @('HR', 18504, 'Float', 'Для режима Т – период следования импульсов в'),
    @('HR', 18600, 'Int16', 'Конфигурация алгоритма работы. Регистр тольк'),
    @('HR', 18601, 'Int16', 'Длительность импульса в миллисекундах'),
    @('HR', 18602, 'Int32', 'Количество проб. Уменьшающийся счётчик. При '),
    @('HR', 18604, 'Float', 'Для режима Т – период следования импульсов в')
)

function Read-Typed([string]$kind, [int]$addr, [string]$type) {
    $fc = 3; if ($kind -eq 'IR' -or $kind -eq 'BASE') { $fc = 4 }
    switch -regex ($type.ToLower()) {
        '^(u?int16)$' { $r = Read-Regs $fc $addr 1; if ($type -ceq 'int16' -or $type -ceq 'Int16') { return Get-S16 $r[0] } else { return $r[0] } }
        '^uint32$' { $r = Read-Regs $fc $addr 2; return Get-U32 $r 0 }
        '^int32$' { $r = Read-Regs $fc $addr 2; return Get-I32 $r 0 }
        '^float$' { $r = Read-Regs $fc $addr 2; return Fmt (Get-F32 $r 0) }
        '^double$' { $r = Read-Regs $fc $addr 4; return (Get-F64 $r 0).ToString('G10', $inv) }
    }
}

function Get-ParamRow([int]$reg, [int]$code) {
    for ($t = 0; $t -lt 3; $t++) {
        $rc = $script:Reconnects
        Write-Regs $reg ([int[]]@($code))
        $nm = Get-CStr (RdHR 7700 71)
        $vl = Get-CStr (RdHR 7800 71)
        if ($rc -eq $script:Reconnects) { return @($nm, $vl) }
    }
    return @($nm, $vl)
}

function Test-Login {
        Sect '9. Проверка входа (HR200 пользователь, HR201 пароль, HR203 уровень)'
        $u = [int](Read-Host 'Номер пользователя (1-5)')
        if ($env:UVP_PASSWORD) { $pw = $env:UVP_PASSWORD }
        else {
            $sec = Read-Host 'Пароль (цифры)' -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            $pw = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $pv = 0L
        if (-not [int64]::TryParse($pw, [ref]$pv)) { Say '  пароль не число - вход не проверялся' }
        else {
            try {
                Say ('  пользователь {0}: запись HR200, затем HR201' -f $u)
                Write-Regs 200 ([int[]]@($u))
                Write-Regs 201 (Enc-U32 $pv)
                Start-Sleep -Milliseconds 700
                $r = RdHR 203 1; Say ('  HR203 после входа = {0}' -f $r[0])
            } catch { Say ('  ошибка: ' + $_.Exception.Message) }
            try {
                Write-Regs 201 ([int[]]@(0, 0))
                Start-Sleep -Milliseconds 700
                $r = RdHR 203 1; Say ('  HR203 после сброса сеанса (HR201=0) = {0}' -f $r[0])
            } catch { Say ('  ошибка сброса: ' + $_.Exception.Message) }
        }
}

# ---------- основной ход ----------
function Run-Probe {
    # 1. Адресация и порядок байт
    Sect '1. Адресация и порядок байт (контрольные константы HR110, 112, 114)'
    $found = $null
    foreach ($off in @(0, -1, 1)) {
        $script:Off = $off
        try { $r = RdHR 110 2 }
        catch {
            if ($_.Exception.Message -match '^MB(\d+)$') { Say ('  смещение {0:+0;-0;0}: ошибка Modbus {1}' -f $off, $Matches[1]); continue }
            throw
        }
        $p = Find-Perm32 $r
        if ($null -ne $p) { Say ('  смещение {0:+0;-0;0}: HR110 = {1}  => константа найдена, порядок байт 32 бит = {2}' -f $off, (Hex $r), ($p -join '')); $found = $off; $script:P32 = $p; break }
        Say ('  смещение {0:+0;-0;0}: HR110 = {1}  не совпало ни с одним порядком байт' -f $off, (Hex $r))
    }
    if ($null -eq $found) { Say '  Контрольная константа не найдена. Дальше значения могут быть неверны.'; $script:Off = 0 }
    else { $script:Off = $found; Say ('  => адрес в запросе = адрес из карты {0:+0;-0;0}' -f $found) }
    $r = Try-Regs 'HR114 (double)' 3 114 4
    if ($null -ne $r) {
        $p = Find-Perm64 $r
        if ($null -ne $p) { $script:P64 = $p; Say ('  HR114 = {0} => порядок байт 64 бит = {1}' -f (Hex $r), ($p -join '')) }
        else { Say ('  HR114 = {0} => порядок байт 64 бит определить не удалось' -f (Hex $r)) }
    }
    $r = Try-Regs 'HR112 (float)' 3 112 2
    if ($null -ne $r) { $v = Get-F32 $r 0; $t = 'НЕВЕРНО'; if ([math]::Abs($v - 123.4567) -lt 1e-4) { $t = 'OK' }; Say ('  HR112 = {0} -> {1} {2}' -f (Hex $r), (Fmt $v), $t) }
    $r = Try-Regs 'HR100' 3 100 1
    if ($null -ne $r) { Say ('  HR100 (как сообщает прибор) = {0:X4}h' -f $r[0]) }
    $r = Try-Regs 'HR101' 3 101 2
    if ($null -ne $r) { Say ('  HR101 (сырые регистры) = {0}' -f (Hex $r)) }
    $r = Try-Regs 'HR203 уровень доступа' 3 203 1
    if ($null -ne $r) { Say ('  HR203 уровень доступа = {0}   (по карте: 1 - базовый, 2 - наладчик)' -f $r[0]) }

    if ($OnlyLogin) { Test-Login; Say ''; Say ('Переподключений за время опроса: {0}' -f $script:Reconnects); return }

    # 2. Идентификация, часы, NTP
    Sect '2. Идентификация, часы, NTP'
    $mask = 0
    $r = Try-Regs 'HR1806 маска трубопроводов' 3 1806 1
    if ($null -ne $r) {
        $mask = $r[0]
        $list = @(); for ($i = 0; $i -lt 16; $i++) { if ((($mask -shr $i) -band 1) -eq 1) { $list += ($i + 1) } }
        Say ('  Маска описанных трубопроводов HR1806 = 0x{0:X4} -> ТП: {1}' -f $mask, ($list -join ', '))
    }
    $r = Try-Regs 'HR1807..1809 ПО' 3 1807 3
    if ($null -ne $r) { Say ('  ПО: версия {0}, сборка {1:00}.{2:00}.{3}' -f $r[0], ($r[1] % 100), [math]::Floor($r[1] / 100), $r[2]) }
    $r = Try-Regs 'HR1810 серийный номер' 3 1810 2
    if ($null -ne $r) { Say ('  Серийный номер: {0}' -f (Get-I32 $r 0)) }
    $devT = $null; $yr = 0
    $r = Try-Regs 'HR1800..1805 часы' 3 1800 6
    if ($null -ne $r) {
        $yr = $r[2]
        Say ('  Часы прибора (как в регистрах): день={0} мес={1} год={2} ч={3} мин={4} с={5}' -f $r[0], $r[1], $r[2], $r[3], $r[4], $r[5])
        try {
            $y4 = $r[2]; if ($y4 -lt 100) { $y4 += 2000 }
            $devT = New-Object DateTime ($y4, $r[1], $r[0], $r[3], $r[4], $r[5])
            $now = Get-Date
            Say ('  Часы прибора: {0};  часы ПК: {1};  разница прибор-ПК: {2} с' -f $devT.ToString('dd.MM.yyyy HH:mm:ss'), $now.ToString('dd.MM.yyyy HH:mm:ss'), [math]::Round(($devT - $now).TotalSeconds))
        } catch { $devT = $null }
    }
    $r = Try-Regs 'HR1005 IP NTP' 3 1005 2
    if ($null -ne $r) { $v = Get-U32 $r 0; Say ('  NTP-сервер HR1005 = {0}.{1}.{2}.{3}' -f [math]::Floor($v / 16777216), (([math]::Floor($v / 65536)) % 256), (([math]::Floor($v / 256)) % 256), ($v % 256)) }
    $r = Try-Regs 'HR1007 статус NTP' 3 1007 1
    if ($null -ne $r) { $v = $r[0]; Say ('  HR1007 = {0}: связь={1}, данные<30с={2}, автосинхр.={3}' -f $v, ($v -band 1), (($v -shr 1) -band 1), (($v -shr 2) -band 1)) }
    $r = Try-Regs 'HR1008 разница с NTP' 3 1008 1
    if ($null -ne $r) { Say ('  HR1008 разница с NTP = {0} с' -f (Get-S16 $r[0])) }

    # 3. Параметры среды
    Sect '3. Параметры среды (HR 1000..1132)'
    $envF = @(@(1000, 'Барометрическое давление, мм рт.ст.'), @(1002, 'Температура холодной воды, C'), @(1010, 'Газ ГОСТ 30319.2: плотность'),
        @(1012, 'CO2, мол.%'), @(1014, 'N2, мол.%'), @(1016, 'Влажность, %'), @(1050, 'Нефть: влажность, %об'), @(1052, 'Вязкость, сСт'),
        @(1070, 'Плотность обезвожен. нефти'), @(1072, 'Плотность пластовой воды'), @(1102, 'МР113-03: CH4'), @(1130, 'МР113-03: абс. влажность'))
    foreach ($e in $envF) {
        $r = Try-Regs ('HR{0} {1}' -f $e[0], $e[1]) 3 $e[0] 2
        if ($null -ne $r) { Say ('  HR{0,-5} {1,-40} {2}' -f $e[0], $e[1], (Fmt (Get-F32 $r 0))) }
    }
    $envI = @(@(1018, 'ошибок в задании газа ГОСТ 30319.2'), @(1068, 'ошибок в задании нефти'), @(1100, 'МР113-03: моль/масс (1/0)'), @(1132, 'ошибок МР113-03'))
    foreach ($e in $envI) {
        $r = Try-Regs ('HR{0} {1}' -f $e[0], $e[1]) 3 $e[0] 1
        if ($null -ne $r) { Say ('  HR{0,-5} {1,-40} {2}' -f $e[0], $e[1], $r[0]) }
    }

    # 4. Трубопроводы (только описанные в маске)
    Sect '4. Трубопроводы: IR 2000+(ТП-1)*100 (СИ: Па, C, кг/с, кг, м3, Вт, Дж)'
    $names = @(@(0, 'P изб, Па'), @(2, 'P абс, Па'), @(4, 'T, C'), @(6, 'Энтальпия, Дж/кг'), @(8, 'Массовый расход, кг/с'),
        @(10, 'Масса нараст., кг'), @(12, 'Объём р.у. нараст., м3'), @(14, 'Объём ст.у. нараст., м3'), @(16, 'Тепл. мощность, Вт'),
        @(18, 'Тепл. энергия, Дж'), @(24, 'Датчик: масс. расход, кг/с'), @(26, 'Датчик: объём. расход р.у., м3/с'),
        @(28, 'Датчик: объём. расход ст.у., м3/с'), @(30, 'Датчик: масса, кг'), @(32, 'Датчик: объём р.у., м3'),
        @(36, 'Плотность ст.у., кг/м3'), @(38, 'Плотность р.у., кг/м3'), @(40, 'Перепад давления, Па'),
        @(42, 'Объёмн. расход ст.у., м3/с'), @(44, 'Объёмн. расход р.у., м3/с'))
    $hist = @(@(60, 'Масса пред.час, кг'), @(62, 'V р.у. пред.час'), @(64, 'V ст.у. пред.час'), @(70, 'Масса на конец часа'),
        @(72, 'V р.у. на конец часа'), @(74, 'V ст.у. на конец часа'), @(80, 'Масса пред.сутки'), @(82, 'V р.у. пред.сутки'),
        @(84, 'V ст.у. пред.сутки'), @(90, 'Масса на конец суток'), @(92, 'V р.у. на конец суток'), @(94, 'V ст.у. на конец суток'))
    $described = @(); for ($i = 0; $i -lt 16; $i++) { if ((($mask -shr $i) -band 1) -eq 1) { $described += ($i + 1) } }
    $firstRaw = $true
    foreach ($tp in $described) {
        $base = 2000 + ($tp - 1) * 100
        Say ('--- ТП{0}' -f $tp)
        $blk = Try-Regs ('IR{0}..{1}' -f $base, ($base + 45)) 4 $base 46
        if ($null -ne $blk) {
            if ($firstRaw) { Say ('    сырые регистры +0..+11: ' + (Hex $blk[0..11])); $firstRaw = $false }
            foreach ($n in $names) { Say ('    +{0,-3} {1,-36} {2}' -f $n[0], $n[1], (Fmt (Get-F32 $blk $n[0]))) }
            Say ('    +{0,-3} {1,-36} {2}' -f 20, 'Время штатной работы, с', (Get-I32 $blk 20))
            Say ('    +{0,-3} {1,-36} {2}' -f 22, 'Время нештатных ситуаций, с', (Get-I32 $blk 22))
            Say ('    +{0,-3} {1,-36} {2}' -f 34, 'Количество ошибок', $blk[34])
            Say ('    +{0,-3} {1,-36} {2}' -f 35, 'Уровень критичности', $blk[35])
        }
        $blk2 = Try-Regs ('IR{0}..{1}' -f ($base + 60), ($base + 99)) 4 ($base + 60) 40
        if ($null -ne $blk2) {
            foreach ($n in $hist) { Say ('    +{0,-3} {1,-36} {2}' -f $n[0], $n[1], (Fmt (Get-F32 $blk2 ($n[0] - 60)))) }
        }
    }

    # 5. Логические входы
    Sect '5. Логические входы: IR 4000+(N-1)*4 (значение float, статус int16)'
    $bits = @('описан константой', 'ниже нижней уставки', 'выше верхней уставки', 'заменён резервным', 'ниже номинального диапазона',
        'выше номинального диапазона', 'обрыв датчика', 'КЗ датчика', 'неверная полярность', 'нет связи Modbus RTU', 'нет связи с ПИК-УВП', 'бит 11 (в карте не описан)')
    $readable = @(); $undescribed = @(); $failed = @()
    foreach ($n in 1..64) {
        $base = 4000 + ($n - 1) * 4
        try { $r = RdIR $base 3 }
        catch { if ($_.Exception.Message -match '^MB\d+$') { $failed += $n; continue }; throw }
        $st = $r[2]
        if ((($st -shr 11) -band 1) -eq 1) { $undescribed += $n; continue }
        $fl = @(); for ($i = 0; $i -lt 12; $i++) { if ((($st -shr $i) -band 1) -eq 1) { $fl += $bits[$i] } }
        Say ('  вх.{0,-2} IR{1}  значение={2}  статус=0x{3:X4} [{4}]' -f $n, $base, (Fmt (Get-F32 $r 0)), $st, ($fl -join ', '))
        $readable += $n
    }
    Say ('  Описанных входов: {0} [{1}]' -f $readable.Count, ($readable -join ', '))
    Say ('  Входы со статусом бит 11 (не описаны): {0} шт.' -f $undescribed.Count)
    if ($failed.Count -gt 0) { Say ('  Входы, на которые прибор ответил ошибкой Modbus: {0}..{1} ({2} шт.)' -f $failed[0], $failed[$failed.Count - 1], $failed.Count) }

    # 6. Карта параметров
    Sect '6. Карта параметров: HR7600/7601 -> имя HR7700.., значение HR7800..'
    foreach ($tp in $described) {
        Say ('--- Трубопровод {0}' -f $tp)
        for ($idx = 0; $idx -lt 120; $idx++) {
            try { $row = Get-ParamRow 7600 (($tp * 256) + $idx) }
            catch { if ($_.Exception.Message -match '^MB(\d+)$') { Say ('    строка {0}: ошибка Modbus {1}' -f $idx, $Matches[1]); break }; throw }
            if ($row[0] -eq '' -and $row[1] -eq '') { break }
            Say ('    {0,3} | {1,-45} | {2}' -f $idx, $row[0], $row[1])
        }
    }
    $skipped = 0
    foreach ($n in ($readable + $failed | Sort-Object)) {
        if ($failed -contains $n) { continue }
        Say ('--- Логический вход {0}' -f $n)
        for ($idx = 0; $idx -lt 120; $idx++) {
            try { $row = Get-ParamRow 7601 (($n * 256) + $idx) }
            catch { if ($_.Exception.Message -match '^MB(\d+)$') { Say ('    строка {0}: ошибка Modbus {1}' -f $idx, $Matches[1]); break }; throw }
            if ($row[0] -eq '' -and $row[1] -eq '') { break }
            Say ('    {0,3} | {1,-45} | {2}' -f $idx, $row[0], $row[1])
        }
    }
    if ($undescribed.Count -gt 0) {
        $n = $undescribed[0]
        Say ('--- Пример недописанного входа {0}' -f $n)
        try { $row = Get-ParamRow 7601 ($n * 256); Say ('    0 | {0} | {1}' -f $row[0], $row[1]) } catch {}
    }

    # 7. Нефть / КМХ / пробоотборник
    Sect '7. Узел учёта нефти: СИКН/БИК/линии (IR 3500+), итоги периодов (IR 15000+), управление (HR 17100+), КМХ (HR 18000+), пробоотборник (HR 18500+)'
    if ($NoOil) { Say '  пропущено (-NoOil)' }
    else {
        $groups = @{}
        $order = New-Object System.Collections.Generic.List[string]
        foreach ($e in $OilTable) {
            $kind = $e[0]; $addr = [int]$e[1]
            if ($kind -eq 'BASE') { $key = 'BASE|' + [math]::Floor($addr / 100) } else { $key = $kind + '|' + [math]::Floor($addr / 100) }
            if (-not $groups.ContainsKey($key)) { $groups[$key] = New-Object System.Collections.ArrayList; $order.Add($key) }
            [void]$groups[$key].Add($e)
        }
        foreach ($key in $order) {
            $items = $groups[$key]
            $kind = $key.Split('|')[0]
            $bases = @(0); if ($kind -eq 'BASE') { $bases = @(15000, 15500, 16000) }
            foreach ($b in $bases) {
                $title = $key; if ($kind -eq 'BASE') { $title = ('BASE={0}, блок +{1}00' -f $b, $key.Split('|')[1]) }
                $first = $items[0]
                $ok = $true
                try { [void](Read-Typed $first[0] ($b + [int]$first[1]) $first[2]) }
                catch {
                    if ($_.Exception.Message -match '^MB(\d+)$') { Say ('  {0,-34} нет (ошибка {1} на {2} {3})' -f $title, $Matches[1], $first[0], ($b + [int]$first[1])); $ok = $false }
                    else { throw }
                }
                if (-not $ok) { continue }
                Say ('  --- {0}' -f $title)
                $errs = 0
                foreach ($e in $items) {
                    $addr = $b + [int]$e[1]
                    try { $v = Read-Typed $e[0] $addr $e[2]; Say ('    {0,-4} {1,-6} {2,-7} {3,-44} {4}' -f $e[0], $addr, $e[2], $e[3], $v) }
                    catch {
                        if ($_.Exception.Message -match '^MB(\d+)$') { $errs++; if ($errs -le 3) { Say ('    {0,-4} {1,-6} {2,-7} {3,-44} ошибка Modbus {4}' -f $e[0], $addr, $e[2], $e[3], $Matches[1]) } }
                        else { throw }
                    }
                }
                if ($errs -gt 3) { Say ('    ... ещё ошибок: {0}' -f ($errs - 3)) }
            }
        }
    }

    # 8. Архив
    Sect '8. Пробный запрос архива (HR7900-7914), последние 2 часа'
    if ($NoArchive -or $described.Count -eq 0 -or $null -eq $devT) { Say '  пропущено' }
    else {
        $tp = $described[0]
        $end = $devT.Date.AddHours($devT.Hour)
        $start = $end.AddHours(-2)
        foreach ($mode in @('как в регистре часов', 'четырёхзначный год')) {
            $ys = $start.Year; $ye = $end.Year
            if ($mode -eq 'как в регистре часов' -and $yr -lt 100) { $ys = $ys - 2000; $ye = $ye - 2000 }
            if ($mode -eq 'четырёхзначный год' -and $yr -ge 100) { continue }
            $rid = Get-Random -Minimum 1 -Maximum 30000
            try {
                Write-Regs 7900 ([int[]]@($rid, $tp, $start.Day, $start.Month, $ys, $start.Hour, $start.Minute, $start.Second, $end.Day, $end.Month, $ye, $end.Hour, $end.Minute, $end.Second))
                Write-Regs 7914 ([int[]]@(7))
            } catch {
                if ($_.Exception.Message -match '^MB(\d+)$') { Say ('  [{0}] запись запроса отвергнута: ошибка {1}' -f $mode, $Matches[1]); continue }
                throw
            }
            Say ('  [{0}] запрос: ТП{1}, {2} .. {3}, год в регистрах {4}/{5}, опции=7, id={6}' -f $mode, $tp, $start.ToString('dd.MM.yyyy HH:mm:ss'), $end.ToString('dd.MM.yyyy HH:mm:ss'), $ys, $ye, $rid)
            $sw = [Diagnostics.Stopwatch]::StartNew(); $st = -1
            while ($sw.Elapsed.TotalSeconds -lt 25) {
                $st = (RdHR 8000 1)[0]
                if ($st -ne 1) { break }
                Start-Sleep -Milliseconds 400
            }
            Say ('  [{0}] статус HR8000 = {1} ({2} с)   (2 - готово, 4 - ошибка начального времени, 5 - конечного)' -f $mode, $st, (Fmt $sw.Elapsed.TotalSeconds))
            if ($st -eq 2 -and $mode -eq 'как в регистре часов') {
                $hd = RdHR 8001 2
                $len = $hd[1]
                Say ('  id в ответе = {0}, длина = {1} байт' -f $hd[0], $len)
                $need = [int][math]::Ceiling($len / 2)
                $all = New-Object System.Collections.Generic.List[int]
                $addr = 8003
                while ($all.Count -lt $need) {
                    $n = [math]::Min(120, $need - $all.Count)
                    foreach ($x in (RdHR $addr $n)) { $all.Add($x) }
                    $addr += $n
                }
                $txt = Get-CStr $all.ToArray()
                Say '  --- строка архива ---'
                foreach ($part in $txt.Split(';')) { Say ('    ' + ($part -replace "`r?`n", ' | ')) }
            }
        }
    }

    if ($TestLogin) { Test-Login }
    Say ''
    Say ('Переподключений за время опроса: {0}' -f $script:Reconnects)
}

# ---------- запуск ----------
if (-not $Out) { $Out = Join-Path (Get-Location) ('uvp_probe_{0}_{1}.txt' -f ($Ip -replace ':', '_'), (Get-Date -Format 'yyyyMMdd_HHmmss')) }
Say ('uvp_probe.ps1 v2: {0}:{1} unit={2}, {3}' -f $Ip, $Port, $Unit, (Get-Date -Format 's'))
Say ('PowerShell {0}' -f $PSVersionTable.PSVersion)
try {
    Connect-Dev
    Run-Probe
}
catch { Say ''; Say ('ОПРОС ПРЕРВАН: ' + $_.Exception.Message) }
finally {
    if ($null -ne $script:Client) { try { $script:Client.Close() } catch {} }
    [IO.File]::WriteAllText($Out, (($script:Lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($true)))
    Write-Host ''
    Write-Host ('Готово. Файл с результатом: ' + $Out)
}
