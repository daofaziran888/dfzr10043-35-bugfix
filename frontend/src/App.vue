<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">织流工坊 · 网页流程自动化与RPA采集执行平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向RPA流程模板编排、元素定位漂移检测、机器人节点调度、执行步骤轨迹、凭据轮换、文件归集、采集批次确认与下游交接的流程自动化采集执行平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "恢复清单", path: "/recovery-console" }, { label: "流程模板", path: "/flow_template" }, { label: "元素定位", path: "/element_locator" }, { label: "机器人节点", path: "/robot_node" }, { label: "流程执行", path: "/flow_run" }, { label: "步骤轨迹", path: "/step_trace" }, { label: "任务队列", path: "/task_queue" }, { label: "凭据保管", path: "/credential_vault" }, { label: "表单字段", path: "/form_field" }, { label: "文件归集", path: "/file_drop" }, { label: "识别区域", path: "/ocr_region" }, { label: "排程计划", path: "/schedule_plan" }, { label: "异常规则", path: "/exception_rule" }, { label: "重试策略", path: "/retry_policy" }, { label: "采集批次", path: "/capture_batch" }, { label: "字段映射", path: "/field_mapping" }, { label: "数据落库", path: "/data_landing" }, { label: "审计轨迹", path: "/audit_trail" }, { label: "资源额度", path: "/resource_pool" }, { label: "版本发布", path: "/version_release" }, { label: "运行指标", path: "/metric_board" }, { label: "抽检样本", path: "/sample_check" }, { label: "交接包", path: "/handoff_pack" }]
</script>
