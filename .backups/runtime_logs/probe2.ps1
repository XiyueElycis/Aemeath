Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -AssemblyName System.Drawing,System.Windows.Forms
$log="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\uia_probe2.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
$root=[System.Windows.Automation.AutomationElement]::RootElement
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$win=$null
foreach($el in $all){ if($el.Current.ProcessId -eq 10884 -and $el.Current.ClassName -eq "Window"){ $win=$el; break } }
if(-not $win){ "NO WIN" | Out-File $log -Append; return }
$cc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ComboBox)
$combos=$win.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cc)
"combos=$($combos.Count)" | Out-File $log -Append
if($combos.Count -lt 1){ "NO COMBOS - panel closed?" | Out-File $log -Append; return }
$provider=$combos[0]
$r=$provider.Current.BoundingRectangle
"rect X=$([int]$r.X) Y=$([int]$r.Y) W=$([int]$r.Width) H=$([int]$r.Height)" | Out-File $log -Append
$ecp=$provider.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
"state before: $($ecp.Current.ExpandCollapseState)" | Out-File $log -Append
try { $ecp.Expand(); "Expand() called OK" | Out-File $log -Append } catch { "Expand threw: $_" | Out-File $log -Append }
Start-Sleep -Milliseconds 700
"state after: $($ecp.Current.ExpandCollapseState)" | Out-File $log -Append
# 1) combo 子树内所有控件
$sub=$provider.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
"subtree controls: $($sub.Count)" | Out-File $log -Append
foreach($e in $sub){ "  SUB [{0}] '{1}'" -f $e.Current.ControlType.ProgrammaticName,$e.Current.Name | Out-File $log -Append }
# 2) 全局 popup/list
$lc=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
$items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$lc)
$n=0
foreach($it in $items){ if($it.Current.ProcessId -eq 10884){ "  GLOBAL ListItem: $($it.Current.Name)" | Out-File $log -Append; $n++ } }
"global listitems(pid): $n" | Out-File $log -Append
# 截图
$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.X,$vs.Y,0,0,(New-Object System.Drawing.Size($vs.Width,$vs.Height)))
$shot="D:\Python\游戏辅助安装智能体\.backups\runtime_logs\provider_expand2.png"
$bmp.Save($shot,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bmp.Dispose()
"SHOT $shot ($($vs.Width)x$($vs.Height))" | Out-File $log -Append
