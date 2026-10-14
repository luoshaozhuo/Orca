<script setup lang="ts">
import { computed, ref } from 'vue'
import * as echarts from 'echarts'
import { onMounted, onUnmounted, watch } from 'vue'
import type { ECharts } from 'echarts'
import { chartVisuals, chartAxisText, chartLegendText } from '../utils/chartTheme'

type Experiment = { id:string; law:string; kp:number; ki:number; delay:number; settle:number; error:number; overshoot:number }
const functionId=ref('AGC')
const disturbance=ref('step')
const amplitude=ref(15)
const duration=ref(120)
const selectedLaw=ref('PI')
const kp=ref(0.8)
const ki=ref(0.12)
const latency=ref(1.2)
const startKp=ref(0.3)
const endKp=ref(1.5)
const steps=ref(7)
const breakpoint=ref('control')
const cursor=ref(0)
const snapshot=ref<Experiment[]>([])
const scan=ref<Experiment[]>([])
const chartEl=ref<HTMLDivElement|null>(null)
let chart:ECharts|undefined
let ro:ResizeObserver|undefined

const stages=[
 {id:'source',name:'参考有效性'}, {id:'preprocess',name:'信号预处理'},
 {id:'trajectory',name:'目标轨迹'}, {id:'control',name:'控制律'},
 {id:'coordination',name:'功能协调'}, {id:'allocation',name:'设备分配'},
 {id:'guard',name:'执行保护'}
]
const breakpointIndex=computed(()=>stages.findIndex(stage=>stage.id===breakpoint.value))
const canRunToBreakpoint=computed(()=>breakpointIndex.value>cursor.value)
const stageLabel=computed(()=>stages[cursor.value]?.name ?? '结束')
const stageOutput=computed(()=>({
 source:'有效参考与来源授权检查',preprocess:'质量有效的同步输入',
 trajectory:'斜坡约束后的目标',control:'候选有功指令',
 coordination:'多控制需求合成目标',allocation:'设备计划与分配残差',
 guard:'仅离线验证，无生产写入'
} as Record<string,string>)[stages[cursor.value]?.id ?? ''] ?? '当前周期检查结束')

function simulate(k:number, integral:number, law:string):Experiment {
 // Illustrative heuristic, not a plant/controller model or validated transfer function.
 const gain=law==='P'?k:law==='PI'?k+integral*2:law==='FEEDFORWARD_PI'?k+integral*2+0.35:0.7
 const tau=18/(Math.max(0.1,gain))
 const settle=tau*3.2+latency.value
 const error=law==='P'?amplitude.value/(1+gain*4):amplitude.value/(1+gain*28)
 const overshoot=Math.max(0,(gain-1.05)*8)
 return {id:law+'-'+k.toFixed(2),law,kp:k,ki:integral,delay:latency.value,settle:+settle.toFixed(2),error:+error.toFixed(3),overshoot:+overshoot.toFixed(2)}
}
const current=computed(()=>simulate(kp.value,ki.value,selectedLaw.value))
const comparison=computed(()=>[...snapshot.value,current.value])
function addBaseline(){snapshot.value=[...snapshot.value,{...current.value,id:'方案 '+(snapshot.value.length+1)}].slice(-5)}
function scanParameters(){
 if(startKp.value<=0||endKp.value<startKp.value||steps.value<2||steps.value>30)return
 scan.value=Array.from({length:steps.value},(_,i)=>simulate(startKp.value+(endKp.value-startKp.value)*i/(steps.value-1),ki.value,selectedLaw.value))
}
function draw(){
 if(!chart)return
 const rows=scan.value.length?scan.value:comparison.value
 chart.setOption({
  ...chartVisuals(),legend:{top:0,...chartLegendText()},grid:{top:48,bottom:38,left:65,right:65},
  xAxis:{type:'category',name:'方案 / Kp',data:rows.map(r=>r.kp.toFixed(2)),...chartAxisText()},
  yAxis:[{type:'value',name:'调节时间 s',scale:true,...chartAxisText()},{type:'value',name:'超调 %',scale:true,...chartAxisText()}],
  series:[
   {name:'调节时间',type:'line',data:rows.map(r=>r.settle)},
   {name:'超调量',type:'line',yAxisIndex:1,data:rows.map(r=>r.overshoot)}
  ]
 },true)
}
onMounted(()=>{if(!chartEl.value)return;chart=echarts.init(chartEl.value);draw();ro=new ResizeObserver(()=>chart?.resize());ro.observe(chartEl.value)})
watch([scan,comparison],draw,{deep:true})
onUnmounted(()=>{ro?.disconnect();chart?.dispose()})
</script>

