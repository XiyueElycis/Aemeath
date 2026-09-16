Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;using System.Runtime.InteropServices;
public class WE {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
}
"@
$dir="D:\Python\游戏辅助安装智能体\.backups\runtime_logs"
$log="$dir\e2e_switch.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$win=$null
foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
  if($el.Current.ClassName -eq "Window" -and $el.Current.Name -like "*Game Doctor*"){ $win=$el; break }
}
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$procId=$win.Current.ProcessId
[WE]::ShowWindow([IntPtr]$win.Current.NativeWindowHandle,9)|Out-Null
[WE]::SetForegroundWindow([IntPtr]$win.Current.NativeWindowHandle)|Out-Null
Start-Sleep -Milliseconds 1000

$settingsName=[string]([char]0x8BBE)+[string]([char]0x7F6E)   # 设置
$saveName=[string]([char]0x4FDD)+[string]([char]0x5B58)       # 保存

function FindName($parent,$name){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name)
  return $parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c)
}
$cbCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$liCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)

$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
if($combos.Count -lt 2){
  $b=FindName $win $settingsName
  if($b){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 2200 }
  $combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
}
"combos=$($combos.Count)" | Out-File $log -Append
$provider=$combos[0]; $model=$combos[1]

$ep=$provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
if($ep.Current.ExpandCollapseState -ne [System.Windows.Automation.ExpandCollapseState]::Expanded){ $ep.Expand() }
for($i=0;$i -lt 15;$i++){
  Start-Sleep -Milliseconds 200
  if($ep.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded){ break }
}
"provider state=$($ep.Current.ExpandCollapseState)" | Out-File $log -Append
Start-Sleep -Milliseconds 700

# 截图服务商下拉
try{
  Add-Type -AssemblyName System.Drawing,System.Windows.Forms
  $vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp=New-Object System.Drawing.Bitmap($vs.Width,$vs.Height)
  $g=[System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($vs.Left,$vs.Top,0,0,$bmp.Size)
  $bmp.Save("$dir\dd_provider_fixed.png",[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose();$bmp.Dispose()
}catch{}

# popup 中的服务商项：对象类型 ProviderInfo，UIA 名为类名；展开时可见
$cand=@()
foreach($it in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$liCond)){
  if($it.Current.ProcessId -eq $procId -and $it.Current.Name -like "*ProviderInfo*" -and -not $it.Current.IsOffscreen){
    $cand += $it
  }
}
$cand=@($cand | Sort-Object { $_.Current.BoundingRectangle.Y })
"PROVIDER ITEMS ($($cand.Count)):" | Out-File $log -Append
for($k=0;$k -lt $cand.Count;$k++){ "  [$k] y=$([int]$cand[$k].Current.BoundingRectangle.Y)" | Out-File $log -Append }

# 选最上方一项（catalog 首位 DeepSeek；当前选中为第 2 项智谱）
$picked=$null
if($cand.Count -ge 1){ $picked=$cand[0]; "pick top item y=$([int]$picked.Current.BoundingRectangle.Y)" | Out-File $log -Append }
if($picked){
  try{ $picked.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); "Select() ok" | Out-File $log -Append }
  catch{ "Select failed: $_" | Out-File $log -Append }
} else { "no provider item" | Out-File $log -Append }
Start-Sleep -Milliseconds 1300

$modelText=""
try{ $vp=$model.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); $modelText=$vp.Current.Value }catch{ $modelText="<no value>" }
"after pick: model='$modelText'" | Out-File $log -Append

$save=FindName $win $saveName
if($save){
  "save enabled=$($save.Current.IsEnabled)" | Out-File $log -Append
  $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  "save invoked" | Out-File $log -Append
}
Start-Sleep -Milliseconds 2200
try{
  $s=Invoke-RestMethod "http://127.0.0.1:8765/settings" -TimeoutSec 3
  "BACKEND NOW: provider=$($s.llm.provider) model=$($s.llm.model) base=$($s.llm.api_base)" | Out-File $log -Append
}catch{ "backend query failed: $_" | Out-File $log -Append }
"DONE-PHASE1" | Out-File $log -Append
