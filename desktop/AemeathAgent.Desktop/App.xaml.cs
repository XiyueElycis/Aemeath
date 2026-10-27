using System.Windows;
using System.Windows.Controls;

namespace AemeathAgent.Desktop;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // WPF 的 TextBlock 原生不支持文字拖选，全局挂一个右键「复制」菜单兜底；
        // 聊天气泡、事件流等长文本另用 SelectableText（只读 TextBox）支持拖选。
        EventManager.RegisterClassHandler(
            typeof(TextBlock),
            FrameworkElement.LoadedEvent,
            new RoutedEventHandler(OnTextBlockLoaded));

        // ---------------------------------------------------------- //
        // 启动序列：启动动画（按偏好）→ 主控制台
        // ---------------------------------------------------------- //
        var startupPrefs = StartupPrefs.Load();

        if (startupPrefs.ShouldPlay)
        {
            // 注意：不能直接写 MainWindow.GetBaseUrl() —— 在 App 类中
            // 「MainWindow」会优先解析为继承自 Application 的 MainWindow 属性，
            // 必须用类型完全限定。
            string api = global::AemeathAgent.Desktop.MainWindow.GetBaseUrl();
            var startup = new StartupWindow(api, startupPrefs);
            startup.ShowDialog();
        }

        // 仅首次模式：完整看过（或跳过）即标记，下次不再播放
        if (startupPrefs.Mode == "first")
        {
            startupPrefs.Seen = true;
            startupPrefs.Save();
        }

        var mainWindow = new MainWindow();
        mainWindow.Show();

        // 主窗口已存在：恢复默认的「最后一个窗口关闭即退出」
        ShutdownMode = ShutdownMode.OnLastWindowClose;
    }

    private static void OnTextBlockLoaded(object sender, RoutedEventArgs e)
    {
        if (sender is not TextBlock tb || tb.ContextMenu != null || string.IsNullOrEmpty(tb.Text))
        {
            return;
        }

        var menu = new ContextMenu();
        var copyItem = new MenuItem { Header = "复制" };
        copyItem.Click += (_, _) =>
        {
            if (menu.PlacementTarget is TextBlock target)
            {
                Clipboard.SetText(target.Text);
            }
        };
        menu.Items.Add(copyItem);
        tb.ContextMenu = menu;
    }
}
