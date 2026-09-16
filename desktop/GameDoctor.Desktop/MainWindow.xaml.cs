using System.Collections.ObjectModel;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Win32;

namespace GameDoctor.Desktop;

public partial class MainWindow : Window
{
    /// <summary>对话气泡（role + text + 是否用户 + 是否正在输入 + 思考过程）。</summary>
    public sealed class ChatMessage
    {
        public string Role { get; init; } = "";
        public string Text { get; init; } = "";
        public bool IsUser { get; init; }
        /// <summary>「正在输入」气泡：显示三个蓝点滑动动画而非文字。</summary>
        public bool IsTyping { get; init; }
        /// <summary>思考过程步骤列表（LLM 决策/执行/回复全链路），可展开查看。</summary>
        public List<string> Thinking { get; init; } = new();
        /// <summary>是否有思考过程可展示（Thinking 非空即显示展开器）。</summary>
        public bool HasThinking => !IsUser && Thinking.Count > 0;
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
    private SettingsView? _settings;

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
        Closing += (_, _) => _approvalTimer.Stop();

        Loaded += async (_, _) => await InitializeAsync();
    }

    /// <summary>后端地址：环境变量 GAMEDOCTOR_API 覆盖，默认本机 8765。 </summary>
    private static string GetBaseUrl() =>
        Environment.GetEnvironmentVariable("GAMEDOCTOR_API") ?? "http://127.0.0.1:8765";

    private async Task InitializeAsync()
    {
        await LoadSettingsAsync();
        await LoadAgentsAsync();
        await RefreshPendingSandboxesAsync();
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
            SetStatus("就绪");
        }
        catch (Exception ex)
        {
            SetStatus($"加载智能体失败：{ex.Message}");
            MessageBox.Show(this, "无法连接后端服务。\n请先启动：gamedoctor-server\n\n" + ex.Message,
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
            RenderSavedKeys();

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

    /// <summary>当前运行模式：edit（编辑，可执行文件操作）/ plan（方案，只读调查）。</summary>
    private string GetMode() => PlanModeRadio.IsChecked == true ? "plan" : "edit";

    private void OnModeChanged(object sender, RoutedEventArgs e)
    {
        // InitializeComponent 期间 EditModeRadio 的 IsChecked=True 会提前触发 Checked，
        // 此时 PlanModeRadio / StatusText 尚未创建，须跳过以避免 NullReferenceException。
        if (PlanModeRadio == null || EditModeRadio == null) return;
        SetStatus(PlanModeRadio.IsChecked == true
            ? "方案模式：只做调查、给出方案，不修改文件"
            : "编辑模式：大模型可调用智能体执行文件操作");
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

        _messages.Add(new ChatMessage { Role = "你", Text = text, IsUser = true });
        ChatInput.Clear();

        // 在对话流末尾插入「正在输入」气泡（三个蓝点滑动动画）
        var typing = new ChatMessage { Role = "助手", Text = "", IsTyping = true };
        _messages.Add(typing);
        ScrollChatToEnd();

        SetSending(true);
        SetStatus("正在与大模型对话...");
        // 沙箱模式：对话期间轮询授权请求；direct 模式不起表
        _seenApprovalIds.Clear();
        _chatActive = true;
        if (accessMode == "sandbox")
        {
            _approvalTimer.Start();
        }
        string? reviewTicketId = null;
        try
        {
            var resp = await _client.ChatAsync(text, gameDir, enableSearch, thinkLevel,
                GetMode(), accessMode);
            var reply = resp.Reply ?? "（无回复）";
            if (!string.IsNullOrWhiteSpace(resp.AgentUsed))
            {
                reply += $"\n\n〔由智能体 {resp.AgentUsed} 完成〕";
            }
            var thinking = resp.Thinking ?? new List<string>();
            _messages.Add(new ChatMessage
            {
                Role = "助手",
                Text = reply,
                IsUser = false,
                Thinking = thinking,
            });
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
            _messages.Add(new ChatMessage { Role = "助手", Text = $"[错误] {ex.Message}", IsUser = false });
            ScrollChatToEnd();
            SetStatus("对话出错");
        }
        finally
        {
            // 收到回复（或出错）后移除「正在输入」气泡；授权轮询随会话结束停止
            _messages.Remove(typing);
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
        SandboxAccessRadio.IsEnabled = !sending;
        DirectAccessRadio.IsEnabled = !sending;
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