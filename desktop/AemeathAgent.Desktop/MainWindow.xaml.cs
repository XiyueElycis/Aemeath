using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Microsoft.Win32;

namespace AemeathAgent.Desktop;

public partial class MainWindow : Window
{
    /// <summary>
    /// 对话气泡。
    ///
    /// <see cref="Text"/> 可写并发通知：流式回复时逐段追加，用户能看到爱弥斯
    /// 一个字一个字写出来，而不是等一整块弹出（后者很「机器吐答案」）。
    /// <see cref="Narration"/> 是「过程记录」，用后端的人话版 narration，
    /// 而不是 thinking（那是「任务票/准奏」的机器日志，给排查用）。
    /// </summary>
    public sealed class ChatMessage : INotifyPropertyChanged
    {
        private string _text = "";
        private List<string> _narration = new();

        public string Role { get; init; } = "";
        public string Text
        {
            get => _text;
            set
            {
                if (_text == value) return;
                _text = value;
                OnPropertyChanged(nameof(Text));
            }
        }
        public bool IsUser { get; init; }
        /// <summary>「正在输入」气泡：显示三个蓝点滑动动画而非文字。</summary>
        public bool IsTyping { get; init; }
        /// <summary>过程记录（后端 narration 的人话版），可展开查看。</summary>
        public List<string> Narration
        {
            get => _narration;
            set
            {
                _narration = value;
                OnPropertyChanged(nameof(Narration));
                OnPropertyChanged(nameof(HasNarration));
            }
        }
        /// <summary>是否有过程记录可展示。</summary>
        public bool HasNarration => !IsUser && Narration.Count > 0;

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnPropertyChanged(string name) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    private readonly AgentApiClient _client;
    private readonly ObservableCollection<ChatMessage> _messages = new();
    private readonly DispatcherTimer _statusTimer;
    /// <summary>沙箱模式对话期间轮询待决任务授权（1s）。</summary>
    private readonly DispatcherTimer _approvalTimer;
    /// <summary>本轮对话已弹过窗的授权请求 id，避免重复弹窗。</summary>
    private readonly HashSet<string> _seenApprovalIds = new();
    private bool _statusPolling;
    private bool _approvalPolling;
    private bool _approvalDialogOpen;
    private bool _chatActive;
    private bool _entrancePlayed;
    private SettingsView? _settings;
    private UiPrefs _uiPrefs = new();
    private StartupPrefs _startupPrefs = new();

    /// <summary>顶栏微型 HUD 数据定时器（2s，对应文档 ≤1Hz 装饰定时器约束的放宽：数据展示）。</summary>
    private readonly DispatcherTimer _hudTimer;
    private readonly Random _rng = new();

    public MainWindow()
    {
        InitializeComponent();
        _client = new AgentApiClient(GetBaseUrl());
        ChatMessages.ItemsSource = _messages;

        // 模型下拉是可编辑的：用户手动输入自定义模型名时，顶部模型标签实时跟随
        ModelCombo.AddHandler(
            System.Windows.Controls.Primitives.TextBoxBase.TextChangedEvent,
            new TextChangedEventHandler((_, _) => UpdateModelLabel()));

        _statusTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(800) };
        _statusTimer.Tick += async (_, _) => await PollStatusAsync();
        _statusTimer.Start();

        _approvalTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _approvalTimer.Tick += async (_, _) => await PollApprovalsAsync();
        Closing += (_, _) => { _approvalTimer.Stop(); _hudTimer?.Stop(); };

        // 顶栏 HUD：每 2s 微变温度/氧气/网络
        _hudTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _hudTimer.Tick += (_, _) => TickHud();
        _hudTimer.Start();

        Loaded += OnWindowLoadedAsync;
    }

    private async void OnWindowLoadedAsync(object sender, RoutedEventArgs e)
    {
        await InitializeAsync();
    }

    /// <summary>窗口入场：0.5s 淡入 + 12px 上移（承接启动动画洗白帧）。</summary>
    private void PlayEntrance()
    {
        if (_entrancePlayed) return;
        _entrancePlayed = true;
        if (_uiPrefs.ReducedMotion) return;
        RootGrid.Opacity = 0;
        RootGrid.RenderTransform = new TranslateTransform(0, 12);
        RootGrid.BeginAnimation(OpacityProperty,
            new DoubleAnimation(0, 1, TimeSpan.FromSeconds(0.5)));
        ((TranslateTransform)RootGrid.RenderTransform).BeginAnimation(
            TranslateTransform.YProperty,
            new DoubleAnimation(12, 0, TimeSpan.FromSeconds(0.5)));
    }

