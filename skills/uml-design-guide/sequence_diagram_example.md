## 7. 完整示例

### 7.1 简单同步交互（带异常处理）

```json
{
  "version": "1.0",
  "name": "User Login Flow",
  "diagram_type": "sequence",
  "component_id": "",
  "classes": [],
  "relations": [],
  "lifelines": [
    {
      "id": "life_client",
      "name": "Client",
      "class_ref": "",
      "x": 100.0,
      "activations": []
    },
    {
      "id": "life_auth",
      "name": "AuthService",
      "class_ref": "",
      "x": 350.0,
      "activations": []
    },
    {
      "id": "life_db",
      "name": "Database",
      "class_ref": "",
      "x": 600.0,
      "activations": []
    }
  ],
  "messages": [
    {
      "id": "msg_1",
      "from_lifeline": "life_client",
      "to_lifeline": "life_auth",
      "label": "login(username, password)",
      "type": "sync",
      "order": 1,
      "y": 190.0,
      "note": "用户发起登录请求，传入用户名和密码"
    },
    {
      "id": "msg_2",
      "from_lifeline": "life_auth",
      "to_lifeline": "life_db",
      "label": "findUser(username)",
      "type": "sync",
      "order": 2,
      "y": 230.0,
      "note": "根据用户名查询用户记录"
    },
    {
      "id": "msg_3",
      "from_lifeline": "life_db",
      "to_lifeline": "life_auth",
      "label": "return userRecord",
      "type": "return",
      "order": 3,
      "y": 270.0,
      "note": "返回查询到的用户信息或 null"
    },
    {
      "id": "msg_missing",
      "from_lifeline": "life_auth",
      "to_lifeline": "life_client",
      "label": "return userNotFound",
      "type": "return",
      "order": 4,
      "y": 335,
      "note": "No user record: end this login interaction before checking the password"
    },
    {
      "id": "msg_4",
      "from_lifeline": "life_auth",
      "to_lifeline": "life_auth",
      "label": "validatePassword(hash)",
      "type": "self",
      "order": 5,
      "y": 400,
      "note": "验证密码哈希是否匹配"
    },
    {
      "id": "msg_5",
      "from_lifeline": "life_auth",
      "to_lifeline": "life_client",
      "label": "return authToken",
      "type": "return",
      "order": 6,
      "y": 480,
      "note": "认证成功后返回 JWT Token"
    },
    {
      "id": "msg_invalid",
      "from_lifeline": "life_auth",
      "to_lifeline": "life_client",
      "label": "return invalidCredentials",
      "type": "return",
      "order": 7,
      "y": 600,
      "note": "Password mismatch: return authentication failure"
    }
  ],
  "fragments": [
    {
      "id": "frag_missing",
      "type": "break",
      "label": "Missing user",
      "x": 80,
      "width": 700,
      "y_start": 295,
      "y_end": 365,
      "lifeline_ids": [
        "life_client",
        "life_auth",
        "life_db"
      ],
      "parent_fragment_id": "",
      "parent_operand_id": "",
      "operands": [
        {
          "id": "operand_missing",
          "guard": "[userRecord is null]",
          "message_ids": [
            "msg_missing"
          ],
          "y_start": 318,
          "y_end": 358
        }
      ]
    },
    {
      "id": "frag_alt",
      "type": "alt",
      "label": "Authentication result",
      "x": 80,
      "width": 480,
      "y_start": 440,
      "y_end": 650,
      "lifeline_ids": [
        "life_client",
        "life_auth"
      ],
      "parent_fragment_id": "",
      "parent_operand_id": "",
      "operands": [
        {
          "id": "operand_valid",
          "guard": "[password matches]",
          "message_ids": [
            "msg_5"
          ],
          "y_start": 464,
          "y_end": 525
        },
        {
          "id": "operand_invalid",
          "guard": "[else]",
          "message_ids": [
            "msg_invalid"
          ],
          "y_start": 560,
          "y_end": 642
        }
      ]
    }
  ],
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

### 7.2 带可选处理的业务交互（OTA 通知流程）

```json
{
  "version": "1.0",
  "name": "OTA Notification Flow",
  "diagram_type": "sequence",
  "component_id": "",
  "classes": [],
  "relations": [],
  "lifelines": [
    {
      "id": "life_ota",
      "name": "OtaTask",
      "class_ref": "",
      "x": 120.0,
      "activations": []
    },
    {
      "id": "life_crow",
      "name": "CrowTask",
      "class_ref": "",
      "x": 500.0,
      "activations": []
    }
  ],
  "messages": [
    {
      "id": "msg_notify",
      "from_lifeline": "life_ota",
      "to_lifeline": "life_crow",
      "label": "notifyOtaRequest()",
      "type": "sync",
      "order": 1,
      "y": 190.0,
      "note": "通知鸡叫进程 OTA 的请求和预计升级时间"
    },
    {
      "id": "msg_check",
      "from_lifeline": "life_crow",
      "to_lifeline": "life_crow",
      "label": "checkCrowTiming()",
      "type": "self",
      "order": 2,
      "y": 250.0,
      "note": "检查鸡叫时间是否与 OTA 时间冲突"
    },
    {
      "id": "msg_cancel",
      "from_lifeline": "life_crow",
      "to_lifeline": "life_crow",
      "label": "cancelScheduledCrow()",
      "type": "self",
      "order": 3,
      "y": 310.0,
      "note": "如果冲突，取消预定鸡叫任务"
    },
    {
      "id": "msg_return",
      "from_lifeline": "life_crow",
      "to_lifeline": "life_ota",
      "label": "return result",
      "type": "return",
      "order": 4,
      "y": 370.0,
      "note": "将鸡叫进程对 OTA 的处理结果返回"
    }
  ],
  "fragments": [
    {
      "id": "frag_opt_conflict",
      "type": "opt",
      "label": "Cancel conflicting schedule",
      "x": 120.0,
      "width": 700.0,
      "y_start": 270.0,
      "y_end": 355.0,
      "lifeline_ids": ["life_crow"],
      "parent_fragment_id": "",
      "parent_operand_id": "",
      "operands": [{
        "id": "operand_conflict",
        "guard": "[crow timing conflicts with OTA]",
        "message_ids": ["msg_cancel"],
        "y_start": 290.0,
        "y_end": 350.0
      }]
    }
  ],
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
