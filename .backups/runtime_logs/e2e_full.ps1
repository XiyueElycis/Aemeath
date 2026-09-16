Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;using System.Runtime.InteropServices;
public class WF {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
}
"@
$dir="D:\Python\游戏辅助安装智能体\.backups\runtime_logs"
$log="$dir\e2e_full.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
function GetWin(){
  foreach($el in $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)){
    if($el.Current.ClassName -eq "Window" -and $el.Current.Name -like "*Game Doctor*"){ return $el }
  }
  return $null
}
$win=GetWin
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$procId=$win.Current.ProcessId
[WF]::ShowWindow([IntPtr]$win.Current.NativeWindowHandle,9)|Out-Null
[WF]::SetForegroundWindow([IntPtr]$win.Current.NativeWindowHandle)|Out-Null
Start-Sleep -Milliseconds 900

$settingsName=[string]([char]0x8BBE)+[string]([char]0x7F6E)
$saveName=[string]([char]0x4FDD)+[string]([char]0x5B58)
$cbCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$liCond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
function FindName($parent,$name){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name)
  return $parent.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c)
}

function Switch-Save($index,$tag){
  $w=GetWin
  [WF]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle)|Out-Null
  Start-Sleep -Milliseconds 400
  $combos=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
  if($combos.Count -lt 2){
    $b=FindName $w $settingsName
    if($b){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep -Milliseconds 2200 }
    $combos=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cbCond)
  }
  $provider=$combos[0]; $model=$combos[1]
  $ep=$provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  if($ep.Current.ExpandCollapseState -ne [System.Windows.Automation.ExpandCollapseState]::Expanded){ $ep.Expand() }
  $cand=$null
  for($try=0;$try -lt 12;$try++){
    Start-Sleep -Milliseconds 350
    $raw=@()
    foreach($it in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$liCond)){
      if($it.Current.ProcessId -eq $procId -and $it.Current.Name -like "*ProviderInfo*"){
        $y=$it.Current.BoundingRectangle.Y
        if($y -gt 500 -and $y -lt 1180){ $raw += $it }
      }
    }
    # 按 Y 去重（UIA 镜像），保留每项一个
    $cand=@($raw | Sort-Object { $_.Current.BoundingRectangle.Y } | Group-Object { [int]$_.Current.BoundingRectangle.Y } | ForEach-Object { $_.Group[0] })
    if($cand.Count -ge 6){ break }
  }
  "[$tag] candidates=$($cand.Count)" | Out-File $log -Append
  for($k=0;$k -lt $cand.Count;$k++){ "    [$k] y=$([int]$cand[$k].Current.BoundingRectangle.Y)" | Out-File $log -Append }
  if($index -ge $cand.Count){ "[$tag] index out of range"; return }
  $cand[$index].GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
  Start-Sleep -Milliseconds 1300
  $mt=""
  try{ $mt=$model.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value }catch{}
  "[$tag] after select model='$mt'" | Out-File $log -Append
  $save=FindName $w $saveName
  $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  Start-Sleep -Milliseconds 2300
  try{ $s=Invoke-RestMethod "http://127.0.0.1:8765/settings" -TimeoutSec 3; "[$tag] BACKEND provider=$($s.llm.provider) model=$($s.llm.model)" | Out-File $log -Append }catch{ "[$tag] backend err $_" | Out-File $log -Append }
}

Switch-Save 0 "TO-DEEPSEEK"
Start-Sleep -Milliseconds 800
Switch-Save 1 "TO-ZHIPU"
"DONE" | Out-File $log -Append
