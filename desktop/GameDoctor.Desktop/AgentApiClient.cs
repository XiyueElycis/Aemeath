using System.ComponentModel;
using System.Net.Http;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GameDoctor.Desktop;

/// <summary>智能体基本信息（列表用，含中文名 / 启用状态）。</summary>
public class AgentInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("capabilities")] public List<string> Capabilities { get; set; } = new();
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("enabled")] public bool Enabled { get; set; } = true;
}

/// <summary>智能体执行结果（与后端 AgentResult 序列化结构一致）。</summary>
public class AgentResultDto
{
    [JsonPropertyName("agent")] public string Agent { get; set; } = "";
    [JsonPropertyName("success")] public bool Success { get; set; }
    [JsonPropertyName("message")] public string Message { get; set; } = "";
    [JsonPropertyName("data")] public JsonElement? Data { get; set; }
    [JsonPropertyName("errors")] public List<string> Errors { get; set; } = new();
    [JsonPropertyName("warnings")] public List<string> Warnings { get; set; } = new();
}

/// <summary>LLM 配置项（不含明文密钥，只回传「是否已配置密钥」）。</summary>
public class LlmInfo
{
    [JsonPropertyName("provider")] public string Provider { get; set; } = "";
    [JsonPropertyName("model")] public string Model { get; set; } = "";
    [JsonPropertyName("api_base")] public string ApiBase { get; set; } = "";
    [JsonPropertyName("has_api_key")] public bool HasApiKey { get; set; }
}

/// <summary>LLM provider 目录项（设置面板：大模型选择）。</summary>
public class ProviderInfo
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("api_base")] public string ApiBase { get; set; } = "";
    [JsonPropertyName("default_model")] public string DefaultModel { get; set; } = "";
    [JsonPropertyName("last_model")] public string LastModel { get; set; } = "";
    [JsonPropertyName("models")] public List<string> Models { get; set; } = new();
}

