using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace GameDoctor.Desktop;

/// <summary>
/// 任务授权对话框（沙箱三段式·第一段）：列出所有待决请求，逐条批准/拒绝，
/// 或一键全部批准。每次裁决后重新拉取 pending，清空后自动关闭。
/// </summary>
public class ApprovalDialog : Window
{
    private static readonly Brush SecondaryBrush =
        (Brush)Application.Current.Resources["TextSecondaryBrush"];
    private static readonly Brush AccentBrush =
        (Brush)Application.Current.Resources["AccentDeepBrush"];

    private readonly AgentApiClient _client;
    private readonly StackPanel _listHost;
    private readonly Button _approveAllButton;
    private List<ApprovalInfo> _items = new();

    /// <summary>智能体内部名 → 中文显示名（已知者）。</summary>
    private static readonly Dictionary<string, string> AgentNames = new()
    {
        ["script_editor"] = "脚本编辑",
        ["web_search"] = "联网搜索",
        ["installation"] = "安装执行",
        ["log_analyzer"] = "日志分析",
        ["perf_security"] = "性能安全",
        ["game_content"] = "游戏内容管理",
    };

    public ApprovalDialog(AgentApiClient client, List<ApprovalInfo> items)
    {
        _client = client;

        Title = "任务授权请求";
        Width = 580;
        Height = 500;
        MinWidth = 440;
        MinHeight = 320;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (Brush)Application.Current.Resources["BgBrush"];
        FontFamily = new FontFamily("Microsoft YaHei UI");
        FontSize = 13;

        var root = new DockPanel { Margin = new Thickness(18) };

        // 顶部：后果说明
        var intro = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
        intro.Children.Add(new TextBlock
        {
            Text = "智能体请求执行以下任务，请逐项授权：",
            FontWeight = FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        intro.Children.Add(new TextBlock
        {
            Text = "批准 → 任务在沙箱副本中执行；拒绝 → 任务跳过。未处理的请求将在 10 分钟后自动拒绝。",
            Foreground = SecondaryBrush,
            FontSize = 11.5,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 4, 0, 0),
        });
        DockPanel.SetDock(intro, Dock.Top);
        root.Children.Add(intro);

        // 底部：全部批准 / 稍后处理
        var bottom = new DockPanel { Margin = new Thickness(0, 12, 0, 0) };
        _approveAllButton = new Button
        {
            Content = "全部批准",
            MinWidth = 110,
            Style = (Style)Application.Current.Resources["NeuAccentButtonStyle"],
        };
        _approveAllButton.Click += async (_, _) => await ApproveAllAsync();
        DockPanel.SetDock(_approveAllButton, Dock.Right);
        bottom.Children.Add(_approveAllButton);

        var laterButton = new Button { Content = "稍后处理", MinWidth = 96, Margin = new Thickness(0, 0, 8, 0) };
        laterButton.Click += (_, _) => DialogResult = false;
        DockPanel.SetDock(laterButton, Dock.Right);
        bottom.Children.Add(laterButton);
        DockPanel.SetDock(bottom, Dock.Bottom);
        root.Children.Add(bottom);

        // 中部：请求卡片列表
        var scroll = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = _listHost = new StackPanel(),
        };
        root.Children.Add(scroll);

        Content = root;
        Render(items);
    }

