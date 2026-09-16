Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;using System.Text;using System.Collections.Generic;using System.Runtime.InteropServices;
public class WinEnum {
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern IntPtr GetWindowLong(IntPtr h, int idx);
 public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
 public struct RECT{public int L,T,R,B;}
 public static List<string> ForPid(uint target){
   var outp=new List<string>();
   EnumWindows((h,l)=>{
     uint pid; GetWindowThreadProcessId(h,out pid);
     if(pid==target){
       var cn=new StringBuilder(256); GetClassName(h,cn,256);
       var tx=new StringBuilder(256); GetWindowText(h,tx,256);
       RECT r; GetWindowRect(h,out r);
       long style=GetWindowLong(h,-16).ToInt64();
       outp.Add(string.Format("hwnd={0} class='{1}' title='{2}' vis={3} rect=({4},{5},{6},{7}) ws=0x{8:X8}",
         h,cn,tx,IsWindowVisible(h),r.L,r.T,r.R,r.B,style));
     }
     return true;
   },IntPtr.Zero);
   return outp;
 }
}
"@
$dir="D:\Python\游戏辅助安装智能体\.backups\runtime_logs"
$log="$dir\dropdown_test3.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$win=$null
foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
  if($el.Current.ClassName -eq "Window" -and $el.Current.Name -like "*Game Doctor*"){ $win=$el; break }
}
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$procId=$win.Current.ProcessId
"target pid=$procId" | Out-File $log -Append
# 恢复并前置
Add-Type @"
using System;using System.Runtime.InteropServices;
public class WF3 { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@
[WF3]::ShowWindow([IntPtr]$win.Current.NativeWindowHandle,9)|Out-Null
[WF3]::SetForegroundWindow([IntPtr]$win.Current.NativeWindowHandle)|Out-Null
Start-Sleep -Milliseconds 1200

$cbCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
if($combos.Count -lt 2){
  $bc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,"设置")
  $b=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$bc)
  if($b){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 2200 }
  $combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
}
"combos=$($combos.Count)" | Out-File $log -Append
$provider=$combos[0]
$ep=$provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)

"--- top-level windows BEFORE expand ---" | Out-File $log -Append
[WinEnum]::ForPid($procId) | ForEach-Object { $_ | Out-File $log -Append }

$ep.Expand()
Start-Sleep -Milliseconds 1000
"state=$($ep.Current.ExpandCollapseState)" | Out-File $log -Append

"--- top-level windows AFTER expand ---" | Out-File $log -Append
[WinEnum]::ForPid($procId) | ForEach-Object { $_ | Out-File $log -Append }

# Raw 视图：combo 全部后代的控件类型
"--- combo RAW descendants ---" | Out-File $log -Append
$walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
function Walk($el,$depth){
  if($depth -gt 6){return}
  $c=$el.Current
  $indent="  "*$depth
  "$indent$($c.ControlType.ProgrammaticName) name='$($c.Name)' cls='$($c.ClassName)' off=$($c.IsOffscreen)" | Out-File $log -Append
  $ch=$walker.GetFirstChild($el)
  while($ch -ne $null){ Walk $ch ($depth+1); $ch=$walker.GetNextSibling($ch) }
}
Walk $provider 0

# 全局 Raw 找本进程的 List / Menu / Pane（popup 容器）
"--- global RAW List/Menu/Pane for pid 3136 ---" | Out-File $log -Append
$allRaw=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
foreach($it in $allRaw){
  if($it.Current.ProcessId -ne $procId){continue}
  $ct=$it.Current.ControlType.ProgrammaticName
  if($ct -match "List|Menu|Pane|Popup|ToolTip|Window"){
    $r=$it.Current.BoundingRectangle
    "  $ct name='$($it.Current.Name)' cls='$($it.Current.ClassName)' off=$($it.Current.IsOffscreen) rect=($([int]$r.X),$([int]$r.Y),$([int]$r.Width),$([int]$r.Height))" | Out-File $log -Append
  }
}
try{$ep.Collapse()}catch{}
"DONE" | Out-File $log -Append
