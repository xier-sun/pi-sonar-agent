using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

var options = new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    WriteIndented = false,
};

if (args.Length == 0 || args[0] == "--probe")
{
    var probe = new
    {
        available = true,
        engine = "AgentFixEngine",
        supportedRules = new[] { "csharpsquid:S107" },
        mode = "analysis_only",
    };
    Console.WriteLine(JsonSerializer.Serialize(probe, options));
    return;
}

var rawRequest = Console.In.ReadToEnd();
if (string.IsNullOrWhiteSpace(rawRequest))
{
    Console.WriteLine(JsonSerializer.Serialize(
        new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:invalid_request",
            Error = "stdin request payload is empty",
        },
        options));
    return;
}

FixRequest? request;
try
{
    request = JsonSerializer.Deserialize<FixRequest>(rawRequest, options);
}
catch (Exception ex)
{
    Console.WriteLine(JsonSerializer.Serialize(
        new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:invalid_request",
            Error = $"failed to parse request: {ex.Message}",
        },
        options));
    return;
}

if (request is null)
{
    Console.WriteLine(JsonSerializer.Serialize(
        new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:invalid_request",
            Error = "request payload resolved to null",
        },
        options));
    return;
}

var response = args[0] switch
{
    "--request" => HandleSingleFileRequest(request),
    "--solution-request" => HandleSolutionRequest(request),
    _ => new FixResponse
    {
        Applied = false,
        Strategy = "roslyn:unsupported_mode",
        Error = $"unsupported mode: {args[0]}",
    },
};

Console.WriteLine(JsonSerializer.Serialize(response, options));

static FixResponse HandleSingleFileRequest(FixRequest request)
{
    if (!string.Equals(request.RuleId, "csharpsquid:S107", StringComparison.OrdinalIgnoreCase))
    {
        return new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:unsupported_rule",
            Summary = $"rule `{request.RuleId}` is not implemented in the Roslyn engine yet.",
        };
    }

    var content = request.FileContent ?? string.Empty;
    var lines = content.Replace("\r\n", "\n").Split('\n');
    return AnalyzeS107Request(
        request,
        lines,
        request.FilePath,
        scanWorkspace: false,
        workspaceRoot: request.WorkspaceRoot);
}

static FixResponse HandleSolutionRequest(FixRequest request)
{
    if (!string.Equals(request.RuleId, "csharpsquid:S107", StringComparison.OrdinalIgnoreCase))
    {
        return new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:unsupported_rule",
            Summary = $"rule `{request.RuleId}` is not implemented in the Roslyn engine yet.",
        };
    }

    var targetPath = ResolveTargetPath(request);
    if (targetPath is null || !File.Exists(targetPath))
    {
        return new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:file_missing",
            Error = $"unable to resolve target file for `{request.FilePath}`",
        };
    }

    var lines = File.ReadAllLines(targetPath);
    return AnalyzeS107Request(
        request,
        lines,
        request.FilePath,
        scanWorkspace: true,
        workspaceRoot: request.WorkspaceRoot);
}

static FixResponse AnalyzeS107Request(
    FixRequest request,
    string[] lines,
    string? relativePath,
    bool scanWorkspace,
    string? workspaceRoot)
{
    var declaration = FindMethodDeclaration(lines, request.StartLine);
    if (declaration is null)
    {
        return new FixResponse
        {
            Applied = false,
            Strategy = "roslyn:s107_method_not_found",
            Summary = "could not locate a method declaration near the Sonar issue line",
        };
    }

    var safetyFlags = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    if (declaration.ParameterCount <= 7)
    {
        safetyFlags.Add("parameter_count_not_exceeding_threshold");
    }

    if (!string.Equals(declaration.AccessModifier, "private", StringComparison.OrdinalIgnoreCase)
        && !string.Equals(declaration.AccessModifier, "internal", StringComparison.OrdinalIgnoreCase))
    {
        safetyFlags.Add("public_or_protected_surface");
    }

    if (declaration.IsOverrideOrVirtual)
    {
        safetyFlags.Add("override_or_virtual_member");
    }

    if (declaration.IsPartial)
    {
        safetyFlags.Add("partial_member");
    }

    if (declaration.HasParameterModifiers)
    {
        safetyFlags.Add("parameter_modifier_present");
    }

    if (scanWorkspace && !string.IsNullOrWhiteSpace(workspaceRoot) && Directory.Exists(workspaceRoot))
    {
        var propagation = ScanPropagationRisk(
            workspaceRoot,
            relativePath ?? string.Empty,
            declaration.MethodName,
            declaration.StartLine,
            declaration.EndLine);

        foreach (var flag in propagation.SafetyFlags)
        {
            safetyFlags.Add(flag);
        }
    }

    if (safetyFlags.Count > 0)
    {
        return new FixResponse
        {
            Applied = false,
            CanFixSafely = false,
            Strategy = "roslyn:s107_cannot_fix_safely",
            Summary = "S107 candidate rejected by Roslyn safety analysis; keep the issue on a specialized/manual path.",
            SafetyFlags = safetyFlags.OrderBy(flag => flag, StringComparer.OrdinalIgnoreCase).ToArray(),
        };
    }

    return new FixResponse
    {
        Applied = false,
        CanFixSafely = true,
        Strategy = "roslyn:s107_candidate_identified",
        Summary = "S107 candidate is safe for a C# 8 parameter-object refactor, but patch generation is not enabled yet.",
        SafetyFlags = Array.Empty<string>(),
    };
}

