# Browser Matrix

状态：`PARTIAL`

浏览器：本地 Playwright CLI 控制的 headed Chromium；CSS viewport 按 100% 运行。项目：`04487593ac38458daf0f9ccce4b182b0`。服务端口：`127.0.0.1:8767`。

## 视口矩阵

| Viewport | 密度 | Shell/URL | 主区内容 | 横向滚动 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1366×768 | Compact | StoryFlow deep link | Canvas-first，Explorer/Inspector 可关闭/覆盖；Canvas `1308px` | `scrollWidth=1366` | `IMPLEMENTED` |
| 1440×900 | Standard | StoryFlow route retained | Explorer 关闭时 Canvas `1382px`；打开 Explorer/外层 Inspector 仍可用 | `scrollWidth=1440` | `IMPLEMENTED` |
| 1536×864 | Standard | StoryFlow route retained | Explorer 关闭时 Canvas `1478px` | `scrollWidth=1536` | `IMPLEMENTED` |
| 1920×1080 | Standard | StoryFlow route retained | Explorer 关闭时 Canvas `1862px` | `scrollWidth=1920` | `IMPLEMENTED` |
| 2560×1440 | Expanded | StoryFlow route retained | Explorer 关闭时 Canvas `2502px` | `scrollWidth=2560` | `IMPLEMENTED` |

## 路由矩阵

| URL suffix | `StudioShell` workspace | `S.page` | 页面标题/内容 | 状态 |
| --- | --- | --- | --- | --- |
| `/write` | `write` | `chapters` | 章节工作台 | `IMPLEMENTED` |
| `/plan` | `plan` | `planning` | 规划总览 | `IMPLEMENTED` |
| `/storyflow` | `storyflow` | `storyflow` | StoryFlow 故事画布 | `IMPLEMENTED` |
| `/canon` | `canon` | `wizard` | 世界观向导 | `IMPLEMENTED` |
| `/review` | `review` | `jointreview` | 联合审查 | `IMPLEMENTED` |
| `/timeline` | `timeline` | `storyflow` | StoryFlow 时间线 intent | `IMPLEMENTED` |
| `/more/tasks` | `more` | `tasks` | 任务管理 | `IMPLEMENTED` |

## 交互矩阵

| 交互 | 结果 | 状态 |
| --- | --- | --- |
| Project button | Compact 打开 Explorer overlay | `IMPLEMENTED` |
| Main area outside click | Compact Explorer 关闭并写入 layout | `IMPLEMENTED` |
| `Esc` | 关闭 Command Palette 或 Compact Explorer | `IMPLEMENTED` |
| `Ctrl/Cmd+K` | 打开 Command Palette | `IMPLEMENTED` |
| Palette Tab | 焦点在 input/命令按钮之间循环 | `IMPLEMENTED` |
| `Ctrl/Cmd+Shift+F` | Focus Mode 切换 | `IMPLEMENTED` |
| Focus exit button | Focus Mode 内可见，点击后恢复 Shell | `IMPLEMENTED` |
| Bottom resize | 高度按 workspace 保存并限幅 | `IMPLEMENTED` |

## 尚未覆盖

125%/150% 缩放、Firefox/WebKit、真实 Provider、Worker 真执行、网络断开、刷新恢复和长时间 Canvas 性能均为 `PARTIAL` 或 `NOT_IMPLEMENTED`，不是本矩阵的绿色项。