    /// <summary>顶栏 HUD 数值微变：围绕基准小幅漂移，保留一位小数。
    /// 更新后对数值文本播一次 150ms 闪动（减少动画时跳过），复刻游戏 HUD 手感。</summary>
    private void TickHud()
    {
        double Drift(double center, double amp) =>
            center + (_rng.NextDouble() * 2 - 1) * amp;
        HudTempVal.Text = Drift(36.7, 0.08).ToString("0.0");
        HudOxyVal.Text = Drift(98.4, 0.35).ToString("0.0");
        HudNetVal.Text = Drift(1.8, 0.25).ToString("0.0");

        if (!_uiPrefs.ReducedMotion)
        {
            foreach (var tb in new[] { HudTempVal, HudOxyVal, HudNetVal })
            {
                var flash = new DoubleAnimation
                {
                    From = 0.55,
                    To = 1,
                    Duration = TimeSpan.FromMilliseconds(150),
                };
                tb.BeginAnimation(OpacityProperty, flash);
            }
        }
    }

    /// <summary>后端地址：环境变量 AEMEATH_AGENT_API 覆盖，默认本机 8765。 </summary>
    internal static string GetBaseUrl() =>
        Environment.GetEnvironmentVariable("AEMEATH_AGENT_API") ?? "http://127.0.0.1:8765";

    private async Task InitializeAsync()
    {
        // 本地偏好（与后端无关）先加载：动效门控影响入场与所有装饰动画
        LoadLocalPrefs();
        ApplyUiPrefs();
        PlayEntrance();

        await LoadSettingsAsync();
        await LoadAgentsAsync();
        await RefreshPendingSandboxesAsync();
    }

    // ------------------------------------------------------------------ //
    // 本地偏好：加载 / 应用 / 保存回调
    // ------------------------------------------------------------------ //
    private void LoadLocalPrefs()
    {
        _uiPrefs = UiPrefs.Load();
        _startupPrefs = StartupPrefs.Load();
    }

    /// <summary>按偏好设置动效门控、网格/星空显隐与常驻循环。</summary>
    private void ApplyUiPrefs()
    {
        Fx.SetMotionEnabled(this, !_uiPrefs.ReducedMotion);
        GridLayer.Visibility = _uiPrefs.ShowGrid ? Visibility.Visible : Visibility.Collapsed;
        GridLayer.Opacity = 1;

        if (_uiPrefs.ReducedMotion)
        {
            // 停止所有常驻装饰循环
            StopGridPan();
            StopBackendPulse();
            StopThinkScan();
        }
        else
        {
            StartGridPan();
            StartBackendPulse();
        }
    }

    /// <summary>设置面板的「界面与动效」开关：立即生效 + 落盘。</summary>
    private void OnUiPrefsChanged(object sender, RoutedEventArgs e)
    {
        // Checked/Unchecked 在 InitializeComponent 期间也可能触发，控件未就绪须跳过
        if (GridBgCheck == null || ReducedMotionCheck == null) return;
        _uiPrefs.ShowGrid = GridBgCheck.IsChecked == true;
        _uiPrefs.ReducedMotion = ReducedMotionCheck.IsChecked == true;
        ApplyUiPrefs();
        _uiPrefs.Save();
    }

    /// <summary>启动动画偏好开关：写回 StartupPrefs（下次启动生效）。</summary>
    private void OnStartupPrefsChanged(object sender, RoutedEventArgs e)
    {
        if (StartupModeAlways == null || StartupModeFirst == null
            || StartupModeNever == null || StartupSoundCheck == null)
            return;
        if (StartupModeAlways.IsChecked == true) _startupPrefs.Mode = "always";
        else if (StartupModeFirst.IsChecked == true) _startupPrefs.Mode = "first";
        else if (StartupModeNever.IsChecked == true) _startupPrefs.Mode = "never";
        _startupPrefs.Sound = StartupSoundCheck.IsChecked == true;
        _startupPrefs.Save();
    }

    private void SyncLocalPrefsUi()
    {
        StartupModeAlways.IsChecked = _startupPrefs.Mode == "always";
        StartupModeFirst.IsChecked = _startupPrefs.Mode == "first";
        StartupModeNever.IsChecked = _startupPrefs.Mode == "never";
        StartupSoundCheck.IsChecked = _startupPrefs.Sound;
        GridBgCheck.IsChecked = _uiPrefs.ShowGrid;
        ReducedMotionCheck.IsChecked = _uiPrefs.ReducedMotion;
    }

    // ------------------------------------------------------------------ //
    // 常驻装饰动画（代码控制，便于减少动画时整体停止）
    // ------------------------------------------------------------------ //
    private void StartGridPan()
    {
        // 60s 缓慢位移一个网格单元，无缝循环（TileMode 平铺，平移 28 后与初始重合）
        GridPan.BeginAnimation(
            TranslateTransform.XProperty,
            new DoubleAnimation(0, -28, TimeSpan.FromSeconds(60)) { RepeatBehavior = RepeatBehavior.Forever });
        GridPan.BeginAnimation(
            TranslateTransform.YProperty,
            new DoubleAnimation(0, -28, TimeSpan.FromSeconds(90)) { RepeatBehavior = RepeatBehavior.Forever });
    }