<template>
  <div class="engineering-stack">
    <el-alert title="离线设计沙箱 · 下方响应数据由简化示意模型生成，不代表 Controller 实际控制性能或标准验收结果。所有操作均不写设备。" type="info" :closable="false" show-icon />
    <div class="panel-card panel">
      <h2 class="app-panel-title panel-title">试验工况与控制律</h2>
      <el-form label-position="top" class="engineering-form">
        <el-form-item label="控制功能"><el-select v-model="functionId"><el-option v-for="f in ['AGC','AVC','PFR','IR','VR']" :key="f" :label="f" :value="f"/></el-select></el-form-item>
        <el-form-item label="扰动"><el-select v-model="disturbance"><el-option label="阶跃" value="step"/><el-option label="斜坡" value="ramp"/><el-option label="脉冲" value="pulse"/><el-option label="历史回放（待接入）" value="history" disabled/></el-select></el-form-item>
        <el-form-item label="幅值（示意）"><el-input-number v-model="amplitude" :min="0.1" :max="100" :step="1"/></el-form-item>
        <el-form-item label="持续时间 (s)"><el-input-number v-model="duration" :min="10" :max="600" :step="10"/></el-form-item>
        <el-form-item label="候选控制律"><el-select v-model="selectedLaw"><el-option label="P" value="P"/><el-option label="PI" value="PI"/><el-option label="前馈 + PI" value="FEEDFORWARD_PI"/></el-select></el-form-item>
        <el-form-item label="Kp"><el-input-number v-model="kp" :min="0.01" :max="5" :step="0.1" :precision="2"/></el-form-item>
        <el-form-item label="Ki"><el-input-number v-model="ki" :disabled="selectedLaw==='P'" :min="0" :max="5" :step="0.01" :precision="2"/></el-form-item>
        <el-form-item label="链路延时 (s)"><el-input-number v-model="latency" :min="0" :max="30" :step="0.1" :precision="1"/></el-form-item>
      </el-form>
      <el-button type="primary" @click="addBaseline">加入方案对比（本地）</el-button>
    </div>
    <el-row :gutter="12">
      <el-col :xs="24" :lg="12"><div class="panel-card panel engineering-panel">
        <h2 class="app-panel-title panel-title">Pipeline 离线断点检查</h2>
        <el-select v-model="breakpoint" aria-label="停止节点" style="width:100%">
          <el-option v-for="s in stages" :key="s.id" :label="s.name" :value="s.id"/>
        </el-select>
        <el-steps :active="cursor+1" direction="vertical" class="engineering-steps" finish-status="success">
          <el-step v-for="s in stages" :key="s.id" :title="s.name"/>
        </el-steps>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="当前节点">{{stageLabel}}</el-descriptions-item>
          <el-descriptions-item label="节点输出语义">{{stageOutput}}</el-descriptions-item>
        </el-descriptions>
        <div class="engineering-actions">
          <el-button :disabled="cursor===0" @click="cursor=0">重置</el-button>
          <el-button :disabled="cursor>=stages.length-1" @click="cursor=Math.min(stages.length-1,cursor+1)">单步</el-button>
          <el-button :disabled="!canRunToBreakpoint" @click="cursor=breakpointIndex">运行至断点</el-button>
        </div>
        <p class="section-note">仅离线节点游标演示，不执行实际 Controller 算法；目标断点必须位于当前节点之后。实时控制循环禁止断点暂停。</p>
      </div></el-col>
      <el-col :xs="24" :lg="12"><div class="panel-card panel engineering-panel">
        <h2 class="app-panel-title panel-title">参数扫描与方案比较</h2>
        <el-form label-position="top" class="engineering-form">
          <el-form-item label="扫描起始 Kp"><el-input-number v-model="startKp" :min="0.01" :max="5" :step="0.1"/></el-form-item>
          <el-form-item label="扫描结束 Kp"><el-input-number v-model="endKp" :min="0.01" :max="5" :step="0.1"/></el-form-item>
          <el-form-item label="扫描点数"><el-input-number v-model="steps" :min="2" :max="30" :step="1"/></el-form-item>
        </el-form>
        <el-button :disabled="endKp<startKp" @click="scanParameters">执行示意参数扫描</el-button>
        <div ref="chartEl" class="engineering-chart" role="img" aria-label="候选参数扫描的调节时间和超调量趋势"/>
      </div></el-col>
    </el-row>
    <div class="panel-card panel">
      <h2 class="app-panel-title panel-title">候选方案 A/B 比较</h2>
      <el-table :data="comparison" stripe>
        <el-table-column prop="id" label="方案" min-width="125"/>
        <el-table-column prop="law" label="控制律"/>
        <el-table-column prop="kp" label="Kp"/>
        <el-table-column prop="ki" label="Ki"/>
        <el-table-column prop="settle" label="调节时间 (s)"/>
        <el-table-column prop="overshoot" label="超调 (%)"/>
        <el-table-column prop="error" label="稳态偏差 (MW)"/>
      </el-table>
      <p class="section-note">比较结果只用于演示操作流程，不用于调参决策或验收。生产发布必须另经合法性、仿真、联锁和授权审批。</p>
    </div>
  </div>
</template>

<style scoped>
.engineering-stack{display:flex;flex-direction:column;gap:var(--app-space-3)}
.engineering-form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--app-space-2)}
.engineering-form :deep(.el-input-number),.engineering-form :deep(.el-select){width:100%}
.engineering-panel{height:100%;min-width:0}
.engineering-steps{margin:var(--app-space-3) 0}
.engineering-actions{display:flex;flex-wrap:wrap;gap:var(--app-space-2);margin-top:var(--app-space-3)}
.engineering-chart{width:100%;height:290px;min-width:0}
@media(max-width:1199px){.engineering-form{grid-template-columns:repeat(2,minmax(0,1fr))}.engineering-panel{margin-bottom:var(--app-space-3)}}
@media(max-width:767px){.engineering-form{grid-template-columns:1fr}}
</style>