    private void Render(List<ApprovalInfo> items)
    {
        _items = items;
        _listHost.Children.Clear();
        _approveAllButton.IsEnabled = items.Count > 0;
        if (items.Count == 0)
        {
            _listHost.Children.Add(new TextBlock
            {
                Text = "（没有待决请求）",
                Foreground = SecondaryBrush,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 24, 0, 0),
            });
            return;
        }
        foreach (var info in items)
        {
            _listHost.Children.Add(BuildCard(info));
        }
    }

    private UIElement BuildCard(ApprovalInfo info)
    {
        var card = new NeuPanel
        {
            Margin = new Thickness(0, 0, 0, 10),
            Padding = new Thickness(14, 10, 14, 12),
            CornerRadius = new CornerRadius(12),
        };
        var panel = new StackPanel();

        // 行首：性质标签 + 智能体/操作
        var head = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };
        var tag = new Border
        {
            Background = KindBrush(info.Kind),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(8, 2, 8, 2),
            Child = new TextBlock
            {
                Text = KindLabel(info.Kind),
                Foreground = Brushes.White,
                FontSize = 11.5,
                FontWeight = FontWeights.SemiBold,
            },
        };
        DockPanel.SetDock(tag, Dock.Left);
        head.Children.Add(tag);
        head.Children.Add(new TextBlock
        {
            Text = $"  {AgentLabel(info.Agent)} · {info.Operation}",
            FontWeight = FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        });
        panel.Children.Add(head);

        if (!string.IsNullOrWhiteSpace(info.Summary))
        {
            panel.Children.Add(new TextBlock
            {
                Text = info.Summary,
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 2, 0, 0),
            });
        }
        if (!string.IsNullOrWhiteSpace(info.Target))
        {
            panel.Children.Add(new TextBlock
            {
                Text = "目标：" + info.Target,
                TextWrapping = TextWrapping.Wrap,
                Foreground = SecondaryBrush,
                FontSize = 11.5,
                Margin = new Thickness(0, 3, 0, 0),
            });
        }

        var buttons = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 8, 0, 0),
        };
        var rejectButton = new Button { Content = "拒绝", MinWidth = 72, Padding = new Thickness(12, 5, 12, 5) };
        var approveButton = new Button
        {
            Content = "批准",
            MinWidth = 72,
            Margin = new Thickness(8, 0, 0, 0),
            Padding = new Thickness(12, 5, 12, 5),
            Style = (Style)Application.Current.Resources["NeuAccentButtonStyle"],
        };
        rejectButton.Click += async (_, _) => await DecideOneAsync(info.Id, false, card);
        approveButton.Click += async (_, _) => await DecideOneAsync(info.Id, true, card);
        buttons.Children.Add(rejectButton);
        buttons.Children.Add(approveButton);
        panel.Children.Add(buttons);

        card.Content = panel;
        return card;
    }

    private async Task DecideOneAsync(string id, bool approve, UIElement card)
    {
        SetCardEnabled(card, false);
        try
        {
            await _client.DecideApprovalAsync(id, approve);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "裁决失败：" + ex.Message, "授权",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            SetCardEnabled(card, true);
            return;
        }
        await RefreshAsync();
    }

    private async Task ApproveAllAsync()
    {
        var snapshot = _items.ToList();
        _approveAllButton.IsEnabled = false;
        foreach (var info in snapshot)
        {
            try
            {
                await _client.DecideApprovalAsync(info.Id, true);
            }
            catch
            {
                // 单个失败（已超时/已被别处处理）不影响其余，最终以重新拉取为准
            }
        }
        await RefreshAsync();
    }

    private async Task RefreshAsync()
    {
        try
        {
            var fresh = await _client.GetPendingApprovalsAsync();
            if (fresh.Count == 0)
            {
                DialogResult = true;
                return;
            }
            Render(fresh);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "刷新待决请求失败：" + ex.Message, "授权",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            _approveAllButton.IsEnabled = true;
        }
    }

    private static void SetCardEnabled(UIElement card, bool enabled)
    {
        if (card is not NeuPanel panel || panel.Content is not StackPanel sp) return;
        if (sp.Children.Count > 0 && sp.Children[^1] is StackPanel buttons)
        {
            foreach (var child in buttons.Children)
            {
                if (child is Button b) b.IsEnabled = enabled;
            }
        }
    }

    private static string AgentLabel(string agent) =>
        AgentNames.TryGetValue(agent, out var name) ? name : agent;

    private static string KindLabel(string kind) => kind switch
    {
        "read" => "只读",
        "write" => "写入",
        "download" => "下载",
        "install" => "安装",
        _ => string.IsNullOrEmpty(kind) ? "任务" : kind,
    };

    private static Brush KindBrush(string kind) => kind switch
    {
        "write" => new SolidColorBrush(Color.FromRgb(0xC2, 0x41, 0x0C)),
        "download" => new SolidColorBrush(Color.FromRgb(0x7C, 0x3A, 0xED)),
        "install" => new SolidColorBrush(Color.FromRgb(0xBE, 0x12, 0x3C)),
        _ => new SolidColorBrush(Color.FromRgb(0x25, 0x63, 0xEB)), // 只读/未知：蓝
    };
}

