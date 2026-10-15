<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ControlPipeline from '../components/ControlPipeline.vue'
import ResponseChart from '../components/ResponseChart.vue'
import { station, controls, units, response, unitResponse } from '../mock/data'
import { useEventReadModel, useResource, useStationReadModel } from '../composables/useControllerReadModel'
import { controllerApi } from '../api/client'
import type { HistorySeries, Measurement } from '../api/contracts'
import { isUsableMeasurement, isUsableStation, isUsableState } from '../api/quality'

const props = defineProps<{ mock: boolean }>()
const emit = defineEmits<{ navigate: [page: 'commissioning' | 'events'] }>()
const selectedControl = ref('station')
const selectedUnit = ref(units[0].id)
const activeUnit = computed(() => units.find(unit => unit.id === selectedUnit.value) ?? units[0])
const sampledUnits = computed(() => units.filter(unit => unit.type === '风机').slice(0, 8))
const live = useStationReadModel()
const auditEvents = useEventReadModel()
const recentEvent = computed(() => (auditEvents.data.value ?? []).filter(event =>
  selectedControl.value === 'station' ? event.objectId === 'station' || event.objectId === '全场' : event.objectId === selectedUnit.value
).sort((a,b) => b.timestamp.localeCompare(a.timestamp))[0] ?? null)
const recentExecution = computed(() => recentEvent.value ? `${recentEvent.value.result} · ${recentEvent.value.timestamp}` : 'UNKNOWN · 暂无审计记录')
const recentCommandId = computed(() => recentEvent.value?.commandId ?? '—')
const historySignals = computed(() => selectedControl.value === 'station'
  ? ['station.active_power','station.active_power_target']
  : [`device.${selectedUnit.value}.active_power`,`device.${selectedUnit.value}.target_power`])
const liveHistory = useResource<HistorySeries[]>(() => {
  const now = Date.now()
  return controllerApi.history(historySignals.value, new Date(now - 30*60*1000).toISOString(), new Date(now).toISOString())
}, 5000)
watch(historySignals, () => void liveHistory.refresh())
const historySeries = computed(() => liveHistory.data.value ?? [])
const historyRows = computed(() => {
  const [actualId,targetId] = historySignals.value
  const actual = historySeries.value.find(item => item.signalId === actualId)
  const target = historySeries.value.find(item => item.signalId === targetId)
  if (!actual || !target) return []
  const references = new Map(target.points.filter(point => point.quality === 'GOOD' && point.value !== null)
    .map(point => [point.timestamp, point.value!]))
  return actual.points.filter(point => point.quality === 'GOOD' && point.value !== null && references.has(point.timestamp))
    .map(point => ({time:point.timestamp,target:references.get(point.timestamp)!,
      actual:point.value!,total:point.value!,limit:0})).slice(-180)
})
const historyState = liveHistory.state
const historyError = liveHistory.error
const liveStation = computed(() => live.station.data.value)
const liveDevice = computed(() => live.devices.data.value?.find(unit => unit.id === selectedUnit.value))
const liveControls = computed(() => (live.functions.data.value ?? []).map(item => ({
  name: item.name,
  target: formatMeasurement(item.target),
  active: formatMeasurement(item.actual),
  limit: isUsableState(item) ? item.constraint ?? '未知' : 'UNKNOWN'
})))
const liveUnits = computed(() => (live.devices.data.value ?? []).slice(0, 8).map(item => ({
  id: item.id,
  target: formatMeasurement(item.targetPower),
  active: formatMeasurement(item.activePower),
  status: isUsableState(item) ? item.status : 'UNKNOWN'
})))
const availableUnits = computed(() => props.mock ? units.map(item => ({ id: item.id, type: item.type })) :
  (live.devices.data.value ?? []).map(item => ({ id: item.id, type: item.type })))
watch(availableUnits, items => {
  if (items.length && !items.some(item => item.id === selectedUnit.value)) selectedUnit.value = items[0].id
})
function formatMeasurement(value: Measurement | undefined): string {
  return isUsableMeasurement(value)
    ? `${value.value.toFixed(2)} ${value.unit}` : '—'
}
const currentTarget = computed(() => props.mock
  ? selectedControl.value === 'station' ? '195.0 MW' : `${activeUnit.value.target} MW`
  : selectedControl.value === 'station' ? formatMeasurement(liveStation.value?.targetPower) : formatMeasurement(liveDevice.value?.targetPower))
const currentActual = computed(() => props.mock
  ? selectedControl.value === 'station' ? `${station.active} MW` : `${activeUnit.value.active} MW`
  : selectedControl.value === 'station' ? formatMeasurement(liveStation.value?.activePower) : formatMeasurement(liveDevice.value?.activePower))
const controlRows = computed(() => props.mock
  ? selectedControl.value === 'station' ? controls : sampledUnits.value
  : selectedControl.value === 'station' ? liveControls.value : liveUnits.value)

