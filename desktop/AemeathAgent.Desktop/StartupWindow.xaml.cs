using System;
using System.Text.Json;
using System.Windows;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;

namespace AemeathAgent.Desktop;

/// <summary>
/// 启动动画宿主：无边框全屏 WebView2 加载 <c>/startup/</c>。
///
/// 与页面的契约：
/// * 页面动画结束（或用户 ESC/SKIP）→ postMessage <c>{"type":"done"}</c> → 关窗；
/// * 设置面板改动 → postMessage <c>{"type":"prefs", ...}</c> → 写回 StartupPrefs。
/// </summary>
public partial class StartupWindow : Window
{
    private readonly string _baseUrl;
    private readonly StartupPrefs _prefs;
    private int _navRetries;
    private readonly DispatcherTimer _fallback;

    public StartupWindow(string baseUrl, StartupPrefs prefs)
    {
        _baseUrl = (baseUrl ?? "http://127.0.0.1:8765").TrimEnd('/');
        _prefs = prefs ?? new StartupPrefs();
        InitializeComponent();
        Loaded += OnLoadedAsync;
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };

        // 兜底：页面异常（脚本挂死/导航失败）时 30s 后自动进入主界面，
        // 保证动画永远不会把用户挡在应用之外。
        _fallback = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
        _fallback.Tick += (_, _) => Close();
        _fallback.Start();
    }

    private async void OnLoadedAsync(object sender, RoutedEventArgs e)
    {
        Loaded -= OnLoadedAsync;
        Web.DefaultBackgroundColor = System.Drawing.Color.FromArgb(0x06, 0x04, 0x0E);
        await Web.EnsureCoreWebView2Async();
        Web.CoreWebView2.WebMessageReceived += OnWebMessage;
        Web.NavigationCompleted += OnNavigationCompleted;
        Navigate();
    }

    private void Navigate()
    {
        string sound = _prefs.Sound ? "1" : "0";
        Web.CoreWebView2.Navigate(
            $"{_baseUrl}/startup/?sound={sound}&vol={_prefs.Volume}");
    }

    /// <summary>后端可能尚未就绪：导航失败时每 500ms 重试，最多 60 次（30s）。</summary>
    private void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (e.IsSuccess)
            return;
        if (++_navRetries <= 60)
        {
            var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(500) };
            timer.Tick += (s, _) => { ((DispatcherTimer)s!).Stop(); Navigate(); };
            timer.Start();
        }
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        JsonElement root;
        try
        {
            // 页面侧是 postMessage(JSON.stringify(obj))，即发送**字符串**。
            // 此时 WebMessageAsJson 是「字符串的 JSON 表示」（形如 "{\"type\":\"done\"}"），
            // 直接解析会得到 ValueKind=String 而不是 Object，必须先按字符串取出再解析。
            // 同时兼容直接 postMessage(obj) 的对象形式（此时 TryGetWebMessageAsString 抛错）。
            string? asString = null;
            try { asString = e.TryGetWebMessageAsString(); }
            catch { asString = null; }

            using var doc = string.IsNullOrEmpty(asString)
                ? JsonDocument.Parse(e.WebMessageAsJson)
                : JsonDocument.Parse(asString);
            root = doc.RootElement.Clone();
        }
        catch
        {
            return; // 非约定消息忽略
        }

        if (root.ValueKind != JsonValueKind.Object)
            return;

        string type = root.TryGetProperty("type", out var t) ? t.GetString() ?? "" : "";

        if (type == "done")
        {
            Close();
        }
        else if (type == "prefs")
        {
            if (root.TryGetProperty("mode", out var mode))
                _prefs.Mode = mode.GetString() ?? _prefs.Mode;
            if (root.TryGetProperty("sound", out var sound))
                _prefs.Sound = sound.GetBoolean();
            if (root.TryGetProperty("volume", out var volume))
                _prefs.Volume = Math.Clamp(volume.GetInt32(), 0, 100);
            _prefs.Save();
        }
    }

    protected override void OnClosed(EventArgs e)
    {
        _fallback.Stop();
        base.OnClosed(e);
    }
}