static string? ResolveTargetPath(FixRequest request)
{
    if (!string.IsNullOrWhiteSpace(request.WorkspaceRoot) && !string.IsNullOrWhiteSpace(request.FilePath))
    {
        var candidate = Path.Combine(
            request.WorkspaceRoot,
            request.FilePath.Replace('/', Path.DirectorySeparatorChar));
        if (File.Exists(candidate))
        {
            return candidate;
        }
    }

    if (!string.IsNullOrWhiteSpace(request.FilePath) && File.Exists(request.FilePath))
    {
        return request.FilePath;
    }

    return null;
}

static MethodDeclaration? FindMethodDeclaration(string[] lines, int startLine)
{
    if (lines.Length == 0)
    {
        return null;
    }

    var searchStart = Math.Max(0, startLine - 6);
    var searchEnd = Math.Min(lines.Length - 1, Math.Max(startLine + 4, startLine));
    for (var lineIndex = searchStart; lineIndex <= searchEnd; lineIndex++)
    {
        var signature = CollectSignature(lines, lineIndex);
        if (signature is null)
        {
            continue;
        }

        var match = Regex.Match(
            signature.Text,
            @"\b(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?<params>.*)\)",
            RegexOptions.Singleline);
        if (!match.Success)
        {
            continue;
        }

        var methodName = match.Groups["name"].Value.Trim();
        if (string.IsNullOrWhiteSpace(methodName))
        {
            continue;
        }

        var parameterList = match.Groups["params"].Value;
        var parameters = SplitTopLevel(parameterList, ',');
        var normalizedSignature = $" {signature.Text} ";
        var accessModifier = new[] { "private", "internal", "public", "protected" }
            .FirstOrDefault(token => normalizedSignature.Contains($" {token} ", StringComparison.OrdinalIgnoreCase))
            ?? string.Empty;

        return new MethodDeclaration(
            methodName,
            accessModifier,
            parameters.Length,
            signature.StartLine + 1,
            signature.EndLine + 1,
            normalizedSignature.Contains(" override ", StringComparison.OrdinalIgnoreCase)
                || normalizedSignature.Contains(" virtual ", StringComparison.OrdinalIgnoreCase),
            normalizedSignature.Contains(" partial ", StringComparison.OrdinalIgnoreCase),
            parameters.Any(parameter => Regex.IsMatch(parameter, @"\b(ref|out|in|params)\b")));
    }

    return null;
}

static SignatureWindow? CollectSignature(string[] lines, int startIndex)
{
    var currentLine = lines[startIndex].Trim();
    if (string.IsNullOrWhiteSpace(currentLine) || currentLine.StartsWith("//", StringComparison.Ordinal))
    {
        return null;
    }

    var builder = new List<string>();
    var openParenSeen = false;
    var closeParenSeen = false;
    for (var index = startIndex; index < Math.Min(lines.Length, startIndex + 8); index++)
    {
        var text = lines[index].Trim();
        if (string.IsNullOrWhiteSpace(text) || text.StartsWith("//", StringComparison.Ordinal))
        {
            continue;
        }

        builder.Add(text);
        if (text.Contains('('))
        {
            openParenSeen = true;
        }

        if (text.Contains(')'))
        {
            closeParenSeen = true;
        }

        if (openParenSeen && closeParenSeen)
        {
            return new SignatureWindow(string.Join(" ", builder), startIndex, index);
        }
    }

    return null;
}

static string[] SplitTopLevel(string text, char separator)
{
    var parts = new List<string>();
    var current = new List<char>();
    var angleDepth = 0;
    var roundDepth = 0;
    var squareDepth = 0;

    foreach (var ch in text)
    {
        switch (ch)
        {
            case '<':
                angleDepth++;
                current.Add(ch);
                break;
            case '>':
                angleDepth = Math.Max(0, angleDepth - 1);
                current.Add(ch);
                break;
            case '(':
                roundDepth++;
                current.Add(ch);
                break;
            case ')':
                roundDepth = Math.Max(0, roundDepth - 1);
                current.Add(ch);
                break;
            case '[':
                squareDepth++;
                current.Add(ch);
                break;
            case ']':
                squareDepth = Math.Max(0, squareDepth - 1);
                current.Add(ch);
                break;
            default:
                if (ch == separator && angleDepth == 0 && roundDepth == 0 && squareDepth == 0)
                {
                    var part = new string(current.ToArray()).Trim();
                    if (!string.IsNullOrWhiteSpace(part))
                    {
                        parts.Add(part);
                    }

                    current.Clear();
                    break;
                }

                current.Add(ch);
                break;
        }
    }

    var tail = new string(current.ToArray()).Trim();
    if (!string.IsNullOrWhiteSpace(tail))
    {
        parts.Add(tail);
    }

    return parts.ToArray();
}

