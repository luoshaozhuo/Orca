export const station = {
  name:'海上示范风储电站',rated:300,active:186.7,reactive:12.4,voltage:35.12,frequency:49.98,
  available:263.8,period:200,periodDeviation:3.8,strategy:'default',version:'2026.10.14-rc1',
  controlAuthority:'远方自动',lastSample:'14:41:32.204',uptime:'12天 08时 16分'
}
export const controls = [
 {name:'AGC',enabled:'已投入',response:'跟踪中',algorithm:'前馈 + PI',uptime:'12天 08时',target:'195.0 MW',actual:'186.7 MW',limit:'正常'},
 {name:'AVC',enabled:'已投入',response:'调节中',algorithm:'电压 PI',uptime:'12天 08时',target:'35.0 kV',actual:'35.12 kV',limit:'正常'},
 {name:'PFR',enabled:'已投入',response:'待触发',algorithm:'下垂 + 死区',uptime:'12天 08时',target:'0.0 MW',actual:'0.0 MW',limit:'正常'},
 {name:'IR',enabled:'已退出',response:'无输出',algorithm:'虚拟惯量',uptime:'—',target:'0.0 MW',actual:'0.0 MW',limit:'未投入'}
]
export const units = Array.from({length:18},(_,i)=>{
 const storage=i>=14
 return {
  id:storage?`BESS-${String(i-13).padStart(2,'0')}`:`WTG-${String(i+1).padStart(2,'0')}`,
  type:storage?'储能 PCS':'风机',
  capacity:storage?5:6.25,
  active:+(storage?(1.4+(i%4)*0.35):(3.1+(i%6)*0.42)).toFixed(2),
  target:+(storage?(1.8+(i%4)*0.25):(3.6+(i%6)*0.32)).toFixed(2),
  available:storage?4.8:6.1,
  enabled:i!==6,
  status:i===6?'维护旁路':i===11?'限功率':'正常',
  weight:1,
  soc:storage?+(53+i*2.1).toFixed(1):null
 } 
})
export const events = Array.from({length:32},(_,i)=>({
 time:`2026-10-14 14:${String(41-Math.floor(i/2)).padStart(2,'0')}:${i%2?'42':'12'}`,
 kind:['AGC调节','配置应用','设备约束','通信事件','调试试验'][i%5],
 object:i%3===0?'全场':units[i%units.length].id,
 result:i%13===0?'UNKNOWN':i%7===0?'受限':'成功',
 command:`CMD-20261014-${String(1032-i).padStart(4,'0')}`,
 detail:i%13===0?'设备写入结果未确认，禁止自动重传':i%7===0?'可用容量限制，分配残差已记录':'执行链路及审计记录可追溯'
}))
export const tests = [
 {id:'AGC-014',date:'2026-10-14 14:20',algorithm:'前馈 + PI',settle:17.2,overshoot:2.1,error:0.62},
 {id:'AGC-013',date:'2026-10-14 13:35',algorithm:'PI',settle:24.8,overshoot:4.6,error:0.95},
 {id:'AGC-012',date:'2026-10-14 11:10',algorithm:'前馈 + PI',settle:19.4,overshoot:2.5,error:0.71}
]
export type PlotPoint={time:string; target:number; actual:number; total:number; limit:number}
export const response:PlotPoint[]=Array.from({length:91},(_,i)=>{
 const sec=i*2,after=Math.max(sec-20,0)
 const target=sec<20?160:195
 const actual=sec<20?160+0.4*Math.sin(sec/4):160+35*(1-Math.exp(-after/19))+(after>10?2.8*Math.exp(-Math.max(after-20,0)/25)*Math.sin(after/8):0)
 return {time:`${Math.floor(sec/60).toString().padStart(2,'0')}:${(sec%60).toString().padStart(2,'0')}`,target,actual:+actual.toFixed(2),total:+(actual-2.2).toFixed(2),limit:205}
})
export const unitResponse=response.map((p,i)=>({...p,target:i<10?3.6:4.9,actual:+(i<10?3.6:(3.6+1.3*(1-Math.exp(-(i-10)/12)))).toFixed(2),limit:6.25}))
export const reasons=[
 {name:'风机响应滞后',contribution:'高',evidence:'11 台风机响应时间高于参考模型',level:'warning'},
 {name:'有功可用容量限制',contribution:'中',evidence:'WTG-12 等机组发生限功率',level:'warning'},
 {name:'指令执行异常',contribution:'低',evidence:'最近试验无确认失败的指令',level:'success'}
]
