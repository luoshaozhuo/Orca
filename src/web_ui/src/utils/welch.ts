/** Single-sided Welch PSD for uniformly sampled real-valued data.
 * Units: inputUnit^2/Hz. No resampling or missing-data interpolation.
 */
export type WindowName = 'hann' | 'rectangular'
export type SpectralBin = { frequency: number; power: number }
export type WelchResult = { bins: SpectralBin[]; segments: number; resolutionHz: number }

export function welchPsd(
  samples: readonly number[],
  periodSeconds: number,
  segmentSize: number,
  overlapFraction: number,
  windowName: WindowName,
  detrend = true,
): WelchResult {
  if (!Number.isFinite(periodSeconds) || periodSeconds <= 0) throw new Error('采样周期必须大于零')
  if (!Number.isInteger(segmentSize) || segmentSize < 4 || segmentSize > samples.length) {
    throw new Error('分段长度必须为 4 到样本量之间的整数')
  }
  if (!(overlapFraction >= 0 && overlapFraction < 1)) throw new Error('重叠率必须位于 [0, 1)')
  if (!samples.every(Number.isFinite)) throw new Error('信号中存在非有限数值')
  const hop = Math.max(1, Math.round(segmentSize * (1 - overlapFraction)))
  const window = Array.from({ length: segmentSize }, (_, index) =>
    windowName === 'hann'
      ? 0.5 * (1 - Math.cos(2 * Math.PI * index / (segmentSize - 1)))
      : 1,
  )
  const windowPower = window.reduce((sum, weight) => sum + weight * weight, 0)
  const samplingHz = 1 / periodSeconds
  const spectrum = new Array<number>(Math.floor(segmentSize / 2) + 1).fill(0)
  let segments = 0

  for (let start = 0; start + segmentSize <= samples.length; start += hop) {
    const block = samples.slice(start, start + segmentSize)
    const offset = detrend ? block.reduce((sum, value) => sum + value, 0) / segmentSize : 0
    for (let k = 0; k < spectrum.length; k++) {
      let real = 0
      let imaginary = 0
      for (let index = 0; index < segmentSize; index++) {
        const phase = 2 * Math.PI * k * index / segmentSize
        const value = (block[index] - offset) * window[index]
        real += value * Math.cos(phase)
        imaginary -= value * Math.sin(phase)
      }
      const isNyquist = segmentSize % 2 === 0 && k === segmentSize / 2
      const sidedFactor = k === 0 || isNyquist ? 1 : 2
      spectrum[k] += sidedFactor * (real * real + imaginary * imaginary)
        / (samplingHz * windowPower)
    }
    segments++
  }
  return {
    bins: spectrum.map((power, k) => ({
      frequency: k * samplingHz / segmentSize,
      power: power / segments,
    })),
    segments,
    resolutionHz: samplingHz / segmentSize,
  }
}
