# RenderDoc MCP 服务器

作为 RenderDoc UI 扩展运行的 MCP 服务器。AI 助手可以访问 RenderDoc 的捕获数据，并辅助图形调试。

## 架构

**混合进程隔离方式**：

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (标准 Python + FastMCP 2.0)
        │ 基于文件的 IPC (%TEMP%/renderdoc_mcp/)
        ▼
RenderDoc Process (Extension + File Polling)
```

## 项目结构

```
RenderDocMCP/
├── mcp_server/                        # MCP 服务器
│   ├── server.py                      # FastMCP 入口，注册 MCP 工具
│   ├── config.py                      # 配置
│   └── bridge/
│       └── client.py                  # 基于文件的 IPC 客户端
│
├── renderdoc_extension/               # RenderDoc 扩展
│   ├── __init__.py                    # register()/unregister()
│   ├── extension.json                 # 扩展清单
│   ├── socket_server.py               # 基于文件的 IPC 服务端
│   ├── request_handler.py             # 请求路由与处理
│   ├── renderdoc_facade.py            # RenderDoc API Facade
│   ├── services/                      # 具体功能服务
│   │   ├── capture_manager.py         # 捕获文件管理
│   │   ├── action_service.py          # Draw/Action 查询与统计
│   │   ├── search_service.py          # Shader/Texture/Resource 反向搜索
│   │   ├── resource_service.py        # Buffer/Texture 数据读取
│   │   ├── pipeline_service.py        # Shader 与 Pipeline State
│   │   └── mesh_service.py            # Mesh 顶点/索引解码
│   └── utils/                         # 解析、序列化、辅助函数
│
├── docs/                              # 文档资源
├── scripts/
│   └── install_extension.py           # 扩展安装脚本
├── 1.安装RenderDoc扩展.bat             # Windows 快捷安装脚本
├── 2.安装MCP服务器.bat                 # Windows 快捷安装脚本
└── 3.启动MCP Server.bat                # Windows 调试启动脚本
```

## MCP 工具

当前可用接口与 `README.md` 保持一致：

| 工具名 | 说明 |
|--------|------|
| `ping` | 检查 RenderDoc MCP Bridge 是否可达 |
| `get_capture_status` | 检查捕获文件的加载状态 |
| `get_frame_summary` | 获取当前帧的统计信息（API、Draw 数、Marker 列表等） |
| `get_draw_calls` | 以层级结构获取绘制调用列表 |
| `get_draw_call_details` | 获取指定绘制调用的详细信息 |
| `get_action_timings` | 获取 GPU 计时（按 event_id / marker 过滤） |
| `enumerate_counters` | 列出当前捕获可用的 GPU performance counters |
| `fetch_counters` | 按 counter ID 获取 GPU counter 数值 |
| `get_debug_messages` | 获取 API validation / driver debug messages |
| `debug_pixel` | 调试指定 event 下某个屏幕像素的 Pixel Shader 执行过程 |
| `debug_vertex` | 调试指定 event 下某个顶点的 Vertex Shader 执行过程 |
| `find_draws_by_shader` | 按 Shader 名查找使用该 Shader 的 Draw |
| `find_draws_by_texture` | 按贴图名查找使用该贴图的 Draw |
| `find_draws_by_resource` | 按 Resource ID 精确查找使用该资源的 Draw |
| `get_shader_info` | 获取着色器源代码和常量缓冲区的值 |
| `get_bound_textures` | 获取指定 event/stage 绑定的纹理，并推断 albedo/normal/roughness 等用途 |
| `get_buffer_contents` | 获取缓冲区内容 (Base64)，可选 `event_id` 读取瞬态缓冲 |
| `get_textures` | 列出当前捕获中的所有纹理资源 |
| `get_buffers` | 列出当前捕获中的所有 buffer 资源 |
| `get_resources` | 列出当前捕获中的所有 RenderDoc resources |
| `get_texture_info` | 获取纹理元数据 |
| `get_texture_data` | 获取纹理像素数据 (Base64) |
| `pick_pixel` | 读取指定纹理/RT 的单个像素 RGBA 值 |
| `pixel_history` | 获取指定 RT 像素在整帧中的修改历史 |
| `get_pipeline_state` | 获取管线状态（含 IA 布局、VB/IB 绑定） |
| `get_mesh_data` | 提取 Draw 的解码后顶点/索引数据（含属性按 format 解析） |
| `list_captures` | 列出目录中的 .rdc 文件 |
| `open_capture` | 在 RenderDoc 中打开指定捕获文件 |
| `launch_renderdoc` | 启动 qrenderdoc 并打开 .rdc，等待 MCP Bridge ready |

### get_draw_calls 过滤选项

```python
get_draw_calls(
    include_children=True,      # 包含子 Action
    marker_filter="Camera.Render",  # 只获取该 Marker 下的内容
    exclude_markers=["GUI.Repaint", "UIR.DrawChain"],  # 要排除的 Marker
    event_id_min=7372,          # event_id 范围起点
    event_id_max=7600,          # event_id 范围终点
    only_actions=True,          # 排除 Marker，只返回实际 Action
    flags_filter=["Drawcall", "Dispatch"],  # 只返回指定 flag
)
```

### 捕获管理工具

```python
# 列出目录中的捕获文件
list_captures(directory="D:\\captures")
# → {"count": 3, "captures": [{"filename": "game.rdc", "path": "...", "size_bytes": 12345, "modified_time": "..."}, ...]}

