import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ScoreDisplay } from './Status'

describe('ScoreDisplay', () => {
  it('labels an uncalibrated value as a raw score', () => {
    render(<ScoreDisplay raw={0.8123} calibrated={null} />)
    expect(screen.getByText('0.812')).toBeInTheDocument()
    expect(screen.getByText('Raw score')).toBeInTheDocument()
    expect(screen.queryByText('Calibrated')).not.toBeInTheDocument()
  })

  it('uses the calibrated probability when deployment calibration exists', () => {
    render(<ScoreDisplay raw={0.91} calibrated={0.73} />)
    expect(screen.getByText('0.730')).toBeInTheDocument()
    expect(screen.getByText('Calibrated')).toBeInTheDocument()
  })
})
