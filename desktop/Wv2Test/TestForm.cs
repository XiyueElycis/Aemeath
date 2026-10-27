using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Wv2Test;

public class TestForm : Form
{
    private readonly WebView2 _wv2 = new();

    public TestForm()
    {
        Text = "Wv2Test";
        FormBorderStyle = FormBorderStyle.None;
        BackColor = Color.Lime;
        TransparencyKey = Color.Lime;
        TopMost = true;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(100, 100);
        Size = new Size(460, 400);

        _wv2.Dock = DockStyle.Fill;
        _wv2.DefaultBackgroundColor = Color.Transparent;
        Controls.Add(_wv2);

        Load += async (_, _) =>
        {
            var env = await CoreWebView2Environment.CreateAsync(
                null,
                null,
                new CoreWebView2EnvironmentOptions
                {
                    AdditionalBrowserArguments = "--disable-features=msWebOOUI,msPdfOOUI"
                });
            await _wv2.EnsureCoreWebView2Async(env);
            _wv2.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "pet.assets",
                @"D:\Python\游戏辅助安装智能体\desktop\AemeathAgent.Desktop\Assets\live2d",
                CoreWebView2HostResourceAccessKind.Allow);
            _wv2.CoreWebView2.Navigate("https://pet.assets/index.html");
        };
    }
}
