<script setup lang="ts">
import { computed } from 'vue'
import EngineeringWorkbench from '../components/EngineeringWorkbench.vue'
import { controllerApi } from '../api/client'
import { useResource } from '../composables/useControllerReadModel'
import type { OfflineExperiment } from '../api/contracts'

defineProps<{ mock: boolean }>()
const experiments = useResource<OfflineExperiment[]>(controllerApi.experiments)
const experimentRows = computed(() => (experiments.data.value ?? []).map(item => ({
  id: item.id, createdAt: item.createdAt, algorithm: item.algorithmId,
  status: item.status, metrics: Object.entries(item.metrics).map(([name,value]) =>
    `${name}: ${value ?? '—'}`).join(' · ')
})))
const experimentState = experiments.state
const experimentError = experiments.error
</script>

<template>
  <div class="engineering-stack">
    <el-card v-if="!mock" shadow="never" class="panel-card panel">
      <h2 class="app-panel-title panel-title">离线试验记录（只读）</h2>
      <el-alert title="试验结果与生产控制指令相互隔离。此页面不提供参数发布、设备写入或控制器在线断点。" type="info" :closable="false" show-icon />
      <el-alert v-if="experimentState==='error'" :title="experimentError ?? '试验记录接口不可用'" type="error" :closable="false" />
      <el-table v-if="experimentRows.length" :data="experimentRows" stripe>
        <el-table-column prop="id" label="试验 ID" min-width="125" />
        <el-table-column prop="createdAt" label="创建时间" min-width="175" />
        <el-table-column prop="algorithm" label="算法" min-width="120" />
        <el-table-column prop="status" label="状态" min-width="100" />
        <el-table-column prop="metrics" label="指标" min-width="220" />
      </el-table>
      <el-empty v-else description="离线试验记录暂无可用数据" />
      <el-button :disabled="experimentState==='loading'" @click="experiments.refresh">读取试验记录</el-button>
    </el-card>
    <EngineeringWorkbench />
  </div>
</template>
