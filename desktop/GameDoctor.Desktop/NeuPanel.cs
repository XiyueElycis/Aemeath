using System.Windows;
using System.Windows.Controls;

namespace GameDoctor.Desktop
{
    /// <summary>
    /// Neumorphism（新拟物派）风格卡片容器。
    /// 浅灰同色背景 + 左上高光 / 右下阴影的双重投影，模拟元素从表面"挤压"而出的立体感。
    /// Inset=True 时切换为凹陷（通道/凹槽）形态。
    /// </summary>
    public class NeuPanel : ContentControl
    {
        public static readonly DependencyProperty CornerRadiusProperty =
            DependencyProperty.Register(
                nameof(CornerRadius),
                typeof(CornerRadius),
                typeof(NeuPanel),
                new PropertyMetadata(new CornerRadius(16)));

        /// <summary>卡片圆角（新拟物风格要求 12-24px 柔和曲率）。</summary>
        public CornerRadius CornerRadius
        {
            get => (CornerRadius)GetValue(CornerRadiusProperty);
            set => SetValue(CornerRadiusProperty, value);
        }

        public static readonly DependencyProperty InsetProperty =
            DependencyProperty.Register(
                nameof(Inset),
                typeof(bool),
                typeof(NeuPanel),
                new PropertyMetadata(false));

        /// <summary>凹陷形态：去除外阴影并将背景压暗，用于输入区、凹槽面板。</summary>
        public bool Inset
        {
            get => (bool)GetValue(InsetProperty);
            set => SetValue(InsetProperty, value);
        }
    }
}
