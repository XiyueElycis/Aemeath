using System;
using System.ComponentModel;
using System.Text.Json;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace AemeathAgent.Desktop;

/// <summary>心动模式专属窗口：全屏 WebView2 承载 Galgame 页面。</summary>
public partial class GalgameWindow : Window
{
    private readonly string _baseUrl;
    /// <summary>淡出结束后的「真关闭」标志，避免 OnClosing 递归。</summary>
    private bool _reallyClose;

    public GalgameWindow(string baseUrl)
    {
        _baseUrl = (baseUrl ?? "http://127.0.0.1:8765").TrimEnd('/');
        InitializeComponent();
        Loaded += OnLoadedAsync;
        // 初始透明，页面就绪后淡入（与主界面压暗转场同步，不生硬跳切）
        Opacity = 0;
    }

    private async void OnLoadedAsync(object sender, RoutedEventArgs e)
    {
        // 避免 WebView2 初始化前的白屏闪烁
        Web.DefaultBackgroundColor = System.Drawing.Color.FromArgb(0x0E, 0x0A, 0x1F);
        await Web.EnsureCoreWebView2Async();
        Web.CoreWebView2.WebMessageReceived += OnWebMessage;
        Web.CoreWebView2.Navigate($"{_baseUrl}/galgame/");

        // 窗口 0.4s 淡入
        BeginAnimation(OpacityProperty,
            new System.Windows.Media.Animation.DoubleAnimation(0, 1, TimeSpan.FromSeconds(0.4)));
        Loaded -= OnLoadedAsync;
    }

    /// <summary>关闭前先 0.3s 淡出，淡出结束再真正关闭。</summary>
    protected override void OnClosing(CancelEventArgs e)
    {
        // 应用正在退出时不做淡出：取消关闭会让关机流程悬停等待
        bool shuttingDown = Application.Current is null
                            || Application.Current.Dispatcher.HasShutdownStarted;
        if (!_reallyClose && !shuttingDown)
        {
            e.Cancel = true;
            BeginAnimation(OpacityProperty,
                new System.Windows.Media.Animation.DoubleAnimation(Opacity, 0, TimeSpan.FromSeconds(0.3)));
            var timer = new System.Windows.Threading.DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(0.32),
            };
            timer.Tick += (s, _) =>
            {
                ((System.Windows.Threading.DispatcherTimer)s!).Stop();
                _reallyClose = true;
                Close();
            };
            timer.Start();
            return;
        }
        base.OnClosing(e);
    }

    /// <summary>页面「返回控制台」按钮通过 postMessage 通知宿主关窗。</summary>
    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string? message = null;
        try
        {
            message = e.TryGetWebMessageAsString();
        }
        catch
        {
            // 兼容 postMessage({type:"back"}) 对象形式
            try
            {
                using var doc = JsonDocument.Parse(e.WebMessageAsJson);
                if (doc.RootElement.TryGetProperty("type", out var type))
                    message = type.GetString();
            }
            catch { /* 忽略非约定消息 */ }
        }
        if (message == "back")
            Close();
    }
}