/// <summary>
/// 沙箱变更审核窗口（三段式·第三段）：列出 ready 会话的变更，
/// 文本类变更新旧内容并排只读预览；应用（先备份）或丢弃。
/// </summary>
public class SandboxReviewWindow : Window
{
    private static readonly Brush SecondaryBrush =
        (Brush)Application.Current.Resources["TextSecondaryBrush"];

    private readonly AgentApiClient _client;
    private readonly ComboBox _ticketCombo;
    private readonly TextBlock _rootText;
    private readonly ListBox _changeList;
    private readonly TextBox _oldBox;
    private readonly TextBox _newBox;
    private readonly Button _applyButton;
    private readonly Button _discardButton;
    private readonly TextBlock _resultText;

    private List<PendingSandboxInfo> _pending = new();
    private SandboxInfo? _detail;
    private bool _loading;

    public SandboxReviewWindow(AgentApiClient client, string? selectTicketId)
    {
        _client = client;

        Title = "沙箱变更审核";
        Width = 960;
        Height = 660;
        MinWidth = 680;
        MinHeight = 460;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (Brush)Application.Current.Resources["BgBrush"];
        FontFamily = new FontFamily("Microsoft YaHei UI");
        FontSize = 13;

        var root = new DockPanel { Margin = new Thickness(18) };

        // 顶部：会话选择
        var top = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
        var selector = new DockPanel();
        var title = new TextBlock
        {
            Text = "待审核会话：",
            VerticalAlignment = VerticalAlignment.Center,
            FontWeight = FontWeights.SemiBold,
        };
        DockPanel.SetDock(title, Dock.Left);
        selector.Children.Add(title);
        var refreshButton = new Button { Content = "刷新", MinWidth = 64, Padding = new Thickness(10, 5, 10, 5) };
        refreshButton.Click += async (_, _) => await RefreshPendingAsync(null);
        DockPanel.SetDock(refreshButton, Dock.Right);
        selector.Children.Add(refreshButton);
        _ticketCombo = new ComboBox { Margin = new Thickness(8, 0, 8, 0) };
        _ticketCombo.SelectionChanged += async (_, _) => await OnTicketSelectedAsync();
        selector.Children.Add(_ticketCombo);
        top.Children.Add(selector);
        _rootText = new TextBlock
        {
            Foreground = SecondaryBrush,
            FontSize = 11.5,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 6, 0, 0),
        };
        top.Children.Add(_rootText);
        DockPanel.SetDock(top, Dock.Top);
        root.Children.Add(top);

        // 底部：操作
        var bottom = new DockPanel { Margin = new Thickness(0, 12, 0, 0) };
        _applyButton = new Button
        {
            Content = "应用全部变更",
            MinWidth = 130,
            Style = (Style)Application.Current.Resources["NeuAccentButtonStyle"],
        };
        _applyButton.Click += async (_, _) => await ApplyAsync();
        DockPanel.SetDock(_applyButton, Dock.Right);
        bottom.Children.Add(_applyButton);
        _discardButton = new Button { Content = "丢弃", MinWidth = 96, Margin = new Thickness(0, 0, 8, 0) };
        _discardButton.Click += async (_, _) => await DiscardAsync();
        DockPanel.SetDock(_discardButton, Dock.Right);
        bottom.Children.Add(_discardButton);
        _resultText = new TextBlock
        {
            VerticalAlignment = VerticalAlignment.Center,
            TextWrapping = TextWrapping.Wrap,
            Foreground = SecondaryBrush,
        };
        bottom.Children.Add(_resultText);
        DockPanel.SetDock(bottom, Dock.Bottom);
        root.Children.Add(bottom);

        // 中部：变更列表 + 新旧内容
        var center = new Grid();
        center.RowDefinitions.Add(new RowDefinition
        {
            Height = new GridLength(220),
            MinHeight = 80,
        });
        center.RowDefinitions.Add(new RowDefinition { Height = new GridLength(8) });
        center.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        _changeList = new ListBox { Margin = new Thickness(0) };
        _changeList.SelectionChanged += (_, _) => ShowSelectedChange();
        Grid.SetRow(_changeList, 0);
        center.Children.Add(_changeList);

