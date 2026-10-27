using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace AemeathAgent.Desktop
{
    /// <summary>
    /// PanelBackground 空值回退：值非空时原样返回（蓝色面板刷），
    /// 为 null 时回退到传入的 ConverterParameter（默认凹槽色）。
    /// 用于 NeuPanel 模板中 Inset 形态的 Bg 背景绑定。
    /// </summary>
    public sealed class PanelBackgroundConverter : IValueConverter
    {
        public static readonly PanelBackgroundConverter Instance = new();

        public object Convert(object value, Type targetType, object parameter,
                              CultureInfo culture)
        {
            if (value is Brush brush)
            {
                return brush;
            }
            if (parameter is Brush fallback)
            {
                return fallback;
            }
            // 理论上不会到达（模板恒传凹槽回退刷）；用透明白兜底
            return new SolidColorBrush(Colors.Transparent);
        }

        public object ConvertBack(object value, Type targetType, object parameter,
                                   CultureInfo culture)
            => Binding.DoNothing;
    }
}