# 打开捕获文件（已有捕获会自动关闭）
open_capture(capture_path="D:\\captures\\game.rdc")
# → {"success": true, "filename": "game.rdc", "api": "D3D11"}
```

### 反向搜索工具

```python
# 按 Shader 名搜索（部分匹配）
find_draws_by_shader(shader_name="Toon", stage="pixel")

# 按贴图名搜索（部分匹配）
find_draws_by_texture(texture_name="CharacterSkin")

# 按资源 ID 搜索（完全匹配）
find_draws_by_resource(resource_id="ResourceId::12345")
```

### GPU 计时获取

```python
# 获取所有 Action 的计时
get_action_timings()
# → {"available": true, "unit": "CounterUnit.Seconds", "timings": [...], "total_duration_ms": 12.5, "count": 150}

# 只获取指定 event_id
get_action_timings(event_ids=[100, 200, 300])

# 按 Marker 过滤
get_action_timings(marker_filter="Camera.Render", exclude_markers=["GUI.Repaint"])
```

**注意**：GPU 计时计数器可能因硬件或驱动而不可用。
如果返回 `available: false`，说明该捕获无法获取计时信息。

### 资源与几何数据

```python
# 读取缓冲区的一部分
get_buffer_contents(resource_id="ResourceId::123", offset=256, length=512)

# 读取某个 Draw 上的瞬态缓冲（例如常量缓冲上传）
get_buffer_contents(resource_id="ResourceId::123", event_id=456)

# 获取纹理像素数据
get_texture_data(resource_id="ResourceId::123", mip=0, slice=0)

