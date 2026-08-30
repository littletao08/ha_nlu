# HA NLU - 智能家居意图识别组件

基于本地 BERT 意图+槽位模型（ONNX）的 Home Assistant conversation agent。
音箱语音 → Assist → 本组件 → 本地 intent 服务 (`/plan`) → 执行 HA service / 读传感器 → 中文回复。

## 架构
```
NSP/音箱 --STT--> Assist Pipeline --text--> ha_nlu (ConversationEntity)
                                          |
  POST {intent_service}/plan {text}   <---+  (本地 BERT ONNX + entities.json)
  status=query   -> 读 sensor state -> 中文回复
  status=execute -> 调 HA service    -> 确认文案
  status=ask     -> 反问（缺 ROOM 等），多轮
```

## 安装（HACS 自定义仓库）
1. HACS → 自定义存储库 → 加 `https://github.com/littletao08/ha_nlu`（分类：Integration）→ 下载
2. 重启 HA
3. 设置 → 设备与服务 → 添加集成 → "HA Intent Service" → 填 intent 服务地址
4. Assist → 对话代理 选 "HA 意图服务"

## 前置
- intent 服务：本地 FastAPI + ONNX（见 `ha_intent_recognition/service`），默认 `http://127.0.0.1:5500`
- 依赖 HA 组件：`conversation`、`intent`、`assist_pipeline`

## 支持意图
climate_control / light_control / media_control / env_query / occupancy_query /
switch_control / vacuum_control / unknown

## 开发
```
python3 -m compileall custom_components/ha_nlu
```
HA 版本要求：`_async_handle_message` + `ChatLog` 协议（2024.9+，目标 2026.8）。