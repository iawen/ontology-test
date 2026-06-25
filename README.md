```bash
cd src/frontend
npm install

npm run dev
```


```bash
cd src/backend
pip intall -r requirements.txt

python main.py
```


```
Action（type=workflow）触发
  │
  ▼
create_and_run_workflow()
  │
  ├── 创建 workflow_instance 记录
  ├── 解析 steps_json
  └── 逐步执行：
      ├── Step 0: "提交采购申请" (type=auto) → 自动执行 → ✅
      ├── Step 1: "采购经理审批" (type=approval) → 等待审批 → ⏳
      ├── Step 2: "供应商确认" (type=notification) → 发送通知 → ✅
      └── Step 3: "入库验收" (type=manual) → 等待人工 → ⏳

```

我需要新增一个功能: “Schema 优化”，功能具体是：
1. 用户在后台上传对应的文档（目前先支持docs、pdf、.xlsx，l），这些文档需要管理起来，以便用户更新文档后，重写迭代）
2. 读取文档内容，然后通过大模型来迭代优化目前的class、relationships、metrics、concepts（is_reviewed为0的部分）
3. “Schema 优化”过程中，需要忽略 class的fields部分
4. “Schema 优化”的后端代码放到ontology 目录，管理部分需要你来建议（目前的SchemaManager.tsx 有点重了，文件内容偏长）

```
故事起点：一个业务人员随口提出的问题
某周一下午，区域督导小王打开手机，对着智能问数Agent问了一句—— “过去一周，杭州五家直营店日销低于周边门店的核心原因是什么？”

不到10秒，Agent返回了一个反直觉的结论：动销下滑并非因整体客流下降，而是“现烤坚果类试吃转化率从32%骤降至16%” ，直接拖累了门店连带购买率。

数据进一步交叉分析显示：四家配置了“现烤现卖”热食的设备，其中两台因设备故障停用五天以上，店员被迫切换到冷食试吃盘；而没有现烤试吃的门店，顾客试吃后购买率下降超一半。
```


## Prompt

请先按照下面的步骤去理解和分析我上传的后端代码：
1. 先去网上搜索一下依托大模型并基于本体论的理论来实现的ChatBI，理解然后思考
2. 然后思考目前我们这个后端的API接口已经数据库架构是否满足？还缺哪些？
3. 帮我检测一下目前的prompt和工具的设计是否满足？还有哪些欠缺？
4. 数据的查询（ontology_engine.py、data_query.py）是否足够灵活？还可以从哪些方面优化？
5. 重点是“/api/chat"接口相关的逻辑实现是否有不足？同样的，还可以从哪些方面优化？

以下是我的一些建议：
1. 物理字段映射（Field Map）在 SQL 生成中丢失
2. 升级多表关联（JOIN）的表达能力: 在 schema_mapping.json 的 relationships 中，将 join_key 细化为 source_key 和 target_key
3. 解决过滤条件（Filters）的“类型安全”风险


然后，就要以上的思考结果，你需要：
1. 告知我优化和需要调整的点，以及理由
2. 基于你的理由，给出对的改动策略和修改后的代码
3. 将更新后的代码打包，并提供下载链接