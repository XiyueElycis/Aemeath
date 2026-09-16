Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W32 {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
}
"@
$log="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\save_probe.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$win=$null
foreach($el in $all){ if($el.Current.ProcessId -eq 10884 -and $el.Current.ClassName -eq "Window"){ $win=$el; break } }
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$hwnd=[IntPtr]$win.Current.NativeWindowHandle
[W32]::ShowWindow($hwnd,9) | Out-Null
[W32]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 600
function FC($parent,$name){ $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name); return $parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c) }
# 若面板关着则打开
$save=FC $win "保存"
if(-not $save){
  $b=FC $win "设置"; if($b){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 1500 }
  $save=FC $win "保存"
}
if(-not $save){ "SAVE BUTTON NOT FOUND" | Out-File $log -Append; return }
"save button found, enabled=$($save.Current.IsEnabled) off=$($save.Current.IsOffscreen)" | Out-File $log -Append
# 记录当前顶层窗口（检测弹窗）
function TopWindows { $t=@(); foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){ if($el.Current.ProcessId -eq 10884){ $t+=$el.Current.Name } }; return $t }
"before windows: $((TopWindows) -join ' | ')" | Out-File $log -Append
try {
  $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  "Invoke() returned" | Out-File $log -Append
} catch { "Invoke threw: $_" | Out-File $log -Append }
Start-Sleep -Milliseconds 2500
"after windows: $((TopWindows) -join ' | ')" | Out-File $log -Append
# 查找消息框文本
foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
  if($el.Current.ProcessId -eq 10884 -and $el.Current.Name -ne "Game Doctor 智能体控制台"){
    "POPUP WINDOW: $($el.Current.Name)" | Out-File $log -Append
    $txt=$el.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Text)))
    foreach($t in $txt){ "  TXT: $($t.Current.Name)" | Out-File $log -Append }
  }
}
"DONE" | Out-File $log -Append