    private void StopGridPan()
    {
        GridPan.BeginAnimation(TranslateTransform.XProperty, null);
        GridPan.BeginAnimation(TranslateTransform.YProperty, null);
    }

    private void StartBackendPulse()
    {
        BackendLight.BeginAnimation(
            OpacityProperty,
            new DoubleAnimation(1, 0.45, TimeSpan.FromSeconds(0.75))
            { AutoReverse = true, RepeatBehavior = RepeatBehavior.Forever });
    }

    private void StopBackendPulse()
    {
        BackendLight.BeginAnimation(OpacityProperty, null);
        BackendLight.Opacity = 1;
    }

    /// <summary>思考扫描线启动：1px 青绿线在聊天卡片顶部横向往复，1.8s/次。</summary>
    private void StartThinkScan()
    {
        if (_uiPrefs.ReducedMotion) return;
        ThinkScanLine.Visibility = Visibility.Visible;
        double width = ThinkScanLine.Parent is FrameworkElement fe ? fe.ActualWidth : 800;
        var anim = new DoubleAnimation(-150, width + 20, TimeSpan.FromSeconds(1.8))
        {
            RepeatBehavior = RepeatBehavior.Forever,
        };
        ThinkScanX.BeginAnimation(TranslateTransform.XProperty, anim);
    }

    private void StopThinkScan()
    {
        ThinkScanX.BeginAnimation(TranslateTransform.XProperty, null);
        ThinkScanLine.Visibility = Visibility.Collapsed;
    }

    // ------------------------------------------------------------------ //
    // 后端连接状态灯：在线 / 异常
    // ------------------------------------------------------------------ //
    private void SetBackendState(bool online)
    {
        BackendLight.Fill = online
            ? (Brush)FindResource("StatusOkBrush")
            : (Brush)FindResource("StatusErrorBrush");
        BackendStateText.Text = online ? "后端在线" : "后端离线";
        BackendStateText.Foreground = online
            ? (Brush)FindResource("StatusOkBrush")
            : (Brush)FindResource("StatusErrorBrush");
        if (!online) StopBackendPulse();
        else if (!_uiPrefs.ReducedMotion) StartBackendPulse();
    }