# 提取 Draw 的 IB + 解码后的顶点属性
get_mesh_data(event_id=123)
```

## 通信协议

基于文件的 IPC：

- IPC 目录：`%TEMP%/renderdoc_mcp/`
- `request.json`：请求（MCP 服务器 → RenderDoc）
- `response.json`：响应（RenderDoc → MCP 服务器）
- `lock`：写入中的锁文件
- 轮询间隔：RenderDoc 侧 100ms，MCP 服务器侧 50ms

## 开发说明

- RenderDoc 内置 Python 缺少 `socket` / `QtNetwork` 模块，因此采用基于文件的 IPC。
- RenderDoc 扩展侧需要兼容 RenderDoc 内置 Python 3.6 标准库。
- 访问 `ReplayController` 必须通过 `BlockInvoke`，确保操作运行在 RenderDoc replay 线程。
- `renderdoc_facade.py` 只做分发，具体逻辑放在 `renderdoc_extension/services/` 中。
- 新增 MCP 工具时，需要同时更新 `mcp_server/server.py`、`renderdoc_extension/request_handler.py`、`renderdoc_extension/renderdoc_facade.py` 和对应 service。

## 维护 / 扩展开发（改动本仓库源码时必读）

代码分两部分，**改哪部分决定重启什么**：

| 改动文件 | 角色 | 生效需要 |
|---|---|---|
| `mcp_server/server.py` | MCP 工具 schema（工具名/参数签名/docstring） | **重启 MCP server** |
| `renderdoc_extension/**`（service/facade/handler） | 在 RenderDoc 进程内执行的实现 | **重启 RenderDoc**（不是 MCP server） |

### 部署步骤（扩展改动后）

```bash
cd <仓库根目录>          # 即本 CLAUDE.md 所在目录
python -m py_compile renderdoc_extension/services/<改的文件>.py   # 先语法自检
python scripts/install_extension.py install --target all          # 拷贝到所有 extensions 目录并启用 Always Load
# 清理已安装副本的字节码缓存，避免加载旧 .pyc
find "$APPDATA/qrenderzzs/extensions/renderdoc_mcp_bridge" -name __pycache__ -type d -exec rm -rf {} +
```
然后**重启 RenderDoc + 重新打开截帧**，扩展会通过 Always Load 自动加载。
安装目标目录的多目标配置见 README「自定义 / 自编译 RenderDoc（多目标支持）」。

### ⚠️ 关键坑：文件拷贝不会热重载

RenderDoc 在启动时把扩展模块 import 进内存；**只拷贝文件 / 改源码不会让运行中的进程重新加载**。
新增/改完工具后，运行中的 RenderDoc 仍执行**旧代码**。必须重启 RenderDoc 才生效。
（同理 `server.py` schema 改动后，运行中的 MCP server 仍是旧 schema，必须重启 MCP server。）

### 诊断规则

- **报错 `<built-in method BlockInvoke ...> returned NULL without setting an error`**：
  说明 RenderDoc 回调里抛了**未捕获的 Python 异常**。
  - 若你刚把回调体用 `try/except + traceback` 包好了却仍报这条原始信息 → **运行中的 RenderDoc 还是旧代码**，
    需要重启（不是再改代码）。重启后要么成功，要么回传带 traceback 的真实错误。
  - 写扩展回调时**务必**整体 `try/except` 并把 `traceback.format_exc()` 写进 `result["error"]`，
    否则异常会变成无信息的 "returned NULL"。
- **大二进制别走 inline**：纹理用 `export_texture_to_file`、大模型用 `export_mesh_to_file`，
  宿主侧落盘只回传元信息。`get_texture_data` / `get_mesh_data` 的 base64 会经过对话上下文，
  1024² 贴图或大网格单次即溢出窗口（`get_mesh_data` 会 `Expecting ',' delimiter` 截断报错）。

### 已修复记录

- **cb0 数值读取（2026-06）**：`get_pipeline_state` / `get_shader_info` 现已能取到 cbuffer 变量的
  **完整数值**（含嵌套 `_hlslcc_mtx4x4...` 矩阵的逐行 `members`）。修复点：`pipeline_service.py` 用
  `pipe.GetConstantBlock(stage, i, 0)` 返回 `UsedDescriptor`（取代不存在的 `GetConstantBuffer`），
  入口点用 `pipe.GetShaderEntryPoint(stage)`。

## 参考链接

- [FastMCP](https://github.com/jlowin/fastmcp)
- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)
- [RenderDoc Extension Registration](https://renderdoc.org/docs/how/how_python_extension.html)
