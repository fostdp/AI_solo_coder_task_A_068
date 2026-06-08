# 海上风电场海缆保护与船舶碰撞预警系统

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Nginx (80)                                   │
│                   Gzip / 反向代理 / WebSocket                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    FastAPI (gunicorn+uvicorn)                        │
│                         api:8000                                     │
│  ┌───────────┐ ┌──────────────┐ ┌────────────┐ ┌────────────────┐  │
│  │AISIngestor│ │CollisionEval │ │ AnchorGuard│ │  AlarmRouter   │  │
│  │ MQTT→Stream│ │ DCPA/TCPA    │ │ 停泊检测   │ │  告警分级推送  │  │
│  └─────┬─────┘ └──────┬───────┘ └─────┬──────┘ └───────┬────────┘  │
│        │               │               │                │           │
│  ┌─────▼───────────────▼───────────────▼────────────────▼──────┐    │
│  │                    Redis Streams                             │    │
│  │  ais:raw ──► collision:risk ──► alert:created               │    │
│  │  ais:raw ──► anchor:risk     ──► alert:created              │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
        │               │               │                │
┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│  Mosquitto   │ │   MongoDB   │ │    Redis    │ │   卫星推送   │
│  1883 QoS1   │ │  27017      │ │   6379      │ │ 海事中心+    │
│              │ │ 2dsphere    │ │  Streams    │ │ 运维船       │
│              │ │ 分片        │ │             │ │ 离线缓存     │
└───────┬──────┘ └─────────────┘ └─────────────┘ └─────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│              AIS/雷达模拟器                                │
│  80台风机 | 15-25艘船 | 10秒间隔 | 场景注入              │
└──────────────────────────────────────────────────────────┘
```

### 数据流

```
模拟器 ──MQTT QoS1──► Mosquitto ──► AISIngestor ──► Redis[ais:raw]
                                                      │
                                    ┌─────────────────┤
                                    │                 │
                                    ▼                 ▼
                          CollisionEvaluator    AnchorGuard
                          DCPA/TCPA模糊逻辑     停泊+禁航区检测
                                    │                 │
                                    ▼                 ▼
                          Redis[collision:risk]  Redis[anchor:risk]
                                    │                 │
                                    └────────┬────────┘
                                             ▼
                                       AlarmRouter
                                    L1碰撞+L2海缆告警
                                    双通道推送+离线缓存
                                             │
                                             ▼
                                    Redis[alert:created]
```

## 快速部署

### 前提条件

- Docker 20.10+
- Docker Compose v2+

### 一键启动

```bash
# 克隆项目
cd AI_solo_coder_task_A_068

# 启动所有服务
docker-compose up -d

# 查看 MongoDB 初始化日志
docker-compose logs mongo-init

# 查看所有服务状态
docker-compose ps
```

服务启动后：
- 前端界面: http://localhost
- API 文档: http://localhost/api/docs
- MongoDB: localhost:27017
- Redis: localhost:6379
- MQTT Broker: localhost:1883

### 带场景注入启动

```bash
# 启用碰撞风险+锚害风险场景注入
SIM_INJECT_COLLISION=true SIM_INJECT_ANCHOR=true docker-compose up -d
```

或在 `docker-compose.yml` 中修改 simulator 环境变量：

```yaml
simulator:
  environment:
    - SIM_INJECT_COLLISION=true
    - SIM_INJECT_ANCHOR=true
```

### 停止服务

```bash
docker-compose down

# 清除数据卷
docker-compose down -v
```

## AIS/雷达模拟器

### 命令行用法

```bash
# 本地运行（需先启动 Mosquitto）
pip install paho-mqtt
python simulator/ais_simulator.py --broker-host localhost --broker-port 1883 --interval 10

# 指定步数
python simulator/ais_simulator.py --steps 100 --interval 10

# 注入碰撞风险场景（第5步自动生成一艘朝风机高速行驶的油轮）
python simulator/ais_simulator.py --inject-collision

# 注入锚害风险场景（第3步自动在海缆附近生成一艘锚泊货船）
python simulator/ais_simulator.py --inject-anchor

