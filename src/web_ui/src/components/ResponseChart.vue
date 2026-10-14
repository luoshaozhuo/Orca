<script setup lang="ts">
import { computed } from 'vue'
import BaseChart from './BaseChart.vue'
import { chartVisuals, chartAxisText, chartLegendText, chartZoomText } from '../utils/chartTheme'
import type { PlotPoint } from '../mock/data'
const props=withDefaults(defineProps<{ data:PlotPoint[]; unit?:string; title?:string }>(),{unit:'MW',title:'指令与响应'})
const option=computed(()=>({
 ...chartVisuals(),
 legend:{top:0,data:['下发目标','实际反馈','功率限值'],...chartLegendText()},
 grid:{left:58,right:20,top:45,bottom:44},
 xAxis:{type:'category' as const,boundaryGap:false,data:props.data.map(p=>p.time),...chartAxisText(),axisLabel:{...chartAxisText().axisLabel,interval:14}},
 yAxis:{type:'value' as const,name:props.unit,scale:true,...chartAxisText()},
 dataZoom:[{type:'inside' as const},{type:'slider' as const,height:14,bottom:8,...chartZoomText()}],
 series:[
  {name:'下发目标',type:'line' as const,step:'end' as const,showSymbol:false,data:props.data.map(p=>p.target)},
  {name:'实际反馈',type:'line' as const,smooth:true,showSymbol:false,data:props.data.map(p=>p.actual)},
  {name:'功率限值',type:'line' as const,showSymbol:false,lineStyle:{type:'dashed' as const},data:props.data.map(p=>p.limit)}
 ]
}))
</script>
<template><div class="trend-panel"><div class="trend-heading">{{title}} <span>MOCK DATA</span></div><BaseChart :option="option" height="330px" :aria-label="title" /></div></template>
<style scoped>
.trend-panel{min-width:0;width:100%}.trend-heading{font-weight:var(--app-font-weight-semibold);font-size:var(--app-font-panel-title);margin-bottom:var(--app-space-2)}.trend-heading span{font-size:var(--app-font-caption);color:var(--app-text-secondary);font-weight:var(--app-font-weight-regular);margin-left:var(--app-space-2)}
.trend-chart{height:330px;width:100%;min-width:0}
</style>
