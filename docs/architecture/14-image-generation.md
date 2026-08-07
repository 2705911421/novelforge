# NovelForge Architecture V2：图像生成

Image Generation 是独立的 `image_generate` Task，使用 Image Agent 路由到显式配置的 Image Model。请求记录源实体、Story Bible 版本、结构化视觉约束、prompt 版本、provider/model、版权/来源字段与生成结果。

支持封面、世界/区域地图、势力范围、人物行动路径和剧情地点示意。地图类图片必须由结构化 Location/Relationship 数据派生；生成失败或用户拒绝不会影响 StoryState。`ImageAsset` 只存储文件/元数据引用，文件本体位于受项目边界限制的资产目录。