# 同时注入两种场景
python simulator/ais_simulator.py --inject-collision --inject-anchor
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MQTT_HOST` | localhost | MQTT Broker 地址 |
| `MQTT_PORT` | 1883 | MQTT Broker 端口 |
| `SIM_INTERVAL` | 10 | 上报间隔（秒） |
| `SIM_STEPS` | 0 | 模拟步数（0=无限） |
| `SIM_INJECT_COLLISION` | false | 自动注入碰撞风险场景 |
| `SIM_INJECT_ANCHOR` | false | 自动注入锚害风险场景 |
| `NUM_TURBINES` | 80 | 风机数量 |
| `WIND_FARM_CENTER_LNG` | 121.5 | 风场中心经度 |
| `WIND_FARM_CENTER_LAT` | 31.0 | 风场中心纬度 |
| `FARM_RADIUS` | 0.12 | 风场半径（度） |

### 场景注入说明

**碰撞风险注入** (`--inject-collision`):
- 在第5步自动生成一艘重型船舶（油轮/货船，吃水10-15m）
- 从风机附近 1.5-2.5km 处以 10-16 节速度直冲目标风机
- 船舶持续朝目标航行，不偏离，确保 DCPA 持续减小

**锚害风险注入** (`--inject-anchor`):
- 在第3步自动生成一艘锚泊重型船舶（吃水8-14m）
- 位于海缆路线 300m 范围内，持续锚泊
- 3分钟后触发 L2 海缆锚害告警

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| API 服务 | FastAPI + gunicorn + uvicorn worker | 4 worker, Gzip 中间件 |
| 反向代理 | Nginx 1.25 | Gzip 压缩, WebSocket 代理 |
| 消息队列 | Redis 7 Streams | 模块间异步通信 |
| 数据库 | MongoDB 7.0 | 2dsphere 地理索引, 分片 |
| 消息中间件 | Eclipse Mosquitto 2 | QoS 1 持久化 |
| 碰撞评估 | 模糊逻辑 (YAML 配置) | DCPA/TCPA + EMA + 回滞 |
| 前端 | Leaflet + Canvas + L.heatLayer | 暗色海事主题 |
| 容器化 | Docker multi-stage build | 前后端分离镜像 |

## 项目结构

```
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + 生命周期
│   │   ├── config.py            # pydantic-settings 配置
│   │   ├── database.py          # Motor async MongoDB
│   │   ├── redis.py             # Redis Stream 通信层
│   │   ├── models/schemas.py    # Pydantic 数据模型
│   │   ├── routes/api.py        # REST API 路由
│   │   └── services/
│   │       ├── ais_ingestor.py       # MQTT→Redis Stream
│   │       ├── collision.py          # DCPA/TCPA + 模糊逻辑
│   │       ├── collision_evaluator.py# 碰撞评估消费/生产者
│   │       ├── anchor_warning.py     # 锚害检测算法
│   │       ├── anchor_guard.py       # 锚害预警消费/生产者
│   │       ├── alarm_router.py       # 告警分级+双通道推送
│   │       └── alert.py             # 告警管理器
│   └── config/
│       └── fuzzy_rules.yaml     # 模糊逻辑配置
├── frontend/
│   ├── index.html
│   └── static/js/
│       ├── windfarm_map.js      # 海域图图层管理
│       ├── vessel_panel.js      # 船舶标记+信息面板
│       ├── alerts.js            # 告警列表+声音
│       ├── heatmap.js           # 24h交通热力图
│       ├── stats.js             # 月度统计
│       └── app.js               # 应用控制器
├── simulator/
│   └── ais_simulator.py         # AIS/雷达模拟器
├── scripts/
│   ├── mongo_init.py            # 本地 MongoDB 初始化
│   └── mongo_init_docker.py     # Docker MongoDB 初始化(含2dsphere+分片)
├── nginx/nginx.conf             # Nginx 反向代理+Gzip
├── mosquitto/mosquitto.conf     # MQTT Broker QoS1 配置
├── docker-compose.yml
├── Dockerfile                   # FastAPI 多阶段构建
├── Dockerfile.simulator         # 模拟器镜像
└── requirements.txt
```
