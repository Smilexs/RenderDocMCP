# RenderDoc MCP 服务器

作为 RenderDoc UI 扩展运行的 MCP 服务器。AI 助手可以访问 RenderDoc 的捕获数据，并辅助图形调试。

## 架构

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (Python + FastMCP 2.0)
        │ 基于文件的 IPC (%TEMP%/renderdoc_mcp/)
        ▼
RenderDoc Process (Extension)
```

由于 RenderDoc 内置的 Python 没有 socket 模块，因此使用基于文件的 IPC 进行通信。

## 一次调用的完整链路

以 AI 客户端调用 `get_pipeline_state(event_id=123)` 为例，整个请求会跨越 **3 个进程** 和一个 **文件 IPC 目录**（`%TEMP%/renderdoc_mcp/`）。

### 涉及的三个进程

| 进程 | 角色 | 入口 |
|------|------|------|
| AI 客户端 | 发起工具调用（Claude Desktop / Claude Code 等） | `claude_desktop_config.json` / `.mcp.json` |
| MCP Server | 桥接 LLM 协议与 RenderDoc | `mcp_server/server.py` → FastMCP 2.0 |
| RenderDoc 扩展 | 在 RenderDoc 内部运行，访问 ReplayController | `renderdoc_extension/__init__.py` |

### 文件 IPC 目录约定

`%TEMP%/renderdoc_mcp/` 下使用 3 个文件做同步：

| 文件 | 含义 |
|------|------|
| `request.json` | MCP Server → RenderDoc 的请求 |
| `response.json` | RenderDoc → MCP Server 的响应 |
| `lock` | 写入中标记。存在时表示正在写 `request.json`，对方不应读取 |

### 调用流程（9 步）

1. **客户端发起**：AI 客户端通过 stdio 把工具调用消息发给 `renderdoc-mcp` 进程。
2. **FastMCP 分发**：`server.py` 的 `@mcp.tool` 函数把参数打包成 `{id, method, params}`，交给 `bridge.call(...)`。
3. **写请求**（`bridge/client.py`）：
   - 创建 `lock` 文件 → 写入 `request.json` → 删除 `lock`（表示写入完成）
   - 随后每 50ms 轮询 `response.json`，超时 30s。
4. **读请求**（`renderdoc_extension/socket_server.py`）：后台守护线程每 100ms 轮询。发现 `request.json` 存在且 `lock` 不存在时，读取并删除请求文件。
5. **路由分发**（`request_handler.py`）：通过方法名表查到对应 `_handle_xxx`，调用 `RenderDocFacade` 的同名方法。
6. **Service 分层**（`renderdoc_facade.py`）：Facade 不做具体工作，分派给 6 个职责单一的 Service：`CaptureManager` / `ActionService` / `SearchService` / `ResourceService` / `PipelineService` / `MeshService`。
7. **进入 Replay 线程**：所有需要访问 `ReplayController` 的代码都经 `ctx.Replay().BlockInvoke(callback)` 排到 RenderDoc 的 replay 线程执行，避免线程安全问题。
8. **写响应**：Handler 把返回值打包成 `{id, result}`（或异常时 `{id, error}`），写到 `response.json`。
9. **客户端读响应**：MCP Server 端读取 `response.json` → 删除 → 返回 Python dict → FastMCP 序列化为 MCP 协议消息回传给 AI 客户端。

### 时序图

![调用流程时序图](docs/mermaid-diagram-调用流程.png)

```mermaid
sequenceDiagram
    autonumber
    participant AI as AI Client
    participant MCP as MCP Server<br/>(renderdoc-mcp)
    participant FS as File IPC<br/>(%TEMP%/renderdoc_mcp/)
    participant RD as RenderDoc Extension<br/>(poll thread)
    participant Replay as Replay Thread<br/>(ReplayController)

    AI->>MCP: tool call (stdio)
    MCP->>MCP: @mcp.tool 包装 {id, method, params}
    MCP->>FS: 写 lock
    MCP->>FS: 写 request.json
    MCP->>FS: 删除 lock
    loop 每 100ms 轮询
        RD->>FS: 检查 request.json + 无 lock
    end
    RD->>FS: 读取并删除 request.json
    RD->>RD: RequestHandler 路由 → Facade → Service
    RD->>Replay: BlockInvoke(callback)
    Replay-->>RD: 同步返回结果
    RD->>FS: 写 response.json
    loop 每 50ms 轮询 (最长 30s)
        MCP->>FS: 检查 response.json
    end
    MCP->>FS: 读取并删除 response.json
    MCP-->>AI: 返回 dict（FastMCP 序列化）
