# 数据分析实验与数据应用服务平台

面向指标口径管理、分析实验设计、OLAP查询、数据看板构建与数据API发布的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度类型 |
| 数据集管理 | `dataset` | 分析数据集 | 数据集编号、数据集名称、来源类型 |
| 分析实验 | `experiment` | 实验方案 | 方案编号、实验名称、分析数据集 |
| OLAP查询 | `query_builder` | 查询模板 | 模板编号、模板名称、查询语句 |
| 数据看板 | `dashboard` | 看板配置 | 看板编号、看板名称、包含图表 |
| 图表配置 | `chart` | 图表 | 图表编号、图表名称、图表类型 |
| 数据API发布 | `data_service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 智能告警 | `alert` | 告警规则 | 规则编号、告警名称、监控指标 |
| 分析用户组 | `user_group` | 用户组 | 组编号、组名称、组描述 |
| 数据快照 | `snapshot` | 快照记录、可恢复清单、恢复依据包 | 快照编号、数据集名称、快照时间、校验哈希 |
| 任务调度 | `scheduler` | 调度任务 | 任务编号、任务名称、调度类型 |
| 缓存管理 | `cache` | 缓存配置 | 缓存编号、缓存目标、缓存策略 |
| 分析管道 | `pipeline` | 分析节点 | 节点编号、输入数据集、分析算子 |
| 分析模型 | `model` | 模型配置 | 模型编号、模型名称、模型类型 |
| 数据集市 | `data_mart` | 集市表 | 表编号、表名称、主题域 |
| 数据导出 | `export` | 导出任务 | 任务编号、导出对象、导出格式 |
| 数据审计 | `audit` | 审计记录 | 记录编号、操作类型、操作人 |

## 数据快照：可恢复清单与恢复依据包

快照页在作业时间线之外，还提供三块能力，接口都挂在 `/api/snapshot` 下：

- **可恢复快照清单**：`GET /api/snapshot/recoverable` 按数据集分组，支持 `dataset`、
  `verification`、`date_from`、`date_to`、`only_recoverable` 过滤，返回 `items`、`groups`、
  `summary`、`datasets`、`selection`。清单每行绑定一份快照记录，用记录指纹判断是否过期；
  快照记录被改写后旧行不再展示（`include_stale=true` 可查看）。`GET /recoverable/export`
  导出 CSV，每行末尾带「筛选依据」，保留这份文件是按什么条件选出来的。
- **快照恢复依据包**：`POST /{id}/verify` 解析校验哈希并把处理输入按版本持久化，
  `GET /{id}/basis` 回查（可带 `version`、`source`），返回 `overview`（当前版本与差异摘要）、
  `detail`（处理输入与逐字段差异）；`GET /{id}/basis/versions` 列出全部版本。
  数据审计 `GET /api/audit/snapshot-basis`、数据导出 `GET /api/export/snapshot-basis`
  只读同一份依据包，用于跨领域核对版本。
- **恢复入口**：`POST /{id}/recover` 只接受已确认版本里的校验哈希，依据包过期或确认版本
  为不可恢复时直接拒绝；受理记录可通过 `GET /{id}/recovery-requests` 回查。

快照目录检查点（`GET/POST /api/snapshot/checkpoints`）用来承接清理完成通知和恢复请求。
两个事件并发落到同一检查点时，先到者成为唯一确认版本，后到者以「已驳回」版本留痕，
不会覆盖确认结果；检查点确认后再按版次生成恢复依据包，因此冲突条件下仍保留版本边界。

校验哈希口径统一在 `backend/app/services/snapshot_manifest.py`：按
「快照编号 | 数据集名称 | 快照时间 | 行数快照 | 存储位置」拼清单文本取 `sha256:`。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
