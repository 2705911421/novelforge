# NovelForge Architecture V2：AI Runtime

## 契约

每次模型调用创建 `GenerationRun`：`task_id`、agent role、provider/model、prompt version、输入引用、token/延迟、输出 artifact、错误分类。Prompt 以 Registry key 和版本解析，业务代码不得拼接匿名核心 prompt。

## 执行步骤

1. Application service 校验 Task、Book、权限和所需前置产物。
2. Context Agent 根据预算组装只读 ContextBundle，记录输入 fact/chunk/version id。
3. Model Router 按 Agent role 解析允许的 provider/model override；无可用模型返回可恢复配置错误。
4. Gateway 调用 provider，流式 token 作为持久化事件发布；最终输出按 schema 解析并验证。
5. 解析失败、配额、速率限制、网络和 provider 拒绝映射为明确 error code；可重试错误按退避策略重试并记录 attempt。
6. 输出成为草稿/审查/提取 artifact，只有后续质量门或 commit 可改变故事事实。

## 路由角色

Planner、Writer、Reviewer、Revision、Context、Fact Extraction、Embedding、Rerank、Image 各自可指定默认模型及 per-run override。凭据仅存于操作系统密钥存储或加密配置；API 永不回显密钥。测试 Provider 必须显式标注 `deterministic-test`，不得伪装为真实生成。

