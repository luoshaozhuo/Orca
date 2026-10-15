<script setup lang="ts">
import { computed, ref } from 'vue'
import { events } from '../mock/data'
import { useEventReadModel } from '../composables/useControllerReadModel'
import type { AuditEvent, CommandAudit } from '../api/contracts'
import { controllerApi } from '../api/client'
const props = defineProps<{ mock: boolean }>()
const eventQuery = ref('')
const auditDrawer = ref(false)
const auditLoading = ref(false)
const auditError = ref<string | null>(null)
const auditRecord = ref<CommandAudit | null>(null)
let auditRequest = 0
async function openAudit(id: string) {
  if (props.mock || !id || id === '—') return
  const request = ++auditRequest
  auditDrawer.value = true
  auditLoading.value = true
  auditRecord.value = null
  auditError.value = null
  try {
    const record = await controllerApi.commandAudit(id)
    if (request === auditRequest) auditRecord.value = record
  } catch (cause) {
    if (request === auditRequest) auditError.value = cause instanceof Error ? cause.message : '无法读取指令审计'
  } finally {
    if (request === auditRequest) auditLoading.value = false
  }
}
const eventResource = useEventReadModel()
const eventState = eventResource.state
const eventError = eventResource.error
const eventData = eventResource.data
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
                <el-table-column label="追踪" width="90"><template #default="{row}"><el-button link :disabled="mock || row.command==='—'" @click="openAudit(row.command)">查看</el-button></template></el-table-column>
              </el-table>
              <el-alert v-if="!mock && eventState === 'error'" type="error" :title="eventError ?? '事件接口失败'" :closable="false" />
              <el-empty v-if="!mock && !eventData?.length" description="事件数据未连接或暂不可用" />
            </el-card>
            <el-drawer v-model="auditDrawer" title="指令逐点执行审计" direction="rtl" size="50%">
              <el-alert v-if="auditError" type="error" :title="auditError" :closable="false" />
              <el-skeleton v-if="auditLoading" :rows="4" animated />
              <template v-else-if="auditRecord">
                <el-descriptions :column="1" border>
                  <el-descriptions-item label="指令 ID">{{auditRecord.commandId}}</el-descriptions-item>
                  <el-descriptions-item label="执行状态">
                    <el-tag :type="auditRecord.state==='SUCCEEDED'?'success':auditRecord.state==='FAILED'?'danger':'warning'">{{auditRecord.state}}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="审计时间">{{auditRecord.timestamp}}</el-descriptions-item>
                </el-descriptions>
                <el-table :data="auditRecord.deviceResults" stripe>
                  <el-table-column prop="deviceId" label="设备" min-width="110" />
                  <el-table-column prop="state" label="状态" min-width="115" />
                  <el-table-column prop="reason" label="详细原因" min-width="180" />
                </el-table>
                <p class="section-note">UNKNOWN 表示执行结果未确认，不能视作失败后自动重传。</p>
              </template>
            </el-drawer>

 </div>
</template>
