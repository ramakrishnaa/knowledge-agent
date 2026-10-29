import { Component, ElementRef, afterRenderEffect, inject, signal, viewChild } from '@angular/core'
import { ApiService } from './api.service'
import { AssistantMessageComponent } from './assistant-message.component'
import { KnowledgeBaseComponent } from './knowledge-base.component'
import { AgentInfo, AssistantMessage, Message, applyEvent } from './models'

const EXAMPLES = [
  'What documents are in the knowledge base?',
  'Summarise the travel policy in five bullet points.',
  'What is the hotel limit per night in Tokyo?',
  'When does a trip need approval from the department head?',
]

@Component({
  selector: 'app-root',
  imports: [AssistantMessageComponent, KnowledgeBaseComponent],
  templateUrl: './app.component.html',
})
export class AppComponent {
  private api = inject(ApiService)

  protected readonly examples = EXAMPLES
  protected messages = signal<Message[]>([])
  protected input = signal('')
  protected busy = signal(false)
  protected agents = signal<AgentInfo[]>([])
  private sessionId: string | null = null
  private abort: AbortController | null = null
  private bottom = viewChild<ElementRef<HTMLElement>>('bottom')

  constructor() {
    this.api.fetchAgents().then((a) => this.agents.set(a)).catch(() => this.agents.set([]))

    afterRenderEffect(() => {
      this.messages()
      this.bottom()?.nativeElement.scrollIntoView({ behavior: 'smooth' })
    })
  }

  private updateAssistant(fn: (m: AssistantMessage) => AssistantMessage) {
    this.messages.update((prev) => {
      const last = prev[prev.length - 1]
      return last?.role === 'assistant' ? [...prev.slice(0, -1), fn(last)] : prev
    })
  }

  async send(text: string) {
    const message = text.trim()
    if (!message || this.busy()) return
    this.input.set('')
    this.busy.set(true)
    this.messages.update((prev) => [
      ...prev,
      { role: 'user', text: message },
      { role: 'assistant', parts: [], pending: true },
    ])

    const controller = new AbortController()
    this.abort = controller
    try {
      await this.api.streamChat({
        message,
        sessionId: this.sessionId,
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === 'session') this.sessionId = event['session_id']
          else if (event.type === 'done') this.updateAssistant((m) => ({ ...m, pending: false, usage: event['usage'] }))
          else this.updateAssistant((m) => ({ ...m, parts: applyEvent(m.parts, event) }))
        },
      })
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        this.updateAssistant((m) => ({ ...m, parts: applyEvent(m.parts, { type: 'error', message: err.message }) }))
      }
    } finally {
      this.updateAssistant((m) => ({ ...m, pending: false }))
      this.busy.set(false)
      this.abort = null
    }
  }

  stop() {
    this.abort?.abort()
  }

  async newChat() {
    this.abort?.abort()
    if (this.sessionId) await this.api.deleteSession(this.sessionId).catch(() => {})
    this.sessionId = null
    this.messages.set([])
  }

  onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      this.send(this.input())
    }
  }
}
