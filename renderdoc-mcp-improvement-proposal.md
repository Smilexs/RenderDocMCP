# RenderDoc MCP 改进提案

## 背景

在 Unity 项目中分析 RenderDoc 捕获时，存在以下问题：

1. **UI 噪声问题**: 从 Unity Editor 捕获时，会包含大量 `GUI.Repaint`、`UIR.DrawChain` 等 Editor UI 绘制内容，很难找到实际的游戏绘制（`Camera.Render` 下的内容）
2. **响应大小问题**: `get_draw_calls(include_children=true)` 的结果会超过 70KB，占用 LLM 上下文
3. **探索效率低**: 为了找到使用特定着色器或纹理的绘制调用，需要逐个检查所有绘制调用

## 改进提案

### 1. 标记过滤（优先级: 高）

只获取指定标记下的内容，或排除指定标记后再获取的功能。

```python
get_draw_calls(
    include_children=True,
    marker_filter="Camera.Render",  # 只获取该标记下的内容
    exclude_markers=["GUI.Repaint", "UIR.DrawChain", "UGUI.Rendering"]
)
```

**用例**:
- 从 Unity Editor 捕获中只提取游戏绘制
- 只调查特定渲染路径（Shadows、PostProcess 等）

**预期效果**:
- 将响应大小减少到 10-20%
- 控制在 LLM 可以直接解析的大小内

---

### 2. 指定 event_id 范围（优先级: 高）

只获取指定 event_id 范围内内容的功能。

```python
get_draw_calls(
    event_id_min=7372,
    event_id_max=7600,
    include_children=True
)
```

**用例**:
- 当已知 `Camera.Render` 的 event_id 时，只获取其附近内容
- 详细调查存在问题的绘制调用周边内容

**预期效果**:
- 快速获取必要部分
- 支持分阶段探索

---

### 3. 按着色器/纹理/资源反向搜索（优先级: 中）

搜索使用特定资源的绘制调用。

```python
# 按着色器名称搜索（部分匹配）
find_draws_by_shader(shader_name="Toon")

# 按纹理名称搜索（部分匹配）
find_draws_by_texture(texture_name="CharacterSkin")

# 按资源 ID 搜索（完全匹配）
find_draws_by_resource(resource_id="ResourceId::12345")
```

**返回值示例**:
```json
{
  "matches": [
    {"event_id": 7538, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"},
    {"event_id": 7620, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"}
  ],
  "total_matches": 2
}
```

**用例**:
- 直接回答“哪些绘制调用使用了这个着色器？”这一最常见问题
- 追踪特定纹理在哪里被使用
- 确定着色器缺陷的影响范围

---

### 4. 获取帧摘要（优先级: 中）

获取整帧概要的功能。

```python
get_frame_summary()
```

**返回值示例**:
```json
{
  "api": "D3D11",
  "total_events": 7763,
  "statistics": {
    "draw_calls": 64,
    "dispatches": 193,
    "clears": 5,
    "copies": 8
  },
  "top_level_markers": [
    {"name": "WaitForRenderJobs", "event_id": 118},
    {"name": "CustomRenderTextures.Update", "event_id": 6451},
    {"name": "Camera.Render", "event_id": 7372},
    {"name": "UIR.DrawChain", "event_id": 6484}
  ],
  "render_targets": [
    {"resource_id": "ResourceId::22573", "name": "MainRT", "resolution": "1920x1080"},
    {"resource_id": "ResourceId::22585", "name": "ShadowMap", "resolution": "2048x2048"}
  ],
  "unique_shaders": {
    "vertex": 12,
    "pixel": 15,
    "compute": 8
  }
}
```

**用例**:
- 作为探索起点，先掌握整体情况
- 判断应该深入查看哪个标记下的内容
- 掌握性能概要

---

### 5. 仅获取绘制调用模式（优先级: 中）

排除标记（PushMarker/PopMarker），只获取实际绘制调用的功能。

