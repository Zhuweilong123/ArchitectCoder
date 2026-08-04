## 6. 完整示例

### 6.1 简单类图（任务调度系统）

```json
{
  "version": "1.0",
  "name": "TaskScheduler",
  "diagram_type": "class",
  "classes": [
    {
      "id": "class_base_task",
      "name": "BaseTask",
      "stereotype": "abstract",
      "attributes": [
        { "name": "taskId", "type": "string", "visibility": "#", "default_value": null, "is_static": false },
        { "name": "status", "type": "TaskStatus", "visibility": "#", "default_value": null, "is_static": false }
      ],
      "methods": [
        { "name": "execute", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": true },
        { "name": "cancel", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": false }
      ],
      "position": { "x": 350.0, "y": 50.0 },
      "size": { "width": 200.0, "height": 150.0 },
      "note": "任务基类：定义通用任务接口和生命周期状态",
      "provided_interfaces": ["ITask"],
      "required_interfaces": []
    },
    {
      "id": "class_ota_task",
      "name": "OtaTask",
      "stereotype": "class",
      "attributes": [
        { "name": "isRandom", "type": "bool", "visibility": "+", "default_value": null, "is_static": false },
        { "name": "clearCrowAccumulation", "type": "bool", "visibility": "+", "default_value": null, "is_static": false }
      ],
      "methods": [
        { "name": "execute", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": false }
      ],
      "position": { "x": 50.0, "y": 250.0 },
      "size": { "width": 200.0, "height": 150.0 },
      "note": "OTA升级任务\n1. 升级任务随机触发\n2. 升级可以清除鸡叫时间累计和预约的鸡叫请求",
      "provided_interfaces": [],
      "required_interfaces": ["ILogger"]
    },
    {
      "id": "class_crow_task",
      "name": "CrowTask",
      "stereotype": "class",
      "attributes": [
        { "name": "intervalDays", "type": "int", "visibility": "+", "default_value": null, "is_static": false },
        { "name": "scheduledTime", "type": "DateTime", "visibility": "#", "default_value": null, "is_static": false }
      ],
      "methods": [
        { "name": "scheduleNextCrow", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": false },
        { "name": "clearCrowFlag", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": false }
      ],
      "position": { "x": 650.0, "y": 250.0 },
      "size": { "width": 200.0, "height": 150.0 },
      "note": "鸡叫任务\n1. 每七天鸡叫一次，到达鸡叫时间随机预约 2:00-4:00 之间\n2. 鸡叫不可打断，鸡叫标志可清除",
      "provided_interfaces": [],
      "required_interfaces": []
    },
    {
      "id": "class_scheduler",
      "name": "TaskScheduler",
      "stereotype": "class",
      "attributes": [
        { "name": "taskList", "type": "List<BaseTask>", "visibility": "-", "default_value": null, "is_static": false }
      ],
      "methods": [
        { "name": "addTask", "return_type": "void", "params": "task: BaseTask", "visibility": "+", "is_static": false, "is_abstract": false },
        { "name": "removeTask", "return_type": "void", "params": "taskId: string", "visibility": "+", "is_static": false, "is_abstract": false },
        { "name": "executeTasks", "return_type": "void", "params": "", "visibility": "+", "is_static": false, "is_abstract": false }
      ],
      "position": { "x": 350.0, "y": 400.0 },
      "size": { "width": 200.0, "height": 150.0 },
      "note": "",
      "provided_interfaces": [],
      "required_interfaces": []
    }
  ],
  "relations": [
    {
      "id": "rel_inherit_ota",
      "source": "class_ota_task",
      "target": "class_base_task",
      "type": "inheritance",
      "multiplicity_source": "",
      "multiplicity_target": "",
      "role_name": "",
      "note": ""
    },
    {
      "id": "rel_inherit_crow",
      "source": "class_crow_task",
      "target": "class_base_task",
      "type": "inheritance",
      "multiplicity_source": "",
      "multiplicity_target": "",
      "role_name": "",
      "note": ""
    },
    {
      "id": "rel_aggregate_ota",
      "source": "class_scheduler",
      "target": "class_ota_task",
      "type": "aggregation",
      "multiplicity_source": "1",
      "multiplicity_target": "*",
      "role_name": "tasks",
      "note": "调度器聚合管理所有任务"
    },
    {
      "id": "rel_aggregate_crow",
      "source": "class_scheduler",
      "target": "class_crow_task",
      "type": "aggregation",
      "multiplicity_source": "1",
      "multiplicity_target": "*",
      "role_name": "tasks",
      "note": ""
    }
  ],
  "lifelines": [],
  "messages": [],
  "fragments": [],
  "components": [],
  "comp_relations": [],
  "grid_visible": true,
  "grid_size": 20,
  "grid_color": "#e0e0e0",
  "grid_thickness": 1,
  "snap_to_grid": true,
  "zoom": 1.0,
  "pan_x": 0.0,
  "pan_y": 0.0
}
```

---
