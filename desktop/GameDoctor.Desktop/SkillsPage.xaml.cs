using System.Windows;
using System.Windows.Controls;

namespace GameDoctor.Desktop;

public partial class SkillsPage : Page
{
    private readonly AgentApiClient _client;
    private readonly Action? _onBack;

    public SkillsPage(AgentApiClient client, Action? onBack = null)
    {
        InitializeComponent();
        _client = client;
        _onBack = onBack;
        Loaded += async (_, _) => await LoadAsync();
    }

    private async Task LoadAsync()
    {
        try
        {
            var items = await _client.GetSkillsAsync();
            SkillList.ItemsSource = items;
            StatusText.Text = $"已加载 {items.Count} 个技能";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"加载失败：{ex.Message}";
        }
    }

    private async void OnSaveClick(object sender, RoutedEventArgs e)
    {
        var map = new Dictionary<string, bool>();
        foreach (var item in SkillList.Items)
        {
            if (item is SkillInfo s) map[s.Name] = s.Enabled;
        }
        try
        {
            await _client.UpdateSkillsAsync(map);
            StatusText.Text = "已保存";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"保存失败：{ex.Message}";
        }
    }

    private void OnBackClick(object sender, RoutedEventArgs e) => _onBack?.Invoke();
}