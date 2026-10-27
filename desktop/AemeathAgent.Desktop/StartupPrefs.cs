using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AemeathAgent.Desktop;

/// <summary>
/// 启动动画偏好：每次播放 / 仅首次 / 关闭，以及音效与音量。
/// 以独立 JSON 存于 <c>~/.aemeath_agent/startup.json</c>，
/// 不依赖后端设置（启动动画在主窗口加载前播放，后端字段尚不可用）。
/// </summary>
public sealed class StartupPrefs
{
    /// <summary>always=每次播放；first=仅首次播放；never=关闭。</summary>
    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "always";

    /// <summary>仅首次模式下，是否已经完整看过一次。</summary>
    [JsonPropertyName("seen")]
    public bool Seen { get; set; }

    [JsonPropertyName("sound")]
    public bool Sound { get; set; } = true;

    [JsonPropertyName("volume")]
    public int Volume { get; set; } = 60;

    /// <summary>本次启动是否应播放动画（由模式与 seen 共同决定）。</summary>
    public bool ShouldPlay => Mode switch
    {
        "never" => false,
        "first" => !Seen,
        _ => true,
    };

    private static string Dir =>
        Path.Combine(
            System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile),
            ".aemeath_agent");

    private static string FilePath => Path.Combine(Dir, "startup.json");

    public static StartupPrefs Load()
    {
        try
        {
            if (File.Exists(FilePath))
            {
                var loaded = JsonSerializer.Deserialize<StartupPrefs>(File.ReadAllText(FilePath));
                if (loaded != null)
                {
                    loaded.Volume = System.Math.Clamp(loaded.Volume, 0, 100);
                    return loaded;
                }
            }
        }
        catch
        {
            // 配置损坏时静默回落默认值，绝不阻断启动
        }
        return new StartupPrefs();
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
            // 偏好写入失败不影响主流程
        }
    }
}
