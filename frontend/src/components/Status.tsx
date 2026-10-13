import { Tag, Tooltip } from 'antd'
import { CircleAlert, ShieldCheck } from 'lucide-react'
import { formatScore, titleCase } from '../format'
import type { ReliabilityLevel, ReviewStatus, RunStatus } from '../types'

const reliabilityColors: Record<ReliabilityLevel, string> = {
  recommended: 'green',
  review: 'gold',
  rejected: 'red',
}

const reviewColors: Record<ReviewStatus, string> = {
  unreviewed: 'default',
  accepted: 'green',
  rejected: 'red',
  skipped: 'blue',
}

const runColors: Record<RunStatus, string> = {
  queued: 'default',
  validating: 'processing',
  generating_candidates: 'processing',
  inferencing: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
  interrupted: 'warning',
}

export function ReliabilityTag({ value }: { value: ReliabilityLevel }) {
  return <Tag color={reliabilityColors[value]}>{titleCase(value)}</Tag>
}

export function ReviewTag({ value }: { value: ReviewStatus }) {
  return <Tag color={reviewColors[value]}>{titleCase(value)}</Tag>
}

export function RunStatusTag({ value }: { value: RunStatus }) {
  return <Tag color={runColors[value]}>{titleCase(value)}</Tag>
}

export function ScoreDisplay({
  raw,
  calibrated,
  compact = false,
}: {
  raw: number
  calibrated: number | null
  compact?: boolean
}) {
  if (calibrated === null) {
    return (
      <Tooltip title="No deployment-validated calibration profile was applied. This is a raw model score, not a calibrated probability.">
        <span className="score-display score-unvalidated">
          <CircleAlert size={14} />
          <span>{formatScore(raw)}{!compact && <small>Raw score</small>}</span>
        </span>
      </Tooltip>
    )
  }
  return (
    <Tooltip title={`Raw score: ${formatScore(raw)}`}>
      <span className="score-display score-validated">
        <ShieldCheck size={14} />
        <span>{formatScore(calibrated)}{!compact && <small>Calibrated</small>}</span>
      </span>
    </Tooltip>
  )
}
