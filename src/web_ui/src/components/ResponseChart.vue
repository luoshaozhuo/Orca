<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import BaseChart from './BaseChart.vue'
import { chartVisuals, chartAxisText, chartLegendText, chartZoomText } from '../utils/chartTheme'
import type { PlotPoint } from '../api/contracts'

const props=withDefaults(defineProps<{
  data: PlotPoint[]
  unit?: string
  title?: string
  showLimit?: boolean
  preview?: boolean
}>(), { unit: 'MW', title: '指令与响应', showLimit: true, preview: true })

function values(field: 'target' | 'actual' | 'limit'): Array<number | [string, number]> {
  return props.data.map(point => props.preview ? point[field] : [point.time, point[field]])
}

const option=computed<EChartsOption>(()=>({
  ...chartVisuals(),
  legend:{top:0,data:props.showLimit?['下发目标','实际反馈','功率限值']:['下发目标','实际反馈'],...chartLegendText()},
  grid:{left:58,right:20,top:45,bottom:44},
  xAxis:props.preview
    ? {type:'category' as const,boundaryGap:false,data:props.data.map(point=>point.time),
       ...chartAxisText(),axisLabel:{...chartAxisText().axisLabel,interval:14}}
    : {type:'time' as const,boundaryGap:false,...chartAxisText()},
  yAxis:{type:'value' as const,name:props.unit,scale:true,...chartAxisText()},
  dataZoom:[{type:'inside' as const},{type:'slider' as const,height:14,bottom:8,...chartZoomText()}],
  series:[
    {name:'下发目标',type:'line' as const,step:'end' as const,showSymbol:false,data:values('target')},
    {name:'实际反馈',type:'line' as const,smooth:props.preview,showSymbol:false,data:values('actual')},
    ...(props.showLimit ? [{name:'功率限值',type:'line' as const,showSymbol:false,
      lineStyle:{type:'dashed' as const},data:values('limit')}] : [])
  ]
}))
</script>
<template>
  <div class="trend-panel">
    <div class="trend-heading">{{title}} <span v-if="preview">模拟数据</span></div>
    <BaseChart :option="option" height="330px" :aria-label="title" />
  </div>
</template>
<style scoped>
.trend-panel{min-width:0;width:100%}
.trend-heading{font-weight:var(--app-font-weight-semibold);font-size:var(--app-font-panel-title);margin-bottom:var(--app-space-2)}
.trend-heading span{font-size:var(--app-font-caption);color:var(--app-text-secondary);font-weight:var(--app-font-weight-regular);margin-left:var(--app-space-2)}
</style>
