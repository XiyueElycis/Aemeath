using System.Windows;

namespace AemeathAgent.Desktop;

/// <summary>
/// 动效门控附加属性。
///
/// <c>Fx.MotionEnabled</c> 是**可继承**的附加属性：在窗口上设置一次，
/// 所有模板内的元素（聊天气泡容器、输入圆点）都能通过 DataTrigger 读到，
/// 从而在「减少动画」时不启动入场/循环故事板。
/// </summary>
public static class Fx
{
    public static readonly DependencyProperty MotionEnabledProperty =
        DependencyProperty.RegisterAttached(
            "MotionEnabled",
            typeof(bool),
            typeof(Fx),
            new FrameworkPropertyMetadata(
                true,
                FrameworkPropertyMetadataOptions.Inherits));

    public static bool GetMotionEnabled(DependencyObject obj) =>
        (bool)obj.GetValue(MotionEnabledProperty);

    public static void SetMotionEnabled(DependencyObject obj, bool value) =>
        obj.SetValue(MotionEnabledProperty, value);
}
