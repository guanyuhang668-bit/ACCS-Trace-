import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import ReactEChartsCore from 'echarts-for-react/esm/core'

echarts.use([BarChart, GridComponent, TooltipComponent, SVGRenderer])

interface ScoreBin {
  lower: number
  upper: number
  count: number
}

export function ScoreChart({ bins, calibrationValidated }: { bins: ScoreBin[]; calibrationValidated: boolean }) {
  const option = {
    animationDuration: 300,
    grid: { left: 46, right: 18, top: 34, bottom: 42 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ axisValue: string; value: number }>) =>
        `${params[0]?.axisValue}<br/>Candidates: ${params[0]?.value ?? 0}`,
    },
    xAxis: {
      type: 'category',
      name: calibrationValidated ? 'Calibrated probability' : 'Raw model score',
      nameLocation: 'middle',
      nameGap: 28,
      data: bins.map((bin) => `${bin.lower.toFixed(1)}–${bin.upper.toFixed(1)}`),
      axisLabel: { color: '#5d666f' },
    },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#5d666f' } },
    series: [
      {
        type: 'bar',
        data: bins.map((bin) => bin.count),
        itemStyle: { color: '#167f79', borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 54,
      },
    ],
  }

  return <ReactEChartsCore echarts={echarts} option={option} style={{ height: 330 }} notMerge />
}
