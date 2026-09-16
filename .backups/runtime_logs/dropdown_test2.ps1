Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W32b {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
$dir="D:\Python\游戏辅助安装智能体\.backups\runtime_logs"
$log="$dir\dropdown_test2.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$win=$null
foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
  if($el.Current.ClassName -eq "Window" -and $el.Current.Name -like "*Game Doctor*"){ $win=$el; break }
}
if(-not $win){ "NO WINDOW" | Out-File $log -Append; return }
$procId=$win.Current.ProcessId
"target pid=$procId" | Out-File $log -Append
$hwnd=[IntPtr]$win.Current.NativeWindowHandle
[W32b]::ShowWindow($hwnd,9)|Out-Null
[W32b]::SetForegroundWindow($hwnd)|Out-Null
Start-Sleep -Milliseconds 1200
"foreground hwnd=$([W32b]::GetForegroundWindow()) target=$hwnd" | Out-File $log -Append

function FindElem($parent,$name){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name)
  return $parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c)
}
function Shot($file){
  $vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp=New-Object System.Drawing.Bitmap($vs.Width,$vs.Height)
  $g=[System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($vs.Left,$vs.Top,0,0,$bmp.Size)
  $bmp.Save($file,[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose();$bmp.Dispose()
}

# 打开设置（若已开则跳过）
$cbCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
if($combos.Count -lt 2){
  $btn=FindElem $win "设置"
  if($btn){ $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 2200 }
  $combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
}
"combos=$($combos.Count)" | Out-File $log -Append

$liCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)

function TestCombo($combo,$tag,$shotfile){
  $r=$combo.Current.BoundingRectangle
  "$tag rect=($([int]$r.X),$([int]$r.Y),$([int]$r.Width),$([int]$r.Height)) enabled=$($combo.Current.IsEnabled)" | Out-File $log -Append
  $ep=$combo.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  "  state-before=$($ep.Current.ExpandCollapseState)" | Out-File $log -Append
  $ep.Expand()
  Start-Sleep -Milliseconds 900
  "  state-after-expand=$($ep.Current.ExpandCollapseState)" | Out-File $log -Append
  # 立即截图（不切前台）
  try{ Shot $shotfile; "  shot ok" | Out-File $log -Append }catch{ "  shot fail $_" | Out-File $log -Append }
  # 立即全局枚举
  $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$liCond)
  "  GLOBAL ListItem=$($all.Count)" | Out-File $log -Append
  $i=0
  foreach($it in $all){
    $n=$it.Current.Name; $pid2=$it.Current.ProcessId
    $rr=$it.Current.BoundingRectangle
    # 只打印属于本进程 或 非空名 的前若干
    if($pid2 -eq $procId){
      "    item='$n' cls='$($it.Current.ClassName)' off=$($it.Current.IsOffscreen) y=$([int]$rr.Y)" | Out-File $log -Append
      $i++
      if($i -ge 40){ break }
    }
  }
  "  items printed=$i" | Out-File $log -Append
  try{ $ep.Collapse() }catch{}
  Start-Sleep -Milliseconds 500
}

TestCombo $combos[0] "PROVIDER" "$dir\dd_provider.png"
TestCombo $combos[1] "MODEL" "$dir\dd_model.png"
"DONE" | Out-File $log -Append
