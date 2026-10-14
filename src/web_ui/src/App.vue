<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import ResponseChart from './components/ResponseChart.vue'
import { station, controls, units, events, tests, response, unitResponse, reasons } from './mock/data'
type Page = 'overview' | 'control' | 'config' | 'commissioning' | 'events'
type Health = 'healthy' | 'unavailable' | 'unknown'
type Service = { id: string; name: string; url: string; state: Health; detail: string; checkedAt?: string }
const page = ref<Page>('overview')
const names: Record<Page,string> = {
  overview:'运行总览',control:'控制与分析',config:'配置管理',
  commissioning:'调试与整定',events:'事件与审计'
}
const services = ref<Service[]>([
  { id:'controller',name:'Controller',url:import.meta.env.VITE_CONTROLLER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'commander',name:'Wind Hub Commander',url:import.meta.env.VITE_COMMANDER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' },
  { id:'broker',name:'消息中间件',url:import.meta.env.VITE_BROKER_HEALTH_URL ?? '',state:'unknown',detail:'尚未配置健康接口' }
])
const menu = [
  { index:'overview',label:'运行总览' },{ index:'config',label:'配置管理' },
  { index:'control',label:'控制与分析' },{ index:'commissioning',label:'调试与整定' },
  { index:'events',label:'事件与审计' }
]
const mobile = ref(false)
const drawer = ref(false)
const showDetails = ref(false)
const trial = ref('agc')
const mode = ref('simulation')
const selectedConfig = ref('station')
const selectedControl = ref('station')
const selectedAnalysis = ref('realtime')
const commissioningTab = ref('test')
const mock = ref(true)
const selectedUnit = ref(units[0].id)
const activeUnit = computed(() => units.find(u => u.id === selectedUnit.value) ?? units[0])
const sampledUnits = computed(() => units.filter(u => u.type === '风机').slice(0,8))
const eventQuery = ref('')
const filteredEvents = computed(() => events.filter(e => Object.values(e).some(v => String(v).toLowerCase().includes(eventQuery.value.trim().toLowerCase()))))
const selectedTest = ref(tests[0].id)
const errors = computed(() => services.value.filter(s => s.state === 'unavailable'))
const statusLabel = (s: Health) => s === 'healthy' ? '可达' : s === 'unavailable' ? '不可达' : '未检测'
const statusType = (s: Health) => s === 'healthy' ? 'success' : s === 'unavailable' ? 'danger' : 'info'
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

          <template v-if="page==='overview'">
            <div class="summary-grid">
              <div class="panel-card panel" v-for="m in [
                {label:'并网点有功',value:mock?station.active.toFixed(1):'—',unit:'MW',foot:mock?'负荷率 62.2% · 演示':'实际测量待接入'},
                {label:'并网点无功',value:mock?station.reactive.toFixed(1):'—',unit:'Mvar',foot:mock?'全场 SVG + 逆变器 · 演示':'实际测量待接入'},
                {label:'并网点电压',value:mock?station.voltage.toFixed(2):'—',unit:'kV',foot:mock?'35 kV 母线 · 演示':'实际测量待接入'},
                {label:'系统频率',value:mock?station.frequency.toFixed(2):'—',unit:'Hz',foot:mock?'额定 50 Hz · 演示':'实际测量待接入'}
              ]" :key="m.label">
                <div class="metric-label">{{m.label}}</div><div class="app-metric metric-value">{{m.value}} <small>{{m.unit}}</small></div><div class="metric-foot">{{m.foot}}</div>
              </div>
            </div>
            <el-row :gutter="12" class="overview-rows">
              <el-col :xs="24" :lg="14">
                <div class="panel-card panel">
                  <h2 class="app-panel-title panel-title">控制功能与算法</h2>
                  <el-table :data="mock?controls:['AGC','AVC','PFR','IR'].map(name=>({name,enabled:'—',response:'—',algorithm:'—',uptime:'—'}))" stripe>
                    <el-table-column prop="name" label="功能" width="90"/>
                    <el-table-column prop="enabled" label="投入状态"/>
                    <el-table-column prop="response" label="响应状态"/>
                    <el-table-column prop="algorithm" label="当前算法"/>
                    <el-table-column prop="uptime" label="运行时长"/>
                  </el-table>
                  <p class="section-note">功能投入与功能响应分开显示；待接入 Controller 运行状态。</p>
                </div>
              </el-col>
              <el-col :xs="24" :lg="10">
                <div class="panel-card panel">
                  <h2 class="app-panel-title panel-title">上位机及 Wind Hub 通信</h2>
                  <div v-if="mock" class="status-row"><span>上位机 / 调度指令源</span><el-tag type="success">正常 · 演示</el-tag></div>
                  <div v-if="mock" class="status-row"><span>Commander gRPC</span><el-tag type="success">正常 · 演示</el-tag></div>
                  <div v-if="mock" class="status-row"><span>消息中间件订阅</span><el-tag type="success">正常 · 演示</el-tag></div>
                  <div v-if="!mock" class="status-row"><span>上位机 / 调度指令源</span><el-tag type="info">待接入</el-tag></div>
                  <div v-if="!mock" v-for="s in services" :key="s.id" class="status-row">
                    <span>{{s.name}}</span><el-tag :type="statusType(s.state)">{{statusLabel(s.state)}}</el-tag>
                  </div>
                  <p class="section-note">服务可达性不代表业务健康。数据时效、订阅质量及写入能力需后台独立上报。</p>

                </div>
              </el-col>
            </el-row>
            <el-row :gutter="12" class="overview-rows">
              <el-col :xs="24" :lg="14">
                <div class="panel-card panel">
                  <h2 class="app-panel-title panel-title">实时性能与总体配置</h2>
                  <el-descriptions :column="2" border>
                    <el-descriptions-item label="控制周期设定">{{mock?station.period:'—'}} ms</el-descriptions-item>
                    <el-descriptions-item label="控制周期偏差">{{mock?station.periodDeviation:'—'}} ms</el-descriptions-item>
                    <el-descriptions-item label="协调策略">{{mock?station.strategy:'—'}}</el-descriptions-item>
                    <el-descriptions-item label="配置版本">{{mock?station.version:'—'}}</el-descriptions-item>
                    <el-descriptions-item label="控制权限">{{mock?station.controlAuthority:'未知'}}</el-descriptions-item>
                    <el-descriptions-item label="最后有效测量">{{mock?station.lastSample:'—'}}</el-descriptions-item>
                  </el-descriptions>
                </div>
              </el-col>
              <el-col :xs="24" :lg="10">
                <div class="panel-card panel">
                  <h2 class="app-panel-title panel-title">运行概览</h2>
                  <div class="status-row"><span>全场额定容量</span><b>{{mock?station.rated:'—'}} MW</b></div>
                  <div class="status-row"><span>可用调节容量</span><b>{{mock?station.available:'—'}} MW</b></div>
                  <div class="status-row"><span>控制执行异常</span><b>{{mock?'1 项待确认':'—'}}</b></div>
                  <div class="status-row"><span>最近控制事件</span><b>{{mock?'14:41:12 AGC 调节':'—'}}</b></div>
                </div>
              </el-col>
            </el-row>
            <div class="panel-card panel overview-trend"><ResponseChart v-if="mock" :data="response" title="全场有功 · 调度目标与实际反馈"/><el-empty v-else description="等待并网点趋势数据"/></div>
          </template>

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

          <template v-else-if="page==='control'">
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
                    <el-select v-model="selectedAnalysis" style="width:140px"><el-option label="实时趋势" value="realtime"/><el-option label="历史回放" value="history"/></el-select>
                  </div>
                  <ResponseChart v-if="mock" :data="selectedControl==='station'?response:unitResponse" :title="selectedControl==='station'?'全场指令与实测响应':'机组指令与实测响应'"/><div v-else class="chart-placeholder">趋势接口待接入</div>
                  <p class="section-note">同一时轴展示指令时间、反馈数据时间及质量状态。可跳转调试页查看性能指标。</p>
                  <el-button @click="navigate('commissioning')">转到调试与效果分析</el-button>
                </div>
                <div class="panel-card panel"><h2 class="app-panel-title panel-title">本次控制效果</h2>
                  <div class="status-row"><span>跟踪误差</span><b>{{mock?'0.62 MW':'—'}}</b></div>
                  <div class="status-row"><span>响应延迟</span><b>{{mock?'1.4 s':'—'}}</b></div>
                  <div class="status-row"><span>调节时间</span><b>{{mock?'17.2 s':'—'}}</b></div>
                  <div class="status-row"><span>指令 ID / 审计</span><b>{{mock?'CMD-20261014-1032':'—'}}</b></div>
                  <el-button @click="navigate('events')">查看控制审计</el-button>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="page==='commissioning'">
            <el-tabs v-model="commissioningTab">
              <el-tab-pane label="试验与整定" name="test"/>
              <el-tab-pane label="效果与成因分析" name="performance"/>
              <el-tab-pane label="历史回放" name="replay"/>
              <el-tab-pane label="通信诊断" name="communication"/>
            </el-tabs>
            <div class="panel-card panel stack" v-if="commissioningTab==='test'">
              <h2 class="app-panel-title panel-title">仿真试验与参数整定</h2>
              <div class="toolbar"><span>控制功能</span>
                <el-select v-model="trial" style="width:150px"><el-option label="AGC" value="agc"/>
                  <el-option label="PFR（二期）" value="pfr" disabled/><el-option label="IR（二期）" value="ir" disabled/>
                  <el-option label="AVC（二期）" value="avc" disabled/></el-select>
                <span>试验模式</span><el-select v-model="mode" style="width:150px"><el-option label="离线仿真" value="simulation"/></el-select>
                <el-button type="primary" disabled>运行试验</el-button><el-button disabled>保存候选参数</el-button>
              </div>
              <ResponseChart v-if="mock" :data="response" title="AGC 阶跃试验 · 指令 / 响应"/><div v-else class="chart-placeholder">试验引擎待接入</div>
              <el-table v-if="mock" :data="tests" stripe><el-table-column prop="id" label="试验编号"/><el-table-column prop="date" label="时间"/><el-table-column prop="algorithm" label="算法"/><el-table-column prop="settle" label="调节时间 (s)"/><el-table-column prop="overshoot" label="超调 (%)"/><el-table-column prop="error" label="稳态误差 (MW)"/></el-table>
              <p class="section-note">调试参数与正式运行参数分离；未确认之前不自动生效。</p>
            </div>
            <div class="panel-card panel" v-else-if="commissioningTab==='performance'">
              <h2 class="app-panel-title panel-title">性能指标与成因诊断</h2>
              <template v-if="mock"><ResponseChart :data="response" title="控制效果分析 · AGC-014"/><div class="summary-grid"><div class="panel-card panel" v-for="(m,i) in [{label:'调节时间',value:'17.2 s'},{label:'超调量',value:'2.1%'},{label:'稳态误差',value:'0.62 MW'},{label:'最大分配残差',value:'2.4 MW'}]" :key="i"><div class="metric-label">{{m.label}}</div><div class="app-metric metric-value">{{m.value}}</div></div></div><el-table :data="reasons"><el-table-column prop="name" label="候选成因"/><el-table-column prop="contribution" label="影响程度（示意）"/><el-table-column prop="evidence" label="证据示意" min-width="200"/></el-table></template><el-empty v-else description="性能与诊断接口待接入"/>
            </div>
            <div class="panel-card panel" v-else-if="commissioningTab==='replay'">
              <h2 class="app-panel-title panel-title">历史指令与响应回放</h2>
              <template v-if="mock"><el-select v-model="selectedTest" style="width:200px"><el-option v-for="t in tests" :key="t.id" :label="t.id+' · '+t.date" :value="t.id"/></el-select><ResponseChart :data="response" title="历史控制记录回放 · 示例"/></template><el-empty v-else description="历史回放接口待接入"/>
            </div>
            <div class="panel-card panel" v-else>
              <h2 class="app-panel-title panel-title">Controller、Commander、消息中间件诊断</h2>
              <div class="service-health"><div class="service-tile" v-for="s in services" :key="s.id">
                <b>{{s.name}}</b><el-tag :type="mock?'success':statusType(s.state)">{{mock?'健康 · 演示':statusLabel(s.state)}}</el-tag>
                <p class="secondary">{{mock?'连接正常，最近消息 56 ms 前 · 模拟':s.detail}}</p><span class="section-note">检查：{{s.checkedAt??'—'}}</span>
              </div></div>
              <p class="section-note">这里只检查服务及数据链路，设备协议调试仍由 Wind Hub 负责。</p>
            </div>
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
