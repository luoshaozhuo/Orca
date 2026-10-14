<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
type Page = 'overview' | 'control' | 'devices' | 'config' | 'commissioning' | 'events' | 'services'
type Health = 'healthy' | 'unavailable' | 'unknown'
type Service = { id: string; name: string; url: string; state: Health; detail: string; checkedAt?: string }
const page = ref<Page>('overview')
const names: Record<Page,string> = {
  overview:'运行总览',control:'控制监视',devices:'设备与数据',config:'配置管理',
  commissioning:'调试工具',events:'事件与审计',services:'服务通信诊断'
}
const services = ref<Service[]>([
  { id:'controller',name:'Controller',url:import.meta.env.VITE_CONTROLLER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'commander',name:'Wind Hub Commander',url:import.meta.env.VITE_COMMANDER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'broker',name:'消息中间件',url:import.meta.env.VITE_BROKER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' }
])
const menu = [
  { index:'overview',label:'运行总览' },{ index:'control',label:'控制监视' },
  { index:'devices',label:'设备与数据' },{ index:'config',label:'配置管理' },
  { index:'commissioning',label:'调试工具' },{ index:'events',label:'事件与审计' },
  { index:'services',label:'服务通信诊断' }
]
const mobile = ref(false)
const drawer = ref(false)
const showDetails = ref(false)
const trial = ref('agc')
const mode = ref('simulation')
const mock = ref(false)
const errors = computed(() => services.value.filter(s => s.state === 'unavailable'))
const statusLabel = (s: Health) => s === 'healthy' ? '可达' : s === 'unavailable' ? '不可达' : '未检测'
const statusType = (s: Health) => s === 'healthy' ? 'success' : s === 'unavailable' ? 'danger' : 'info'
function navigate(key:string){page.value=key as Page;drawer.value=false}
let timer: ReturnType<typeof setInterval> | undefined
async function checkServices(){
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
}
function resize(){mobile.value=window.innerWidth<768}
onMounted(()=>{resize();window.addEventListener('resize',resize);void checkServices();timer=setInterval(()=>void checkServices(),15000)})
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
          <el-tag type="info" effect="plain">Prototype</el-tag>
          <el-tag :type="errors.length?'danger':'info'" effect="plain">{{ errors.length? `${errors.length} 项通信异常`:'服务按需连接' }}</el-tag>
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
      <div v-if="showDetails && errors.length" class="panel">
        <div v-for="s in errors" :key="s.id" class="log-line">{{s.name}}：{{s.detail}} · 最近检查 {{s.checkedAt??'未检查'}}</div>
      </div>
      <el-main class="app-main">
        <div class="content">
          <div class="page-head">
            <div><h1>{{ names[page] }}</h1><p>统一 Web 工作台 · 工程原型（未连接业务数据时不显示虚构运行值）</p></div>
            <div class="toolbar"><el-button @click="checkServices">刷新服务状态</el-button></div>
          </div>

          <template v-if="page==='overview'">
            <div class="summary-grid">
              <div class="panel" v-for="m in [{label:'并网点有功',value:'—',unit:'MW'},{label:'并网点无功',value:'—',unit:'Mvar'},{label:'控制运行状态',value:'待接入',unit:''},{label:'可达后端',value:String(services.filter(s=>s.state==='healthy').length),unit:'/ 3'}]" :key="m.label">
                <div class="metric-label">{{m.label}}</div><div class="metric-value">{{m.value}} <small>{{m.unit}}</small></div>
                <div class="metric-foot">等待实际测量/状态接口</div>
              </div>
            </div>
            <div class="content-grid">
              <div class="panel"><h2 class="panel-title">控制运行概览</h2>
                <div v-for="c in ['AGC','AVC','PFR','IR']" :key="c" class="status-row"><b>{{c}}</b><el-tag type="info">状态未接入</el-tag></div>
              </div>
              <div class="panel"><h2 class="panel-title">服务状态</h2>
                <div class="status-row" v-for="s in services" :key="s.id">
                  <span>{{s.name}}</span><el-tag :type="statusType(s.state)">{{statusLabel(s.state)}}</el-tag>
                </div>
                <p class="section-note">HTTP 健康端点可达性不代表控制安全状态或消息数据新鲜度。</p>
              </div>
            </div>
          </template>

          <template v-else-if="page==='control'">
            <div class="panel"><h2 class="panel-title">控制功能</h2>
              <el-table :data="['AGC','AVC','PFR','IR'].map(name=>({name,state:'未知',target:'—',actual:'—',reason:'业务状态接口待接入'}))" stripe>
                <el-table-column prop="name" label="功能"/><el-table-column prop="state" label="投入状态"/>
                <el-table-column prop="target" label="控制参考"/><el-table-column prop="actual" label="实际反馈"/>
                <el-table-column prop="reason" label="说明" min-width="180"/>
              </el-table>
              <p class="section-note">原型只读；不得以网页连接状态代替控制授权及写入状态机。</p>
            </div>
          </template>

          <template v-else-if="page==='devices'">
            <div class="panel"><h2 class="panel-title">设备与实时数据</h2>
              <el-empty description="Wind Hub 数据查询接口待接入；设备测点将以表格及质量状态展示。"/>
            </div>
          </template>

          <template v-else-if="page==='config'">
            <div class="panel"><h2 class="panel-title">配置管理（原 Admin 能力）</h2>
              <el-tabs><el-tab-pane label="控制器"/><el-tab-pane label="协调策略"/><el-tab-pane label="资源"/><el-tab-pane label="任务"/><el-tab-pane label="系统"/></el-tabs>
              <el-empty description="配置读写契约尚未对接；本阶段禁止伪保存。"/>
            </div>
          </template>

          <template v-else-if="page==='commissioning'">
            <div class="panel stack">
              <div><h2 class="panel-title">控制调试试验</h2><span class="section-note">第一期以 AGC 仿真为切入点，提供可扩展试验入口。</span></div>
              <div class="toolbar">
                <span>控制功能</span>
                <el-select v-model="trial" style="width:150px">
                  <el-option label="AGC" value="agc"/><el-option label="PFR（规划）" value="pfr" disabled/>
                  <el-option label="IR（规划）" value="ir" disabled/><el-option label="AVC（规划）" value="avc" disabled/>
                </el-select>
                <span>试验模式</span><el-select v-model="mode" style="width:140px"><el-option label="离线仿真" value="simulation"/></el-select>
                <el-button type="primary" disabled>运行试验（待接入）</el-button>
              </div>
              <div class="content-grid">
                <div><h2 class="panel-title">目标与反馈曲线</h2><div class="chart-placeholder">时间序列曲线区域<br>等待真实试验数据；不伪造仿真结果</div></div>
                <div><h2 class="panel-title">性能与诊断</h2>
                  <div class="status-row"><span>响应延迟</span><b>—</b></div>
                  <div class="status-row"><span>调节时间</span><b>—</b></div>
                  <div class="status-row"><span>稳态偏差</span><b>—</b></div>
                  <div class="status-row"><span>成因诊断</span><span class="secondary">等待试验</span></div>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="page==='events'">
            <div class="panel"><h2 class="panel-title">控制事件与执行审计</h2>
              <el-empty description="事件审计查询接口待接入；支持按时间、控制功能与结果筛选。"/>
            </div>
          </template>

          <template v-else-if="page==='services'">
            <div class="panel stack"><h2 class="panel-title">服务可达性</h2>
              <div class="service-health">
                <div class="service-tile" v-for="s in services" :key="s.id"><b>{{s.name}}</b>
                  <el-tag :type="statusType(s.state)">{{statusLabel(s.state)}}</el-tag>
                  <p class="secondary">{{s.detail}}</p><span class="section-note">最近检查：{{s.checkedAt??'—'}}</span>
                </div>
              </div>
              <p class="section-note">仅执行安全的 HTTP GET 探测。Commander gRPC 与消息新鲜度应由 Web Server 提供经过聚合的健康诊断接口。</p>
            </div>
          </template>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>