```

### 关键设计点

- **没有 socket，用文件 IPC**：RenderDoc 内嵌 Python 3.6 缺少 `socket` / `QtNetwork`，所以用 lock 文件 + JSON 文件做信号同步。`lock` 存在 = 写入中，避免读到半截 JSON。
- **服务分层**：Facade 只做分发，具体逻辑分散在 `services/` 下 6 个 Service，底层共享 `utils/`（解析、序列化、helper），便于扩展新工具。
- **BlockInvoke 线程模型**：访问 `ReplayController` 必须经 `BlockInvoke` 排到 replay 线程，否则 RenderDoc 会崩溃，这是 Facade 强制约束。
- **菜单注册**：扩展加载时还会在 RenderDoc 的 `Tools` 菜单注册 "MCP Bridge → Status" 项，方便确认桥接是否在运行。

## 设置

> **快捷方式**：项目根目录提供 3 个 `.bat` 脚本（Windows），按顺序双击即可完成全部安装与启动：
> - `1.安装RenderDoc扩展.bat` — 把扩展拷贝到 RenderDoc 配置目录
> - `2.安装MCP服务器.bat` — 用 `uv` 安装 `renderdoc-mcp` 命令
> - `3.启动MCP Server.bat` — 手动启动一个 MCP Server 进程（用于调试，正常由客户端拉起）
>
> 下面是对应的命令行步骤，若 bat 脚本不满足需求可参考。

### 1. 安装 RenderDoc 扩展

```bash
python scripts/install_extension.py
```

默认安装到 `%APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge`（Linux/macOS: `~/.local/share/qrenderdoc/extensions/`）。

#### 自定义 / 自编译 RenderDoc（多目标支持）

如果你使用自编译版本（例如可执行文件改名为 `qrenderzzs.exe`，配置目录就会变成 `%APPDATA%\qrenderzzs\`），可以通过以下几种方式指定安装位置，**优先级从高到低**：

1. **命令行参数**（一次性使用，支持多次指定）：
   ```bash
   python scripts/install_extension.py --extension-dir "%APPDATA%\qrenderzzs\extensions"
   python scripts/install_extension.py --extension-dir A --extension-dir B   # 一次装多处
   ```

2. **配置文件 `.renderdocmcp.json`**（项目根目录，已被 `.gitignore` 忽略，适合个人配置）：
   ```json
   {
     "targets": {
       "official":   { "extension_dir": "%APPDATA%/qrenderdoc/extensions" },
       "qrenderzzs": { "extension_dir": "%APPDATA%/qrenderzzs/extensions" }
     },
     "default_targets": ["official", "qrenderzzs"]
   }
   ```
   参考 `.renderdocmcp.json.example`。配置好后直接 `python scripts/install_extension.py` 会同时装到所有 `default_targets`；也可以 `--target qrenderzzs` 只装一个，或 `--target all` 装全部。

3. **环境变量** `RENDERDOC_EXTENSION_DIR`（CI 或临时切换场景）：
   ```bash
   set RENDERDOC_EXTENSION_DIR=%APPDATA%\qrenderzzs\extensions
   python scripts/install_extension.py
   ```
   支持用 `;`（Windows）或 `:`（Unix）分隔多个路径。

卸载同样支持上述参数：`python scripts/install_extension.py uninstall [--target ...]`。

### 2. 在 RenderDoc 中启用扩展

1. 启动 RenderDoc
2. Tools > Manage Extensions
3. 启用 "RenderDoc MCP Bridge"

### 3. 安装 MCP 服务器

```bash
uv tool install
uv tool update-shell  # 添加到 PATH
```

重启 shell 后即可使用 `renderdoc-mcp` 命令。

> **Note**: 添加 `--editable` 后，源代码的修改会立即生效（便于开发）。
> 如果要安装稳定版本，请使用 `uv tool install .`。

### 4. 配置 MCP 客户端

#### Claude Desktop

添加到 `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