        var diff = new Grid { Margin = new Thickness(0, 8, 0, 0) };
        diff.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        diff.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(10) });
        diff.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var oldHost = new DockPanel();
        var oldLabel = new TextBlock
        {
            Text = "变更前（真实文件当前内容）",
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 0, 0, 6),
        };
        DockPanel.SetDock(oldLabel, Dock.Top);
        oldHost.Children.Add(oldLabel);
        _oldBox = MakeReadOnlyBox();
        oldHost.Children.Add(_oldBox);
        Grid.SetColumn(oldHost, 0);
        diff.Children.Add(oldHost);

        var newHost = new DockPanel();
        var newLabel = new TextBlock
        {
            Text = "变更后（沙箱副本内容，应用后生效）",
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 0, 0, 6),
        };
        DockPanel.SetDock(newLabel, Dock.Top);
        newHost.Children.Add(newLabel);
        _newBox = MakeReadOnlyBox();
        newHost.Children.Add(_newBox);
        Grid.SetColumn(newHost, 2);
        diff.Children.Add(newHost);

        Grid.SetRow(diff, 2);
        center.Children.Add(diff);
        root.Children.Add(center);

        Content = root;
        Loaded += async (_, _) => await RefreshPendingAsync(selectTicketId);
    }

    private static TextBox MakeReadOnlyBox() => new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        TextWrapping = TextWrapping.NoWrap,
        FontFamily = new FontFamily("Consolas"),
        FontSize = 12,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
    };

    private async Task RefreshPendingAsync(string? selectTicketId)
    {
        try
        {
            _pending = await _client.GetPendingSandboxesAsync();
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "拉取待审核会话失败：" + ex.Message, "沙箱审核",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        _loading = true;
        _ticketCombo.Items.Clear();
        foreach (var info in _pending)
        {
            _ticketCombo.Items.Add($"{info.TicketId}（{info.ChangeCount} 项变更）");
        }
        if (_pending.Count > 0)
        {
            int idx = string.IsNullOrEmpty(selectTicketId)
                ? 0
                : Math.Max(0, _pending.FindIndex(p => p.TicketId == selectTicketId));
            _ticketCombo.SelectedIndex = idx;
            _ticketCombo.IsEnabled = true;
        }
        else
        {
            _detail = null;
            _rootText.Text = "";
            _changeList.Items.Clear();
            _oldBox.Text = "";
            _newBox.Text = "";
            _resultText.Text = "没有待审核的沙箱会话。";
            _applyButton.IsEnabled = false;
            _discardButton.IsEnabled = false;
            _ticketCombo.IsEnabled = false;
        }
        _loading = false;
        if (_ticketCombo.SelectedIndex >= 0)
        {
            await LoadDetailAsync(_pending[_ticketCombo.SelectedIndex].TicketId);
        }
    }

    private async Task OnTicketSelectedAsync()
    {
        if (_loading) return;
        int idx = _ticketCombo.SelectedIndex;
        if (idx < 0 || idx >= _pending.Count) return;
        await LoadDetailAsync(_pending[idx].TicketId);
    }

    private async Task LoadDetailAsync(string ticketId)
    {
        try
        {
            _detail = await _client.GetSandboxSessionAsync(ticketId);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "读取会话详情失败：" + ex.Message, "沙箱审核",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        _rootText.Text = $"真实根目录：{_detail.RealRoot}    状态：{_detail.Status}";
        _changeList.Items.Clear();
        foreach (var c in _detail.Changes)
        {
            string suffix = string.IsNullOrWhiteSpace(c.Error) ? "" : $"  ⚠ {c.Error}";
            _changeList.Items.Add($"[{OpLabel(c.Op)}] {c.Relpath}{suffix}");
        }
        bool hasChanges = _detail.Changes.Count > 0;
        _applyButton.IsEnabled = hasChanges;
        _discardButton.IsEnabled = hasChanges;
        _resultText.Text = hasChanges ? $"共 {_detail.Changes.Count} 项暂存变更，尚未写入真实目录。" : "";
        if (hasChanges)
        {
            _changeList.SelectedIndex = 0;
        }
        else
        {
            _oldBox.Text = "";
            _newBox.Text = "";
        }
    }

    private void ShowSelectedChange()
    {
        if (_detail == null) return;
        int idx = _changeList.SelectedIndex;
        if (idx < 0 || idx >= _detail.Changes.Count) return;
        var c = _detail.Changes[idx];

        if (!c.IsText && c.Op is not ("mkdir" or "side_write"))
        {
            string binary = $"（二进制文件，大小 {(c.Size ?? 0):N0} 字节，不提供文本预览）";
            _oldBox.Text = c.Op == "create" ? "（新建文件，无原内容）" : binary;
            _newBox.Text = c.Op == "delete" ? "（删除）" : binary;
            return;
        }

        _oldBox.Text = c.Op switch
        {
            "create" => "（新建文件，无原内容）",
            "delete" => c.OldText ?? "（原文件无法按文本读取）",
            "mkdir" => "（新建目录）",
            "side_write" => "（根外写入：应用时复制到真实位置）",
            _ => c.OldText ?? "（原文件无法按文本读取）",
        };
        _newBox.Text = c.Op switch
        {
            "delete" => "（删除）",
            "mkdir" => "（新建目录）",
            _ => c.NewText ?? "（无文本预览）",
        };
        if (c.Truncated)
        {
            _newBox.Text += "\n\n……（内容过大，预览已截断，应用时写入完整内容）";
        }
    }

    private async Task ApplyAsync()
    {
        if (_detail == null) return;
        int n = _detail.Changes.Count;
        var confirm = MessageBox.Show(this,
            $"将把 {n} 项变更写入真实目录。\n\n" +
            "• 修改/删除的文件会先自动备份到 .gamedoctor/backups；\n" +
            "• 写入后沙箱清空，本窗口不能再撤回（可用备份恢复）。\n\n" +
            "确认应用？",
            "应用沙箱变更", MessageBoxButton.YesNo, MessageBoxImage.Question);
        if (confirm != MessageBoxResult.Yes) return;

        _applyButton.IsEnabled = false;
        try
        {
            var result = await _client.ApplySandboxAsync(_detail.TicketId);
            string errors = string.Join("\n",
                result.Details.Where(d => !d.Ok).Select(d => $"• {d.Relpath}: {d.Error}"));
            _resultText.Text = result.Failed == 0
                ? $"已全部应用：成功 {result.Succeeded}/{result.Total} 项（原件已备份）。"
                : $"应用完成：成功 {result.Succeeded}，失败 {result.Failed}。";
            if (!string.IsNullOrEmpty(errors))
            {
                MessageBox.Show(this, "以下项应用失败：\n" + errors, "部分失败",
                    MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "应用失败：" + ex.Message, "沙箱审核",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            _applyButton.IsEnabled = true;
            return;
        }
        await RefreshPendingAsync(null);
    }

    private async Task DiscardAsync()
    {
        if (_detail == null) return;
        int n = _detail.Changes.Count;
        var confirm = MessageBox.Show(this,
            $"丢弃这 {n} 项暂存变更？\n\n真实文件不会发生任何变化，丢弃后暂存内容不可恢复。",
            "丢弃沙箱变更", MessageBoxButton.YesNo, MessageBoxImage.Warning);
        if (confirm != MessageBoxResult.Yes) return;

        _discardButton.IsEnabled = false;
        try
        {
            await _client.DiscardSandboxAsync(_detail.TicketId);
            _resultText.Text = "已丢弃，真实文件零变化。";
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "丢弃失败：" + ex.Message, "沙箱审核",
                MessageBoxButton.OK, MessageBoxImage.Warning);
            _discardButton.IsEnabled = true;
            return;
        }
        await RefreshPendingAsync(null);
    }

    private static string OpLabel(string op) => op switch
    {
        "create" => "新建",
        "modify" => "修改",
        "delete" => "删除",
        "download" => "下载",
        "mkdir" => "建目录",
        "side_write" => "根外写入",
        _ => string.IsNullOrEmpty(op) ? "变更" : op,
    };
}
