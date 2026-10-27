using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AemeathAgent.Desktop;

/// <summary>
/// 主界面装饰偏好：科技网格背景开关、「减少动画」开关。
/// 存于 <c>~/.aemeath_agent/ui.json</c>。这两个偏好是纯桌面端视觉设置，
/// 不进入后端配置，避免改动后端配置 schema。
/// </summary>
public sealed class UiPrefs
{
    [JsonPropertyName("show_grid")]
    public bool ShowGrid { get; set; } = true;

    /// <summary>
    /// 减少动画：停止常驻装饰循环（状态灯呼吸、网格漂移、思考扫描线、
    /// 气泡入场、输入圆点），对应 CSS prefers-reduced-motion。
    /// </summary>
    [JsonPropertyName("reduced_motion")]
    public bool ReducedMotion { get; set; }

    private static string Dir =>
        Path.Combine(
            System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile),
            ".aemeath_agent");

    private static string FilePath => Path.Combine(Dir, "ui.json");

    public static UiPrefs Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<UiPrefs>(File.ReadAllText(FilePath)) ?? new UiPrefs();
        }
        catch
        {
            // 损坏静默回落
        }
        return new UiPrefs();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Dir);
            File.WriteAllText(
                FilePath,
                JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch
        {
            // 写入失败不阻断
        }
    }
}
