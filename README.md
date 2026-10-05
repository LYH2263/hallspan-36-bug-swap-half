# HallSpan 考场间距排座

在考室网格上按最小曼哈顿距离排座，同试卷套不得四邻相邻，并输出违规与统计。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4900 |
| API | http://localhost:9900 |
| API 文档 | http://localhost:9900/docs |
| Postgres | localhost:5450 |

健康检查：`GET http://localhost:9900/api/health`

## 使用说明

1. 在「考室」「考生」「试卷套」确认基础数据。
2. 打开「排座图」执行间距排座。
3. 在排座图点选两名在座考生执行「对调」：对调是一次方案版本切换——成功新写一版方案，图/违规/统计跟随新版本（第三人座位与未排集合不变）；失败则座位、违规、统计、当前版本全部回到操作前。找不到人、未在座、同卷对角相邻等情形会整次拒绝。
4. 「作废当前版本」将最新方案作废（历史版本内容不改字）；已作废方案禁止对调，需重新排座。
5. 在「违规」查看间距或同卷相邻问题。
6. 在「统计」查看占用与违规汇总。

### 排座接口

- `POST /api/seating/run`：重新排座，追加一版方案。
- `POST /api/seating/swap`：入参 `candidate_a_id`、`candidate_b_id`（可选 `hall_id`）。成功返回新版本方案（`id`/`version` 前进，`based_on_id` 指向上一版）；冲突返回 409 且不写入任何版本。
- `POST /api/seating/void`：作废当前最新版本。
- `GET /api/seating/latest|violations|stats`：始终读取最新的未作废版本。

## 开发与测试

```bash
docker compose exec api pytest -q
```
