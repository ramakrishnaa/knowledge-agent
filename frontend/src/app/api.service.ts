import { Injectable } from '@angular/core'
import { AgentInfo, ChatEvent, DocumentInfo } from './models'

@Injectable({ providedIn: 'root' })
export class ApiService {
  // POST a chat message and parse the Server-Sent Events stream the backend returns.
  async streamChat(opts: {
    message: string
    sessionId: string | null
    onEvent: (event: ChatEvent) => void
    signal: AbortSignal
  }): Promise<void> {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: opts.message, session_id: opts.sessionId }),
      signal: opts.signal,
    })
    if (!res.ok || !res.body) throw new Error(`Request failed: HTTP ${res.status}`)

    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() ?? ''
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((l) => l.startsWith('data: '))
        if (line) opts.onEvent(JSON.parse(line.slice(6)))
      }
    }
  }

  async fetchAgents(): Promise<AgentInfo[]> {
    const res = await fetch('/api/agents')
    return res.ok ? res.json() : []
  }

  async listDocuments(): Promise<DocumentInfo[]> {
    const res = await fetch('/api/documents')
    return res.ok ? res.json() : []
  }

  async uploadDocument(file: File): Promise<DocumentInfo> {
    const body = new FormData()
    body.append('file', file)
    const res = await fetch('/api/documents', { method: 'POST', body })
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Upload failed: HTTP ${res.status}`)
    return res.json()
  }

  async addTextDocument(title: string, text: string): Promise<DocumentInfo> {
    const res = await fetch('/api/documents/text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, text }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Saving failed: HTTP ${res.status}`)
    return res.json()
  }

  async deleteDocument(source: string): Promise<void> {
    await fetch(`/api/documents/${encodeURIComponent(source)}`, { method: 'DELETE' })
  }

  async deleteSession(sessionId: string): Promise<void> {
    await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
  }
}
