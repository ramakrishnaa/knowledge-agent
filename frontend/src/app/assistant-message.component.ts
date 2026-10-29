import { Component, input } from '@angular/core'
import { DecimalPipe } from '@angular/common'
import { AssistantMessage } from './models'
import { MarkdownPipe } from './markdown.pipe'
import { ToolCardComponent } from './tool-card.component'

@Component({
  selector: 'app-assistant-message',
  imports: [DecimalPipe, MarkdownPipe, ToolCardComponent],
  host: { class: 'message assistant' },
  template: `
    @for (part of message().parts; track $index) {
      @if (part.kind === 'handoff') {
        <div class="handoff">
          <span class="handoff-agent">→ {{ part.title }}</span>
          @if (part.reason) {
            <span class="muted">{{ part.reason }}</span>
          }
        </div>
      } @else if (part.kind === 'text') {
        <div [innerHTML]="part.text | markdown"></div>
      } @else if (part.kind === 'thinking') {
        <details class="thinking">
          <summary>Thinking</summary>
          <p>{{ part.text }}</p>
        </details>
      } @else if (part.kind === 'tool') {
        <app-tool-card [part]="part" />
      } @else {
        <div [class]="part.kind">{{ part.text }}</div>
      }
    }
    @if (message().pending && message().parts.length === 0) {
      <div class="muted">Working…</div>
    }
    @if (message().usage; as usage) {
      <div class="usage">{{ usage.input_tokens | number }} in · {{ usage.output_tokens | number }} out</div>
    }
  `,
})
export class AssistantMessageComponent {
  message = input.required<AssistantMessage>()
}
