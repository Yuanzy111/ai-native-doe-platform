import { describe, expect, it } from 'vitest'
import { canGenerateInitialDesign, displayStatus } from './runState'

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
})
