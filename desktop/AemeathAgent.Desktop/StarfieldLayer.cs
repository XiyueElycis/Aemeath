using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;

namespace AemeathAgent.Desktop;

/// <summary>
/// 星空粒子背景层：深靛夜空上的呼吸星点，主界面的「星海」氛围来源。
///
/// 设计约束（游戏化动效 + 性能预算）：
/// * 星点数 ≤ 120，按窗口面积 / 16000 生成（最低 12 颗）；
/// * 全部动画只作用于 <see cref="UIElement.Opacity"/>（合成线程，无布局失效），
///   每星一条 AutoReverse 循环动画，聚合进一个 Storyboard 一次启动；
/// * 所有 Brush 创建后立即 Freeze，降低渲染开销；
/// * <see cref="IsHitTestVisible"/> 恒为 False，不参与任何交互；
/// * 「减少动画」时通过 <see cref="EnableMotion"/>(false) 停止闪烁，
///   星点静止呈现为静夜图（整层透明度固定 0.55）。
/// </summary>
public sealed class StarfieldLayer : Canvas
{
    // 星色分布：70% 浅紫白 / 18% 爱弥斯粉 / 12% 冰蓝（与三色语义一致）
    private static readonly (Color Color, double Weight)[] Palette =
    {
        (Color.FromRgb(0xED, 0xE9, 0xF7), 0.70),
        (Color.FromRgb(0xFF, 0x8F, 0xC7), 0.18),
        (Color.FromRgb(0x4A, 0x8A, 0xF0), 0.12),
    };

    private Storyboard? _twinkle;
    private bool _motionEnabled = true;
    private readonly Random _rng = new();

    public StarfieldLayer()
    {
        IsHitTestVisible = false;
        UseLayoutRounding = true;
        Loaded += OnLoadedOnce;
        SizeChanged += (_, _) => Regenerate();
    }

    /// <summary>动效开关：false 时停止闪烁循环，星点静态（静夜图）。</summary>
    public void EnableMotion(bool enabled)
    {
        _motionEnabled = enabled;
        if (_twinkle is null) return;
        if (enabled)
        {
            Opacity = 1;
            _twinkle.Begin(this, true);
        }
        else
        {
            _twinkle.Stop(this);
            Opacity = 0.55; // 静夜：整层压暗一点但星仍可见
        }
    }

    private void OnLoadedOnce(object sender, RoutedEventArgs e)
    {
        Loaded -= OnLoadedOnce;
        Regenerate();
    }

    private void Regenerate()
    {
        Children.Clear();
        _twinkle?.Stop(this);
        _twinkle = null;

        if (ActualWidth < 10 || ActualHeight < 10) return;

        double area = Math.Max(1, ActualWidth * ActualHeight);
        int count = Math.Clamp((int)(area / 16000), 12, 120);

        var storyboard = new Storyboard();

        for (int i = 0; i < count; i++)
        {
            if (!TryCreateStar(i, count, out var star, out var anim)) continue;

            // 先光晕后星：光晕在下层
            if (star.Glow != null) Children.Add(star.Glow);
            Children.Add(star.Star);

            if (anim != null)
            {
                // 动画目标直接绑定到具体星点元素（Freezable 冻结前完成绑定）
                Storyboard.SetTarget(anim, star.Star);
                Storyboard.SetTargetProperty(anim, new PropertyPath(OpacityProperty));
                storyboard.Children.Add(anim);
            }
        }

        _twinkle = storyboard;
        if (_motionEnabled)
        {
            _twinkle.Begin(this, true);
        }
        else
        {
            Opacity = 0.55;
        }
    }

    /// <summary>生成一颗星（可能附带静态光晕）与其呼吸动画。</summary>
    private bool TryCreateStar(int index, int total, out (Ellipse Star, Ellipse? Glow) star, out DoubleAnimation? anim)
    {
        star = (null!, null);
        anim = null;

        // 按权重选色
        double roll = _rng.NextDouble();
        double acc = 0;
        var color = Palette[0].Color;
        foreach (var (c, w) in Palette)
        {
            acc += w;
            if (roll <= acc) { color = c; break; }
        }

        // 每 ~20 颗出一颗 3px 主星（亮白 + 同色光晕）；普通星 1–2.4px
        bool major = index % 20 == 7 && index < total;
        double size = major ? 3.0 : 1.0 + _rng.NextDouble() * 1.4;

        var starBrush = new SolidColorBrush(major ? Color.FromRgb(0xFF, 0xFF, 0xFF) : color);
        starBrush.Freeze();
        var dot = new Ellipse { Width = size, Height = size, Fill = starBrush };

        double baseOpacity = 0.25 + _rng.NextDouble() * 0.65;
        dot.Opacity = baseOpacity;

        double x = _rng.NextDouble() * ActualWidth;
        double y = _rng.NextDouble() * ActualHeight;
        SetLeft(dot, x);
        SetTop(dot, y);

        Ellipse? glow = null;
        if (major)
        {
            var glowBrush = new SolidColorBrush(Color.FromArgb(0x33, color.R, color.G, color.B));
            glowBrush.Freeze();
            glow = new Ellipse { Width = 6, Height = 6, Fill = glowBrush };
            SetLeft(glow, x - 1.5);
            SetTop(glow, y - 1.5);
        }

        // 呼吸闪烁：2–6s 随机周期 + 随机错相（BeginTime）
        anim = new DoubleAnimation
        {
            From = baseOpacity,
            To = Math.Max(0.05, baseOpacity - 0.2 - _rng.NextDouble() * 0.15),
            Duration = new Duration(TimeSpan.FromSeconds(2.0 + _rng.NextDouble() * 4.0)),
            AutoReverse = true,
            RepeatBehavior = RepeatBehavior.Forever,
            BeginTime = TimeSpan.FromMilliseconds(_rng.Next(0, 6000)),
        };

        star = (dot, glow);
        return true;
    }
}