/// <summary>单个智能体的启用开关项（设置面板）。</summary>
public class AgentSettingInfo : INotifyPropertyChanged
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";

    private bool _enabled = true;
    [JsonPropertyName("enabled")]
    public bool Enabled
    {
        get => _enabled;
        set { if (_enabled != value) { _enabled = value; OnPropertyChanged(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}

/// <summary>已保存密钥条目（脱敏展示）。</summary>
public class SavedApiKeyInfo
{
    [JsonPropertyName("provider")] public string Provider { get; set; } = "";
    [JsonPropertyName("masked")] public string Masked { get; set; } = "";
}

/// <summary>已安装技能（Skill）条目，含启用开关。</summary>
public class SkillInfo : INotifyPropertyChanged
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";

    private bool _enabled = true;
    [JsonPropertyName("enabled")]
    public bool Enabled
    {
        get => _enabled;
        set { if (_enabled != value) { _enabled = value; OnPropertyChanged(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}

/// <summary>已安装 MCP（Model Context Protocol）条目，含启用开关。</summary>
public class McpInfo : INotifyPropertyChanged
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("transport")] public string Transport { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";

    private bool _enabled = true;
    [JsonPropertyName("enabled")]
    public bool Enabled
    {
        get => _enabled;
        set { if (_enabled != value) { _enabled = value; OnPropertyChanged(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}

/// <summary>设置面板聚合视图（GET /settings）。</summary>
public class SettingsView
{
    [JsonPropertyName("llm")] public LlmInfo Llm { get; set; } = new();
    [JsonPropertyName("providers")] public List<ProviderInfo> Providers { get; set; } = new();
    [JsonPropertyName("api_keys")] public List<SavedApiKeyInfo> ApiKeys { get; set; } = new();
    [JsonPropertyName("agents")] public List<AgentSettingInfo> Agents { get; set; } = new();
}

/// <summary>对话回复（POST /chat）。</summary>
public class ChatResponse
{
    [JsonPropertyName("reply")] public string Reply { get; set; } = "";
    [JsonPropertyName("agent_used")] public string? AgentUsed { get; set; }
    [JsonPropertyName("result")] public JsonElement? Result { get; set; }
    [JsonPropertyName("thinking")] public List<string>? Thinking { get; set; }
    /// <summary>沙箱会话摘要（direct 模式为 null；0 变更时 change_count=0）。</summary>
    [JsonPropertyName("sandbox")] public SandboxSummary? Sandbox { get; set; }
}

/// <summary>/chat 回包中的沙箱会话摘要。</summary>
public class SandboxSummary
{
    [JsonPropertyName("ticket_id")] public string TicketId { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("change_count")] public int ChangeCount { get; set; }
    [JsonPropertyName("changes")] public List<ChangeInfo> Changes { get; set; } = new();
}

/// <summary>任务授权请求（GET /approvals/pending）。</summary>
public class ApprovalInfo
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("ticket_id")] public string TicketId { get; set; } = "";
    [JsonPropertyName("agent")] public string Agent { get; set; } = "";
    [JsonPropertyName("operation")] public string Operation { get; set; } = "";
    [JsonPropertyName("target")] public string Target { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("summary")] public string Summary { get; set; } = "";
    [JsonPropertyName("created_at")] public double CreatedAt { get; set; }
}

/// <summary>沙箱中的单项变更（会话详情含 old/new 文本预览）。</summary>
public class ChangeInfo
{
    [JsonPropertyName("key")] public string Key { get; set; } = "";
    [JsonPropertyName("relpath")] public string Relpath { get; set; } = "";
    [JsonPropertyName("real_path")] public string RealPath { get; set; } = "";
    [JsonPropertyName("op")] public string Op { get; set; } = "";
    [JsonPropertyName("virtual_path")] public string VirtualPath { get; set; } = "";
    // 目录 / 删除 / 建目录等变更后端给 null，必须可空
    [JsonPropertyName("size")] public long? Size { get; set; }
    [JsonPropertyName("is_text")] public bool IsText { get; set; }
    [JsonPropertyName("backup_path")] public string? BackupPath { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
    [JsonPropertyName("updated_at")] public string UpdatedAt { get; set; } = "";
    [JsonPropertyName("old_text")] public string? OldText { get; set; }
    [JsonPropertyName("new_text")] public string? NewText { get; set; }
    [JsonPropertyName("truncated")] public bool Truncated { get; set; }
}

/// <summary>沙箱会话详情（GET /sandbox/sessions/{id}）。</summary>
public class SandboxInfo
{
    [JsonPropertyName("ticket_id")] public string TicketId { get; set; } = "";
    [JsonPropertyName("real_root")] public string RealRoot { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("created_at")] public string CreatedAt { get; set; } = "";
    [JsonPropertyName("updated_at")] public string UpdatedAt { get; set; } = "";
    [JsonPropertyName("applied_at")] public string? AppliedAt { get; set; }
    [JsonPropertyName("changes")] public List<ChangeInfo> Changes { get; set; } = new();
}

/// <summary>待审核会话条目（GET /sandbox/pending）。</summary>
public class PendingSandboxInfo
{
    [JsonPropertyName("ticket_id")] public string TicketId { get; set; } = "";
    [JsonPropertyName("real_root")] public string RealRoot { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("updated_at")] public string UpdatedAt { get; set; } = "";
    [JsonPropertyName("change_count")] public int ChangeCount { get; set; }
}

/// <summary>apply 逐项结果。</summary>
public class ApplyDetail
{
    [JsonPropertyName("key")] public string Key { get; set; } = "";
    [JsonPropertyName("relpath")] public string Relpath { get; set; } = "";
    [JsonPropertyName("op")] public string Op { get; set; } = "";
    [JsonPropertyName("real_path")] public string RealPath { get; set; } = "";
    [JsonPropertyName("backup_path")] public string? BackupPath { get; set; }
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
}

/// <summary>apply 整体结果（POST /sandbox/sessions/{id}/apply）。</summary>
public class ApplyResult
{
    [JsonPropertyName("ticket_id")] public string TicketId { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("succeeded")] public int Succeeded { get; set; }
    [JsonPropertyName("failed")] public int Failed { get; set; }
    [JsonPropertyName("details")] public List<ApplyDetail> Details { get; set; } = new();
}

/// <summary>当前运行的智能体状态（GET /status current）。</summary>
public class CurrentStatusDto
{
    [JsonPropertyName("agent")] public string Agent { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("phase")] public string Phase { get; set; } = "";
    [JsonPropertyName("path")] public string Path { get; set; } = "";
    [JsonPropertyName("started_at")] public double StartedAt { get; set; }
}

/// <summary>单条操作事件（GET /status events）。</summary>
public class StatusEventDto
{
    [JsonPropertyName("ts")] public double Ts { get; set; }
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("message")] public string Message { get; set; } = "";
    [JsonPropertyName("path")] public string Path { get; set; } = "";
}

/// <summary>运行状态快照（GET /status）。</summary>
public class StatusSnapshot
{
    [JsonPropertyName("current")] public CurrentStatusDto? Current { get; set; }
    [JsonPropertyName("events")] public List<StatusEventDto> Events { get; set; } = new();
}

/// <summary>对 Python 后端的 HTTP 封装。</summary>
public class AgentApiClient
{
    private readonly HttpClient _http;
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public AgentApiClient(string baseUrl)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl) };
        _http.Timeout = TimeSpan.FromMinutes(3);
    }

    public async Task<List<AgentInfo>> GetAgentsAsync()
    {
        return await _http.GetFromJsonAsync<List<AgentInfo>>("/agents", JsonOpts) ?? new();
    }

    public async Task<List<SkillInfo>> GetSkillsAsync()
    {
        return await _http.GetFromJsonAsync<List<SkillInfo>>("/skills", JsonOpts) ?? new();
    }

    public async Task<List<McpInfo>> GetMcpsAsync()
    {
        return await _http.GetFromJsonAsync<List<McpInfo>>("/mcps", JsonOpts) ?? new();
    }

    public async Task<Dictionary<string, bool>> UpdateSkillsAsync(Dictionary<string, bool> skills)
    {
        var req = new { items = skills };
        using var resp = await _http.PostAsJsonAsync("/settings/skills", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<Dictionary<string, bool>>(JsonOpts) ?? new();
    }

    public async Task<Dictionary<string, bool>> UpdateMcpsAsync(Dictionary<string, bool> mcps)
    {
        var req = new { items = mcps };
        using var resp = await _http.PostAsJsonAsync("/settings/mcps", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<Dictionary<string, bool>>(JsonOpts) ?? new();
    }

    public async Task<SettingsView> GetSettingsAsync()
    {
        return await _http.GetFromJsonAsync<SettingsView>("/settings", JsonOpts) ?? new();
    }

    public async Task<StatusSnapshot> GetStatusAsync()
    {
        return await _http.GetFromJsonAsync<StatusSnapshot>("/status", JsonOpts) ?? new();
    }

    public async Task<ChatResponse> ChatAsync(string message, string gameDir, bool enableSearch = false, string thinkLevel = "medium", string mode = "edit", string accessMode = "sandbox")
    {
        var req = new { message, game_dir = gameDir, enable_search = enableSearch, think_level = thinkLevel, mode, access_mode = accessMode };
        using var resp = await _http.PostAsJsonAsync("/chat", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<ChatResponse>(JsonOpts) ?? new();
    }

    // ------------------------------------------------------------------ //
    // 沙箱三段式：授权裁决 / 变更审核
    // ------------------------------------------------------------------ //

    /// <summary>列出待决任务授权请求（可按票据过滤）。</summary>
    public async Task<List<ApprovalInfo>> GetPendingApprovalsAsync(string? ticketId = null)
    {
        string url = "/approvals/pending";
        if (!string.IsNullOrEmpty(ticketId))
            url += "?ticket_id=" + Uri.EscapeDataString(ticketId);
        var result = await _http.GetFromJsonAsync<PendingListWrapper<ApprovalInfo>>(url, JsonOpts);
        return result?.Items ?? new();
    }

    /// <summary>裁决一条授权请求；404/409 等失败状态会抛 HttpRequestException。</summary>
    public async Task DecideApprovalAsync(string id, bool approve, string reason = "")
    {
        var req = new { decision = approve ? "approve" : "reject", reason };
        using var resp = await _http.PostAsJsonAsync($"/approvals/{Uri.EscapeDataString(id)}", req);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>待审核沙箱会话列表（ready 状态）。</summary>
    public async Task<List<PendingSandboxInfo>> GetPendingSandboxesAsync()
    {
        var result = await _http.GetFromJsonAsync<PendingListWrapper<PendingSandboxInfo>>("/sandbox/pending", JsonOpts);
        return result?.Items ?? new();
    }

    /// <summary>沙箱会话详情（含文本变更新旧内容预览）。</summary>
    public async Task<SandboxInfo> GetSandboxSessionAsync(string ticketId)
    {
        return await _http.GetFromJsonAsync<SandboxInfo>(
            $"/sandbox/sessions/{Uri.EscapeDataString(ticketId)}", JsonOpts) ?? new();
    }

    /// <summary>应用沙箱全部变更（服务端先统一备份再落盘）。</summary>
    public async Task<ApplyResult> ApplySandboxAsync(string ticketId)
    {
        using var resp = await _http.PostAsync(
            $"/sandbox/sessions/{Uri.EscapeDataString(ticketId)}/apply", null);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<ApplyResult>(JsonOpts) ?? new();
    }

    /// <summary>丢弃沙箱全部暂存变更（真实文件零变化）。</summary>
    public async Task DiscardSandboxAsync(string ticketId)
    {
        using var resp = await _http.PostAsync(
            $"/sandbox/sessions/{Uri.EscapeDataString(ticketId)}/discard", null);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>后端列表包装：{items:[...], count:N}。</summary>
    private sealed class PendingListWrapper<T>
    {
        [JsonPropertyName("items")] public List<T> Items { get; set; } = new();
        [JsonPropertyName("count")] public int Count { get; set; }
    }

    public async Task<LlmInfo> UpdateLlmAsync(string provider, string model, string apiBase, string apiKey)
    {
        var req = new { provider, model, api_base = apiBase, api_key = apiKey };
        using var resp = await _http.PostAsJsonAsync("/settings/llm", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<LlmInfo>(JsonOpts) ?? new();
    }

    /// <summary>取某厂商已保存的 API Key 明文（切换厂商时自动回填用）。</summary>
    public async Task<string> GetApiKeyAsync(string provider)
    {
        var result = await _http.GetFromJsonAsync<ApiKeyPayload>($"/settings/api-key/{provider}", JsonOpts);
        return result?.ApiKey ?? "";
    }

    /// <summary>删除某厂商已保存的 API Key。</summary>
    public async Task DeleteApiKeyAsync(string provider)
    {
        using var resp = await _http.DeleteAsync($"/settings/api-key/{provider}");
        resp.EnsureSuccessStatusCode();
    }

    private sealed class ApiKeyPayload
    {
        [JsonPropertyName("provider")] public string Provider { get; set; } = "";
        [JsonPropertyName("api_key")] public string ApiKey { get; set; } = "";
    }

    public async Task<Dictionary<string, bool>> UpdateAgentsAsync(Dictionary<string, bool> agents)
    {
        var req = new { agents };
        using var resp = await _http.PostAsJsonAsync("/settings/agents", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<Dictionary<string, bool>>(JsonOpts) ?? new();
    }

    public async Task<AgentResultDto> RunAsync(string gameName, string gameDir, string agent)
    {
        var req = new { game_name = gameName, game_dir = gameDir, agent };
        using var resp = await _http.PostAsJsonAsync("/run", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<AgentResultDto>(JsonOpts) ?? new();
    }

    public async Task<List<AgentResultDto>> AnalyzeAsync(string gameName, string gameDir, List<string>? agents = null)
    {
        var req = new { game_name = gameName, game_dir = gameDir, agents };
        using var resp = await _http.PostAsJsonAsync("/analyze", req);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<List<AgentResultDto>>(JsonOpts) ?? new();
    }
}