    // ------------------------------------------------------------------ //
    // 加载
    // ------------------------------------------------------------------ //
    private async Task LoadAgentsAsync()
    {
        try
        {
            var agents = await _client.GetAgentsAsync();
            AgentList.ItemsSource = agents;
            AgentCountText.Text = $"已加载 {agents.Count} 个智能体";
            // 折叠标题带上数量，收起时也知道里面有多少个
            AgentToggleText.Text = $"智能体 · {agents.Count}";
            SetStatus("就绪");
            SetBackendState(true);
        }
        catch (Exception ex)
        {
            SetStatus($"加载智能体失败：{ex.Message}");
            SetBackendState(false);
            MessageBox.Show(this, "无法连接后端服务。\n请先启动：aemeath_agent-server\n\n" + ex.Message,
                "连接失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task LoadSettingsAsync()
    {
        try
        {
            _settings = await _client.GetSettingsAsync();

            // provider / model 下拉
            ProviderCombo.ItemsSource = _settings.Providers;
            ProviderCombo.SelectedItem = _settings.Providers
                .FirstOrDefault(p => p.Id == _settings.Llm.Provider);
            ApplyProviderModels();
            ModelCombo.Text = _settings.Llm.Model;

            ApiBaseBox.Text = _settings.Llm.ApiBase;

            // 回填当前厂商已保存的密钥（切换厂商时也会自动带出）
            if (ProviderCombo.SelectedItem is ProviderInfo cur)
            {
                await PrefillApiKeyAsync(cur.Id);
            }

            AgentSettingList.ItemsSource = _settings.Agents;
            PersonaEnabledCheck.IsChecked = _settings.Persona.Enabled;
            RenderSavedKeys();

            // 本地偏好（界面动效 / 启动动画）回填
            SyncLocalPrefsUi();

            UpdateModelLabel();
        }
        catch (Exception ex)
        {
            SetStatus($"加载设置失败：{ex.Message}");
        }
    }

    /// <summary>把某厂商已保存的密钥回填到输入框（无痕：以遮罩形式展示）。</summary>
    private async Task PrefillApiKeyAsync(string providerId)
    {
        try
        {
            string saved = await _client.GetApiKeyAsync(providerId);
            _syncingApiKey = true;
            ApiKeyBox.Password = saved;
            ApiKeyText.Text = saved;
            _syncingApiKey = false;
            ApiKeyBox.ToolTip = string.IsNullOrEmpty(saved)
                ? "未配置密钥，请输入"
                : "已自动填充该厂商保存的密钥";
        }
        catch
        {
            // 后端不可达时忽略，不影响面板其余部分
        }
    }

    /// <summary>刷新「已保存的密钥」管理列表。</summary>
    private void RenderSavedKeys()
    {
        if (_settings == null) return;
        var names = _settings.Providers.ToDictionary(p => p.Id, p => p.Name);
        SavedKeysList.ItemsSource = _settings.ApiKeys
            .Select(k => new SavedKeyDisplay(
                k.Provider,
                $"{names.GetValueOrDefault(k.Provider, k.Provider)}：{k.Masked}"))
            .ToList();
    }

    /// <summary>已保存密钥条目的展示模型。</summary>
    public sealed class SavedKeyDisplay
    {
        public string Provider { get; }
        public string Display { get; }
        public SavedKeyDisplay(string provider, string display)
        {
            Provider = provider;
            Display = display;
        }
    }

    private void ApplyProviderModels()
    {
        if (ProviderCombo.SelectedItem is ProviderInfo p)
        {
            ModelCombo.ItemsSource = p.Models;
        }
    }

    private void UpdateModelLabel()
    {
        var providerName = (ProviderCombo.SelectedItem as ProviderInfo)?.Name
                           ?? _settings?.Llm.Provider ?? "—";
        var model = ModelCombo.Text?.Trim();
        if (string.IsNullOrWhiteSpace(model))
            model = _settings?.Llm.Model ?? "—";
        ModelNameText.Text = $"{providerName} · {model}";
    }

    // ------------------------------------------------------------------ //
    // 事件：智能体选择 / 目录浏览 / 设置
    // ------------------------------------------------------------------ //
    /// <summary>同步标志：防止 PasswordBox 与明文 TextBox 互相同步时死循环。</summary>
    private bool _syncingApiKey;

    private void OnApiKeyBoxChanged(object sender, RoutedEventArgs e)
    {
        if (_syncingApiKey) return;
        _syncingApiKey = true;
        ApiKeyText.Text = ApiKeyBox.Password;
        _syncingApiKey = false;
    }

    private void OnApiKeyTextChanged(object sender, TextChangedEventArgs e)
    {
        if (_syncingApiKey) return;
        _syncingApiKey = true;
        ApiKeyBox.Password = ApiKeyText.Text;
        _syncingApiKey = false;
    }

    private void OnShowApiKeyToggle(object sender, RoutedEventArgs e)
    {
        bool show = ShowApiKeyCheck.IsChecked == true;
        ApiKeyText.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        ApiKeyBox.Visibility = show ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>当前输入的 API Key（取当前可见控件的内容）。</summary>
    private string GetApiKeyInput() =>
        ShowApiKeyCheck.IsChecked == true ? ApiKeyText.Text : ApiKeyBox.Password;

    private void OnAgentSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (AgentList.SelectedItem is AgentInfo info && info.Capabilities.Count > 0)
        {
            SetStatus($"{info.DisplayName} 能力：{string.Join("、", info.Capabilities)}");
        }
    }

    /// <summary>
    /// 折叠 / 展开智能体列表。
    /// 十几项常驻在侧栏会把「心动模式」挤下去，所以默认收起，点标题才展开。
    /// </summary>
    private void OnAgentToggleClick(object sender, RoutedEventArgs e)
    {
        bool expand = AgentPanel.Visibility != Visibility.Visible;
        AgentPanel.Visibility = expand ? Visibility.Visible : Visibility.Collapsed;
        AgentChevron.Text = expand ? "▾" : "▸";
        if (expand)
        {
            AgentToggleButton.Focus();   // 保持键盘可达性，别让焦点留在空处
        }
    }

    private void OnBrowseClick(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFolderDialog
        {
            Title = "选择游戏文件根目录",
            InitialDirectory = Directory.Exists(GameDirBox.Text) ? GameDirBox.Text : null,
        };
        if (dialog.ShowDialog(this) == true)
        {
            GameDirBox.Text = dialog.FolderName;
        }
    }

    // ------------------------------------------------------------------ //
    // 视图切换：智能控制台 ↔ 指令中枢
    // ------------------------------------------------------------------ //
    private void OnConsoleNavClick(object sender, RoutedEventArgs e) => ShowConsole(true);

    private void OnDirectiveNavClick(object sender, RoutedEventArgs e) => ShowConsole(false);

    private void ShowConsole(bool console)
    {
        ConsoleView.Visibility = console ? Visibility.Visible : Visibility.Collapsed;
        DirectiveView.Visibility = console ? Visibility.Collapsed : Visibility.Visible;
        SetNavActive(console ? ConsoleNavButton : DirectiveNavButton);
    }

    /// <summary>更新侧栏导航的选中字重（轻量指示，不改变导航样式模板）。</summary>
    private void SetNavActive(Button active)
    {
        foreach (var b in new[]
        {
            ConsoleNavButton, HeartModeButton, DirectiveNavButton, SkillsButton, McpButton
        })
        {
            b.FontWeight = FontWeights.Normal;
            b.Foreground = (Brush)FindResource("TextSecondaryBrush");
        }
        active.FontWeight = FontWeights.SemiBold;
        active.Foreground = (Brush)FindResource("TextPrimaryBrush");
    }

    private void OnSettingsClick(object sender, RoutedEventArgs e)
    {
        bool show = SettingsPanel.Visibility != Visibility.Visible;
        SettingsPanel.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        if (show)
        {
            _ = RefreshSettingsAsync();
        }
    }

    private void OnSkillsClick(object sender, RoutedEventArgs e) =>
        ShowPage(new SkillsPage(_client, () => PageHost.Visibility = Visibility.Collapsed));

    private void OnMcpClick(object sender, RoutedEventArgs e) =>
        ShowPage(new McpPage(_client, () => PageHost.Visibility = Visibility.Collapsed));

    /// <summary>进入心动模式：先确认，再打开 Galgame 专属窗口（WebView2）。</summary>
    private void OnHeartModeClick(object sender, RoutedEventArgs e)
    {
        var choice = MessageBox.Show(
            this,
            "是否进入「心动模式」？\n\n" +
            "· 爱弥斯将以女友的身份与你相处\n" +
            "· 含好感度养成、随机事件与暧昧剧情（R16）\n" +
            "· 可随时点击左上角「返回控制台」退出",
            "爱弥斯 · 心动模式",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question,
            MessageBoxResult.No);
        if (choice != MessageBoxResult.Yes)
            return;

        // 场景转场（禁止生硬跳切）：主界面 0.3s 压暗，心动窗口随后淡入
        HeartDimOverlay.BeginAnimation(
            OpacityProperty,
            new DoubleAnimation(0, 0.62, TimeSpan.FromSeconds(0.3)));

        var gal = new GalgameWindow(GetBaseUrl()) { Owner = this };
        // 心动窗口关闭后：遮罩 0.4s 淡出，主界面恢复
        gal.Closed += (_, _) =>
            HeartDimOverlay.BeginAnimation(
                OpacityProperty,
                new DoubleAnimation(0.62, 0, TimeSpan.FromSeconds(0.4)));
        gal.Show();
    }

    /// <summary>在覆盖层 Frame 中打开二级页面（技能 / MCP 管理），页面通过回调关闭。</summary>
    private void ShowPage(Page page)
    {
        PageHost.Navigate(page);
        PageHost.Visibility = Visibility.Visible;
    }

    private async Task RefreshSettingsAsync()
    {
        await LoadSettingsAsync();
    }

    private void OnCloseSettingsClick(object sender, RoutedEventArgs e)
    {
        SettingsPanel.Visibility = Visibility.Collapsed;
    }

    private async void OnProviderChanged(object sender, SelectionChangedEventArgs e)
    {
        ApplyProviderModels();
        // 切换服务商时自动填充：内置 API 地址 + 上次使用的模型（无记录回落默认）+ 已保存密钥
        if (ProviderCombo.SelectedItem is ProviderInfo p && !string.IsNullOrEmpty(p.Id))
        {
            ApiBaseBox.Text = p.ApiBase;
            ModelCombo.Text = !string.IsNullOrWhiteSpace(p.LastModel)
                ? p.LastModel
                : p.DefaultModel;
            await PrefillApiKeyAsync(p.Id);
        }
        UpdateModelLabel();
    }

    private async void OnDeleteApiKeyClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string provider } || string.IsNullOrEmpty(provider))
            return;

        var confirm = MessageBox.Show(this, $"确定删除 {provider} 的已保存密钥吗？",
            "删除密钥", MessageBoxButton.YesNo, MessageBoxImage.Question);
        if (confirm != MessageBoxResult.Yes) return;

        try
        {
            await _client.DeleteApiKeyAsync(provider);
            // 若删的是当前厂商，同步清空输入框
            if (ProviderCombo.SelectedItem is ProviderInfo cur && cur.Id == provider)
            {
                _syncingApiKey = true;
                ApiKeyBox.Password = "";
                ApiKeyText.Text = "";
                _syncingApiKey = false;
                ApiKeyBox.ToolTip = "未配置密钥，请输入";
            }
            await RefreshSettingsAsync();
            SetStatus($"已删除 {provider} 的密钥");
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "删除失败：\n" + ex.Message, "删除密钥",
                MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void OnSaveSettingsClick(object sender, RoutedEventArgs e)
    {
        try
        {
            SetStatus("正在保存设置...");
            var provider = (ProviderCombo.SelectedItem as ProviderInfo)?.Id ?? "deepseek";
            var model = ModelCombo.Text?.Trim() ?? "";
            var apiBase = ApiBaseBox.Text?.Trim() ?? "";
            var apiKey = GetApiKeyInput() ?? "";

            var updated = await _client.UpdateLlmAsync(provider, model, apiBase, apiKey);
            ModelNameText.Text = $"{provider} · {updated.Model}";

            // 智能体启用配置
            var enabledMap = new Dictionary<string, bool>();
            foreach (var item in AgentSettingList.Items)
            {
                if (item is AgentSettingInfo info)
                    enabledMap[info.Name] = info.Enabled;
            }
            await _client.UpdateAgentsAsync(enabledMap);

            // 角色人格开关
            await _client.UpdatePersonaAsync(PersonaEnabledCheck.IsChecked == true);

            // 刷新设置视图（更新已保存密钥列表）
            await RefreshSettingsAsync();
            SetStatus("设置已保存");
            SettingsPanel.Visibility = Visibility.Collapsed;
        }
        catch (Exception ex)
        {
            SetStatus("保存设置失败");
            MessageBox.Show(this, "保存设置失败：\n" + ex.Message,
                "设置", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    // ------------------------------------------------------------------ //
    // 对话
    // ------------------------------------------------------------------ //
    private void OnChatInputKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
        {
            e.Handled = true;
            _ = SendMessageAsync();
        }
    }

    private void OnSearchToggleChanged(object sender, RoutedEventArgs e)
    {
        SetStatus(SearchToggle.IsChecked == true
            ? "联网搜索已开启：大模型可联网检索与下载文件"
            : "联网搜索已关闭");
    }

    // ------------------------------------------------------------------ //
    // 思考强度：默认收纳，点击「思考强度」按钮展开滑块（轻度/中度/重度）
    // ------------------------------------------------------------------ //
    /// <summary>思考强度三档名称（与后端 think_level 对应）。</summary>
    private static readonly string[] ThinkLevelNames = { "light", "medium", "heavy" };
    private static readonly string[] ThinkLevelLabels = { "轻度", "中度", "重度" };

    private void OnThinkToggleChanged(object sender, RoutedEventArgs e)
    {
        bool expanded = ThinkToggle.IsChecked == true;
        ThinkPanel.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        ThinkToggle.Content = expanded ? "思考强度 ▾" : "思考强度 ▸";
    }

    private void OnThinkLevelChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        int idx = (int)Math.Round(ThinkSlider.Value);
        ThinkLevelLabel.Text = $"思考强度：{ThinkLevelLabels[idx]}";
    }

    /// <summary>当前选择的思考强度（light/medium/heavy）。</summary>
    private string GetThinkLevel()
    {
        int idx = (int)Math.Round(ThinkSlider.Value);
        idx = Math.Clamp(idx, 0, ThinkLevelNames.Length - 1);
        return ThinkLevelNames[idx];
    }

    /// <summary>当前运行模式：edit / plan / companion。</summary>
    private string GetMode()
    {
        if (CompanionModeRadio.IsChecked == true) return "companion";
        return PlanModeRadio.IsChecked == true ? "plan" : "edit";
    }

    private void OnModeChanged(object sender, RoutedEventArgs e)
    {
        // InitializeComponent 期间单选框 IsChecked=True 会提前触发 Checked，
        // 此时其余控件尚未创建，须跳过以避免 NullReferenceException。
        if (PlanModeRadio == null || EditModeRadio == null || CompanionModeRadio == null) return;
        if (CompanionModeRadio.IsChecked == true)
        {
            SetStatus("陪伴模式：只聊天，不调用智能体、不操作文件");
            // 陪伴模式不涉及联网开关与访问模式，置灰避免误解
            SearchToggle.IsEnabled = false;
            SandboxAccessRadio.IsEnabled = false;
            DirectAccessRadio.IsEnabled = false;
        }
        else
        {
            SearchToggle.IsEnabled = true;
            SandboxAccessRadio.IsEnabled = true;
            DirectAccessRadio.IsEnabled = true;
            SetStatus(PlanModeRadio.IsChecked == true
                ? "方案模式：只做调查、给出方案，不修改文件"
                : "编辑模式：大模型可调用智能体执行文件操作");
        }
    }

    /// <summary>当前访问模式：sandbox（沙箱授权，默认）/ direct（直接访问）。</summary>
    private string GetAccessMode() =>
        DirectAccessRadio.IsChecked == true ? "direct" : "sandbox";

    private void OnAccessModeChanged(object sender, RoutedEventArgs e)
    {
        // 同 OnModeChanged：InitializeComponent 期间会提前触发，控件未就绪须跳过。
        if (SandboxAccessRadio == null || DirectAccessRadio == null) return;
        SetStatus(DirectAccessRadio.IsChecked == true
            ? "直接访问：智能体获任务后直接操作真实文件（无授权与沙箱）"
            : "沙箱授权：改动任务先授权、沙箱执行、审核后才写入真实目录");
    }

    private void OnSendClick(object sender, RoutedEventArgs e) => _ = SendMessageAsync();

    private async Task SendMessageAsync()
    {
        string text = ChatInput.Text?.Trim() ?? "";
        if (string.IsNullOrWhiteSpace(text)) return;

        string gameDir = GameDirBox.Text?.Trim() ?? "";
        bool enableSearch = SearchToggle.IsChecked == true;
        string thinkLevel = GetThinkLevel();
        string accessMode = GetAccessMode();
        string mode = GetMode();

        // 陪伴模式：当前消息入列前，从已有对话记录构造多轮历史
        List<Dictionary<string, string>>? history =
            mode == "companion" ? BuildChatHistory() : null;

        _messages.Add(new ChatMessage { Role = "你", Text = text, IsUser = true });
        ChatInput.Clear();

        // 在对话流末尾插入「正在输入」气泡（三个蓝点滑动动画）
        var typing = new ChatMessage { Role = "爱弥斯", Text = "", IsTyping = true };
        _messages.Add(typing);
        ScrollChatToEnd();

        SetSending(true);
        SetStatus("正在与大模型对话...");
        // 沙箱模式：对话期间轮询授权请求；direct 模式不起表
        _seenApprovalIds.Clear();
        _chatActive = true;
        // 陪伴模式无授权环节，不起轮询
        if (accessMode == "sandbox" && mode != "companion")
        {
            _approvalTimer.Start();
        }
        string? reviewTicketId = null;
        // 流式回复：先挂「正在输入」气泡，收到第一段增量时换成真正的内容气泡，
        // 之后逐段追加——用户能看到爱弥斯边想边说，而不是等一整块弹出。
        ChatMessage? replyMessage = null;
        var streamed = new StringBuilder();
        var progress = new List<string>();
        try
        {
            var resp = await _client.ChatStreamAsync(
                text, gameDir, enableSearch, thinkLevel, mode, accessMode, history,
                onProgress: step =>
                {
                    progress.Add(step);
                    SetStatus(step);
                },
                onDelta: piece =>
                {
                    if (replyMessage is null)
                    {
                        _messages.Remove(typing);
                        replyMessage = new ChatMessage { Role = "爱弥斯" };
                        _messages.Add(replyMessage);
                    }
                    streamed.Append(piece);
                    replyMessage.Text = streamed.ToString();
                    ScrollChatToEnd();
                });

            if (replyMessage is null)
            {
                // 全程没有增量（空回复、或直接给出兜底文案）：补一个气泡接收内容
                _messages.Remove(typing);
                replyMessage = new ChatMessage { Role = "爱弥斯" };
                _messages.Add(replyMessage);
            }
            // 以最终结果为准：增量只负责「过程好看」，内容正确性由 payload 兜底。
            // 注意这里不再追加「〔由智能体 xxx 完成〕」——那是英文标识符暴露给
            // 用户的机械外壳；执行了谁在做，narration 里已经用人话说过了。
            replyMessage.Text = string.IsNullOrWhiteSpace(resp.Reply)
                ? (streamed.Length > 0 ? streamed.ToString() : "（无回复）")
                : resp.Reply;
            replyMessage.Narration = resp.Narration ?? progress;
            ScrollChatToEnd();

            if (resp.Sandbox is { ChangeCount: > 0 } sb)
            {
                reviewTicketId = sb.TicketId;
                SetStatus($"沙箱会话 {sb.TicketId} 已封存：{sb.ChangeCount} 项变更待审核");
            }
            else
            {
                SetStatus("对话完成");
            }
        }
        catch (Exception ex)
        {
            _messages.Remove(typing);
            _messages.Add(new ChatMessage
            {
                Role = "爱弥斯",
                Text = $"[错误] {ex.Message}",
                IsUser = false,
                Narration = progress,
            });
            ScrollChatToEnd();
            SetStatus("对话出错");
        }
        finally
        {
            // 授权轮询随会话结束停止（「正在输入」气泡已在上面移除）
            _approvalTimer.Stop();
            _chatActive = false;
            SetSending(false);
        }

        // 对话结束后再弹审核窗口（此时授权轮询已停，避免两个模态框叠加）
        if (reviewTicketId != null)
        {
            OpenSandboxReview(reviewTicketId);
        }
        await RefreshPendingSandboxesAsync();
    }

    // ------------------------------------------------------------------ //
    // 沙箱三段式：授权轮询 + 变更审核入口
    // ------------------------------------------------------------------ //
    private async Task PollApprovalsAsync()
    {
        if (_approvalPolling || _approvalDialogOpen || !_chatActive) return;
        _approvalPolling = true;
        try
        {
            var pending = await _client.GetPendingApprovalsAsync();
            if (pending.Count == 0) return;
            // 只在出现「未见过」的请求时弹窗；弹窗期间由对话框自行刷新裁决
            bool hasFresh = pending.Any(p => !_seenApprovalIds.Contains(p.Id));
            foreach (var p in pending)
            {
                _seenApprovalIds.Add(p.Id);
            }
            if (!hasFresh) return;

            _approvalDialogOpen = true;
            try
            {
                var dialog = new ApprovalDialog(_client, pending) { Owner = this };
                dialog.ShowDialog();
            }
            catch (Exception ex)
            {
                SetStatus($"授权对话框异常：{ex.Message}");
            }
            finally
            {
                _approvalDialogOpen = false;
            }
        }
        catch
        {
            // 后端偶发不可达不打断对话
        }
        finally
        {
            _approvalPolling = false;
        }
    }

    private void OpenSandboxReview(string ticketId)
    {
        try
        {
            var window = new SandboxReviewWindow(_client, ticketId) { Owner = this };
            window.ShowDialog();
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "打开变更审核窗口失败：\n" + ex.Message,
                "沙箱审核", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    /// <summary>拉取 ready 状态的遗留沙箱会话，维护右下「待审核沙箱」入口。</summary>
    private async Task RefreshPendingSandboxesAsync()
    {
        try
        {
            var items = await _client.GetPendingSandboxesAsync();
            if (items.Count > 0)
            {
                ReviewPendingButton.Content = $"待审核沙箱（{items.Count}）";
                ReviewPendingButton.Visibility = Visibility.Visible;
            }
            else
            {
                ReviewPendingButton.Visibility = Visibility.Collapsed;
            }
        }
        catch
        {
            // 后端未就绪时不显示入口，保持安静
            ReviewPendingButton.Visibility = Visibility.Collapsed;
        }
    }

    private async void OnReviewPendingClick(object sender, RoutedEventArgs e)
    {
        var window = new SandboxReviewWindow(_client, null) { Owner = this };
        window.ShowDialog();
        await RefreshPendingSandboxesAsync();
    }

    /// <summary>从当前对话记录构造 LLM 历史（user/assistant），跳过正在输入气泡。</summary>
    private List<Dictionary<string, string>> BuildChatHistory()
    {
        var history = new List<Dictionary<string, string>>();
        foreach (var m in _messages)
        {
            if (m.IsTyping || string.IsNullOrWhiteSpace(m.Text)) continue;
            history.Add(new Dictionary<string, string>
            {
                ["role"] = m.IsUser ? "user" : "assistant",
                ["content"] = m.Text,
            });
        }
        return history;
    }

    private void ScrollChatToEnd()
    {
        if (_messages.Count > 0)
        {
            ChatMessages.ScrollIntoView(_messages[^1]);
        }
    }

    private void SetSending(bool sending)
    {
        SendButton.IsEnabled = !sending;
        ChatInput.IsEnabled = !sending;
        // 对话进行中锁定模式/访问开关，避免中途切换造成语义不一致
        EditModeRadio.IsEnabled = !sending;
        PlanModeRadio.IsEnabled = !sending;
        CompanionModeRadio.IsEnabled = !sending;
        // 陪伴模式下访问开关本就置灰，保持置灰；其余模式随发送状态切换
        if (CompanionModeRadio.IsChecked != true)
        {
            SandboxAccessRadio.IsEnabled = !sending;
            DirectAccessRadio.IsEnabled = !sending;
        }
        // 思考扫描线：发送开始启动、结束停止（减少动画时 Start 内部直接返回）
        if (sending) StartThinkScan();
        else StopThinkScan();
    }

    // ------------------------------------------------------------------ //
    // 运行状态监控轮询
    // ------------------------------------------------------------------ //
    private async Task PollStatusAsync()
    {
        if (_statusPolling) return;
        _statusPolling = true;
        try
        {
            var status = await _client.GetStatusAsync();
            if (status.Current is { } cur)
            {
                CurrentAgentText.Text = $"{cur.DisplayName}（{cur.Phase}）";
                CurrentPathText.Text = string.IsNullOrWhiteSpace(cur.Path) ? "—" : cur.Path;
                StatusLineText.Text = $"{cur.DisplayName} {cur.Phase}";
            }
            else
            {
                CurrentAgentText.Text = "—";
                CurrentPathText.Text = "—";
                StatusLineText.Text = "空闲";
            }

            EventList.ItemsSource = status.Events
                .Select(ev => FormatEvent(ev))
                .ToList();
        }
        catch
        {
            // 后端未就绪时静默，避免刷屏
        }
        finally
        {
            _statusPolling = false;
        }
    }

    private static string FormatEvent(StatusEventDto ev)
    {
        var tag = ev.Kind switch
        {
            "access" => "访问",
            "modify" => "修改",
            "phase" => "阶段",
            "approval" => "授权",
            _ => "信息",
        };
        return string.IsNullOrWhiteSpace(ev.Path)
            ? $"[{tag}] {ev.Message}"
            : $"[{tag}] {ev.Message}  → {ev.Path}";
    }

    // ------------------------------------------------------------------ //
    private void SetStatus(string text) => StatusText.Text = text;
}