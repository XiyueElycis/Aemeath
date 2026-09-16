Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class Win32 {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint dx,uint dy,uint d,IntPtr e);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 public const uint LD=0x02, LU=0x04;
}
"@
$log="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\uia_probe.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$win=$null
foreach($el in $all){ if($el.Current.ProcessId -eq 10884 -and $el.Current.ClassName -eq "Window"){ $win=$el; break } }
if(-not $win){ "WINDOW NOT FOUND" | Out-File $log -Append; return }
$hwnd=[IntPtr]$win.Current.NativeWindowHandle
[Win32]::ShowWindow($hwnd,9) | Out-Null
[Win32]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 500
function Find-All($parent,$type){ $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,$type); return $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$c) }
# 检查面板状态
$combos=Find-All $win ([System.Windows.Automation.ControlType]::ComboBox)
"combos visible initially: $($combos.Count)" | Out-File $log -Append
$needOpen=$true
if($combos.Count -ge 1){ $needOpen = $combos[0].Current.IsOffscreen }
if($needOpen){
  $bc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,"设置")
  $b=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$bc)
  $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  Start-Sleep -Milliseconds 1200
  $combos=Find-All $win ([System.Windows.Automation.ControlType]::ComboBox)
}
"combos after open: $($combos.Count)" | Out-File $log -Append
if($combos.Count -lt 1){ "NO COMBOS" | Out-File $log -Append; return }
$provider=$combos[0]
$r=$provider.Current.BoundingRectangle
"provider combo rect: X=$([int]$r.X) Y=$([int]$r.Y) W=$([int]$r.Width) H=$([int]$r.Height) enabled=$($provider.Current.IsEnabled)" | Out-File $log -Append
# 真实鼠标点击下拉框中央
$cx=[int]($r.X+$r.Width/2); $cy=[int]($r.Y+$r.Height/2)
[Win32]::SetCursorPos($cx,$cy); Start-Sleep -Milliseconds 200
[Win32]::mouse_event([Win32]::LD,0,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 60
[Win32]::mouse_event([Win32]::LU,0,0,0,[IntPtr]::Zero)
Start-Sleep -Milliseconds 900
# 全局枚举 ListItem
$items=Find-All $root ([System.Windows.Automation.ControlType]::ListItem)
$cnt=0
foreach($it in $items){ if($it.Current.ProcessId -eq 10884){ $n=$it.Current.Name; if($n -like "*深度*" -or $n -like "*智谱*" -or $n -like "*通义*" -or $n -like "*Kimi*" -or $n -like "*豆包*" -or $n -like "*MiniMax*" -or $n -like "*硅基*" -or $n -like "*Ollama*"){ "  ITEM: $n"; $cnt++ } } }
"provider items found in popup: $cnt" | Out-File $log -Append
# 截图整个虚拟屏
$b=[System.Windows.Forms.SystemInformation]::VirtualScreen 2>$null
Add-Type -AssemblyName System.Windows.Forms
$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.X,$vs.Y,0,0,(New-Object System.Drawing.Size($vs.Width,$vs.Height)))
$shot="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\provider_click.png"
$bmp.Save($shot,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bmp.Dispose()
"SHOT $shot" | Out-File $log -Append
