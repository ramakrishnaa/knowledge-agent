export interface ToolInfo {
  name: string
  description: string
}

export interface AgentInfo {
  name: string
  title: string
  description: string
  tools: ToolInfo[]
}

export interface DocumentInfo {
  source: string
  chunks: number
}

export interface Usage {
  input_tokens: number
  output_tokens: number
}

// One event from the backend's SSE stream; fields depend on `type`.
export type ChatEvent = { type: string } & Record<string, any>

export type ToolStatus = 'running' | 'done' | 'error'

export interface ToolPart {
  kind: 'tool'
  id: string
  name: string
  status: ToolStatus
  input?: unknown
  output?: unknown
  isError?: boolean
}

export interface TextPart {
  kind: 'text' | 'thinking' | 'notice' | 'error'
  text: string
}

// The supervisor handing the turn to one of the specialist agents.
export interface HandoffPart {
  kind: 'handoff'
  agent: string
  title: string
  reason: string
}

export type Part = TextPart | ToolPart | HandoffPart

export interface UserMessage {
  role: 'user'
  text: string
}

export interface AssistantMessage {
  role: 'assistant'
  parts: Part[]
  pending: boolean
  usage?: Usage
}

export type Message = UserMessage | AssistantMessage

// Fold one backend event into an assistant message's list of parts.
export function applyEvent(parts: Part[], event: ChatEvent): Part[] {
  const last = parts[parts.length - 1]
  switch (event.type) {
    case 'text':
    case 'thinking':
      if (last?.kind === event.type) {
        return [...parts.slice(0, -1), { ...last, text: last.text + event['text'] }]
      }
      return [...parts, { kind: event.type, text: event['text'] }]
    case 'tool_start':
      return [...parts, { kind: 'tool', id: event['id'], name: event['name'], status: 'running' }]
    case 'tool_call':
      return parts.map((p) => (p.kind === 'tool' && p.id === event['id'] ? { ...p, input: event['input'] } : p))
    case 'tool_result':
      return parts.map((p) =>
        p.kind === 'tool' && p.id === event['id']
          ? { ...p, output: event['output'], isError: event['is_error'], status: event['is_error'] ? 'error' : 'done' }
          : p,
      )
    case 'handoff':
      return [...parts, { kind: 'handoff', agent: event['agent'], title: event['title'], reason: event['reason'] }]
    case 'notice':
    case 'error':
      return [...parts, { kind: event.type, text: event['message'] }]
    default:
      return parts
  }
}

export function formatOutput(output: unknown): string {
  if (typeof output !== 'string') return JSON.stringify(output, null, 2)
  try {
    return JSON.stringify(JSON.parse(output), null, 2)
  } catch {
    return output
  }
}
