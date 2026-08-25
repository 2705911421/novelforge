# Responsive Strategy

状态：`PARTIAL`

## 密度规则

密度基于 CSS viewport `window.innerWidth`，不是物理屏幕像素或设备 DPR：

| Density | CSS viewport | 默认布局策略 |
| --- | ---: | --- |
| Compact | `< 1440` | StoryFlow Canvas-first；Explorer/Inspector 覆盖式且默认关闭；Bottom Panel 默认关闭；工具栏保留核心动作 |
| Standard | `1440–2199` | Explorer、主区和 Inspector 可组成工作台；面板按工作区偏好保存；工具栏显示更多常用动作 |
| Expanded | `>= 2200` | 三列工作台和更宽主区；允许显示更多辅助信息；仍禁止整体横向滚动 |

CSS 断点和 Shell 密度数据属性分别由 `studio-shell.css`、`currentDensity()` 和 `data-shell-density` 管理。面板宽度受 Explorer 180–360px、Inspector 240–420px 限制；Bottom Panel 高度受 144–520px 限制。

## 已跑的 CSS viewport 矩阵

| Viewport | 密度结果 | `scrollWidth` | 结果 |
| --- | --- | ---: | --- |
| 1366×768 | Compact | 1366 | `IMPLEMENTED`：Canvas-first、侧栏关闭无残影、外部点击/Esc 抽屉行为 |
| 1440×900 | Standard | 1440 | `IMPLEMENTED`：密度切换、无整体横向滚动 |
| 1536×864 | Standard | 1536 | `IMPLEMENTED`：密度切换、无整体横向滚动 |
| 1920×1080 | Standard | 1920 | `IMPLEMENTED`：面板打开时无整体横向滚动 |
| 2560×1440 | Expanded | 2560 | `IMPLEMENTED`：三列 StoryFlow 工作台、Bottom Panel、拖拽高度 |

真实浏览器截图证据位于工作树的 `.playwright-cli`：

- Compact Canvas-first：`page-2026-08-21T04-38-08-267Z.png`
- Expanded 三列 StoryFlow：`page-2026-08-21T04-06-22-835Z.png`

## 面板策略

- Compact Explorer 从 Activity Bar/Global Project 按钮打开，覆盖主区，不占永久列宽。
- Compact Shell Inspector 从 Global Inspector 打开，覆盖右侧。
- StoryFlow 业务 Explorer/Inspector 在 Compact 也覆盖 Canvas，关闭时 Canvas 使用单列。
- Standard/Expanded StoryFlow 业务面板可用 resize handle；Bottom Panel 可用垂直 handle。
- Focus Mode 隐藏 Global Bar、Activity Bar、Shell Explorer、Shell Inspector、Bottom Panel 和 StoryFlow 业务两侧栏，只留主编辑/Canvas。

## 尚未执行

- `PARTIAL`：系统缩放 125% 和 150% 的真实浏览器矩阵尚未执行；当前检查的是 100% CSS viewport 尺寸。
- `PARTIAL`：没有在完整浏览器组合（Chromium/Firefox/WebKit）上执行同一矩阵。
- `NOT_IMPLEMENTED`：没有将每个 legacy 页面内部的固定宽度表格、SVG 或第三方组件全部迁移为统一容器协议。
