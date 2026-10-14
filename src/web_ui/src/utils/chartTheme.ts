import type { EChartsOption } from 'echarts'

type Typography = { family: string; body: number; label: number; caption: number; text: string; muted: string; border: string }

function styleValue(style: CSSStyleDeclaration, token: string, fallback: string): string {
  return style.getPropertyValue(token).trim() || fallback
}

/** Use the same typography and semantic colors as Wind Hub / Element Plus. */
export function chartTypography(): Typography {
  const style = getComputedStyle(document.documentElement)
  return {
    family: styleValue(style, '--app-font-family', 'system-ui, sans-serif'),
    body: parseFloat(styleValue(style, '--app-font-body', '12px')),
    label: parseFloat(styleValue(style, '--app-font-label', '11px')),
    caption: parseFloat(styleValue(style, '--app-font-caption', '10px')),
    text: styleValue(style, '--app-text-regular', '#606266'),
    muted: styleValue(style, '--app-text-secondary', '#909399'),
    border: styleValue(style, '--app-border-soft', '#ebeef5'),
  }
}

/** Shared option fragment; local charts should not redeclare sizes or chart fonts. */
export function chartVisuals(): Pick<EChartsOption, 'textStyle' | 'tooltip'> {
  const t = chartTypography()
  return {
    textStyle: { fontFamily: t.family, fontSize: t.body, color: t.text },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontFamily: t.family, fontSize: t.body, color: t.text },
      padding: [7, 10],
    },
  }
}

export function chartAxisText() {
  const t = chartTypography()
  return {
    nameTextStyle: { fontFamily: t.family, fontSize: t.label, color: t.muted },
    axisLabel: { fontFamily: t.family, fontSize: t.label, color: t.muted, hideOverlap: true },
    axisLine: { lineStyle: { color: t.border } },
    splitLine: { lineStyle: { color: t.border } },
  }
}

export function chartLegendText() {
  const t = chartTypography()
  return { textStyle: { fontFamily: t.family, fontSize: t.label, color: t.text }, itemWidth: 12, itemHeight: 7 }
}

export function chartZoomText() {
  const t = chartTypography()
  return { textStyle: { fontFamily: t.family, fontSize: t.caption, color: t.muted } }
}
