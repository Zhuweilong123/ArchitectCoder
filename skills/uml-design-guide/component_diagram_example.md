## 6. 完整示例

### 6.1 简单三层架构

```json
{
  "version": "1.0",
  "name": "Web Application Architecture",
  "diagram_type": "component",
  "classes": [],
  "relations": [],
  "lifelines": [],
  "messages": [],
  "fragments": [],
  "components": [
    {
      "id": "comp_web",
      "name": "WebFrontend",
      "x": 50.0,
      "y": 50.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["UI"],
      "required_interfaces": ["RestApi"]
    },
    {
      "id": "comp_api",
      "name": "ApiGateway",
      "x": 320.0,
      "y": 50.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["RestApi"],
      "required_interfaces": ["DatabaseClient", "CacheService"]
    },
    {
      "id": "comp_db",
      "name": "Database",
      "x": 590.0,
      "y": 50.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["DatabaseClient"],
      "required_interfaces": []
    },
    {
      "id": "comp_cache",
      "name": "RedisCache",
      "x": 590.0,
      "y": 260.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["CacheService"],
      "required_interfaces": []
    }
  ],
  "comp_relations": [
    {
      "id": "crel_web_api",
      "source": "comp_web",
      "target": "comp_api",
      "type": "dependency"
    },
    {
      "id": "crel_api_db",
      "source": "comp_api",
      "target": "comp_db",
      "type": "dependency"
    },
    {
      "id": "crel_api_cache",
      "source": "comp_api",
      "target": "comp_cache",
      "type": "dependency"
    }
  ],
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

### 6.2 带子组件的嵌入式架构（车载系统示例）

```json
{
  "version": "1.0",
  "name": "Vehicle OTA System",
  "diagram_type": "component",
  "classes": [],
  "relations": [],
  "lifelines": [],
  "messages": [],
  "fragments": [],
  "components": [
    {
      "id": "comp_cloud",
      "name": "Cloud",
      "x": -400.0,
      "y": 0.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["CloudSendOtaRequestToTbox()"],
      "required_interfaces": []
    },
    {
      "id": "comp_tbox",
      "name": "Tbox",
      "x": -150.0,
      "y": 0.0,
      "width": 200.0,
      "height": 160.0,
      "parent_id": "",
      "provided_interfaces": ["RecieveOtaRequestFromCloud()", "TboxSendOtaReqToMDC()"],
      "required_interfaces": ["CloudSendOtaRequestToTbox()"]
    },
    {
      "id": "comp_mdc",
      "name": "MDC",
      "x": 100.0,
      "y": -50.0,
      "width": 500.0,
      "height": 280.0,
      "parent_id": "",
      "provided_interfaces": ["RecieveOtaRequestFromTbox()"],
      "required_interfaces": ["TboxSendOtaRequestToTbox()"]
    },
    {
      "id": "comp_ota",
      "name": "OtaTask",
      "x": 380.0,
      "y": 80.0,
      "width": 120.0,
      "height": 60.0,
      "parent_id": "comp_mdc",
      "provided_interfaces": [],
      "required_interfaces": []
    },
    {
      "id": "comp_crow",
      "name": "CrowTask",
      "x": 380.0,
      "y": -30.0,
      "width": 120.0,
      "height": 60.0,
      "parent_id": "comp_mdc",
      "provided_interfaces": [],
      "required_interfaces": []
    },
    {
      "id": "comp_scheduler",
      "name": "TaskScheduler",
      "x": 200.0,
      "y": 40.0,
      "width": 120.0,
      "height": 60.0,
      "parent_id": "comp_mdc",
      "provided_interfaces": [],
      "required_interfaces": []
    },
    {
      "id": "comp_app",
      "name": "MM_APP",
      "x": 50.0,
      "y": 40.0,
      "width": 120.0,
      "height": 60.0,
      "parent_id": "comp_mdc",
      "provided_interfaces": [],
      "required_interfaces": []
    }
  ],
  "comp_relations": [
    {
      "id": "crel_cloud_tbox",
      "source": "comp_cloud",
      "target": "comp_tbox",
      "type": "dependency"
    },
    {
      "id": "crel_tbox_mdc",
      "source": "comp_tbox",
      "target": "comp_mdc",
      "type": "dependency"
    },
    {
      "id": "crel_sched_crow",
      "source": "comp_scheduler",
      "target": "comp_crow",
      "type": "dependency"
    },
    {
      "id": "crel_sched_ota",
      "source": "comp_scheduler",
      "target": "comp_ota",
      "type": "dependency"
    },
    {
      "id": "crel_app_sched",
      "source": "comp_app",
      "target": "comp_scheduler",
      "type": "dependency"
    }
  ],
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
