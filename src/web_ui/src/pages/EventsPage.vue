<script setup lang="ts">
import { computed, ref } from 'vue'
import { events } from '../mock/data'
import { useEventReadModel } from '../composables/useControllerReadModel'
import type { AuditEvent } from '../api/contracts'
const props = defineProps<{ mock: boolean }>()
const eventQuery = ref('')
const eventResource = useEventReadModel()
const liveEvents = computed(() => (eventResource.data.value ?? []).map((event: AuditEvent) => ({
  time: event.timestamp, kind: event.kind, object: event.objectId, result: event.result,
  command: event.commandId ?? '—', detail: event.detail
})))
const filteredEvents = computed(() => (props.mock ? events : liveEvents.value).filter(event => Object.values(event).some(value =>
  String(value).toLowerCase().includes(eventQuery.value.trim().toLowerCase())
)))
</script>
<template>
 <div>

            <el-card shadow="never" class="panel-card panel"><h2 class="app-panel-title panel-title">事件、审计与操作追踪</h2>
              <div class="toolbar"><el-select disabled placeholder="事件类型" />
                <el-input v-model="eventQuery" clearable placeholder="搜索指令 ID、机组或操作人" />
                <el-button disabled>导出审计</el-button></div>
              <el-table :data="filteredEvents" stripe >
                <el-table-column prop="time" label="时间" min-width="175"/><el-table-column prop="kind" label="类型"/><el-table-column prop="object" label="对象"/>
                <el-table-column prop="result" label="执行结果"/><el-table-column prop="command" label="关联指令 ID" min-width="180"/><el-table-column prop="detail" label="详情" min-width="240"/>
              </el-table>
              <el-alert v-if="!mock && eventResource.state === 'error'" type="error" :title="eventResource.error ?? '事件接口失败'" :closable="false" />
              <el-empty v-if="!mock && !eventResource.data?.length" description="事件数据未连接或暂不可用" />
            </el-card>

 </div>
</template>
