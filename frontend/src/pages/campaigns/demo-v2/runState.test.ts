import { describe, expect, it } from 'vitest'
import { canGenerateInitialDesign, displayStatus, isLifecycleLocked } from './runState'

describe('displayStatus', () => {
  it('shows Draft before any run exists', () => {
    expect(displayStatus(null, false)).toBe('Draft')
  })

  it('mirrors the server status when clean', () => {
    expect(displayStatus('DesignSpaceValidated', false)).toBe('DesignSpaceValidated')
  })

  it('drops a validated run back to Draft while there are unsaved edits', () => {
    expect(displayStatus('DesignSpaceValidated', true)).toBe('Draft')
  })

  it('keeps a non-validated server status even while dirty', () => {
    expect(displayStatus('Draft', true)).toBe('Draft')
  })
})

describe('canGenerateInitialDesign', () => {
  it('is enabled only when validated and clean', () => {
    expect(canGenerateInitialDesign('DesignSpaceValidated', false)).toBe(true)
  })

  it('is disabled with unsaved edits', () => {
    expect(canGenerateInitialDesign('DesignSpaceValidated', true)).toBe(false)
  })

  it('is disabled before validation', () => {
    expect(canGenerateInitialDesign('Draft', false)).toBe(false)
    expect(canGenerateInitialDesign(null, false)).toBe(false)
  })

  it('is disabled once a batch already exists', () => {
    expect(canGenerateInitialDesign('DesignSpaceValidated', false, true)).toBe(false)
  })
})

describe('isLifecycleLocked', () => {
  it('is unlocked before an initial design exists', () => {
    expect(isLifecycleLocked(null)).toBe(false)
    expect(isLifecycleLocked('Draft')).toBe(false)
    expect(isLifecycleLocked('DesignSpaceValidated')).toBe(false)
  })

  it('locks once the run has produced recommendations', () => {
    expect(isLifecycleLocked('RecommendationsPending')).toBe(true)
    expect(isLifecycleLocked('AwaitingMeasurements')).toBe(true)
    expect(isLifecycleLocked('RoundClosed')).toBe(true)
    expect(isLifecycleLocked('Completed')).toBe(true)
    expect(isLifecycleLocked('Archived')).toBe(true)
  })
})
