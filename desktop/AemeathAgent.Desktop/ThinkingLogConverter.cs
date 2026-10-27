using System;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Media;

namespace AemeathAgent.Desktop;

/// <summary>
/// 把一条思考过程文本渲染成「终端日志」：
/// 行前 <c>&gt;</c> 前缀；开头的 <c>[接旨]</c>/<c>[规划]</c>/<c>[审议]</c>
/// 之类方括号关键字用青绿高亮，其余为次级灰。
///
/// 返回一个可直接放进 ContentPresenter 的 TextBlock（带多色 Run）。
/// </summary>
public sealed class ThinkingLogConverter : IValueConverter
{
    public static readonly ThinkingLogConverter Instance = new();

    // 明亮生物科技主题：终端日志三色（与主界面文字/数据色一致）
    private static readonly Brush PrefixBrush = new SolidColorBrush(Color.FromRgb(0xA7, 0xAF, 0xB7));
    private static readonly Brush KeyBrush = new SolidColorBrush(Color.FromRgb(0x45, 0xBF, 0xD2));
    private static readonly Brush RestBrush = new SolidColorBrush(Color.FromRgb(0x7F, 0x8A, 0x94));

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        string text = value as string ?? "";
        var tb = new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            FontSize = 11.5,
            LineHeight = 18,
            FontFamily = new FontFamily("Cascadia Code, Consolas, JetBrains Mono"),
        };

        tb.Inlines.Add(new Run("> ") { Foreground = PrefixBrush });

        // 只高亮「行首」的一个 [xxx] 关键字；正文中的括号保持原样。
        var m = System.Text.RegularExpressions.Regex.Match(text, @"^\s*(\[[^\]]+\])");
        if (m.Success)
        {
            tb.Inlines.Add(new Run(m.Groups[1].Value) { Foreground = KeyBrush });
            tb.Inlines.Add(new Run(text.Substring(m.Length)) { Foreground = RestBrush });
        }
        else
        {
            tb.Inlines.Add(new Run(text) { Foreground = RestBrush });
        }
        return tb;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        Binding.DoNothing;
}