#### Claude Code

添加到 `.mcp.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

## 使用方法

1. 启动 RenderDoc，并打开捕获文件 (.rdc)
2. 从 MCP 客户端（如 Claude）访问 RenderDoc 数据

## MCP 工具列表

| 工具 | 说明 |
|--------|------|
| `get_capture_status` | 检查捕获文件的加载状态 |
| `get_frame_summary` | 获取当前帧的统计信息（API、Draw 数、Marker 列表等） |
| `get_draw_calls` | 以层级结构获取绘制调用列表 |
| `get_draw_call_details` | 获取指定绘制调用的详细信息 |
| `get_action_timings` | 获取 GPU 计时（按 event_id / marker 过滤） |
| `find_draws_by_shader` | 按 Shader 名查找使用该 Shader 的 Draw |
| `find_draws_by_texture` | 按贴图名查找使用该贴图的 Draw |
| `find_draws_by_resource` | 按 Resource ID 精确查找使用该资源的 Draw |
| `get_shader_info` | 获取着色器源代码和常量缓冲区的值 |
| `get_buffer_contents` | 获取缓冲区内容 (Base64)，可选 `event_id` 读取瞬态缓冲 |
| `get_texture_info` | 获取纹理元数据 |
| `get_texture_data` | 获取纹理像素数据 (Base64) |
| `get_pipeline_state` | 获取管线状态（含 IA 布局、VB/IB 绑定） |
| `get_mesh_data` | 提取 Draw 的解码后顶点/索引数据（含属性按 format 解析） |
| `list_captures` | 列出目录中的 .rdc 文件 |
| `open_capture` | 在 RenderDoc 中打开指定捕获文件 |

## 使用示例

### 获取绘制调用列表

```
get_draw_calls(include_children=true)
```

### 获取着色器信息

```
get_shader_info(event_id=123, stage="pixel")
```

### 获取管线状态

```
get_pipeline_state(event_id=123)
```

### 获取纹理数据

```
# 获取 2D 纹理的 mip 0
get_texture_data(resource_id="ResourceId::123")

# 获取指定 mip 级别
get_texture_data(resource_id="ResourceId::123", mip=2)

# 获取立方体贴图的指定面 (0=X+, 1=X-, 2=Y+, 3=Y-, 4=Z+, 5=Z-)
get_texture_data(resource_id="ResourceId::456", slice=3)

# 获取 3D 纹理的指定深度切片
get_texture_data(resource_id="ResourceId::789", depth_slice=5)
```

### 部分获取缓冲区数据

```
# 获取整个缓冲区
get_buffer_contents(resource_id="ResourceId::123")

# 从偏移 256 处获取 512 字节
get_buffer_contents(resource_id="ResourceId::123", offset=256, length=512)

# 读取某个 Draw 上的瞬态缓冲（例如常量缓冲上传）
get_buffer_contents(resource_id="ResourceId::123", event_id=456)
```

### 提取 Draw 的几何数据

```
# 一次性拿到 IB + 解码后的所有顶点属性
get_mesh_data(event_id=123)
```

## 要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- RenderDoc 1.20+

> **Note**: 目前仅在 Windows + DirectX 11 环境中进行过运行确认。
> 在 Linux/macOS + Vulkan/OpenGL 环境中也可能运行，但尚未验证。

## 许可证

MIT
