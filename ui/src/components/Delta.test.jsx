import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PrivacyProvider } from '../contexts/PrivacyContext.jsx'
import Delta, { tileDelta } from './Delta.jsx'

function renderDelta(props) {
  return render(
    <PrivacyProvider>
      <Delta label="October" {...props} />
    </PrivacyProvider>
  )
}

describe('Delta', () => {
  it('renders nothing without a comparison', () => {
    const { container } = renderDelta({ delta: null })
    expect(container).toBeEmptyDOMElement()
  })

  it('says "same as" for an exact zero', () => {
    renderDelta({ delta: 0 })
    expect(screen.getByText('same as October')).toBeInTheDocument()
  })

  it('says "same as" when float noise rounds to zero rupees', () => {
    renderDelta({ delta: 0.3 })
    expect(screen.getByText('same as October')).toBeInTheDocument()
    expect(screen.queryByText(/▲|▼/)).not.toBeInTheDocument()
  })

  it('shows the whole-rupee move otherwise', () => {
    renderDelta({ delta: -1250.4 })
    expect(screen.getByText(/▼/)).toHaveTextContent('▼ ₹1,250 vs October')
  })
})

describe('tileDelta', () => {
  it('measures the days and accounts the comparison covers, not the whole month', () => {
    const verdict = { money_out: 6000, compared: { money_out: 1000 }, prev: { money_out: 900 } }
    expect(tileDelta(verdict, 'money_out')).toBe(100)
  })
  it('is null with no comparison', () => {
    expect(tileDelta({ money_out: 6000, compared: null, prev: null }, 'money_out')).toBeNull()
  })
})
