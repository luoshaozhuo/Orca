<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'
import { response } from '../mock/data'

type Channel = 'actual' | 'target' | 'total'
const channel = ref<Channel>('actual')
const mode = ref<'time' | 'spectrum' | 'correlation'>('time')
const windowType = ref<'hann' | 'rectangular'>('hann')
const removeMean = ref(true)
const samplePeriod = 2 // Mock fixture has one sample every 2 seconds.
const seriesMap: Record<Channel,string>={actual:'实测有功',target:'目标有功',total:'设备合计有功'}
const raw = computed(()=>response.map(row=>row[channel.value]))
const stats=computed(()=>{
 const a=raw.value,n=a.length, mean=a.reduce((s,v)=>s+v,0)/n
 return {n,mean,rms:Math.sqrt(a.reduce((s,v)=>s+v*v,0)/n),min:Math.min(...a),max:Math.max(...a)}
})
const spectrum=computed(()=>{
 const values=raw.value,n=values.length,mean=stats.value.mean
 const w=values.map((_,i)=>windowType.value==='hann'?0.5*(1-Math.cos(2*Math.PI*i/(n-1))):1)
 const coherentGain=w.reduce((s,v)=>s+v,0)/n
 const input=values.map((v,i)=>(v-(removeMean.value?mean:0))*w[i])
 return Array.from({length:Math.floor(n/2)+1},(_,k)=>{
  let re=0,im=0
  for(let i=0;i<n;i++){const phase=2*Math.PI*k*i/n;re+=input[i]*Math.cos(phase);im-=input[i]*Math.sin(phase)}
  const amplitude=Math.hypot(re,im)/(n*coherentGain)*(k===0?1:2)
  return {frequency:k/(n*samplePeriod),amplitude}
 })
})
const correlation=computed(()=>{
 const x=response.map(p=>p.target),y=raw.value,n=x.length
 const mx=x.reduce((s,v)=>s+v,0)/n,my=y.reduce((s,v)=>s+v,0)/n
 return Array.from({length:41},(_,i)=>{
  const lag=i-20;let numerator=0,dx=0,dy=0
  for(let j=0;j<n;j++){const k=j+lag;if(k<0||k>=n)continue;const a=x[j]-mx,b=y[k]-my;numerator+=a*b;dx+=a*a;dy+=b*b}
  return {lag:lag*samplePeriod,value:dx>0&&dy>0?numerator/Math.sqrt(dx*dy):0}
 })
})
const chartElement=ref<HTMLDivElement|null>(null)
let chart:ECharts|undefined
let observer:ResizeObserver|undefined
function draw(){
 if(!chart)return
 let x:string[],y:number[],xName:string,yName:string
 if(mode.value==='spectrum'){x=spectrum.value.map(v=>v.frequency.toFixed(4));y=spectrum.value.map(v=>v.amplitude);xName='频率 (Hz)';yName='幅值 (MW)'}
 else if(mode.value==='correlation'){x=correlation.value.map(v=>String(v.lag));y=correlation.value.map(v=>v.value);xName='目标领先/滞后 (s)';yName='相关系数'}
 else {x=response.map(v=>v.time);y=raw.value;xName='时间';yName='功率 (MW)'}
 chart.setOption({
  tooltip:{trigger:'axis'},grid:{left:65,right:28,top:42,bottom:65},
  xAxis:{type:'category',name:xName,boundaryGap:false,data:x,axisLabel:{hideOverlap:true}},
  yAxis:{type:'value',name:yName,scale:mode.value!=='correlation',min:mode.value==='correlation'?-1:undefined,max:mode.value==='correlation'?1:undefined},
  dataZoom:[{type:'inside'},{type:'slider',height:14,bottom:12}],
  series:[{type:'line',name:seriesMap[channel.value],showSymbol:false,data:y.map(v=>Number(v.toFixed(5)))}]
 },true)
}
onMounted(()=>{if(!chartElement.value)return;chart=echarts.init(chartElement.value);draw();observer=new ResizeObserver(()=>chart?.resize());observer.observe(chartElement.value)})
watch([raw,mode,windowType,removeMean],draw)
onUnmounted(()=>{observer?.disconnect();chart?.dispose()})
</script>
<template>
 <div class="panel-card panel">
  <h2 class="app-panel-title panel-title">时域 · 频谱 · 延迟相关分析</h2>
  <el-alert title="基于模拟有功波形进行真实数值计算；频谱为 Hann/矩形窗单次 DFT，不是 Welch PSD。相关性不证明因果。示例采样间隔 2 s。" type="info" :closable="false" show-icon/>
  <div class="signal-tools">
   <el-select v-model="channel" aria-label="分析信号"><el-option v-for="(label,key) in seriesMap" :key="key" :label="label" :value="key"/></el-select>
   <el-segmented v-model="mode" :options="[{label:'时域',value:'time'},{label:'频谱',value:'spectrum'},{label:'时滞相关',value:'correlation'}]"/>
   <el-select v-if="mode==='spectrum'" v-model="windowType" aria-label="窗函数"><el-option label="Hann" value="hann"/><el-option label="矩形" value="rectangular"/></el-select>
   <el-checkbox v-if="mode==='spectrum'" v-model="removeMean" label="去均值"/>
  </div>
  <el-descriptions :column="4" border class="signal-stats">
   <el-descriptions-item label="样本量">{{stats.n}}</el-descriptions-item>
   <el-descriptions-item label="均值">{{stats.mean.toFixed(2)}} MW</el-descriptions-item>
   <el-descriptions-item label="RMS">{{stats.rms.toFixed(2)}} MW</el-descriptions-item>
   <el-descriptions-item label="峰峰值">{{(stats.max-stats.min).toFixed(2)}} MW</el-descriptions-item>
  </el-descriptions>
  <div ref="chartElement" class="signal-plot" role="img" aria-label="时域或频域分析图"/>
  <p class="section-note">频率分辨率 {{(1/(stats.n*samplePeriod)).toFixed(5)}} Hz · Nyquist 频率 {{(1/(2*samplePeriod)).toFixed(3)}} Hz。真实数据接入时必须校验均匀采样、时钟对齐、缺测与质量标记。</p>
 </div>
</template>
<style scoped>
.signal-tools{display:flex;align-items:center;flex-wrap:wrap;gap:var(--app-space-3);margin:var(--app-space-3) 0}
.signal-tools .el-select{width:165px}
.signal-stats{margin-bottom:var(--app-space-3)}
.signal-plot{width:100%;height:380px;min-width:0}
@media(max-width:767px){.signal-stats :deep(.el-descriptions__table){min-width:500px}}
</style>
