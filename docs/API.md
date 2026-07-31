# 发布任务 API

FastAPI 入口：`backend.app.main:app`。创建、继续和读取接口统一返回公开的 `ReleaseTaskResult`，不会暴露 SQLite 记录或 LangGraph checkpoint。

## 创建任务

`POST /release-tasks`

```text
Content-Type: multipart/form-data

task_brief: 我要在小红书宣传一款键盘，美加狮品牌，60键位，无灯标准版到手99元
product_image: <商品实物图片>
```

`task_brief` 和 `product_image` 都是必填字段。系统保存商品原图、分析可见商品信息，并推进到下一次需要用户参与的状态。任务 ID 由系统生成。

## 继续任务

`POST /release-tasks/{task_id}/continue`

```json
{
  "message": "标题短一点，保留价格",
  "confirmation_resolutions": [],
  "turn_id": "turn-demo-001",
  "expected_state_version": 3
}
```

- `message`：修改、提问、比较、恢复文案或调整宣传图等自然语言输入。当前文案可交付时，留空表示确认文案并生成宣传图；宣传图待确认时，留空表示确认图片并生成最终发布包。
- `confirmation_resolutions`：待确认宣传事实的逐项处理决定。每个待确认 `decision_id` 必须且只能出现一次；`confirmed_with_basis` 表示确认该项具有真实依据，`rewrite_without_basis` 表示仅对该项进行风险改写。确认依据的可选说明写入该项的 `evidence_notes`。
- `turn_id`：本轮幂等键。
- `expected_state_version`：乐观并发版本，过期时返回 `409`。

两项宣传事实需要不同处理时，请一次提交完整的逐项决定：

```json
{
  "message": "",
  "confirmation_resolutions": [
    {
      "decision_id": "decision-1-1",
      "resolution": "confirmed_with_basis",
      "evidence_notes": "已有可核验材料"
    },
    {
      "decision_id": "decision-1-2",
      "resolution": "rewrite_without_basis",
      "evidence_notes": ""
    }
  ],
  "turn_id": "turn-demo-002",
  "expected_state_version": 4
}
```

前端不会自动重试创建或继续 mutation，避免重复模型调用。用户可在冲突后刷新任务状态再决定是否重新提交。

## 更换商品实物图

`POST /release-tasks/{task_id}/product-image`

使用 `multipart/form-data` 上传新的 `product_image`，并传入当前 `expected_state_version`。接口保留已审核的当前文案，重新分析商品图。新状态保存成功后才删除旧商品图资产。

## 查询与删除

- `GET /release-tasks`：按更新时间倒序返回任务摘要。
- `GET /release-tasks/{task_id}`：返回完整公开任务结果。
- `DELETE /release-tasks/{task_id}`：永久删除任务、事件、模型和工具调用日志、租约及 LangGraph checkpoint。
- `GET /health`：返回 `{"status":"ok"}`。

删除接口只负责执行删除。React 工作台要求用户点击删除按钮并在确认弹窗中二次确认，不会自动发起删除。

## 结果阶段

- `collect_context`：等待补充关键任务信息。
- `source_image_ready`：商品图分析完成，等待继续文案任务。
- `source_image_retake_required`：商品图不足以可靠识别，等待用户更换图片。
- `draft_review_ready`：当前工作稿已审核，等待修改或确认交付。
- `draft_revision_needed`：当前候选尚未达到可发布状态，等待用户继续修改。
- `evidence_confirmation`：等待事实依据确认或接受风险改写。
- `agent_response_ready`：Agent 已完成提问、比较或恢复请求的回复。
- `promotion_image_review_ready`：宣传图已生成，等待用户确认或用自然语言要求重新生成。
- `release_package_ready`：最终发布包已生成。
- `turn_not_applied`：本轮未安全生效，上一版状态仍保留。

## 错误契约

```json
{
  "detail": {
    "code": "task_not_found",
    "message": "Release task not found: release-demo-001"
  }
}
```

- `404 task_not_found`：任务不存在。
- `409 task_conflict`：任务 ID、turn ID、状态版本或任务租约冲突。
- `422`：请求字段或类型不符合 Pydantic 契约。

## 前端托管

开发时 Vite 将 `/release-tasks` 与 `/health` 代理到 `127.0.0.1:8000`。执行 `pnpm run build` 后，FastAPI 在根路径托管 `frontend/dist`，API 路由优先于静态文件。
