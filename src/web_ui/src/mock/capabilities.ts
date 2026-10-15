export type CapabilityDetail = {
  id: string
  type: string
  reason: string
  secondary?: string
  capacity?: number
  since: string
}
export type CapabilityMetric = {
  id: string
  label: string
  value: string
  unit: string
  summary: string
  definition: string
  details: CapabilityDetail[]
}
const items = (ids: string[], reason: string, capacity = 0): CapabilityDetail[] =>
  ids.map((id, index) => ({ id, type: id.startsWith('BESS') ? '储能 PCS' : '风机', reason,
    capacity, since: `14:${String(22 + index).padStart(2, '0')}:00` }))

// Mock counters and evidence are intentionally paired: card total - available = details.length.
// Do not treat these demonstration records as production device state.
export const capabilityMetrics: CapabilityMetric[] = [
  {
    id: 'communication', label: '通信正常设备', value: '78 / 82', unit: '台',
    summary: '4 台通信异常',
    definition: '分子：同一状态快照中通信健康且设备数据未过期的设备数；分母：已登记并纳入通信监测的设备总数。HTTP 服务可达不等于设备通信正常。',
    details: items(['WTG-03', 'WTG-08', 'WTG-12', 'WTG-16'], '通信异常')
  },
  {
    id: 'active_devices', label: '有功可控设备', value: '76 / 82', unit: '台',
    summary: '2 台标杆机组 · 4 台通信异常',
    definition: '分子：同一快照内具备有功调节资格、通信有效、控制权有效且未旁路或闭锁的设备数；分母：配置的有功控制候选设备总数。零上调裕度不必然表示完全不可控。',
    details: [
      ...items(['WTG-01', 'WTG-02'], '标杆机组'),
      ...items(['WTG-03', 'WTG-08', 'WTG-12', 'WTG-16'], '通信异常')
    ]
  },
  {
    id: 'reactive_devices', label: '无功可控设备', value: '74 / 82', unit: '台',
    summary: '4 台通信异常 · 3 台无功闭锁 · 1 台就地控制',
    definition: '分子：同一快照内具备无功控制资格、控制权及合法动作方向的设备数；分母：配置的无功控制候选设备总数。无功容量需区分吸收与发出方向。',
    details: [
      ...items(['WTG-03', 'WTG-08', 'WTG-12', 'WTG-16'], '通信异常'),
      ...items(['WTG-06', 'WTG-11', 'WTG-14'], '无功闭锁'),
      ...items(['WTG-17'], '就地控制')
    ]
  },
  {
    id: 'active_capacity', label: '可控有功', value: '128.6 / 160.0', unit: 'MW',
    summary: '21.4 MW 设备受限 · 10.0 MW 不可用',
    definition: '分子：当前快照、控制方向和设备约束下可分配的有功能力；分母：配置的候选设备理论有功能力基准。此值不是当前 POI 发电功率，详情中应区分上调与下调裕度。',
    details: [
      ...items(['WTG-03', 'WTG-08'], '通信不可用', 5.0),
      ...items(['WTG-10', 'WTG-13', 'BESS-01'], '调节裕度受限', 7.13)
    ]
  },
  {
    id: 'reactive_capacity', label: '可控无功', value: '72.4 / 96.0', unit: 'Mvar',
    summary: '15.6 Mvar 能力受限 · 8.0 Mvar 闭锁',
    definition: '分子：基于设备 P/Q 联合能力边界计算的当前可分配无功能力；分母：候选设备理论无功能力基准。必须按无功吸收/发出方向计算，不可直接将正负能力相加。',
    details: [
      ...items(['WTG-06', 'WTG-11'], '无功闭锁', 4),
      ...items(['WTG-03', 'BESS-02', 'WTG-14'], 'P/Q 联合能力受限', 5.2)
    ]
  }
]