```python
get_draw_calls(
    only_actions=True,  # 排除标记
    flags_filter=["Drawcall", "Dispatch"]  # 只获取带有指定标志的项目
)
```

**用例**:
- 只需要绘制调用总数和列表时
- 只想调查 Compute Shader（Dispatch）时

---

### 6. 批量获取管线状态（优先级: 低）

一次获取多个 event_id 的管线状态。

```python
get_multiple_pipeline_states(event_ids=[7538, 7558, 7450, 7458])
```

**返回值示例**:
```json
{
  "states": {
    "7538": { /* pipeline state */ },
    "7558": { /* pipeline state */ },
    "7450": { /* pipeline state */ },
    "7458": { /* pipeline state */ }
  }
}
```

**用例**:
- 对多个绘制调用进行比较分析
- 差异调查（比较正常绘制和异常绘制）

---

## 优先级汇总

| 优先级 | 功能 | 实现难度 | 效果 |
|--------|------|-----------|------|
| **高** | 标记过滤 | 中 | 去除 UI 噪声后可显著改善体验 |
| **高** | 指定 event_id 范围 | 低 | 通过部分获取提升速度 |
| **中** | 着色器/纹理反向搜索 | 高 | 直接支持最常见的用例 |
| **中** | 帧摘要 | 中 | 可作为探索起点 |
| **中** | 仅获取绘制调用 | 低 | 简单过滤 |
| **低** | 批量获取 | 低 | 可提升效率，但不是必需功能 |

## Unity 专用过滤预设（可选）

如果提供 Unity 专用预设会更方便：

```python
get_draw_calls(
    preset="unity_game_rendering"
)
```

**预设内容**:
- `marker_filter`: "Camera.Render"
- `exclude_markers`: ["GUI.Repaint", "UIR.DrawChain", "GUITexture.Draw", "UGUI.Rendering.RenderOverlays", "PlayerEndOfFrame", "EditorLoop"]

---

## 实现参考：当前工作流的问题

### 当前流程

```
1. get_draw_calls(include_children=true)
   → 返回 76KB 的 JSON（保存到文件）

2. 使用外部工具（如 Python）解析文件
   → 确定 Camera.Render 的 event_id（例: 7372）

3. 手动指定 event_id 范围进行详细调查
   → get_pipeline_state(7538), get_shader_info(7538, "pixel"), ...
```

### 改进后的理想流程

```
1. get_frame_summary()
   → 得知 Camera.Render 位于 event_id: 7372

2. get_draw_calls(marker_filter="Camera.Render", exclude_markers=[...])
   → 只获取必要的绘制调用（数 KB）

3. find_draws_by_shader(shader_name="MyShader")
   → 直接返回匹配的 event_id

4. 使用 get_pipeline_state(event_id) 确认详情
```

---

## 补充：应跳过的 Unity 标记列表

从 Unity Editor 捕获时应排除的标记：

| 标记名称 | 说明 |
|-----------|------|
| `GUI.Repaint` | IMGUI 绘制 |
| `UIR.DrawChain` | UI Toolkit 绘制 |
| `GUITexture.Draw` | GUI 纹理绘制 |
| `UGUI.Rendering.RenderOverlays` | uGUI 覆盖层 |
| `PlayerEndOfFrame` | 帧结束处理 |
| `EditorLoop` | 编辑器循环处理 |

相反，重要的标记：

| 标记名称 | 说明 |
|-----------|------|
| `Camera.Render` | 主摄像机绘制的起点 |
| `Drawing` | 绘制阶段 |
| `Render.OpaqueGeometry` | 不透明对象绘制 |
| `Render.TransparentGeometry` | 半透明对象绘制 |
| `RenderForward.RenderLoopJob` | 前向渲染的绘制调用组 |
| `Camera.RenderSkybox` | 天空盒绘制 |
| `Camera.ImageEffects` | 后处理 |
| `Shadows.RenderShadowMap` | 阴影贴图生成 |
