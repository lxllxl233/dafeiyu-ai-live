param([int]$x, [int]$y, [int]$times = 2)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class M {
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
$h = (Get-Process -Id 41396).MainWindowHandle
[void][M]::ShowWindow($h, 9)
Start-Sleep -Milliseconds 300
[void][M]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 500
for ($i = 0; $i -lt $times; $i++) {
  [void][M]::SetCursorPos($x, $y)
  Start-Sleep -Milliseconds 200
  [M]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 80
  [M]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 400
}
"fg=$([M]::GetForegroundWindow()) target=$h clicked $x,$y x$times"
