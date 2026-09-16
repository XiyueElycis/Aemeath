using System.Windows;
using System.Windows.Controls;

namespace GameDoctor.Desktop;

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
