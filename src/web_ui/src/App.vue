<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import OverviewPage from './pages/OverviewPage.vue'
import ConfigPage from './pages/ConfigPage.vue'
import EventsPage from './pages/EventsPage.vue'
import ControlPage from './pages/ControlPage.vue'
import EngineeringPage from './pages/EngineeringPage.vue'
import AnalysisPage from './pages/AnalysisPage.vue'
type Page = 'overview' | 'control' | 'analysis' | 'config' | 'commissioning' | 'events'
type Health = 'healthy' | 'unavailable' | 'unknown'
type Service = { id: string; name: string; url: string; state: Health; detail: string; checkedAt?: string }
const page = ref<Page>('overview')
const names: Record<Page,string> = {
  overview:'运行总览',control:'控制工作台',analysis:'分析与评估',config:'配置管理',
  commissioning:'工程与整定',events:'事件与审计'
}
const services = ref<Service[]>([
  { id:'controller',name:'Controller',url:import.meta.env.VITE_CONTROLLER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'commander',name:'Wind Hub Commander',url:import.meta.env.VITE_COMMANDER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'broker',name:'消息中间件',url:import.meta.env.VITE_BROKER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' }
])
const menu = [
  { index:'overview',label:'运行总览' },{ index:'control',label:'控制工作台' },
  { index:'analysis',label:'分析与评估' },{ index:'commissioning',label:'工程与整定' },
  { index:'config',label:'配置管理' },
  { index:'events',label:'事件与审计' }
]
const mobile = ref(false)
const drawer = ref(false)
const showDetails = ref(false)
const mock = ref(true)
const errors = computed(() => services.value.filter(s => s.state === 'unavailable'))
function navigate(key:string){page.value=key as Page;drawer.value=false}
let timer: ReturnType<typeof setInterval> | undefined
let checking = false
async function checkServices(){
  if(checking) return
  checking = true
  try {
    await Promise.all(services.value.map(async service => {
    if(!service.url){service.state='unknown';service.detail='未配置检查端点';return}
    const controller = new AbortController()
    const timeout = setTimeout(()=>controller.abort(),3000)
    try {
      const response=await fetch(service.url,{signal:controller.signal,cache:'no-store',credentials:'same-origin'})
      service.state=response.ok?'healthy':'unavailable'
      service.detail=response.ok?'HTTP 健康端点响应正常':`HTTP ${response.status}`
    }catch(e){
      service.state='unavailable'
      service.detail=e instanceof Error?e.message:'请求失败'
    }finally{clearTimeout(timeout);service.checkedAt=new Date().toLocaleTimeString()}
    }))
  } finally {
    checking = false
  }
}
function resize(){mobile.value=window.innerWidth<768}
onMounted(()=>{resize();window.addEventListener('resize',resize);void checkServices();timer=setInterval(()=>void checkServices(),1000)})
onUnmounted(()=>{window.removeEventListener('resize',resize);if(timer)clearInterval(timer)})
</script>

<template>
  <el-container class="app-shell">
    <el-aside v-if="!mobile" width="216px" class="app-sidebar">
      <div class="brand"><b>OR</b><span>Orca</span></div>
      <el-menu class="app-menu" :default-active="page" @select="navigate">
        <el-menu-item v-for="item in menu" :key="item.index" :index="item.index">{{ item.label }}</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="app-header">
        <div class="actions">
          <el-button v-if="mobile" text aria-label="打开导航" @click="drawer=true">☰</el-button>
          <span class="header-title">Orca <span class="header-subtitle">Control Console</span></span>
        </div>
        <div class="header-right">
          <div class="version-summary" aria-label="版本信息">
            <span class="version-item"><span class="version-label">Web</span><b>v0.1.0</b></span>
            <span class="version-separator">|</span>
            <span class="version-item"><span class="version-label">Controller</span><b>—</b></span>
          </div>
        </div>
      </el-header>
      <el-drawer v-model="drawer" title="Orca" direction="ltr" size="260px">
        <el-menu class="app-menu" :default-active="page" @select="navigate">
          <el-menu-item v-for="item in menu" :key="item.index" :index="item.index">{{ item.label }}</el-menu-item>
        </el-menu>
      </el-drawer>
      <div v-if="errors.length" class="error-bar" role="alert">
        <span>部分后端不可达：{{ errors.map(s=>s.name).join('、') }}。其他可用功能不受影响。</span>
        <el-button link type="danger" @click="showDetails=!showDetails">{{showDetails?'收起':'查看详情'}}</el-button>
      </div>
      <div v-if="showDetails && errors.length" class="panel-card panel">
        <div v-for="s in errors" :key="s.id" class="log-line">{{s.name}}：{{s.detail}} · 最近检查 {{s.checkedAt??'未检查'}}</div>
      </div>
      <el-main class="app-main">
        <div class="content">
          <div class="head page-head">
            <div><h1 class="app-page-title">{{ names[page] }}</h1><p>{{mock?'所有指标、记录与响应曲线均为用于空间与交互评审的模拟数据，不代表实际运行。':'实时接口模式：未连接的数据保持未知，不伪造运行值。'}}</p></div>

          </div>

          <OverviewPage v-if="page==='overview'" :mock="mock" :services="services" />
          <ConfigPage v-else-if="page==='config'" :mock="mock" />
          <ControlPage v-else-if="page==='control'" :mock="mock" @navigate="navigate" />
          <AnalysisPage v-else-if="page==='analysis'" :mock="mock" />

          <EngineeringPage v-else-if="page==='commissioning'" />

          <EventsPage v-else-if="page==='events'" :mock="mock" />

        </div>
      </el-main>
    </el-container>
  </el-container>
</template>
