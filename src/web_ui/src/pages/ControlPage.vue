<script setup lang="ts">
import { computed, ref } from 'vue'
import ControlPipeline from '../components/ControlPipeline.vue'
import ResponseChart from '../components/ResponseChart.vue'
import { station, controls, units, response, unitResponse } from '../mock/data'

defineProps<{ mock: boolean }>()
const emit = defineEmits<{ navigate: [page: 'commissioning' | 'events'] }>()
const selectedControl = ref('station')
const selectedUnit = ref(units[0].id)
const activeUnit = computed(() => units.find(unit => unit.id === selectedUnit.value) ?? units[0])
const sampledUnits = computed(() => units.filter(unit => unit.type === '风机').slice(0, 8))
</script>
<template>
 <div>

            <ControlPipeline />
            <el-tabs v-model="selectedControl">
              <el-tab-pane label="全场控制" name="station"/>
              <el-tab-pane label="机组控制" name="units"/>
            </el-tabs>
            <div class="content-grid">
              <div class="stack">
                <div class="panel-card panel">
                  <h2 class="app-panel-title panel-title">{{selectedControl==='station'?'全场控制工作台':'机组控制工作台'}}</h2>
                  <el-select v-if="selectedControl==='units'" v-model="selectedUnit" filterable style="width:220px"><el-option v-for="u in units" :key="u.id" :label="u.id+' · '+u.type" :value="u.id"/></el-select>
                  <div class="status-row"><span>控制权与安全状态</span><el-tag :type="mock?'success':'info'">{{mock?'远方自动 · 演示':'待接入'}}</el-tag></div>
                  <div class="status-row"><span>当前参考 / 指令</span><b>{{mock?(selectedControl==='station'?'195.0 MW':activeUnit.target+' MW'):'—'}}</b></div>
                  <div class="status-row"><span>实际反馈</span><b>{{mock?(selectedControl==='station'?station.active+' MW':activeUnit.active+' MW'):'—'}}</b></div>
                  <div class="status-row"><span>最近执行结果</span><b>{{mock?'成功 · 14:41:12':'—'}}</b></div>
                  <div class="toolbar" style="margin-top:16px"><el-button type="primary" disabled>下发指令</el-button>
                    <el-button disabled>投入 / 退出</el-button><el-button disabled>人工旁路</el-button></div>
                  <p class="section-note">指令下发需权限、安全状态、控制权代际及逐点执行结果确认；UNKNOWN 不自动重传。</p>
                </div>
                <div class="panel-card panel"><h2 class="app-panel-title panel-title">控制对象</h2>
                  <el-table :data="mock?(selectedControl==='station'?controls:sampledUnits):[]" stripe>
                    <el-table-column :prop="selectedControl==='station'?'name':'id'" :label="selectedControl==='station'?'功能':'机组'"/>
                    <el-table-column :prop="selectedControl==='station'?'target':'target'" label="目标"/><el-table-column :prop="selectedControl==='station'?'actual':'active'" label="反馈"/><el-table-column :prop="selectedControl==='station'?'limit':'status'" label="约束状态"/>
                  </el-table>
                </div>
              </div>
              <div class="stack">
                <div class="panel-card panel">
                  <div class="page-head"><h2 class="app-panel-title panel-title">指令与响应趋势</h2>
                    <el-tag type="info" effect="plain">示意响应 · 历史数据未接入</el-tag>
                  </div>
                  <ResponseChart v-if="mock" :data="selectedControl==='station'?response:unitResponse" :title="selectedControl==='station'?'全场指令与实测响应':'机组指令与实测响应'"/><div v-else class="chart-placeholder">趋势接口待接入</div>
                  <p class="section-note">同一时轴展示指令时间、反馈数据时间及质量状态。可跳转调试页查看性能指标。</p>
                  <el-button @click="emit('navigate','commissioning')">转到工程与整定</el-button>
                </div>
                <div class="panel-card panel"><h2 class="app-panel-title panel-title">本次控制效果</h2>
                  <div class="status-row"><span>跟踪误差</span><b>{{mock?'0.62 MW':'—'}}</b></div>
                  <div class="status-row"><span>响应延迟</span><b>{{mock?'1.4 s':'—'}}</b></div>
                  <div class="status-row"><span>调节时间</span><b>{{mock?'17.2 s':'—'}}</b></div>
                  <div class="status-row"><span>指令 ID / 审计</span><b>{{mock?'CMD-20261014-1032':'—'}}</b></div>
                  <el-button @click="emit('navigate','events')">查看控制审计</el-button>
                </div>
              </div>
            </div>

 </div>
</template>
