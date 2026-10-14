<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import OverviewPage from './pages/OverviewPage.vue'
import ControlPage from './pages/ControlPage.vue'
import EngineeringWorkbench from './components/EngineeringWorkbench.vue'
import AnalysisPage from './pages/AnalysisPage.vue'
import { station, controls, units, events } from './mock/data'
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
const selectedConfig = ref('station')
const mock = ref(true)
const eventQuery = ref('')
const filteredEvents = computed(() => events.filter(e => Object.values(e).some(v => String(v).toLowerCase().includes(eventQuery.value.trim().toLowerCase()))))
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
          <template v-else-if="page==='config'">
            <el-tabs v-model="selectedConfig">
              <el-tab-pane label="全场配置" name="station">
                <div class="content-grid">
                  <div class="panel-card panel">
                    <h2 class="app-panel-title panel-title">电厂与控制配置</h2>
                    <el-tabs>
                      <el-tab-pane label="电厂参数"><el-descriptions v-if="mock" border :column="2"><el-descriptions-item label="电站名称">{{station.name}}</el-descriptions-item><el-descriptions-item label="额定容量">{{station.rated}} MW</el-descriptions-item><el-descriptions-item label="控制周期">{{station.period}} ms</el-descriptions-item><el-descriptions-item label="可调容量">{{station.available}} MW</el-descriptions-item></el-descriptions><el-empty v-else description="并网点、额定容量、运行约束：后端配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="控制功能"><el-table v-if="mock" :data="controls"><el-table-column prop="name" label="控制功能"/><el-table-column prop="enabled" label="状态"/><el-table-column prop="algorithm" label="算法"/></el-table><el-empty v-else description="控制功能配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="协调策略"><el-descriptions v-if="mock" border><el-descriptions-item label="合成策略">default</el-descriptions-item><el-descriptions-item label="IR 优先级">1</el-descriptions-item><el-descriptions-item label="PFR 优先级">2</el-descriptions-item><el-descriptions-item label="AGC 优先级">3</el-descriptions-item></el-descriptions><el-empty v-else description="协调策略配置待接入"/></el-tab-pane>
                    </el-tabs>
                  </div>
                  <div class="panel-card panel"><h2 class="app-panel-title panel-title">接入与配置管理</h2>
                    <div class="status-row"><span>上位机 / 调度接口</span><el-tag :type="mock?'success':'info'">{{mock?'已配置 · 演示':'待配置'}}</el-tag></div>
                    <div class="status-row"><span>Commander gRPC</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>消息中间件及订阅</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>配置版本及生效状态</span><b>{{mock?station.version+' · 生效中':'—'}}</b></div>
                    <div class="toolbar" style="margin-top:16px">
                      <el-button disabled>导入配置</el-button><el-button disabled>导出配置</el-button>
                      <el-button type="primary" disabled>校验并应用</el-button>
                    </div>
                    <p class="section-note">加载、校验、保存、应用分离；接口未接入时不能虚报成功。</p>
                  </div>
                </div>
              </el-tab-pane>
              <el-tab-pane label="机组配置" name="units">
                <div class="panel-card panel"><h2 class="app-panel-title panel-title">机组与资源管理</h2>
                  <div class="toolbar"><el-input placeholder="搜索机组名称或编号" disabled style="max-width:260px"/>
                    <el-select disabled placeholder="设备类型" style="width:150px"/></div>
                  <el-table :data="mock?units:[]" stripe style="margin-top:16px">
                    <el-table-column prop="id" label="设备编号"/><el-table-column prop="type" label="类型"/><el-table-column prop="available" label="可用容量 (MW)"/>
                    <el-table-column label="控制参与"><template #default="scope"><el-tag :type="scope.row.enabled?'success':'info'">{{scope.row.enabled?'参与':'退出'}}</el-tag></template></el-table-column><el-table-column prop="status" label="运行状态"/><el-table-column prop="weight" label="控制权重"/>
                  </el-table>
                  <el-empty v-if="!mock" description="待对接 Wind Hub 设备与测点元数据"/>
                </div>
              </el-tab-pane>
            </el-tabs>
          </template>

          <ControlPage v-else-if="page==='control'" :mock="mock" @navigate="navigate" />
          <AnalysisPage v-else-if="page==='analysis'" :mock="mock" />

          <template v-else-if="page==='commissioning'">
            <EngineeringWorkbench />
          </template>

          <template v-else-if="page==='events'">
            <div class="panel-card panel"><h2 class="app-panel-title panel-title">事件、审计与操作追踪</h2>
              <div class="toolbar"><el-select disabled placeholder="事件类型" style="width:150px"/>
                <el-input v-model="eventQuery" :disabled="!mock" clearable placeholder="搜索指令 ID、机组或操作人" style="max-width:280px"/>
                <el-button disabled>导出审计</el-button></div>
              <el-table :data="mock?filteredEvents:[]" stripe style="margin-top:16px">
                <el-table-column prop="time" label="时间" min-width="175"/><el-table-column prop="kind" label="类型"/><el-table-column prop="object" label="对象"/>
                <el-table-column prop="result" label="执行结果"/><el-table-column prop="command" label="关联指令 ID" min-width="180"/><el-table-column prop="detail" label="详情" min-width="240"/>
              </el-table>
              <el-empty v-if="!mock" description="控制事件、配置变更及逐点执行记录接口待接入"/>
            </div>
          </template>

        </div>
      </el-main>
    </el-container>
  </el-container>
</template>