static PropagationRisk ScanPropagationRisk(
    string workspaceRoot,
    string issueRelativePath,
    string methodName,
    int declarationStartLine,
    int declarationEndLine)
{
    var flags = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    var issuePath = issueRelativePath.Replace('\\', '/').TrimStart('/');
    var propagationBudget = 0;

    foreach (var file in Directory.EnumerateFiles(workspaceRoot, "*.cs", SearchOption.AllDirectories))
    {
        var relative = Path.GetRelativePath(workspaceRoot, file).Replace('\\', '/');
        if (relative.Contains("/bin/", StringComparison.OrdinalIgnoreCase)
            || relative.Contains("/obj/", StringComparison.OrdinalIgnoreCase))
        {
            continue;
        }

        var lines = File.ReadAllLines(file);
        for (var lineIndex = 0; lineIndex < lines.Length; lineIndex++)
        {
            var text = lines[lineIndex].Trim();
            if (string.IsNullOrWhiteSpace(text) || text.StartsWith("//", StringComparison.Ordinal))
            {
                continue;
            }

            if (string.Equals(relative, issuePath, StringComparison.OrdinalIgnoreCase)
                && lineIndex + 1 >= declarationStartLine
                && lineIndex + 1 <= declarationEndLine)
            {
                continue;
            }

            if (!Regex.IsMatch(text, $@"\b{Regex.Escape(methodName)}\s*\(")
                && !text.Contains($"nameof({methodName})", StringComparison.Ordinal))
            {
                continue;
            }

            propagationBudget++;

            if (relative.Contains("/Interfaces/", StringComparison.OrdinalIgnoreCase))
            {
                flags.Add("interface_propagation_target");
            }

            if (relative.Contains("/Controllers/", StringComparison.OrdinalIgnoreCase)
                || relative.EndsWith("Controller.cs", StringComparison.OrdinalIgnoreCase))
            {
                flags.Add("controller_propagation_target");
            }

            var normalized = $" {text} ";
            if (normalized.Contains(" public ", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains(" protected ", StringComparison.OrdinalIgnoreCase))
            {
                flags.Add("public_or_protected_propagation_target");
            }
        }
    }

    if (propagationBudget > 6)
    {
        flags.Add("propagation_budget_exceeded");
    }

    return new PropagationRisk(flags.ToArray(), propagationBudget);
}

internal sealed record SignatureWindow(string Text, int StartLine, int EndLine);

internal sealed record MethodDeclaration(
    string MethodName,
    string AccessModifier,
    int ParameterCount,
    int StartLine,
    int EndLine,
    bool IsOverrideOrVirtual,
    bool IsPartial,
    bool HasParameterModifiers);

internal sealed record PropagationRisk(string[] SafetyFlags, int PropagationBudget);

internal sealed class FixRequest
{
    [JsonPropertyName("ruleId")]
    public string RuleId { get; init; } = string.Empty;

    [JsonPropertyName("solutionPath")]
    public string? SolutionPath { get; init; }

    [JsonPropertyName("workspaceRoot")]
    public string? WorkspaceRoot { get; init; }

    [JsonPropertyName("filePath")]
    public string? FilePath { get; init; }

    [JsonPropertyName("fileContent")]
    public string? FileContent { get; init; }

    [JsonPropertyName("startLine")]
    public int StartLine { get; init; }

    [JsonPropertyName("endLine")]
    public int EndLine { get; init; }
}

internal sealed class FixResponse
{
    [JsonPropertyName("applied")]
    public bool Applied { get; init; }

    [JsonPropertyName("updatedFileContent")]
    public string? UpdatedFileContent { get; init; }

    [JsonPropertyName("strategy")]
    public string Strategy { get; init; } = string.Empty;

    [JsonPropertyName("summary")]
    public string Summary { get; init; } = string.Empty;

    [JsonPropertyName("error")]
    public string Error { get; init; } = string.Empty;

    [JsonPropertyName("canFixSafely")]
    public bool CanFixSafely { get; init; }

    [JsonPropertyName("safetyFlags")]
    public string[] SafetyFlags { get; init; } = Array.Empty<string>();

    [JsonPropertyName("changedFiles")]
    public Dictionary<string, string>? ChangedFiles { get; init; }
}