</script>
<template>
 <div>

            <ControlPipeline :mock="mock" />
            <el-tabs v-model="selectedControl">
              <el-tab-pane label="全场控制" name="station"/>
              <el-tab-pane label="机组控制" name="units"/>
            </el-tabs>
            <el-row :gutter="12" class="control-row">
              <el-col :xs="24" :lg="14"><div class="stack">
                <el-card shadow="never" class="panel-card panel">
                  <h2 class="app-panel-title panel-title">{{selectedControl==='station'?'全场控制工作台':'机组控制工作台'}}</h2>
                  <el-select v-if="selectedControl==='units'" v-model="selectedUnit" filterable><el-option v-for="u in availableUnits" :key="u.id" :label="u.id+' · '+u.type" :value="u.id"/></el-select>
                  <div class="status-row"><span>当前控制权</span>
                    <el-tag type="info">{{mock?'远方自动 · 示意':(isUsableStation(liveStation) ? liveStation.authority ?? '未知':'未知')}}</el-tag>
                  </div>
                  <div class="status-row"><span>执行安全状态</span>
                    <el-tag :type="!mock && isUsableStation(liveStation) && liveStation.safetyState==='BLOCKED' ? 'danger' : 'info'">
                      {{mock?'离线示意 · 不可写':isUsableStation(liveStation) ? liveStation.safetyState ?? 'UNKNOWN' : 'UNKNOWN'}}
                    </el-tag>
                  </div>
                  <div class="status-row"><span>写入许可</span><b>
                    {{mock?'关闭':isUsableStation(liveStation) ?
                      liveStation.writeEnabled===true?'后端允许 · UI 只读':liveStation.writeEnabled===false?'关闭':'未知':'未知'}}
                  </b></div>
                  <div class="status-row"><span>控制权代际</span><b>
                    {{mock?'—':isUsableStation(liveStation) ? liveStation.authorityGeneration ?? '—':'—'}}
                  </b></div>
                  <div class="status-row"><span>当前参考 / 指令</span><b>{{currentTarget}}</b></div>
                  <div class="status-row"><span>实际反馈</span><b>{{currentActual}}</b></div>
                  <div class="status-row"><span>最近执行结果</span><b>{{mock?'成功 · 14:41:12':recentExecution}}</b></div>
                  <div class="toolbar" style="margin-top:16px"><el-button type="primary" disabled>下发指令</el-button>
                    <el-button disabled>投入 / 退出</el-button><el-button disabled>人工旁路</el-button></div>
                  <p class="section-note">指令下发需权限、安全状态、控制权代际及逐点执行结果确认；UNKNOWN 不自动重传。</p>
                </el-card>
                <el-card shadow="never" class="panel-card panel"><h2 class="app-panel-title panel-title">控制对象</h2>
                  <el-table :data="controlRows" stripe>
                    <el-table-column :prop="selectedControl==='station'?'name':'id'" :label="selectedControl==='station'?'功能':'机组'"/>
                    <el-table-column :prop="selectedControl==='station'?'target':'target'" label="目标"/><el-table-column :prop="selectedControl==='station'?'actual':'active'" label="反馈"/><el-table-column :prop="selectedControl==='station'?'limit':'status'" label="约束状态"/>
                  </el-table>
                </el-card>
              </div></el-col>
              <el-col :xs="24" :lg="10"><div class="stack">
                <el-card shadow="never" class="panel-card panel">
                  <div class="page-head"><h2 class="app-panel-title panel-title">指令与响应趋势</h2>
                    <el-tag type="info" effect="plain">{{mock ? '示意响应' : '质量有效的历史数据'}}</el-tag>
                  </div>
                  <ResponseChart v-if="mock || historyRows.length" :data="mock ? (selectedControl==='station'?response:unitResponse) : historyRows"
                    :show-limit="mock" :preview="mock" :title="selectedControl==='station'?'全场指令与实测响应':'机组指令与实测响应'"/>
                  <el-alert v-if="!mock && historyState==='error'" type="error" :title="historyError ?? '历史趋势不可用'" :closable="false" />
                  <el-empty v-else-if="!mock && !historyRows.length" description="当前无质量有效且时间对齐的历史响应" />
                  <p class="section-note">同一时轴展示指令时间、反馈数据时间及质量状态。可跳转调试页查看性能指标。</p>
                  <el-button @click="emit('navigate','commissioning')">转到工程与整定</el-button>
                </el-card>
                <el-card shadow="never" class="panel-card panel"><h2 class="app-panel-title panel-title">本次控制效果</h2>
                  <div class="status-row"><span>跟踪误差</span><b>{{mock?'0.62 MW':'—'}}</b></div>
                  <div class="status-row"><span>响应延迟</span><b>{{mock?'1.4 s':'—'}}</b></div>
                  <div class="status-row"><span>调节时间</span><b>{{mock?'17.2 s':'—'}}</b></div>
                  <div class="status-row"><span>指令 ID / 审计</span><b>{{mock?'CMD-20261014-1032':recentCommandId}}</b></div>
                  <el-button @click="emit('navigate','events')">查看控制审计</el-button>
                </el-card>
              </div></el-col>
            </el-row>

 </div>
</template>
