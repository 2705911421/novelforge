# Studio Browser

## 隔离运行

使用 scripts/seed_storyflow_browser_fixture.py 生成 disposable SQLite fixture：

- root：output/playwright/global-audit-20260820-fixture；
- projectId：storyflow-browser-fixture-project；
- bookId：storyflow-browser-fixture-book；
- 120 chapters；
- health signals；
- Uvicorn：127.0.0.1:8767；
- 环境：NOVELFORGE_ROOT 指向 fixture、NOVELFORGE_DISABLE_STUDIO_WORKER=1。

这不是权威项目 DB，也没有启动生产 worker。

## 浏览器实际步骤

使用 Playwright CLI 真实打开页面并取 snapshot：

1. 首页加载成功，title=NovelForge Studio；
2. 首页显示 1 book、120 chapters、health；
3. 点击 StoryFlow 导航；
4. StoryFlow canvas 显示 SQLite Story Graph、read-only Canon、AI setup required；
5. depth 1 显示 10 real nodes → 10 displayed；
6. depth 2 显示 220 real nodes → 175 displayed；
7. 搜索 Fixture Character 01 返回结果；
8. graph/history/layout/search/actions 等抽样 API 返回 200；
9. console 总消息 0，Errors 0，Warnings 0；
10. screenshot 保存并视觉检查：StoryFlow UI、controls、graph、Inspector 均渲染。

截图路径：output/playwright/global-audit-20260820-fixture/storyflow.png。

## HTTP 证据

~~~text
/api/v1/books                         200
/api/v1/tasks                         200
/api/v1/health                        200
/api/v1/creation/preflight            200
/api/v1/story-graph/.../graph depth 1 200
/api/v1/story-graph/.../graph depth 2 200
/api/v1/story-graph/.../layout         200
/api/v1/story-graph/.../history        200
/api/v1/story-graph/.../nodes          200
/api/v1/story-graph/.../actions/analyze 200
/api/v1/story-graph/.../candidates     200
/api/v1/story-graph/.../recoverable-tasks 200
/api/v1/story-graph/.../search         200
~~~

## 未执行或不可推断

- 完整 23-step StoryFlow acceptance；
- 真实 provider success/failure；
- worker-enabled long task browser flow；
- provider authorization/rate discovery；
- production load/large graph performance；
- all API operations and error schemas。

## 判定

Studio browser implementation verdict：PARTIAL。基础真实 UI/graph/search smoke 已实现且无 console error；全流程与生产运行证据未闭合。
