---
alwaysApply: true
---

# COS 存储配置

- 全量教练视频根路径：`COS_VIDEO_ALL_COCAH`（`.env` 中配置，值为 `charhuang/tt_video/乒乓球合集【较新】/`）
- 旧路径 `COS_VIDEO_PREFIX` 仅用于 Feature-001 遗留兼容，**新功能禁止使用**
- 分页列举：`MaxKeys=1000`，循环读取 `NextMarker` 直到 `IsTruncated != "true"`
- 零字节文件（COS 目录占位符）自动跳过，判断条件：`int(obj["Size"]) == 0`
- 只处理 `.mp4` 后缀文件

# 教练-目录映射

- 静态映射配置：`config/coach_directory_map.json`（20 条目录名 → 教练姓名）
- 无匹配时 `name_source=fallback`，使用目录名作为教练名
- 同名教练去重：第 1 个保持原名，后续按插入顺序加 `_2`、`_3` 数字后缀
- 修改 `coach_directory_map.json` 后需重新运行全量扫描才能生效

# 扫描器使用

- 全量扫描：`POST /api/v1/classifications/scan`（Celery 异步，返回 task_id）
- 增量扫描：只处理 `cos_object_key` 不在 `coach_video_classifications` 表中的新文件
- 扫描进度查询：`GET /api/v1/classifications/scan/{task_id}`
