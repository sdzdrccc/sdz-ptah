> # ⚠️ 已被取代 · 草稿存档
>
> **本文是 2026-11-24 的早期草稿，不要作为实施依据。**
>
> 现行方案见 [`ai-anime-avatar/outputs/AI动漫头像-双通道架构设计.md`](../../ai-anime-avatar/outputs/AI动漫头像-双通道架构设计.md)。
>
> **为什么被取代**：本文按"公有云多租户服务"假设编写（JWT、签名 URL、WebSocket、分块上传），
> 而项目当前已确定的结论是——**不部署 ComfyUI、不租 GPU、阶段一只用 ModelScope 在线推理、
> 单实例、无鉴权、只支持本地/局域网**。两套假设不兼容。
>
> **本文与现有代码的实际差异**（下面这些端点在 `sdz-ptah-app/server.py` 里**并不存在**）：
>
> | 本文写的 | 实际状态 |
> |---|---|
> | `DELETE /api/orders/{id}` | 不存在 |
> | `GET /api/orders/{id}/status` | 不存在（状态在 `GET /api/orders/{name}` 的 `status` 字段里） |
> | `POST /api/orders/{id}/cancel` | 不存在 |
> | `GET /api/characters`、`GET /api/styles` | 不存在（合并进 `GET /api/meta`） |
> | `POST /api/upload` | 不存在（实际是 `POST /api/orders/{name}/upload-reference`） |
> | `GET /api/files/{order_id}/{file}` | 不存在（实际是 `GET /api/orders/{name}/file/{file}`） |
> | `WS /ws/orders/{id}/progress` | 不存在（无 WebSocket，靠轮询） |
> | 分块上传三个端点 | 不存在（实际是单个 base64 data URL，20 MB 上限） |
>
> **唯一可参考的部分**：本文对"移动端需要缩略图和轻量响应"的判断仍然成立，
> 具体做法已并入现行文档第 7.2 节（入库前压缩）与 `avatar_pipeline.py` 的多档导出。
>
> 保留本文的目的：将来若真要上公有云多租户，这里的端点全集可以作为起点重新评估。
>
> ---

# sdz-ptah 多端 API 设计（原文存档，2026-11-24）

## REST API 端点

### 订单管理
```
GET    /api/orders              # 列表
POST   /api/orders              # 创建
GET    /api/orders/{id}         # 详情
DELETE /api/orders/{id}         # 删除
```

### 生成流程
```
POST   /api/orders/{id}/confirm     # 确认门
POST   /api/orders/{id}/generate    # 开始生成
GET    /api/orders/{id}/status      # 查询状态
POST   /api/orders/{id}/cancel      # 取消生成
```

### 资源管理
```
GET    /api/presets                 # 预设方案
GET    /api/characters              # 角色卡列表
GET    /api/styles                  # 风格档案
POST   /api/upload                  # 文件上传
GET    /api/files/{order_id}/{file} # 文件下载
```

### 实时推送 (WebSocket)
```
WS     /ws/orders/{id}/progress
       → { type: "generation_start", round: 1 }
       → { type: "image_ready", path: "...", index: 1 }
       → { type: "scoring_complete", raw_quality: 65 }
       → { type: "round_complete", passed: 2, failed: 0 }
```

## 移动端适配

### 轻量化响应
```json
{
  "order_id": "20261124-001",
  "status": "generating",
  "progress": 0.6,
  "preview_url": "/api/files/.../thumb.jpg",  // 移动端缩略图
  "budget": { "used": 4, "limit": 10 }
}
```

### 分块上传 (大参考图)
```
POST   /api/upload/init     → { upload_id, chunk_size }
POST   /api/upload/chunk    → { chunk_index, upload_id }
POST   /api/upload/complete → { file_id }
```

## 安全考虑

### 本地模式
- 仅监听 127.0.0.1 或局域网
- 可选 token 认证 (config.json)

### 云端模式
- JWT 认证
- HTTPS 强制
- 文件访问签名 URL (有效期 1h)
