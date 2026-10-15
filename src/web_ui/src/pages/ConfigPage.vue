<script setup lang="ts">
import { ref } from 'vue'
import { station, controls, units } from '../mock/data'
import { controllerApi } from '../api/client'
import { useResource, useStationReadModel } from '../composables/useControllerReadModel'
import type { ControlFunction } from '../api/contracts'
import { isUsableMeasurement } from '../api/quality'
import { computed } from 'vue'
defineProps<{ mock: boolean }>()
const selectedConfig = ref('station')
const live = useStationReadModel()
const configuration = useResource(controllerApi.configuration, 0)
const configSnapshot = computed(() => configuration.data.value)
const configState = configuration.state
const configError = configuration.error
const coordinationFields = computed(() => Object.entries(configSnapshot.value?.coordination ?? {}).map(([name,value]) => ({
  name, value: typeof value === 'object' ? JSON.stringify(value) : String(value)
})))
const stationSnapshot = computed(() => live.station.data.value)
const deviceRows = computed(() => (live.devices.data.value ?? []).map(device => ({
  id: device.id, type: device.type, available: isUsableMeasurement(device.availablePower) ? device.availablePower.value : null,
  enabled: device.participation, status: device.status, weight: device.weight
})))
const functionRows = computed(() => (live.functions.data.value ?? []).map((item: ControlFunction) => ({
  name: item.name, enabled: item.enabled === null ? '未知' : item.enabled ? '已投入' : '已退出',
  algorithm: item.algorithm ?? '—'
})))
const validationState = ref<'idle' | 'validating' | 'valid' | 'invalid' | 'error'>('idle')
const validationDetail = ref('')
async function validateConfig() {
  if (!configSnapshot.value || validationState.value === 'validating') return
  validationState.value = 'validating'
  try {
    const result = await controllerApi.validateConfiguration(configSnapshot.value)
    validationState.value = result.valid ? 'valid' : 'invalid'
    validationDetail.value = result.valid ? '校验通过（尚未保存或应用）' :
      result.errors.map(item => `${item.path}: ${item.message}`).join('；')
  } catch (error) {
    validationState.value = 'error'
    validationDetail.value = error instanceof Error ? error.message : '校验请求失败'
  }
}
</script>
<template>
  <div>

            <el-tabs v-model="selectedConfig">
              <el-tab-pane label="全场配置" name="station">
                <el-row :gutter="12" class="config-row">
                  <el-col :xs="24" :lg="14"><el-card shadow="never" class="panel-card panel">
                    <h2 class="app-panel-title panel-title">电厂与控制配置</h2>
                    <el-tabs>
                      <el-tab-pane label="电厂参数"><el-descriptions v-if="mock || stationSnapshot" border :column="2"><el-descriptions-item label="电站名称">{{mock ? station.name : (configSnapshot?.station?.name ?? '—')}}</el-descriptions-item><el-descriptions-item label="额定容量">{{mock ? station.rated : (isUsableMeasurement(stationSnapshot?.ratedPower)?stationSnapshot.ratedPower.value:'—')}} MW</el-descriptions-item><el-descriptions-item label="控制周期">{{mock ? station.period : (isUsableMeasurement(stationSnapshot?.periodMs)?stationSnapshot.periodMs.value:'—')}} ms</el-descriptions-item><el-descriptions-item label="可调容量">{{mock ? station.available : (isUsableMeasurement(stationSnapshot?.availablePower)?stationSnapshot.availablePower.value:'—')}} MW</el-descriptions-item></el-descriptions><el-empty v-else description="并网点、额定容量、运行约束：后端配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="控制功能"><el-table v-if="mock || functionRows.length" :data="mock ? controls : functionRows"><el-table-column prop="name" label="控制功能"/><el-table-column prop="enabled" label="状态"/><el-table-column prop="algorithm" label="算法"/></el-table><el-empty v-else description="控制功能配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="协调策略"><el-descriptions v-if="mock" border><el-descriptions-item label="合成策略">default（模拟）</el-descriptions-item><el-descriptions-item label="IR 优先级">1</el-descriptions-item><el-descriptions-item label="PFR 优先级">2</el-descriptions-item><el-descriptions-item label="AGC 优先级">3</el-descriptions-item></el-descriptions><el-descriptions v-else-if="coordinationFields.length" border :column="1"><el-descriptions-item v-for="item in coordinationFields" :key="item.name" :label="item.name">{{item.value}}</el-descriptions-item></el-descriptions><el-empty v-else description="协调策略配置待接入"/></el-tab-pane>
                    </el-tabs>
                  </el-card></el-col>
                  <el-col :xs="24" :lg="10"><el-card shadow="never" class="panel-card panel"><h2 class="app-panel-title panel-title">接入与配置管理</h2>
                    <div class="status-row"><span>上位机 / 调度接口</span><el-tag :type="mock?'success':'info'">{{mock?'已配置 · 演示':'待配置'}}</el-tag></div>
                    <div class="status-row"><span>Commander gRPC</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>消息中间件及订阅</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>配置版本及生效状态</span><b>{{mock?station.version+' · 生效中':(configSnapshot?.version ?? '—')}}</b></div>
                    <div class="toolbar" >
                      <el-button :disabled="mock || configState==='loading'" @click="configuration.refresh">重新加载</el-button>
                      <el-button disabled>导入配置（未开放）</el-button><el-button disabled>导出配置（未开放）</el-button>
                      <el-button type="primary" :disabled="mock || !configSnapshot || validationState==='validating'" @click="validateConfig">仅校验配置</el-button>
                      <el-button disabled>应用配置（未开放）</el-button>
                    </div>
                    <p class="section-note">加载、校验、保存、应用分离；接口未接入时不能虚报成功。</p>
                    <el-alert v-if="!mock && configState==='error'" type="error" :title="configError ?? '配置读取失败'" :closable="false" />
                    <el-alert v-if="!mock && validationState!=='idle'" :type="validationState==='valid'?'success':validationState==='validating'?'info':'warning'" :title="validationDetail || '校验中'" :closable="false" />
                  </el-card></el-col>
                </el-row>
              </el-tab-pane>
              <el-tab-pane label="机组配置" name="units">
                <el-card shadow="never" class="panel-card panel"><h2 class="app-panel-title panel-title">机组与资源管理</h2>
                  <div class="toolbar"><el-input placeholder="搜索机组名称或编号" disabled />
                    <el-select disabled placeholder="设备类型" /></div>
                  <el-table v-if="mock || deviceRows.length" :data="mock?units:deviceRows" stripe >
                    <el-table-column prop="id" label="设备编号"/><el-table-column prop="type" label="类型"/><el-table-column prop="available" label="可用容量 (MW)"/>
                    <el-table-column label="控制参与"><template #default="scope"><el-tag :type="scope.row.enabled?'success':'info'">{{scope.row.enabled?'参与':'退出'}}</el-tag></template></el-table-column><el-table-column prop="status" label="运行状态"/><el-table-column prop="weight" label="控制权重"/>
                  </el-table>
                  <el-empty v-if="!mock && !deviceRows.length" description="设备与测点元数据暂不可用"/>
                </el-card>
              </el-tab-pane>
            </el-tabs>

  </div>
</template>
