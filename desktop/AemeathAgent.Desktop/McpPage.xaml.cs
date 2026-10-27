using System.Collections.ObjectModel;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace AemeathAgent.Desktop;

public partial class McpPage : Page
{
    private readonly AgentApiClient _client;
    private readonly Action? _onBack;
    private readonly ObservableCollection<McpServerInfo> _servers = new();

    public McpPage(AgentApiClient client, Action? onBack = null)
    {
        InitializeComponent();
        _client = client;
        _onBack = onBack;
        ServerList.ItemsSource = _servers;
        Loaded += async (_, _) => await LoadAsync();
    }

    private async Task LoadAsync()
    {
        try
        {
            var items = await _client.GetMcpsAsync();
            McpList.ItemsSource = items;
            await LoadServersAsync();
            StatusText.Text = $"已加载 {items.Count} 个内置 MCP，{_servers.Count} 个外部服务器";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"加载失败：{ex.Message}";
        }
    }

    private async Task LoadServersAsync()
    {
        var rows = await _client.GetMcpServersAsync();
        _servers.Clear();
        foreach (var row in rows) _servers.Add(row);
    }

    private async void OnSaveClick(object sender, RoutedEventArgs e)
    {
        var map = new Dictionary<string, bool>();
        foreach (var item in McpList.Items)
        {
            if (item is McpInfo m) map[m.Name] = m.Enabled;
        }
        try
        {
            await _client.UpdateMcpsAsync(map);
            StatusText.Text = "已保存";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"保存失败：{ex.Message}";
        }
    }

    /// <summary>
    /// 添加服务器：命令既支持空格分隔（npx -y xxx），也支持 JSON 数组
    /// （["node", "路径带 空格/server.js"]），后者用于参数里含空格的场景。
    /// </summary>
    private async void OnAddServerClick(object sender, RoutedEventArgs e)
    {
        var name = ServerNameBox.Text.Trim();
        var raw = ServerCommandBox.Text.Trim();
        if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(raw))
        {
            StatusText.Text = "服务器名和启动命令都要填";
            return;
        }

        List<string> command;
        if (raw.StartsWith("["))
        {
            try
            {
                command = JsonSerializer.Deserialize<List<string>>(raw) ?? new();
            }
            catch (JsonException)
            {
                StatusText.Text = "命令的 JSON 数组格式不正确";
                return;
            }
        }
        else
        {
            command = raw.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList();
        }
        if (command.Count == 0)
        {
            StatusText.Text = "启动命令不能为空";
            return;
        }

        try
        {
            await _client.SaveMcpServerAsync(new McpServerInfo
            {
                Name = name,
                Command = command,
                Enabled = true,
            });
            ServerNameBox.Text = "";
            ServerCommandBox.Text = "";
            await LoadServersAsync();
            StatusText.Text = $"已添加服务器 {name}，点「测试」可验证能否启动";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"添加失败：{ex.Message}";
        }
    }

    private async void OnDeleteServerClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string name }) return;
        try
        {
            await _client.DeleteMcpServerAsync(name);
            await LoadServersAsync();
            StatusText.Text = $"已删除服务器 {name}";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"删除失败：{ex.Message}";
        }
    }

    private async void OnProbeServerClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string name }) return;
        StatusText.Text = $"正在启动 {name} ...";
        try
        {
            var result = await _client.ProbeMcpServerAsync(name);
            if (!result.Ok)
            {
                StatusText.Text = $"{name} 启动失败：{result.Error}";
                return;
            }
            var tools = string.Join("、", result.Tools.Select(t => t.Name));
            StatusText.Text = result.Tools.Count == 0
                ? $"{name} 已启动，但没有提供工具"
                : $"{name} 可用工具（{result.Tools.Count}）：{tools}";
        }
        catch (Exception ex)
        {
            StatusText.Text = $"测试失败：{ex.Message}";
        }
    }

    private void OnBackClick(object sender, RoutedEventArgs e) => _onBack?.Invoke();
}
