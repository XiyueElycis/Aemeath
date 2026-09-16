Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W32 {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
}
"@
$log="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\dropdown_test.txt"
$shot="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\dropdown_test.png"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$win=$null
foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
  if($el.Current.ProcessId -eq 3136 -and $el.Current.ClassName -eq "Window"){ $win=$el; break }
}
if(-not $win){ "NO WINDOW for 3136" | Out-File $log -Append; return }
$hwnd=[IntPtr]$win.Current.NativeWindowHandle
[W32]::ShowWindow($hwnd,9)|Out-Null
[W32]::SetForegroundWindow($hwnd)|Out-Null
Start-Sleep -Milliseconds 1500

function FindElem($parent,$name){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name)
  return $parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c)
}

# 打开设置面板
$btn=FindElem $win "设置"
if($btn){ "settings btn enabled=$($btn.Current.IsEnabled)" | Out-File $log -Append; $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 2200 }
else { "NO 设置 BUTTON" | Out-File $log -Append }

# 找两个 ComboBox
$cbCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
"combos found=$($combos.Count)" | Out-File $log -Append
$provider=$combos[0]
$r=$provider.Current.BoundingRectangle
"provider combo rect=($([int]$r.X),$([int]$r.Y),$([int]$r.Width),$([int]$r.Height)) enabled=$($provider.Current.IsEnabled)" | Out-File $log -Append

# 展开前 combo 子树里的项数
$liCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
"before-expand child ListItems=$($provider.FindAll([System.Windows.Automation.TreeScope]::Subtree,$liCond).Count)" | Out-File $log -Append

$expanded=$false
try{
  $ep=$provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  "expand state before=$($ep.Current.ExpandCollapseState)" | Out-File $log -Append
  $ep.Expand()
  $expanded=$true
  "Expand() called" | Out-File $log -Append
}catch{ "no ExpandCollapsePattern: $_" | Out-File $log -Append }
Start-Sleep -Milliseconds 1200

# 再前置一次，确保 popup 在最前
[W32]::SetForegroundWindow($hwnd)|Out-Null
Start-Sleep -Milliseconds 600

# 全局枚举 ListItem，过滤厂商
$kw="深度|智谱|通义|Kimi|豆包|MiniMax|硅基|Ollama|DeepSeek|glm|qwen|moonshot|doubao"
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$liCond)
"GLOBAL ListItem total=$($all.Count)" | Out-File $log -Append
$hit=0
foreach($it in $all){
  $n=$it.Current.Name
  if($n -match $kw){
    $rr=$it.Current.BoundingRectangle
    "  PROVIDER-ITEM: '$n' enabled=$($it.Current.IsEnabled) off=$($it.Current.IsOffscreen) rect=($([int]$rr.X),$([int]$rr.Y),$([int]$rr.Width),$([int]$rr.Height))" | Out-File $log -Append
    $hit++
  }
}
"provider items matched=$hit" | Out-File $log -Append
"after-expand combo-subtree ListItems=$($provider.FindAll([System.Windows.Automation.TreeScope]::Subtree,$liCond).Count)" | Out-File $log -Append

# 截图
try{
  $vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp=New-Object System.Drawing.Bitmap($vs.Width,$vs.Height)
  $g=[System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($vs.Left,$vs.Top,0,0,$bmp.Size)
  $bmp.Save($shot,[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose();$bmp.Dispose()
  "screenshot saved" | Out-File $log -Append
}catch{ "screenshot failed: $_" | Out-File $log -Append }

# 收起
if($expanded){ try{ $provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() }catch{} }
"DONE" | Out-File $log -Append
