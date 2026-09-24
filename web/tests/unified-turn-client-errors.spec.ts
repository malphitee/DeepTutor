import { expect, it, vi } from 'vitest'
import { UnifiedTurnClient } from '@/features/chat/transport/UnifiedTurnClient'
import type { ServerEvent } from '@/contracts/generated/turn-protocol'

const fixture = vi.hoisted(() => ({ receive: (_event: ServerEvent) => {} }))
vi.mock('@/features/chat/transport/TurnRuntimeClient', () => ({
  TurnRuntimeClient: class {
    constructor(options: { onEvent: (event: ServerEvent) => void }) { fixture.receive = options.onEvent }
  },
}))

it('surfaces a rejected start as a terminal error instead of leaving the composer streaming', () => {
  const onEvent = vi.fn()
  new UnifiedTurnClient(onEvent)
  fixture.receive({ type: 'protocol_error', protocol_version: '2.0', error_code: 'start_turn_rejected', message: 'The conversation workspace changed. Reload the conversation.', retryable: true, session_id: 'chat', turn_id: '' })
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({
    type: 'error', content: 'The conversation workspace changed. Reload the conversation.', session_id: 'chat',
    metadata: expect.objectContaining({ turn_terminal: true, status: 'failed', reason: 'start_turn_rejected' }),
  }))
})

it('surfaces a failed subscription as a terminal retryable error', () => {
  const onEvent = vi.fn()
  new UnifiedTurnClient(onEvent)
  fixture.receive({ type: 'protocol_error', protocol_version: '2.0', error_code: 'subscription_failed', message: 'Retry subscription', retryable: true, session_id: 'chat', turn_id: 'turn' })
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', metadata: expect.objectContaining({ turn_terminal: true, status: 'failed', retryable: true }) }))
})

it('surfaces a scoped turn lookup failure as a terminal retryable error', () => {
  const onEvent = vi.fn()
  new UnifiedTurnClient(onEvent)
  fixture.receive({ type: 'protocol_error', protocol_version: '2.0', error_code: 'turn_not_found', message: 'Turn not found.', retryable: false, session_id: 'chat', turn_id: 'turn' })
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({
    type: 'error',
    content: 'Turn not found.',
    metadata: expect.objectContaining({ turn_terminal: true, status: 'failed', retryable: true, reason: 'turn_not_found' }),
  }))
})
