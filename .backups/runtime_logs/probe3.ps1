Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
$log="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\uia_probe3.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$win=$null
foreach($el in $all){ if($el.Current.ProcessId -eq 10884 -and $el.Current.ClassName -eq "Window"){ $win=$el; break } }
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$cc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cc)
"combos before: $($combos.Count)" | Out-File $log -Append
if($combos.Count -lt 2){
  $bc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,"设置")
  $b=$win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$bc)
  if($b){ $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "invoked settings" | Out-File $log -Append }
  Start-Sleep -Milliseconds 1500
  $combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cc)
  "combos after invoke: $($combos.Count)" | Out-File $log -Append
}
for($i=0;$i -lt [Math]::Min($combos.Count,3);$i++){
  $cb=$combos[$i]
  $r=$cb.Current.BoundingRectangle
  "=== combo[$i] rect X=$([int]$r.X) Y=$([int]$r.Y) W=$([int]$r.Width) H=$([int]$r.Height) off=$($cb.Current.IsOffscreen)" | Out-File $log -Append
  $vp=""; try{ $vp=$cb.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value }catch{}
  "    value='$vp'" | Out-File $log -Append
  try{
    $e=$cb.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
    $e.Expand(); Start-Sleep -Milliseconds 500
    "    state=$($e.Current.ExpandCollapseState)" | Out-File $log -Append
  } catch { "    no ECP: $_" | Out-File $log -Append }
  $sub=$cb.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
  foreach($s in $sub){
    if($s.Current.ControlType.ProgrammaticName -eq "ControlType.ListItem" -or $s.Current.ControlType.ProgrammaticName -eq "ControlType.List"){
      "      [$($s.Current.ControlType.ProgrammaticName)] '$($s.Current.Name)'" | Out-File $log -Append
    }
  }
  Start-Sleep -Milliseconds 200
}
# 全局再找一次本进程 ListItem
$lc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
$g=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$lc)
$names=@()
foreach($it in $g){ if($it.Current.ProcessId -eq 10884){ $names+=$it.Current.Name } }
"GLOBAL listitems: $($names.Count)"; $names | Select-Object -First 20 | ForEach-Object { "  G: $_" | Out-File $log -Append }
$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height
$gr=[System.Drawing.Graphics]::FromImage($bmp)
$gr.CopyFromScreen($vs.X,$vs.Y,0,0,(New-Object System.Drawing.Size($vs.Width,$vs.Height)))
$shot="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\provider_expand3.png"
$bmp.Save($shot,[System.Drawing.Imaging.ImageFormat]::Png); $gr.Dispose();$bmp.Dispose()
"SHOT $shot ($($vs.Width)x$($vs.Height))" | Out-File $log -Append
